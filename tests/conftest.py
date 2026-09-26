import pytest

import eventos

HOJE = __import__("datetime").date(2026, 7, 24)  # sexta-feira

FATOS = [
    ("p1", "BRAVA ENERGIA S.A.", "Reapresentação do edital da OPA da Ecopetrol", "2026-07-20 18:32", "ACAO"),
    ("p2", "BRAVA ENERGIA S.A.", "Colegiado da CVM dá provimento a recurso da Ecopetrol e revoga suspensão da OPA", "15/07/2026", "ACAO"),
    ("p3", "GERDAU S.A.", "Conclusão da aquisição de participação acionária", "2026-07-21 09:00", "ACAO"),
    ("p4", "LUPATECH S.A.", "Procedimento de OPA para recompra de títulos", "2026-07-22 19:10", "ACAO"),
    ("p5", "LIGHT S.A.", "Homologação do aumento de capital e pedido de saída da Recuperação Judicial", "2026-07-15 20:00", "ACAO"),
    ("p6", "ONCOCLINICAS DO BRASIL SERVICOS MEDICOS S.A.", "Pedido de recuperação extrajudicial", "2026-07-23 21:00", "ACAO"),
    ("p7", "PETROLEO BRASILEIRO S.A. PETROBRAS", "Incorporação de subsidiária", "2026-03-10 18:00", "ACAO"),
    ("p8", "FII MAXI RENDA (MXRF11)", "Oferta pública de distribuição de cotas da 15ª emissão", "2026-07-10 10:00", "FII"),
    ("p9", "WEG S.A.", "Aprovação de desdobramento de ações", "2026-02-02 18:00", "ACAO"),
    ("p10", "BRAVA ENERGIA S.A.", "Reapresentação do edital da OPA da Ecopetrol", "2026-07-20 18:40", "ACAO"),  # duplicata
]


@pytest.fixture
def banco(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "fatos.db"))
    with eventos.conectar() as con:
        eventos.inserir(con, [
            {"protocolo": p, "empresa": e, "categoria": "Fato Relevante", "tipo": None, "assunto": a,
             "data_entrega": d, "link": f"https://www.rad.cvm.gov.br/doc?numProtocolo={p}", "tipo_ativo": t}
            for p, e, a, d, t in FATOS
        ])
    return tmp_path / "fatos.db"
