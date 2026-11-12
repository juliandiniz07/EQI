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


def test_falha_no_download_conta_tentativa_e_para_em_3(banco, monkeypatch):
    hoje(monkeypatch)

    def quebra(cl, link):
        raise RuntimeError("bloqueado")

    for _ in range(4):
        c = resumos.processar_pendentes(ResumidorFalso(), dias=30, limite=1, client=object(), baixar_fn=quebra)
    with eventos.conectar() as con:
        assert con.execute("SELECT MAX(resumo_tentativas) FROM fatos").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM fatos WHERE resumo IS NOT NULL").fetchone()[0] == 0


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
