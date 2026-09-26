"""Núcleo compartilhado: banco SQLite, filtro de relevância, classificação e busca de eventos."""

import hashlib
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS fatos (
    protocolo     TEXT PRIMARY KEY,
    empresa       TEXT,
    categoria     TEXT,
    tipo          TEXT,
    assunto       TEXT,
    data_entrega  TEXT,
    link          TEXT,
    tipo_ativo    TEXT,
    enviado       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS coletas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fonte      TEXT,
    inicio     TEXT,
    fim        TEXT,
    lidos      INTEGER,
    relevantes INTEGER,
    inseridos  INTEGER,
    erro       TEXT
);
"""


def db_path() -> str:
    return os.getenv("DB_PATH", "fatos.db")


@contextmanager
def conectar(caminho: str | None = None):
    con = sqlite3.connect(caminho or db_path(), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------

def normalizar(texto: str | None) -> str:
    """Minúsculas, sem acentos e com espaços simples — base de toda comparação de texto."""
    if not texto:
        return ""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def termo(texto: str | None) -> str:
    """normalizar() + só letras e números separados por espaço, para comparar palavras inteiras."""
    return re.sub(r"[^a-z0-9]+", " ", normalizar(texto)).strip()


# ---------------------------------------------------------------------------
# Relevância e classificação
# ---------------------------------------------------------------------------

# Ordem importa: o primeiro tipo que casar define o badge do evento.
TIPOS_EVENTO: list[tuple[str, str, list[str]]] = [
    ("RECUPERACAO", "Recuperação", [r"recuperacao judicial", r"recuperacao extrajudicial", r"falencia"]),
    ("OPA", "OPA", [r"\bopa\b", r"\bopas\b", r"oferta publica de aquisicao"]),
    (
        "CANCELAMENTO",
        "Cancelamento",
        [
            r"cancelamento (?:de |do |das |dos )?(?:\w+ )?(?:acoes|registro)",
            r"(?:cancelamento|descontinuidade|descontinuacao|encerramento) (?:de |do |dos )?(?:programa (?:de )?)?bdrs?\b",
        ],
    ),
    ("DESDOBRAMENTO", "Desdobramento", [r"desdobramento", r"grupamento"]),
    ("LIQUIDACAO", "Liquidação", [r"liquidacao (?:do |de )?fundo"]),
    (
        "EMISSAO",
        "Emissão",
        [r"oferta publica (?:primaria )?de (?:distribuicao de )?cotas", r"emissao de (?:novas )?cotas", r"subscricao"],
    ),
    ("FUSAO", "Fusão/Aquisição", [r"fusao", r"incorporacao", r"aquisicao", r"cisao"]),
]

_REGEX_TIPOS = [(cod, rotulo, re.compile("|".join(pads))) for cod, rotulo, pads in TIPOS_EVENTO]
ROTULOS = {cod: rotulo for cod, rotulo, _ in TIPOS_EVENTO}


def classificar(assunto: str | None, tipo: str | None = None) -> str | None:
    """Código do tipo de evento (OPA, FUSAO...) ou None se o fato não for de interesse."""
    texto = normalizar(f"{assunto or ''} {tipo or ''}")
    for cod, _, regex in _REGEX_TIPOS:
        if regex.search(texto):
            return cod
    return None


def eh_relevante(assunto: str | None, tipo: str | None = None) -> bool:
    return classificar(assunto, tipo) is not None


# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------

_FORMATOS_DATA = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y")


def parse_data(valor: str | None) -> datetime | None:
    if not valor:
        return None
    valor = valor.strip()
    for fmt in _FORMATOS_DATA:
        try:
            return datetime.strptime(valor, fmt)
        except ValueError:
            continue
    m = re.search(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?", valor)
    if m:
        return parse_data(" ".join(x for x in m.groups() if x))
    return None


def data_iso(valor: str | None) -> str | None:
    """Converte qualquer formato conhecido para 'AAAA-MM-DD HH:MM' (ordenável)."""
    d = parse_data(valor)
    return d.strftime("%Y-%m-%d %H:%M") if d else (valor or None)


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------

def gerar_protocolo(empresa: str, assunto: str, data_entrega: str) -> str:
    base = f"{normalizar(empresa)}|{normalizar(assunto)}|{data_entrega}"
    return "h" + hashlib.sha1(base.encode()).hexdigest()[:20]


def inserir(con: sqlite3.Connection, fatos: list[dict], enviado: int = 0) -> int:
    """INSERT OR IGNORE — nunca duplica pelo protocolo. Retorna quantos entraram de fato."""
    antes = con.total_changes
    con.executemany(
        """INSERT OR IGNORE INTO fatos
           (protocolo, empresa, categoria, tipo, assunto, data_entrega, link, tipo_ativo, enviado)
           VALUES (:protocolo, :empresa, :categoria, :tipo, :assunto, :data_entrega, :link, :tipo_ativo, :enviado)""",
        [{**f, "enviado": enviado} for f in fatos],
    )
    return con.total_changes - antes


def registrar_coleta(con, fonte: str, inicio: datetime, lidos: int, relevantes: int, inseridos: int, erro: str | None):
    con.execute(
        "INSERT INTO coletas (fonte, inicio, fim, lidos, relevantes, inseridos, erro) VALUES (?,?,?,?,?,?,?)",
        (fonte, inicio.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds"), lidos, relevantes, inseridos, erro),
    )


# ---------------------------------------------------------------------------
# Busca
# ---------------------------------------------------------------------------

@dataclass
class Filtro:
    desde: date
    ate: date
    termos_empresa: list[str] = field(default_factory=list)  # em formato termo(); palavra inteira na empresa ou assunto
    tipos: list[str] = field(default_factory=list)  # códigos de TIPOS_EVENTO
    tipo_ativo: str | None = None  # "ACAO" | "FII"


def _linha_para_evento(r: sqlite3.Row, quando: datetime | None, tipo_evento: str | None) -> dict:
    link = r["link"] or ""
    return {
        "protocolo": r["protocolo"],
        "empresa": (r["empresa"] or "").strip(),
        "categoria": r["categoria"],
        "tipo": r["tipo"],
        "assunto": (r["assunto"] or "").strip(),
        "data_entrega": quando.strftime("%Y-%m-%d %H:%M") if quando else r["data_entrega"],
        "data": quando.strftime("%d/%m/%Y") if quando else (r["data_entrega"] or ""),
        "link": link if link.startswith(("http://", "https://")) else None,
        "tipo_ativo": r["tipo_ativo"],
        "tipo_evento": tipo_evento,
        "tipo_evento_rotulo": ROTULOS.get(tipo_evento or "", "Evento"),
    }


def buscar(con: sqlite3.Connection, filtro: Filtro) -> list[dict]:
    """Eventos que atendem ao filtro, do mais recente ao mais antigo, sem duplicatas.

    O filtro de datas é feito em Python porque bancos antigos podem ter data_entrega em
    formatos diferentes (dd/mm/aaaa ou ISO).
    """
    sql = "SELECT * FROM fatos"
    params: list = []
    if filtro.tipo_ativo:
        sql += " WHERE tipo_ativo = ?"
        params.append(filtro.tipo_ativo)

    vistos: set = set()
    resultado: list[tuple[datetime, dict]] = []
    for r in con.execute(sql, params):
        quando = parse_data(r["data_entrega"])
        if not quando or not (filtro.desde <= quando.date() <= filtro.ate):
            continue
        tipo_evento = classificar(r["assunto"], r["tipo"])
        if filtro.tipos and tipo_evento not in filtro.tipos:
            continue
        if filtro.termos_empresa:
            alvo = f" {termo(r['empresa'])} {termo(r['assunto'])} "
            if not any(f" {t} " in alvo for t in filtro.termos_empresa):
                continue
        chave = (normalizar(r["empresa"]), normalizar(r["assunto"]), quando.date())
        if chave in vistos:
            continue
        vistos.add(chave)
        resultado.append((quando, _linha_para_evento(r, quando, tipo_evento)))

    resultado.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in resultado]


def empresas_conhecidas(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT empresa FROM fatos WHERE empresa IS NOT NULL AND empresa != ''")]


def status(con: sqlite3.Connection) -> dict:
    total = con.execute("SELECT COUNT(*) FROM fatos").fetchone()[0]
    ultima = con.execute("SELECT fonte, fim, inseridos, erro FROM coletas ORDER BY id DESC LIMIT 1").fetchone()
    datas = [parse_data(r[0]) for r in con.execute("SELECT data_entrega FROM fatos")]
    datas = [d for d in datas if d]
    return {
        "eventos_no_banco": total,
        "evento_mais_recente": max(datas).strftime("%Y-%m-%d %H:%M") if datas else None,
        "ultima_coleta": dict(ultima) if ultima else None,
    }
