import csv
import io
import zipfile

import httpx

import eventos
import fetch_events

HTML_ACOES = """
<html><body>
<table><tr><td>menu</td></tr></table>
<table id="resultado">
 <thead><tr><th>Empresa</th><th>Categoria</th><th>Tipo</th><th>Assunto</th><th>Data Entrega</th><th>Download</th></tr></thead>
 <tbody>
  <tr><td>BRAVA ENERGIA S.A.</td><td>Fato Relevante</td><td></td><td>Reapresentação do edital da OPA da Ecopetrol</td><td>20/07/2026 18:32</td>
      <td><a href="https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?Tela=ext&numSequencia=1&numVersao=1&numProtocolo=1234567&descTipo=IPE">PDF</a></td></tr>
  <tr><td>ALGUMA S.A.</td><td>Fato Relevante</td><td></td><td>Resultado do 2T26</td><td>21/07/2026 08:00</td><td><a href="/x.pdf">PDF</a></td></tr>
  <tr><td>LIGHT S.A.</td><td>Fato Relevante</td><td></td><td>Homologação do aumento de capital e encerramento da Recuperação Judicial</td><td>15/07/2026</td><td><a href="download.php?id=99">PDF</a></td></tr>
 </tbody>
</table></body></html>
"""

HTML_FII = """
<table>
 <tr><th>Papel</th><th>Fundo</th><th>Descrição</th><th>Data</th><th></th></tr>
 <tr><td>MXRF11</td><td>FII MAXI RENDA</td><td>Encerramento da oferta pública de distribuição de cotas da 15ª emissão</td><td>10/07/2026</td><td><a href="https://fnet.bmfbovespa.com.br/fnet/publico/exibirDocumento?id=555">ver</a></td></tr>
</table>
"""


def test_parse_acoes_mapeia_colunas_e_protocolo():
    fatos, diag = fetch_events.parse_fundamentus(HTML_ACOES, "ACAO", "https://www.fundamentus.com.br/fr.php")
    assert "erro" not in diag
    assert len(fatos) == 3
    brava = fatos[0]
    assert brava["protocolo"] == "1234567"
    assert brava["empresa"] == "BRAVA ENERGIA S.A."
    assert brava["data_entrega"] == "2026-07-20 18:32"
    assert fatos[2]["link"] == "https://www.fundamentus.com.br/download.php?id=99"
    assert fatos[2]["protocolo"] == "99"
    assert fatos[1]["link"] == "https://www.fundamentus.com.br/x.pdf"
    assert fatos[1]["protocolo"].startswith("h")  # sem protocolo no link: hash estável


def test_parse_fii_junta_ticker_ao_nome():
    fatos, _ = fetch_events.parse_fundamentus(HTML_FII, "FII", "https://www.fundamentus.com.br/fii_fr.php")
    assert fatos[0]["empresa"] == "FII MAXI RENDA (MXRF11)"
    assert fatos[0]["tipo_ativo"] == "FII"
    assert eventos.classificar(fatos[0]["assunto"]) == "EMISSAO"


def test_parse_pagina_desconhecida_explica_o_erro():
    fatos, diag = fetch_events.parse_fundamentus("<table><tr><th>X</th><th>Y</th></tr><tr><td>1</td><td>2</td></tr></table>", "ACAO", "u")
    assert fatos == [] and "empresa e assunto" in diag["erro"]


def test_coleta_grava_so_relevantes_sem_duplicar(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "fatos.db"))

    def handler(req):
        if "pg=" in str(req.url):
            return httpx.Response(200, text="<html></html>")
        return httpx.Response(200, text=HTML_FII if "fii_fr" in req.url.path else HTML_ACOES)

    cliente = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(fetch_events, "_cliente", lambda: cliente)
    assert fetch_events.executar_fundamentus() == 3  # Brava (OPA), Light (RJ), MXRF11 (emissão)
    assert fetch_events.executar_fundamentus() == 0
    with eventos.conectar() as con:
        assert con.execute("SELECT COUNT(*) FROM fatos").fetchone()[0] == 3
        assert con.execute("SELECT inseridos FROM coletas ORDER BY id").fetchall()[-1][0] == 0


def test_coleta_com_site_fora_do_ar_nao_quebra(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "fatos.db"))
    cliente = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    monkeypatch.setattr(fetch_events, "_cliente", lambda: cliente)
    assert fetch_events.executar_fundamentus() == 0
    with eventos.conectar() as con:
        assert "503" in con.execute("SELECT erro FROM coletas").fetchone()[0]


def _zip_cvm(linhas):
    buf = io.StringIO()
    campos = ["CNPJ_Companhia", "Nome_Companhia", "Codigo_CVM", "Data_Referencia", "Categoria", "Tipo", "Especie",
              "Assunto", "Data_Entrega", "Tipo_Apresentacao", "Protocolo_Entrega", "Versao", "Link_Download"]
    w = csv.DictWriter(buf, fieldnames=campos, delimiter=";")
    w.writeheader()
    for l in linhas:
        w.writerow({c: l.get(c, "") for c in campos})
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("ipe_cia_aberta_2026.csv", buf.getvalue().encode("latin-1"))
    return z.getvalue()


def test_parse_cvm_filtra_categoria():
    conteudo = _zip_cvm([
        {"Nome_Companhia": "GERDAU S.A.", "Categoria": "Fato Relevante", "Assunto": "Conclusão da aquisição de participação",
         "Data_Entrega": "2026-07-18", "Protocolo_Entrega": "001", "Link_Download": "https://www.rad.cvm.gov.br/x?numProtocolo=001"},
        {"Nome_Companhia": "GERDAU S.A.", "Categoria": "Assembleia", "Assunto": "Ata", "Data_Entrega": "2026-07-18", "Protocolo_Entrega": "002"},
    ])
    fatos = fetch_events.parse_cvm_ipe(conteudo)
    assert [f["protocolo"] for f in fatos] == ["001"]
    assert fatos[0]["data_entrega"] == "2026-07-18 00:00"


def test_classificacao():
    assert eventos.classificar("OPA para cancelamento de registro") == "OPA"
    assert eventos.classificar("Pedido de Recuperação Extrajudicial") == "RECUPERACAO"
    assert eventos.classificar("Aprovação do desdobramento de ações") == "DESDOBRAMENTO"
    assert eventos.classificar("Cancelamento de ações em tesouraria") == "CANCELAMENTO"
    assert eventos.classificar("Incorporação da controlada") == "FUSAO"
    assert eventos.classificar("Liquidação do Fundo") == "LIQUIDACAO"
    assert eventos.classificar("Copa do mundo e resultado trimestral") is None
