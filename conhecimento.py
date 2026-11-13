"""Fichas internas da EQI: material oficial, preparado pela equipe, que o robô usa como fonte prioritária.

Cada ficha é um arquivo .md na pasta `conhecimento/`, com um cabeçalho simples entre linhas `---`:

    ---
    empresa: Raízen
    termos: raizen, raiz55, raiz1, raiz14, raiz16
    assunto: aumento de capital, subscricao   (opcional: a ficha só vale para eventos com esses termos no título)
    validade: 2026-12-08
    ---
    (texto da ficha, no modelo de sempre)

O robô lê a ficha quando a pergunta (ou a pergunta anterior, em seguimentos) cita algum dos `termos`.
Fichas vencidas (depois de `validade`) são ignoradas. Arquivos que começam com `_` não são lidos.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import eventos

log = logging.getLogger("conhecimento")
PASTA = Path(__file__).resolve().parent / "conhecimento"
MAX_FICHAS = 2
MAX_CHARS = 12000

INSTRUCAO_FICHAS = (
    "O bloco 'fichas_internas_eqi' é o material oficial preparado pela EQI e tem PRIORIDADE sobre qualquer outra fonte. "
    "Responda exatamente o que o assessor perguntou, de forma direta, com os valores, datas e passos da ficha; não despeje a ficha inteira. "
    "Se a seção de possíveis perguntas dos clientes cobrir a dúvida, use a resposta dela. Se o assessor perguntar o que fazer, diga o caminho "
    "e os prazos da ficha. Se a ficha não cobrir a pergunta, diga isso com clareza e use só os demais dados. Em perguntas como 'vale a pena?', "
    "nunca opine por conta própria: se a ficha tiver uma seção 'Orientação', apresente-a como a orientação, sem acrescentar nada, junto com o lembrete de suitability; se não tiver, devolva a pergunta-chave da ficha, se houver."
)


@dataclass
class Ficha:
    arquivo: str
    empresa: str
    termos: list[str]
    assuntos: list[str]
    validade: date | None
    texto: str


def _ler(caminho: Path) -> Ficha | None:
    bruto = caminho.read_text(encoding="utf-8-sig")
    meta, corpo = {}, bruto
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n(.*)", bruto, re.S)
    if m:
        for linha in m.group(1).splitlines():
            if ":" in linha:
                k, v = linha.split(":", 1)
                meta[k.strip().lower()] = v.strip()
        corpo = m.group(2)
    termos = [eventos.termo(t) for t in meta.get("termos", "").split(",") if eventos.termo(t)]
    if meta.get("empresa") and eventos.termo(meta["empresa"]) not in termos:
        termos.append(eventos.termo(meta["empresa"]))
    if not termos:
        log.warning("Ficha %s ignorada: cabeçalho sem 'empresa' ou 'termos'.", caminho.name)
        return None
    validade = None
    if meta.get("validade"):
        try:
            validade = date.fromisoformat(meta["validade"])
        except ValueError:
            log.warning("Ficha %s: validade inválida (use AAAA-MM-DD).", caminho.name)
    assuntos = [eventos.termo(t) for t in meta.get("assunto", "").split(",") if eventos.termo(t)]
    return Ficha(caminho.name, meta.get("empresa", caminho.stem), termos, assuntos, validade, corpo.strip()[:MAX_CHARS])


def carregar(pasta: Path = PASTA) -> list[Ficha]:
    fichas = []
    if not pasta.is_dir():
        return fichas
    for caminho in sorted(pasta.glob("*.md")):
        if caminho.name.startswith("_"):
            continue
        try:
            f = _ler(caminho)
        except Exception:
            log.exception("Não foi possível ler a ficha %s", caminho.name)
            continue
        if f:
            fichas.append(f)
    return fichas


def buscar(pergunta: str, historico: list[dict] | None = None, hoje: date | None = None, pasta: Path = PASTA) -> list[Ficha]:
    """Fichas válidas que a pergunta (ou a pergunta anterior do assessor) cita."""
    hoje = hoje or date.today()
    textos = [pergunta]
    anteriores = [m["content"] for m in (historico or []) if m.get("role") == "user" and m.get("content")]
    if anteriores:
        textos.append(anteriores[-1])
    achadas = []
    for t in textos:  # a pergunta atual vem primeiro; o histórico só completa
        norm = f" {eventos.termo(t)} "
        for f in carregar(pasta):
            if f in achadas or (f.validade and f.validade < hoje):
                continue
            if any(f" {termo} " in norm for termo in f.termos):
                achadas.append(f)
        if achadas:
            break
    return achadas[:MAX_FICHAS]


def contexto(fichas: list[Ficha]) -> dict:
    return {"fichas_internas_eqi": [f"{f.empresa}\n{f.texto}" for f in fichas], "instrucao_fichas": INSTRUCAO_FICHAS}


def buscar_do_evento(empresa: str, assunto: str, hoje: date | None = None, pasta: Path = PASTA) -> list[Ficha]:
    """Fichas válidas para UM evento: a empresa bate e, se a ficha declara 'assunto', o título do evento tem algum desses termos."""
    norm = f" {eventos.termo(assunto)} "
    return [f for f in buscar(empresa, hoje=hoje, pasta=pasta) if not f.assuntos or any(a in norm for a in f.assuntos)]
