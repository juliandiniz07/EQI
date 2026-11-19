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
from functools import lru_cache
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import consulta
import eventos

log = logging.getLogger("conhecimento")
PASTA = Path(__file__).resolve().parent / "conhecimento"
MAX_FICHAS = 2
MAX_CHARS = 12000

INSTRUCAO_FICHAS = (
    "NÃO encerre a resposta com aviso genérico: nada de avaliar o suitability, a situação de cada cliente, consultar um "
    "assessor ou ler o documento. O assessor já sabe disso, e o aviso só rouba espaço da informação. "
    "O bloco 'fichas_internas_eqi' é o material oficial preparado pela EQI e tem PRIORIDADE sobre qualquer outra fonte. "
    "NUNCA diga ao assessor que a resposta veio de uma ficha ou de um material interno: fale como quem conhece o evento. "
    "Responda exatamente o que o assessor perguntou, de forma direta, com os valores, datas e passos da ficha; não despeje a ficha inteira. "
    "Se a seção de possíveis perguntas dos clientes cobrir a dúvida, use a resposta dela. Se o assessor ou o cliente perguntar como fazer, como reservar ou onde faz, dê o 'Passo a passo do cliente' (aplicativo) e, para o assessor, o 'Caminho para aprovar' (Access). Se o assessor perguntar o que fazer, diga o caminho "
    "e os prazos da ficha. Se a ficha não cobrir a pergunta, diga isso com clareza e use só os demais dados. PERGUNTAS SOBRE O QUE FAZER: trate como a mesma pergunta qualquer forma de pedir recomendação, orientação, posição, sugestão, conselho ou opinião da casa ('qual a recomendação?', 'qual a orientação?', 'o que vocês indicam/sugerem?', 'o que digo ao cliente?', 'devo vender?', 'compro?', 'subscrevo?', 'vale a pena?', 'o que fazer?'). Nesses casos, se a ficha tiver uma seção 'Orientação', responda com ela logo no início, nas palavras da ficha, chamando-a de orientação, e complete com os prazos relevantes da ficha. Nunca opine por conta própria nem vá além do que a seção diz; se a ficha não tiver a seção, diga que não há orientação cadastrada para esse evento e devolva a pergunta-chave da ficha, se houver."
)


@dataclass
class Ficha:
    arquivo: str
    empresa: str
    termos: list[str]
    assuntos: list[str]
    validade: date | None
    texto: str

    def vencida(self, hoje: date) -> bool:
        return bool(self.validade and self.validade < hoje)


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


def carregar(pasta: Path | None = None) -> list[Ficha]:
    pasta = pasta or PASTA  # resolvido na chamada: assim dá para apontar para outra pasta
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


def _casam(pergunta: str, historico: list[dict] | None, pasta: Path | None, vencida: bool, hoje: date) -> list[Ficha]:
    textos = [pergunta]
    anteriores = [m["content"] for m in (historico or []) if m.get("role") == "user" and m.get("content")]
    # A mensagem anterior só entra quando a pergunta atual é continuação ("e o prazo?"). Uma pergunta nova e
    # completa não pode herdar o assunto de quem falou antes: no grupo, isso faz o robô responder outra coisa.
    if anteriores and consulta.eh_seguimento(pergunta):
        textos.append(anteriores[-1])
    achadas: list[Ficha] = []
    for t in textos:  # a pergunta atual vem primeiro; o histórico só completa
        norm = f" {eventos.termo(t)} "
        for f in carregar(pasta):
            if f in achadas or f.vencida(hoje) != vencida:
                continue
            if any(f" {termo} " in norm for termo in f.termos):
                achadas.append(f)
        if achadas:
            break
    return achadas[:MAX_FICHAS]


def buscar(pergunta: str, historico: list[dict] | None = None, hoje: date | None = None, pasta: Path | None = None) -> list[Ficha]:
    """Fichas válidas que a pergunta (ou a pergunta anterior do assessor) cita."""
    hoje = hoje or date.today()
    return _casam(pergunta, historico, pasta, vencida=False, hoje=hoje)


