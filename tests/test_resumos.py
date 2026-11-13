import eventos
import resumos
from tests.conftest import HOJE


class ResumidorFalso:
    def __init__(self, texto="Opções: (i) ações INTR; (ii) BDR Nível I; (iii) venda facilitada. Prazo até 16/10 às 15h."):
        self.texto, self.vistos = texto, []

    def resumir(self, empresa, assunto, tipo, conteudo):
        self.vistos.append((empresa, tipo))
        return self.texto


def hoje(monkeypatch):
    class D(resumos.date):
        @classmethod
        def today(cls):
            return HOJE
    monkeypatch.setattr(resumos, "date", D)


def test_resume_pendentes_e_guarda_no_banco(banco, monkeypatch):
    hoje(monkeypatch)
    res = ResumidorFalso()
    c = resumos.processar_pendentes(res, dias=30, limite=50, client=object(), baixar_fn=lambda cl, link: ("pdf", b"%PDF-x"))
    assert c["ok"] >= 1 and c["erro"] == 0
    with eventos.conectar() as con:
        assert con.execute("SELECT COUNT(*) FROM fatos WHERE resumo LIKE '%venda facilitada%'").fetchone()[0] == c["ok"]
        # segunda rodada não repete o que já foi resumido
        assert resumos.processar_pendentes(res, dias=30, limite=50, client=object(), baixar_fn=lambda cl, link: ("pdf", b"%PDF-x")) == {"ok": 0, "sem_conteudo": 0, "erro": 0}


def test_falha_no_download_nao_gasta_tentativa_e_interrompe_apos_3(banco, monkeypatch):
    hoje(monkeypatch)
    chamadas = []

    def quebra(cl, link):
        chamadas.append(link)
        raise RuntimeError("bloqueado")

    c = resumos.processar_pendentes(ResumidorFalso(), dias=30, limite=50, client=object(), baixar_fn=quebra)
    assert len(chamadas) == 3 and c["erro"] == 3  # a CVM não entrega: para cedo em vez de queimar a lista toda
    with eventos.conectar() as con:
        assert con.execute("SELECT COALESCE(MAX(resumo_tentativas),0) FROM fatos").fetchone()[0] == 0


def test_sem_conteudo_nao_vira_resumo(banco, monkeypatch):
    hoje(monkeypatch)
    c = resumos.processar_pendentes(ResumidorFalso("SEM_CONTEUDO"), dias=30, limite=50, client=object(), baixar_fn=lambda cl, l: ("texto", "x" * 300))
    assert c["sem_conteudo"] >= 1
    with eventos.conectar() as con:
        assert con.execute("SELECT COUNT(*) FROM fatos WHERE resumo IS NOT NULL").fetchone()[0] == 0


def test_resumo_chega_ao_assistente(banco, monkeypatch):
    import api
    from fastapi.testclient import TestClient
    hoje(monkeypatch)
    resumos.processar_pendentes(ResumidorFalso(), dias=30, limite=50, client=object(), baixar_fn=lambda cl, l: ("pdf", b"%PDF-x"))
    monkeypatch.setenv("APP_SENHA", "s")
    monkeypatch.setenv("SECRET_KEY", "k")

    class Redator:
        dados = None
        def redigir(self, p, h, dados):
            Redator.dados = dados
            return "ok"

    c = TestClient(api.criar_app(redator=Redator(), hoje=lambda: HOJE))
    tok = c.post("/login", json={"senha": "s"}).json()["token"]
    c.post("/consultar", json={"pergunta": "teve OPA da Brava?"}, headers={"Authorization": f"Bearer {tok}"})
    assert "venda facilitada" in Redator.dados["eventos"][0]["resumo_do_documento"]
    assert "instrucao_extra" in Redator.dados


def test_filtra_por_empresa(banco, monkeypatch):
    hoje(monkeypatch)
    res = ResumidorFalso()
    resumos.processar_pendentes(res, dias=400, limite=50, empresa="gerdau", client=object(), baixar_fn=lambda cl, l: ("pdf", b"%PDF-x"))
    assert {e for e, _ in res.vistos} == {"GERDAU S.A."}


def test_refazer_substitui_e_preserva_em_caso_de_falha(banco, monkeypatch):
    hoje(monkeypatch)
    pdf = lambda cl, l: ("pdf", b"%PDF-x")
    resumos.processar_pendentes(ResumidorFalso("versão antiga"), dias=400, limite=50, client=object(), baixar_fn=pdf)
    resumos.processar_pendentes(ResumidorFalso("versão nova"), dias=400, limite=50, client=object(), baixar_fn=pdf, refazer=True)
    with eventos.conectar() as con:
        assert con.execute("SELECT COUNT(*) FROM fatos WHERE resumo='versão antiga'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM fatos WHERE resumo='versão nova'").fetchone()[0] > 0

    def quebra(cl, l):
        raise RuntimeError("fora do ar")

    c = resumos.processar_pendentes(ResumidorFalso("outra"), dias=400, limite=50, client=object(), baixar_fn=quebra, refazer=True)
    assert c["ok"] == 0
    with eventos.conectar() as con:
        assert con.execute("SELECT COUNT(*) FROM fatos WHERE resumo='versão nova'").fetchone()[0] > 0
