import pytest

import atendimento


@pytest.fixture(autouse=True)
def _limpa_cache():
    atendimento._cache_docs.clear()


def ev(data, assunto, link="https://x/doc"):
    return {"empresa": "AXIA ENERGIA S.A.", "data": data[8:10] + "/" + data[5:7] + "/" + data[:4], "data_entrega": data + " 10:00",
            "categoria": "Fato Relevante", "assunto": assunto, "link": link}


LISTA = [
    ev("2026-10-20", "5ª operação de resgate ou conversão das ações PNC", "https://x/5"),
    ev("2026-10-23", "6ª operação de resgate ou conversão das ações PNC - período de manifestação", "https://x/6"),
    ev("2026-11-05", "Cancelamento do registro perante a SEC", "https://x/sec"),
]


def baixar_falso(cl, link):
    return ("pdf", b"%PDF-" + link.encode())


def test_detecta_pergunta_especifica():
    assert atendimento.pergunta_especifica("Qual o prazo para aprovar a pendência de AXIA?")
    assert atendimento.pergunta_especifica("o cliente precisa fazer o quê? quais as opções?")
    assert not atendimento.pergunta_especifica("teve algo da Axia essa semana?")


def test_sem_seletor_usa_o_mais_recente():
    class Redator: ...
    docs, duvidas = atendimento.documentos_para_pergunta(Redator(), "prazo?", LISTA, baixar_fn=baixar_falso)
    assert duvidas is None and [d["conteudo"] for d in docs] == [b"%PDF-https://x/sec"]


def test_seletor_escolhe_a_operacao_pedida():
    class Redator:
        def escolher_eventos(self, pergunta, candidatos, maximo):
            assert candidatos[0]["data"] == "05/11/2026"  # lista entregue do mais recente para o mais antigo
            return [1]
    docs, _ = atendimento.documentos_para_pergunta(Redator(), "prazo da pendência", LISTA, baixar_fn=baixar_falso)
    assert len(docs) == 1 and "6ª operação" in docs[0]["rotulo"]


def test_falha_no_seletor_e_no_download_nao_derruba():
    class Redator:
        def escolher_eventos(self, *a):
            raise RuntimeError("fora do ar")

    def quebra(cl, l):
        raise RuntimeError("bloqueado")
    assert atendimento.documentos_para_pergunta(Redator(), "prazo?", LISTA, baixar_fn=quebra) == ([], None)


def test_evento_ambiguo_pergunta_em_vez_de_chutar():
    class Redator:
        def escolher_eventos(self, pergunta, candidatos, maximo):
            return {"claro": False, "candidatos": [0, 2]}
    docs, duvidas = atendimento.documentos_para_pergunta(Redator(), "qual o prazo?", LISTA, baixar_fn=baixar_falso)
    assert docs == [] and [d["data"] for d in duvidas] == ["05/11/2026", "20/10/2026"]
    ctx = atendimento.contexto_documentos(docs, duvidas)
    assert len(ctx["eventos_possiveis"]) == 2 and "NÃO chute" in ctx["instrucao_resposta_direta"]


def test_sugestoes_de_seguimento():
    from conhecimento import Ficha

    ficha = Ficha("x.md", "Raízen", ["raizen"], [], None, "Orientação\n...\nCronograma\n...\nPasso a passo do cliente\n...\nPossíveis perguntas dos clientes\n")
    s = atendimento.sugestoes("subscrição de Raízen", "Raízen", [ficha], None, [])
    assert s[0] == "Qual a orientação para Raízen?" and len(s) == 4 and all("Raízen" in x for x in s)
    duv = [{"empresa": "RAIZEN S.A.", "assunto": "Aumento de Capital Privado", "data": "06/11/2026"},
           {"empresa": "RAIZEN S.A.", "assunto": "Novo código de negociação: RAIZ55", "data": "03/11/2026"}]
    assert atendimento.sugestoes("qual o prazo?", None, [], duv, [])[0] == "RAIZEN S.A.: Aumento de Capital Privado (06/11)"
    ev = [{"empresa": "LIGHT S.A.", "assunto": "RJ"}, {"empresa": "WEG S.A.", "assunto": "Desdobramento"}]
    assert atendimento.sugestoes("teve algo hoje?", None, [], None, ev) == ["Detalhar LIGHT S.A.: RJ", "Detalhar WEG S.A.: Desdobramento"]
