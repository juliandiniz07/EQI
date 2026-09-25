import json

import httpx
import pytest

from app.providers.base import multiplicador_de_fator
from app.providers.brapi import ProvedorBrapi
from app.providers.mock import ProvedorMock
from app.tools import ErroFerramenta, Ferramentas


@pytest.fixture
def ferramentas():
    return Ferramentas(ProvedorMock(), aliquota_ir_jcp=0.15)


def test_busca_filtra_por_tipo_e_periodo(ferramentas):
    r = ferramentas.buscar_eventos({"ticker": "petr4", "tipos": ["JCP"], "data_inicio": "2026-11-01"})
    assert r["ticker"] == "PETR4"
    assert [e["tipo"] for e in r["eventos"]] == ["JCP"]
    assert r["eventos"][0]["data_pagamento"] == "2026-11-20"


def test_busca_ticker_sem_eventos(ferramentas):
    assert ferramentas.buscar_eventos({"ticker": "XPTO3"})["total_encontrado"] == 0


def test_simulacao_jcp_desconta_ir(ferramentas):
    r = ferramentas.simular_impacto({"ticker": "PETR4", "quantidade": 1000, "tipos": ["JCP"]})
    s = r["simulacoes"][0]
    assert s["valor_bruto"] == 300.0
    assert s["ir_retido_estimado"] == 45.0
    assert s["valor_liquido_estimado"] == 255.0


def test_simulacao_dividendo_sem_ir(ferramentas):
    s = ferramentas.simular_impacto({"ticker": "PETR4", "quantidade": 1000, "tipos": ["DIVIDENDO"]})["simulacoes"][0]
    assert s["valor_liquido_estimado"] == s["valor_bruto"] == 450.0


def test_simulacao_bonificacao_com_fracao(ferramentas):
    s = ferramentas.simular_impacto({"ticker": "ITUB4", "quantidade": 1505, "tipos": ["BONIFICACAO"]})["simulacoes"][0]
    assert s["quantidade_depois"] == 1655
    assert s["fracao_de_acao"] == pytest.approx(0.5)


def test_simulacao_grupamento(ferramentas):
    s = ferramentas.simular_impacto({"ticker": "MGLU3", "quantidade": 1234})["simulacoes"][0]
    assert s["quantidade_depois"] == 123
    assert s["fracao_de_acao"] == pytest.approx(0.4)


@pytest.mark.parametrize("entrada", [
    {"ticker": "PETR4", "quantidade": 0},
    {"ticker": "PETR4", "quantidade": "10"},
    {"ticker": "", "quantidade": 10},
    {"ticker": "PETR4", "quantidade": 10, "data_inicio": "25/09/2026"},
    {"ticker": "PETR4", "quantidade": 10, "tipos": ["BOLO"]},
])
def test_entradas_invalidas(ferramentas, entrada):
    with pytest.raises(ErroFerramenta):
        ferramentas.simular_impacto(entrada)


def test_executar_retorna_json(ferramentas):
    assert json.loads(ferramentas.executar("buscar_eventos_corporativos", {"ticker": "WEGE3"}))["total_encontrado"] == 1
    with pytest.raises(ErroFerramenta):
        ferramentas.executar("nao_existe", {})


@pytest.mark.parametrize("texto,esperado", [("1 para 2", 2.0), ("10:1", 0.1), ("100 para 110", 1.1), ("abc", None), (None, None)])
def test_multiplicador_de_fator(texto, esperado):
    assert multiplicador_de_fator(texto) == (pytest.approx(esperado) if esperado else None)


def _brapi(handler):
    return ProvedorBrapi("https://brapi.test/api", "tok", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_brapi_converte_resposta():
    def handler(req):
        assert req.url.path == "/api/quote/PETR4"
        assert req.url.params["dividends"] == "true"
        assert req.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"results": [{"dividendsData": {
            "cashDividends": [{"label": "JCP", "rate": 0.3, "approvedOn": "2026-08-07T00:00:00.000Z",
                               "lastDatePrior": "2026-08-21T00:00:00.000Z", "paymentDate": "2026-11-20T00:00:00.000Z"}],
            "stockDividends": [{"label": "DESDOBRAMENTO", "completeFactor": "1 para 2", "factor": 2,
                                "lastDatePrior": "2026-10-01T00:00:00.000Z"}],
            "subscriptions": [{"label": "SUBSCRICAO", "priceUnit": 22.5, "percentage": 5}],
        }}]})

    eventos = _brapi(handler).buscar_eventos("petr4")
    assert [e.tipo for e in eventos] == ["JCP", "DESDOBRAMENTO", "SUBSCRICAO"]
    assert eventos[0].data_com.isoformat() == "2026-08-21"
    assert eventos[1].multiplicador_acoes == 2.0
    assert eventos[2].preco_subscricao == 22.5


def test_brapi_erro_vira_erro_de_ferramenta():
    f = Ferramentas(_brapi(lambda req: httpx.Response(404)), 0.15)
    with pytest.raises(ErroFerramenta, match="não encontrado"):
        f.buscar_eventos({"ticker": "XPTO3"})


def test_simulacao_arredonda_centavos_para_cima_no_meio(ferramentas):
    s = ferramentas.simular_impacto({"ticker": "ITUB4", "quantidade": 1505, "tipos": ["JCP"]})["simulacoes"][0]
    assert (s["valor_bruto"], s["ir_retido_estimado"], s["valor_liquido_estimado"]) == (30.10, 4.52, 25.58)
