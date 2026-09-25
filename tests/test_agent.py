from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent import Agente, RespostaRecusada
from app.config import Config
from app.main import criar_app
from app.providers.mock import ProvedorMock
from app.tools import Ferramentas

CONFIG = Config(modelo="claude-opus-5", esforco="medium", provedor_eventos="mock",
                brapi_token="", brapi_base_url="", aliquota_ir_jcp=0.15)


def texto(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id_, name, input_):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


class ClienteFalso:
    """Imita client.beta.messages.create devolvendo respostas pré-definidas."""

    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.chamadas = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.chamadas.append({**kwargs, "messages": list(kwargs["messages"])})
        stop, content = self._respostas.pop(0)
        return SimpleNamespace(stop_reason=stop, content=content)


def agente(respostas):
    cliente = ClienteFalso(respostas)
    return Agente(CONFIG, Ferramentas(ProvedorMock(), 0.15), client=cliente), cliente


def test_loop_executa_ferramentas_e_responde():
    a, cliente = agente([
        ("tool_use", [tool_use("t1", "buscar_eventos_corporativos", {"ticker": "PETR4"}),
                      tool_use("t2", "simular_impacto_na_posicao", {"ticker": "PETR4", "quantidade": 0})]),
        ("end_turn", [texto("PETR4 paga R$ 0,45 em dividendos.")]),
    ])
    assert a.responder("s1", "proventos da PETR4?", hoje=date(2026, 9, 25)) == "PETR4 paga R$ 0,45 em dividendos."

    primeira = cliente.chamadas[0]
    assert primeira["model"] == "claude-opus-5"
    assert primeira["fallbacks"] == "default"
    assert primeira["thinking"] == {"type": "adaptive"}
    assert "2026-09-25" in primeira["messages"][0]["content"][0]["text"]

    resultados = cliente.chamadas[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in resultados] == ["t1", "t2"]
    assert "PETR4" in resultados[0]["content"] and "is_error" not in resultados[0]
    assert resultados[1]["is_error"] is True


def test_historico_persiste_entre_perguntas():
    a, cliente = agente([("end_turn", [texto("oi")]), ("end_turn", [texto("de novo")])])
    a.responder("s1", "primeira")
    a.responder("s1", "segunda")
    assert len(cliente.chamadas[1]["messages"]) == 3


def test_recusa_nao_deixa_turno_pela_metade():
    a, cliente = agente([("refusal", []), ("end_turn", [texto("ok")])])
    with pytest.raises(RespostaRecusada):
        a.responder("s1", "pergunta")
    a.responder("s1", "outra")
    assert len(cliente.chamadas[1]["messages"]) == 1


def test_endpoint_chat():
    a, _ = agente([("end_turn", [texto("Resposta")]), ("refusal", [])])
    http = TestClient(criar_app(a))
    r = http.post("/api/chat", json={"mensagem": "PETR4?"})
    assert r.status_code == 200 and r.json()["resposta"] == "Resposta"
    sessao = r.json()["sessao_id"]
    r = http.post("/api/chat", json={"mensagem": "x", "sessao_id": sessao})
    assert r.status_code == 200 and "Não posso ajudar" in r.json()["resposta"]
    assert http.get("/").status_code == 200
