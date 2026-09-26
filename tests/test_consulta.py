from datetime import date

import pytest

import eventos
from consulta import Interpretador
from tests.conftest import HOJE

NOMES = ["BRAVA ENERGIA S.A.", "GERDAU S.A.", "LUPATECH S.A.", "LIGHT S.A.", "WEG S.A."]


@pytest.fixture(scope="module")
def interp():
    return Interpretador()


def i(interp, pergunta, historico=None):
    return interp.interpretar(pergunta, historico, hoje=HOJE, nomes_no_banco=NOMES)


def test_apelido_petro(interp):
    r = i(interp, "O que aconteceu com a Petro?")
    assert r.empresa == "Petrobras" and "petrobras" in r.termos_empresa
    assert r.dias == 365


def test_vale_e_ticker(interp):
    assert i(interp, "teve algo da Vale?").empresa == "Vale"
    r = i(interp, "novidades de PETR3")
    assert r.empresa == "Petrobras" and r.ticker == "PETR3"


def test_fii_por_ticker_e_nome(interp):
    a = i(interp, "O MXRF11 fez emissão?")
    b = i(interp, "e o Maxi Renda, fez emissão de cotas?")
    assert a.empresa == b.empresa == "Maxi Renda"
    assert a.tipo_ativo == b.tipo_ativo == "FII"
    assert a.tipos == ["EMISSAO"]


def test_empresa_desconhecida_mas_no_banco(interp):
    r = i(interp, "O que aconteceu com a Lupatech?")
    assert r.termos_empresa == ["lupatech"]


def test_tipos_e_periodos(interp):
    r = i(interp, "Teve OPA essa semana?")
    assert r.tipos == ["OPA"] and r.desde == date(2026, 7, 20) and r.ate == HOJE
    r = i(interp, "Quais empresas fizeram desdobramento esse ano?")
    assert r.tipos == ["DESDOBRAMENTO"] and r.desde == date(2026, 1, 1)
    assert i(interp, "Novidades de hoje?").desde == HOJE
    r = i(interp, "Teve recuperação judicial recente?")
    assert r.tipos == ["RECUPERACAO"] and r.dias == 30
    r = i(interp, "Quais FIIs fizeram emissão de cotas?")
    assert r.tipo_ativo == "FII" and r.tipos == ["EMISSAO"]
    assert i(interp, "fusões nos últimos 90 dias").dias == 90
    r = i(interp, "OPAs em março")
    assert (r.desde, r.ate) == (date(2026, 3, 1), date(2026, 3, 31))
    r = i(interp, "semana passada teve algo?")
    assert (r.desde, r.ate) == (date(2026, 7, 13), date(2026, 7, 19))


def test_pergunta_de_seguimento_herda_contexto(interp):
    hist = [{"role": "user", "content": "O que aconteceu com a Brava esse mês?"}, {"role": "assistant", "content": "..."}]
    r = i(interp, "me dá mais detalhes", hist)
    assert "brava" in r.termos_empresa and r.desde == date(2026, 7, 1)
    r = i(interp, "e da Gerdau?", hist)
    assert r.empresa == "Gerdau" and r.desde == date(2026, 7, 1)
    r = i(interp, "Novidades de hoje?", hist)  # pergunta nova, não herda
    assert r.termos_empresa == []


def test_busca(banco, interp):
    with eventos.conectar() as con:
        semana = eventos.buscar(con, i(interp, "Teve algo relevante essa semana?").filtro())
        assert [e["empresa"] for e in semana] == ["ONCOCLINICAS DO BRASIL SERVICOS MEDICOS S.A.", "LUPATECH S.A.", "GERDAU S.A.", "BRAVA ENERGIA S.A."]
        brava = eventos.buscar(con, i(interp, "O que aconteceu com a Brava?").filtro())
        assert len(brava) == 2 and brava[1]["data"] == "15/07/2026"  # duplicata removida, data dd/mm/aaaa lida
        rj = eventos.buscar(con, i(interp, "Teve recuperação judicial?").filtro())
        assert {e["empresa"] for e in rj} == {"LIGHT S.A.", "ONCOCLINICAS DO BRASIL SERVICOS MEDICOS S.A."}
        assert all(e["tipo_evento"] == "RECUPERACAO" for e in rj)
        petro = eventos.buscar(con, i(interp, "O que aconteceu com a Petrobras?").filtro())
        assert petro[0]["tipo_evento"] == "FUSAO"
        ecopetrol = eventos.buscar(con, i(interp, "e a Ecopetrol?").filtro())
        assert len(ecopetrol) == 2  # achada pelo assunto
