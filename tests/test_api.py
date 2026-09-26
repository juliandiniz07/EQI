import pytest
from fastapi.testclient import TestClient

import api
from tests.conftest import HOJE


class RedatorFalso:
    def __init__(self, texto="Resposta redigida"):
        self.texto = texto
        self.chamadas = []

    def redigir(self, pergunta, historico, dados):
        self.chamadas.append((pergunta, historico, dados))
        return self.texto


@pytest.fixture
def cliente(banco, monkeypatch):
    monkeypatch.setenv("APP_SENHA", "segredo-teste")
    monkeypatch.setenv("SECRET_KEY", "k")
    redator = RedatorFalso()
    c = TestClient(api.criar_app(redator=redator, hoje=lambda: HOJE))
    c.redator = redator
    return c


def entrar(c):
    return {"Authorization": "Bearer " + c.post("/login", json={"senha": "segredo-teste"}).json()["token"]}


def test_login(cliente):
    assert cliente.post("/login", json={"senha": "errada"}).status_code == 401
    r = cliente.post("/login", json={"senha": "segredo-teste"})
    assert r.status_code == 200 and "." in r.json()["token"]


def test_consultar_exige_login(cliente):
    assert cliente.post("/consultar", json={"pergunta": "oi"}).status_code == 401
    r = cliente.post("/consultar", json={"pergunta": "oi"}, headers={"Authorization": "Bearer 9999999999.falso"})
    assert r.status_code == 401


def test_consultar(cliente):
    h = entrar(cliente)
    r = cliente.post("/consultar", json={"pergunta": "Teve recuperação judicial?", "historico": []}, headers=h)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["resposta"] == "Resposta redigida"
    assert {e["empresa"] for e in corpo["eventos"]} == {"LIGHT S.A.", "ONCOCLINICAS DO BRASIL SERVICOS MEDICOS S.A."}
    assert corpo["meta"]["tipo"] == ["Recuperação"] and corpo["meta"]["dias"] == 30
    _, _, dados = cliente.redator.chamadas[0]
    assert dados["total_encontrado"] == 2 and dados["eventos"][0]["data"] == "23/07/2026"


def test_amplia_periodo_quando_vazio(cliente):
    h = entrar(cliente)
    corpo = cliente.post("/consultar", json={"pergunta": "Teve desdobramento essa semana?"}, headers=h).json()
    assert corpo["meta"]["periodo_ampliado"] is True
    assert corpo["eventos"][0]["empresa"] == "WEG S.A."


def test_sem_resultado(cliente):
    h = entrar(cliente)
    corpo = cliente.post("/consultar", json={"pergunta": "Teve liquidação de fundo hoje?"}, headers=h).json()
    assert corpo["eventos"] == [] and cliente.redator.chamadas[-1][2]["eventos"] == []


def test_fallback_sem_ia(banco, monkeypatch):
    monkeypatch.setenv("APP_SENHA", "s")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    c = TestClient(api.criar_app(redator=RedatorFalso(texto=None), hoje=lambda: HOJE))
    h = entrar_com(c, "s")
    corpo = c.post("/consultar", json={"pergunta": "O que aconteceu com a Brava?"}, headers=h).json()
    assert corpo["resposta"].startswith("Encontrei 2 evento(s) sobre Brava Energia")
    vazio = c.post("/consultar", json={"pergunta": "Teve liquidação de fundo hoje?"}, headers=h).json()
    assert "ampliar o período" in vazio["resposta"]


def entrar_com(c, senha):
    return {"Authorization": "Bearer " + c.post("/login", json={"senha": senha}).json()["token"]}


def test_limite_de_tentativas_de_login(cliente):
    for _ in range(15):
        assert cliente.post("/login", json={"senha": "segredo-teste"}).status_code == 200  # acertos não contam
    for _ in range(10):
        cliente.post("/login", json={"senha": "x"})
    assert cliente.post("/login", json={"senha": "segredo-teste"}).status_code == 429


def test_health_e_index(cliente):
    r = cliente.get("/health").json()
    assert r["status"] == "ok" and r["eventos_no_banco"] == 10 and r["ia_configurada"] is True
    assert "Assistente EQI" in cliente.get("/").text


def test_token_expirado():
    auth = api.Autenticador("s", "k", horas=0)
    token, _ = auth.emitir()
    assert not auth.valido(token)
    assert not api.Autenticador("s", "outra").valido(api.Autenticador("s", "k").emitir()[0])


def test_redator_gemini_monta_conversa_e_tenta_proximo_modelo():
    from types import SimpleNamespace

    from google.genai import errors

    chamadas = []

    def gerar(model, contents, config):
        chamadas.append((model, contents, config))
        if model == "modelo-a":
            raise errors.ClientError(429, {"error": {"message": "quota"}})
        return SimpleNamespace(text=" Texto final ")

    r = api.RedatorGemini("chave-falsa", ["modelo-a", "modelo-b"])
    r._client = SimpleNamespace(models=SimpleNamespace(generate_content=gerar))
    hist = [{"role": "assistant", "content": "Olá!"}, {"role": "user", "content": "e a Vale?"}, {"role": "assistant", "content": "Nada."}]
    assert r.redigir("E a Petrobras?", hist, {"eventos": []}) == "Texto final"
    modelo, contents, config = chamadas[-1]
    assert modelo == "modelo-b"
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert "PERGUNTA DO ASSESSOR: E a Petrobras?" in contents[-1].parts[0].text
    assert "Assistente EQI" in config.system_instruction
