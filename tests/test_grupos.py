import time

import pytest
from fastapi.testclient import TestClient

import api
import eventos
import grupos
from tests.conftest import HOJE

GRUPO = "120363019502650977@g.us"


class Redator:
    def redigir(self, pergunta, historico, dados):
        return "Resposta do robô"


class Classificador:
    def __init__(self, valor=True):
        self.valor = valor

    def relevante(self, texto, contexto):
        return self.valor


class Enviador:
    def __init__(self):
        self.enviadas = []
        self.citadas = []

    def enviar(self, grupo, texto, citar=None):
        self.enviadas.append((grupo, texto))
        self.citadas.append(citar)


def msg(texto, grupo=GRUPO, quem="5511999990001", mid="m1", fromMe=False, **extra):
    return {"event": "messages.upsert", "data": {"key": {"remoteJid": grupo, "fromMe": fromMe, "id": mid, "participant": quem + "@s.whatsapp.net"},
            "message": {"conversation": texto}}}


@pytest.fixture
def montar(banco, monkeypatch):
    monkeypatch.setenv("GRUPOS_WEBHOOK_SEGREDO", "seg")
    monkeypatch.setenv("GRUPOS_PERMITIDOS", GRUPO)
    monkeypatch.setenv("APP_SENHA", "x")
    monkeypatch.setenv("SECRET_KEY", "k")

    def _montar(modo="ativo", classificador=None, **env):
        monkeypatch.setenv("GRUPOS_MODO", modo)
        monkeypatch.setenv("GRUPOS_ESPERA_SEG", "0")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        enviador = Enviador()
        cfg = grupos.Config()
        from consulta import Interpretador
        robo = grupos.RoboDeGrupos(cfg, Interpretador(), Redator(), classificador or Classificador(), enviador, api.resposta_sem_ia, hoje=lambda: HOJE)
        return robo, enviador
    return _montar


def roda(robo, payload):
    motivo, m = robo.receber(payload)
    return motivo, (robo.processar(m) if m else None)


def test_responde_pergunta_do_assessor(montar):
    robo, env = montar()
    _, r = roda(robo, msg("Boa tarde pessoal, cliente perguntou se temos algum direcionamento sobre a subscrição da AZTE3"))
    assert r == "enviado" and env.enviadas == [(GRUPO, "Resposta do robô")]


def test_modo_sombra_nao_envia(montar):
    robo, env = montar(modo="sombra")
    assert roda(robo, msg("teve alguma OPA essa semana?"))[1] == "sombra"
    assert env.enviadas == []
    with eventos.conectar() as con:
        assert con.execute("SELECT decisao FROM grupos_log").fetchone()[0] == "sombra"


def test_ignora_grupo_nao_autorizado_e_nao_grava(montar):
    robo, env = montar()
    assert roda(robo, msg("teve OPA?", grupo="999@g.us"))[0] == "ignorado:grupo_nao_autorizado"
    with eventos.conectar() as con:
        assert con.execute("SELECT COUNT(*) FROM grupos_log").fetchone()[0] == 0


def test_ignora_privado_proprio_e_duplicada(montar):
    robo, _ = montar()
    assert roda(robo, msg("teve OPA?", grupo="5511999990002@s.whatsapp.net"))[0].startswith("ignorado")
    assert roda(robo, msg("teve OPA?", fromMe=True))[0].startswith("ignorado")
    assert roda(robo, msg("teve OPA?", mid="d"))[1] == "enviado"
    assert roda(robo, msg("teve OPA?", mid="d"))[0] == "ignorado:duplicada"


def test_conversa_sem_tema_nao_gasta_ia(montar):
    class Explode:
        def relevante(self, *a):
            raise AssertionError("não deveria chamar a IA")
    robo, env = montar(classificador=Explode())
    assert roda(robo, msg("bom dia pessoal, alguém vai no almoço?"))[1] == "ignorado"
    assert env.enviadas == []


def test_classificador_diz_que_nao(montar):
    robo, env = montar(classificador=Classificador(False))
    assert roda(robo, msg("o que acham da OPA da Brava, vale comprar?"))[1] == "ignorado"


def test_ia_fora_decide_por_regra(montar):
    robo, env = montar(classificador=Classificador(None))
    assert roda(robo, msg("teve recuperação judicial essa semana?"))[1] == "enviado"
    assert roda(robo, msg("e o que acha de PETR4?", mid="m2"))[1] == "ignorado"  # ticker sozinho não basta sem IA


def test_limite_por_pessoa(montar):
    robo, env = montar(GRUPOS_MAX_POR_PESSOA_10MIN="2")
    res = [roda(robo, msg("teve OPA?", mid=f"x{i}"))[1] for i in range(3)]
    assert res == ["enviado", "enviado", "limite"]


def test_webhook_exige_segredo(montar, banco, monkeypatch):
    montar()
    c = TestClient(api.criar_app(redator=Redator(), hoje=lambda: HOJE))
    assert c.post("/evolution/webhook/errado", json=msg("teve OPA?")).status_code == 404
    r = c.post("/evolution/webhook/seg", json=msg("oi", grupo="5511@s.whatsapp.net"))
    assert r.status_code == 200 and r.json()["status"].startswith("ignorado")


