"""Coletor de fatos relevantes.

Fontes:
  - Fundamentus (quase tempo real): ações (fr.php) e FIIs (fii_fr.php)
  - CVM Dados Abertos (oficial, atualizado 1x por dia): documentos IPE de companhias abertas.
    Serve para carregar o histórico do ano e cobrir qualquer fato que a página do Fundamentus perca.

Uso:
  python fetch_events.py                 # coleta Fundamentus uma vez
  python fetch_events.py --cvm           # coleta também o arquivo do ano na CVM
  python fetch_events.py --cvm-ano 2025  # carrega um ano específico da CVM (histórico)
  python fetch_events.py --diagnostico   # mostra como as páginas do Fundamentus foram lidas, sem gravar
"""

import argparse
import csv
import io
import logging
import re
import zipfile
from datetime import date, datetime
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import eventos

log = logging.getLogger("coletor")

FONTES_FUNDAMENTUS = {
    "ACAO": "https://www.fundamentus.com.br/fr.php",
    "FII": "https://www.fundamentus.com.br/fii_fr.php",
}
PAGINAS_POR_COLETA = 3  # cobre um intervalo longo sem coleta (ex.: servidor fora do ar)
URL_CVM_IPE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"
CATEGORIAS_CVM = {"fato relevante", "comunicado ao mercado", "aviso aos acionistas"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# Cabeçalho da coluna (normalizado) -> campo. A primeira regra que casar vence.
_COLUNAS = [
    ("categoria", ("categoria",)),
    ("tipo", ("tipo", "especie")),
    ("assunto", ("assunto", "descricao", "titulo", "resumo")),
    ("data_entrega", ("data", "entrega", "publicacao")),
    ("ticker", ("papel", "ticker", "codigo")),
    ("empresa", ("empresa", "companhia", "fundo", "nome", "emissor", "razao")),
]
_PARAMS_PROTOCOLO = ("numprotocolo", "protocolo", "numsequencia", "id")
_TICKER_RE = re.compile(r"^[A-Z]{4}\d{1,2}$")


def _cliente() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True)


# ---------------------------------------------------------------------------
# Fundamentus
# ---------------------------------------------------------------------------

def _mapear_colunas(cabecalhos: list[str]) -> dict[int, str]:
    mapa: dict[int, str] = {}
    usados: set[str] = set()
    for i, cab in enumerate(cabecalhos):
        n = eventos.normalizar(cab)
        for campo, chaves in _COLUNAS:
            if campo not in usados and any(k in n for k in chaves):
                mapa[i] = campo
                usados.add(campo)
                break
    return mapa


def _protocolo_do_link(link: str | None) -> str | None:
    if not link:
        return None
    params = {k.lower(): v for k, v in parse_qs(urlparse(link).query).items()}
    for p in _PARAMS_PROTOCOLO:
        if params.get(p) and params[p][0].strip():
            return params[p][0].strip()
    return None


def parse_fundamentus(html: str, tipo_ativo: str, base_url: str) -> tuple[list[dict], dict]:
    """Lê a maior tabela da página. Retorna (fatos, diagnóstico)."""
    soup = BeautifulSoup(html, "html.parser")
    tabelas = soup.find_all("table")
    if not tabelas:
        return [], {"erro": "nenhuma tabela encontrada na página"}
    tabela = max(tabelas, key=lambda t: len(t.find_all("tr")))
    linhas = tabela.find_all("tr")

    cab_linha = next((tr for tr in linhas if tr.find("th")), linhas[0])
    cabecalhos = [c.get_text(" ", strip=True) for c in cab_linha.find_all(["th", "td"])]
    mapa = _mapear_colunas(cabecalhos)
    diag = {"cabecalhos": cabecalhos, "mapeamento": {cabecalhos[i]: c for i, c in mapa.items()}, "linhas": len(linhas) - 1}
    colunas = set(mapa.values())
    if not ({"empresa", "ticker"} & colunas) or "assunto" not in colunas:
        diag["erro"] = "não identifiquei as colunas de empresa e assunto; ajuste _COLUNAS em fetch_events.py"
        return [], diag

    fatos = []
    for tr in linhas:
        if tr is cab_linha:
            continue
        celulas = tr.find_all("td")
        if len(celulas) < len(mapa):
            continue
        valores = {campo: celulas[i].get_text(" ", strip=True) for i, campo in mapa.items() if i < len(celulas)}
        a = tr.find("a", href=True)
        link = urljoin(base_url, a["href"]) if a else None

        ticker = valores.get("ticker", "").upper()
        empresa = valores.get("empresa") or ticker
        if ticker and _TICKER_RE.match(ticker) and ticker not in empresa.upper():
            empresa = f"{empresa} ({ticker})"
        assunto = valores.get("assunto", "")
        data_entrega = eventos.data_iso(valores.get("data_entrega"))
        if not empresa or not assunto:
            continue

        fatos.append(
            {
                "protocolo": _protocolo_do_link(link) or eventos.gerar_protocolo(empresa, assunto, data_entrega or ""),
                "empresa": empresa,
                "categoria": valores.get("categoria") or "Fato Relevante",
                "tipo": valores.get("tipo"),
                "assunto": assunto,
                "data_entrega": data_entrega,
                "link": link,
                "tipo_ativo": tipo_ativo,
            }
        )
    diag["exemplos"] = fatos[:3]
    return fatos, diag