def buscar_vencidas(pergunta: str, historico: list[dict] | None = None, hoje: date | None = None, pasta: Path | None = None) -> list[Ficha]:
    """Fichas que a pergunta cita mas cujo prazo já passou — para dizer que acabou, em vez de calar."""
    hoje = hoje or date.today()
    return _casam(pergunta, historico, pasta, vencida=True, hoje=hoje)


MAX_FICHAS_TEMA = 4

INSTRUCAO_TEMA = (
    "O bloco 'fichas_internas_eqi' traz os eventos que a EQI acompanha de perto e que combinam com o tema perguntado. "
    "Use-os como a parte principal da resposta, citando empresa, prazo e o que o cliente precisa fazer, e só depois "
    "mencione os demais eventos da lista, se couber. NÃO diga que a resposta veio de ficha ou material interno."
)


@lru_cache(maxsize=256)
def _regex_assunto(termo: str) -> re.Pattern:
    """Casa o termo da ficha com a pergunta aceitando plural: 'aumento de capital' encontra 'aumentos de capital'."""
    palavras = [re.escape(p) + "s?" for p in termo.split()]
    return re.compile(r"\b" + r"\s+".join(palavras) + r"\b")


def buscar_por_tema(pergunta: str, hoje: date | None = None, pasta: Path | None = None) -> list[Ficha]:
    """Fichas cujo ASSUNTO casa com a pergunta, mesmo sem citar a empresa.

    É o que faz uma pergunta geral ("quais empresas estão com aumento de capital?") enxergar
    a ficha da Raízen, que de outro modo só apareceria se o nome fosse citado.
    """
    hoje = hoje or date.today()
    norm = eventos.termo(pergunta)
    achadas = [
        f for f in carregar(pasta)
        if not f.vencida(hoje) and f.assuntos and any(_regex_assunto(a).search(norm) for a in f.assuntos)
    ]
    return achadas[:MAX_FICHAS_TEMA]


INSTRUCAO_VENCIDA = (
    "O bloco 'evento_encerrado' é material de um evento cujo PRAZO JÁ PASSOU. Comece a resposta dizendo claramente que "
    "o prazo acabou e até que data ele era, com a data exata da ficha. Depois, se a ficha disser o que ainda é possível "
    "fazer depois do prazo, diga; se não disser, diga que não há mais ação a fazer e ofereça o que a ficha explica sobre "
    "o que aconteceu. NUNCA trate o evento como aberto nem convide o assessor a aderir, votar ou reservar."
)


def contexto_vencidas(fichas: list[Ficha], hoje: date | None = None) -> dict:
    hoje = hoje or date.today()
    return {
        "evento_encerrado": [
            f"{f.empresa} (prazo encerrado em {f.validade.strftime('%d/%m/%Y')})\n{f.texto}" for f in fichas if f.validade
        ],
        "instrucao_resposta_direta": INSTRUCAO_VENCIDA,
    }


def contexto_tema(fichas: list[Ficha]) -> dict:
    return {"fichas_internas_eqi": [f"{f.empresa}\n{f.texto}" for f in fichas], "instrucao_fichas": INSTRUCAO_TEMA}


def contexto(fichas: list[Ficha]) -> dict:
    return {"fichas_internas_eqi": [f"{f.empresa}\n{f.texto}" for f in fichas], "instrucao_fichas": INSTRUCAO_FICHAS}


def buscar_do_evento(empresa: str, assunto: str, hoje: date | None = None, pasta: Path | None = None) -> list[Ficha]:
    """Fichas válidas para UM evento: a empresa bate e, se a ficha declara 'assunto', o título do evento tem algum desses termos."""
    norm = f" {eventos.termo(assunto)} "
    return [f for f in buscar(empresa, hoje=hoje, pasta=pasta) if not f.assuntos or any(a in norm for a in f.assuntos)]