class SemIA:
    """Classificador que nunca responde: força a decisão por regra."""

    def relevante(self, texto, contexto):
        return None


def test_triagem_por_regra_reconhece_empresa_com_ficha(montar):
    robo, _ = montar(classificador=SemIA())
    # nome de empresa que só aparece nas fichas (sem ticker e sem palavra de evento na frase)
    assert robo._relevante("qual a orientacao para Raizen?", [])[0] is True
    assert robo._relevante("o cliente do RBRR11 precisa fazer alguma coisa?", [])[0] is True
    assert robo._relevante("qual o prazo para trocar RAPT3?", [])[0] is True


def test_triagem_por_regra_ignora_conversa_do_grupo(montar):
    robo, _ = montar(classificador=SemIA())
    for frase in ("bom dia pessoal, tudo certo?", "alguem viu o jogo ontem?", "qual o CDI hoje?",
                  "vale a pena comprar PETR4 agora?", "manda o contato do cliente"):
        assert robo._relevante(frase, [])[0] is False, frase


def test_sem_ia_mas_com_ficha_nao_diz_que_nao_encontrou(montar):
    robo, _ = montar(classificador=SemIA())
    robo.redator = None  # IA fora do ar
    resposta, _, camada = robo._montar_resposta("qual a orientacao para Raizen?", [])
    assert "não encontrei" not in resposta.lower() and "Raízen" in resposta
    assert camada.endswith("ficha-sem-ia")


def test_seguimento_chega_ate_a_ia_mas_nao_passa_sozinho_na_regra(montar):
    ctx = ["joao: quando sai o resgate do INBR32?"]
    seguimento = "Mas quando vai creditar na conta?"

    sim = type("Sim", (), {"relevante": lambda self, t, c: True})()
    robo, _ = montar(classificador=sim)
    assert robo._relevante(seguimento, ctx) == (True, "ia")        # com IA, o contexto é considerado
    assert robo._relevante(seguimento, []) == (False, "prefiltro")  # sem conversa anterior, nem chega na IA

    robo, _ = montar(classificador=SemIA())
    assert robo._relevante(seguimento, ctx)[0] is False             # sem IA, o robô não arrisca
    assert robo._relevante("alguem viu o jogo ontem?", ctx)[0] is False


def test_ancora_do_assunto_segue_o_que_o_robo_respondeu(montar):
    robo, _ = montar()
    # A pergunta de outra pessoa; o robô respondeu sobre ela; agora B faz um seguimento vago
    robo._tema["g"] = (time.monotonic(), "quando sai o resgate do INBR32?")
    ancora = robo._ancora("g", proprias=[], contexto=["carlos: alguem tem o contato da mesa?"])
    assert ancora == ["quando sai o resgate do INBR32?"]  # e não a conversa solta que veio no meio


def test_ancora_prefere_a_propria_conversa_do_assessor(montar):
    """A perguntou sobre Raízen; o robô respondeu B sobre outra coisa; o seguimento de A continua Raízen."""
    robo, _ = montar()
    robo._tema["g"] = (time.monotonic(), "prazo da OPA da Randoncorp?")
    ancora = robo._ancora("g", proprias=["subscrição de Raízen"], contexto=[])
    assert ancora[-1] == "subscrição de Raízen"  # o último da lista é o que a interpretação herda


def test_tema_antigo_nao_ancora_mais(montar):
    robo, _ = montar()
    robo._tema["g"] = (time.monotonic() - grupos.TEMA_VALE_SEG - 1, "prazo da OPA da Randoncorp?")
    assert robo._ancora("g", proprias=[], contexto=["ana: bom dia"]) == ["ana: bom dia"]


def test_resposta_cita_a_pergunta_no_grupo(montar):
    robo, enviador = montar()
    roda(robo, msg("teve alguma OPA recente?"))
    assert enviador.enviadas, "deveria ter respondido"
    citado = enviador.citadas[0]
    assert citado["key"]["id"] and citado["message"]["conversation"] == "teve alguma OPA recente?"


def test_pergunta_de_outro_colega_nao_cancela_a_resposta(montar):
    """A pergunta; B pergunta outra coisa no meio da espera; A continua merecendo resposta."""
    robo, enviador = montar(GRUPOS_ESPERA_SEG="20")
    robo.dormir = lambda s: None
    _, mA = robo.receber(msg("quando termina a venda do INBR32?", mid="a1", quem="5511A"))
    robo.receber(msg("Como envio uma ordem?", mid="b1", quem="5511B"))  # entra na janela de espera
    assert robo.processar(mA) == "enviado"


def test_resposta_de_colega_cancela_a_resposta(montar):
    robo, enviador = montar(GRUPOS_ESPERA_SEG="20")
    robo.dormir = lambda s: None
    _, mA = robo.receber(msg("quando termina a venda do INBR32?", mid="a2", quem="5511A"))
    robo.receber(msg("ja respondi ele: o prazo vai ate 17/11", mid="b2", quem="5511B"))
    assert robo.processar(mA) == "humano"