def coletar_fundamentus(client: httpx.Client | None = None, paginas: int = PAGINAS_POR_COLETA) -> tuple[list[dict], list[dict]]:
    """Baixa as páginas do Fundamentus. Retorna (fatos lidos, diagnósticos por página)."""
    client = client or _cliente()
    todos, diagnosticos = [], []
    for tipo_ativo, url in FONTES_FUNDAMENTUS.items():
        for pg in range(1, paginas + 1):
            pagina_url = url if pg == 1 else f"{url}?pg={pg}"
            try:
                resp = client.get(pagina_url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                log.warning("Falha ao baixar %s: %s", pagina_url, e)
                diagnosticos.append({"url": pagina_url, "erro": str(e)})
                break
            fatos, diag = parse_fundamentus(resp.text, tipo_ativo, pagina_url)
            diagnosticos.append({"url": pagina_url, **diag})
            if not fatos:
                break
            todos.extend(fatos)
    return todos, diagnosticos


# ---------------------------------------------------------------------------
# CVM Dados Abertos (IPE)
# ---------------------------------------------------------------------------

def parse_cvm_ipe(conteudo_zip: bytes) -> list[dict]:
    fatos = []
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as zf:
        nome_csv = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        texto = zf.read(nome_csv).decode("latin-1")
    for linha in csv.DictReader(io.StringIO(texto), delimiter=";"):
        if eventos.normalizar(linha.get("Categoria")) not in CATEGORIAS_CVM:
            continue
        protocolo = (linha.get("Protocolo_Entrega") or "").strip()
        empresa = (linha.get("Nome_Companhia") or "").strip()
        assunto = (linha.get("Assunto") or "").strip()
        if not protocolo or not empresa:
            continue
        fatos.append(
            {
                "protocolo": protocolo,
                "empresa": empresa,
                "categoria": linha.get("Categoria"),
                "tipo": linha.get("Tipo") or linha.get("Especie"),
                "assunto": assunto,
                "data_entrega": eventos.data_iso(linha.get("Data_Entrega")),
                "link": (linha.get("Link_Download") or "").strip() or None,
                "tipo_ativo": "ACAO",
            }
        )
    return fatos


def coletar_cvm(ano: int, client: httpx.Client | None = None) -> list[dict]:
    client = client or _cliente()
    resp = client.get(URL_CVM_IPE.format(ano=ano), timeout=120)
    resp.raise_for_status()
    return parse_cvm_ipe(resp.content)


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------

def _gravar(fonte: str, inicio: datetime, fatos: list[dict], enviado: int, erro: str | None = None) -> int:
    relevantes = [f for f in fatos if eventos.eh_relevante(f["assunto"], f.get("tipo"))]
    with eventos.conectar() as con:
        inseridos = eventos.inserir(con, relevantes, enviado=enviado)
        eventos.registrar_coleta(con, fonte, inicio, len(fatos), len(relevantes), inseridos, erro)
    log.info("[%s] lidos=%d relevantes=%d NOVOS INSERIDOS=%d", fonte, len(fatos), len(relevantes), inseridos)
    return inseridos


def executar_fundamentus() -> int:
    inicio = datetime.now()
    try:
        fatos, diags = coletar_fundamentus()
    except Exception as e:  # nunca derruba o agendador
        log.exception("Erro inesperado na coleta do Fundamentus")
        with eventos.conectar() as con:
            eventos.registrar_coleta(con, "fundamentus", inicio, 0, 0, 0, str(e))
        return 0
    erros = [d for d in diags if d.get("erro")]
    for d in erros:
        log.warning("Fundamentus %s: %s", d.get("url"), d["erro"])
    erro = "; ".join(f"{d['url']}: {d['erro']}" for d in erros) or None
    return _gravar("fundamentus", inicio, fatos, enviado=0, erro=erro)


def executar_cvm(ano: int | None = None) -> int:
    """Carga da CVM. Entra com enviado=1: é histórico/conferência, não notícia nova."""
    ano = ano or date.today().year
    inicio = datetime.now()
    try:
        fatos = coletar_cvm(ano)
    except Exception as e:
        log.warning("Falha na coleta da CVM (%s): %s", ano, e)
        with eventos.conectar() as con:
            eventos.registrar_coleta(con, f"cvm-{ano}", inicio, 0, 0, 0, str(e))
        return 0
    return _gravar(f"cvm-{ano}", inicio, fatos, enviado=1)


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cvm", action="store_true", help="coleta também o arquivo do ano corrente da CVM")
    p.add_argument("--cvm-ano", type=int, help="carrega o arquivo de um ano específico da CVM")
    p.add_argument("--diagnostico", action="store_true", help="mostra a leitura do Fundamentus sem gravar")
    args = p.parse_args()

    if args.diagnostico:
        _, diags = coletar_fundamentus(paginas=1)
        for d in diags:
            print("\n==", d.get("url"))
            for k, v in d.items():
                if k != "url":
                    print(f"  {k}: {v}")
        return

    executar_fundamentus()
    if args.cvm or args.cvm_ano:
        executar_cvm(args.cvm_ano)


if __name__ == "__main__":
    main()
