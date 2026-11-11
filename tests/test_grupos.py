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

    def enviar(self, grupo, texto):
        self.enviadas.append((grupo, texto))


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
