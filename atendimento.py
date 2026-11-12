"""Busca e montagem do contexto para responder uma pergunta (usado pelo chat web e pelos grupos de WhatsApp).

Regra geral, válida para qualquer empresa: quando a pergunta cita uma empresa, buscamos TODOS os eventos dela no
período e deixamos a IA julgar, pelo título e pelo resumo, o que responde à pergunta. O tipo de evento só serve
para ordenar (os do tipo pedido aparecem primeiro) e nunca esconde nada.
"""

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import timedelta

import httpx

import eventos
import resumos

log = logging.getLogger("atendimento")

DIAS_AMPLIACAO = 365
MAX_OUTROS_TITULOS = 25

INSTRUCAO = (
    "Cada evento pode trazer 'resumo_do_documento', extraído do comunicado oficial. Quando existir, use-o para responder "
    "com os detalhes (opções do investidor, o que o cliente deve fazer, prazos, valores, fatores) em vez de só citar o título. "
    "Se o resumo não trouxer o que foi perguntado, diga isso com clareza. "
    "'outros_eventos_da_empresa' lista só os títulos de outros comunicados da mesma empresa: use-os para julgar se algum "
    "se relaciona com a pergunta (ex.: 'aumento de capital' ou 'direito de preferência' para uma pergunta sobre subscrição) "
    "e diga que existe, oferecendo detalhar. 'responde_ao_tipo_pedido' indica se o evento é do tipo citado na pergunta. "
    "Se nenhum evento tiver relação com a pergunta, diga isso e cite o que há de mais recente da empresa."
)


@dataclass
class Resultado:
    eventos: list[dict]
    ampliado: bool
    tipo_relaxado: bool  # há eventos da empresa, mas nenhum do tipo pedido

    @property
    def total(self) -> int:
        return len(self.eventos)


def buscar_para_pergunta(con, interp) -> Resultado:
    filtro = interp.filtro()
    por_empresa = bool(interp.termos_empresa)
    if por_empresa:
        filtro.tipos = []
    achados = eventos.buscar(con, filtro)
    ampliado = False
    if not achados and (por_empresa or interp.tipos) and interp.dias < DIAS_AMPLIACAO:
        filtro.desde = interp.ate - timedelta(days=DIAS_AMPLIACAO - 1)
        achados = eventos.buscar(con, filtro)
        ampliado = bool(achados)
    relaxado = False
    if por_empresa and interp.tipos:
        achados, n_tipo = eventos.priorizar(achados, interp.tipos)
        relaxado = n_tipo == 0 and bool(achados)
    return Resultado(achados, ampliado, relaxado)


def contexto_eventos(res: Resultado, interp, limite: int, chars_resumo: int, campos: tuple) -> tuple[list[dict], dict]:
    """(eventos mostrados nos cartões, trechos do contexto para a IA)."""
    mostrados = res.eventos[:limite]
    itens = []
    for e in mostrados:
        item = {k: e[k] for k in campos}
        if interp.tipos:
            item["responde_ao_tipo_pedido"] = e["tipo_evento"] in interp.tipos
        if e.get("resumo"):
            item["resumo_do_documento"] = e["resumo"][:chars_resumo]
        itens.append(item)
    contexto = {"eventos": itens, "instrucao_extra": INSTRUCAO, "tipo_relaxado": res.tipo_relaxado}
    outros = [f"{e['data']}: {(e['assunto'] or e['tipo_evento_rotulo'])[:140]}" for e in res.eventos[limite:limite + MAX_OUTROS_TITULOS]]
    if outros:
        contexto["outros_eventos_da_empresa"] = outros
    return mostrados, contexto


# ---------------------------------------------------------------------------
# Pergunta específica: a resposta sai do documento completo, não só do resumo
# ---------------------------------------------------------------------------

_RE_ESPECIFICA = re.compile(
    r"\bprazo|\bdata\b|\bdatas\b|quando|ate quando|horario|\bhora\b|vencimento|limite|quanto|\bvalor|\bpreco|fator|razao|proporcao|percentual"
    r"|\bcomo\b|\bonde\b|\bquem\b|procedimento|passo a passo|manifest|aderir|adesao|pagamento|credito|condic|regra|pendencia"
    r"|o que (?:o )?(?:cliente|investidor|acionista) (?:precisa|deve|pode|tem que|faz)|qual (?:a|e a) opcao|opcoes|tratamento tributario|imposto"
)
_cache_docs: dict[str, tuple] = {}
_lock = threading.Lock()
_client: httpx.Client | None = None

INSTRUCAO_DOCUMENTOS = (
    "Os documentos anexados são os comunicados mais recentes e mais relevantes para a pergunta. Responda EXATAMENTE ao dado pedido, "
    "com a informação na primeira frase (ex.: 'O prazo vai até 29/10, às 18h'), seguida de uma linha de contexto dizendo de qual "
    "comunicado ou operação vem (ex.: '6ª operação, comunicado de 23/10'). Quando houver várias operações ou edições do mesmo "
    "evento, use sempre a mais recente e não misture dados de outras. Compare datas e prazos com 'hoje': se o prazo já passou, diga "
    "que encerrou. Se o documento não trouxer o dado pedido, diga isso claramente, sem estimar."
)


def pergunta_especifica(texto: str) -> bool:
    return bool(_RE_ESPECIFICA.search(eventos.termo(texto)))


def _baixar_cacheado(link: str, baixar_fn=None):
    global _client
    with _lock:
        if link in _cache_docs:
            return _cache_docs[link]
    if baixar_fn is None:
        if _client is None:
            _client = httpx.Client(headers=resumos.HEADERS, timeout=30, follow_redirects=True)
        doc = resumos.baixar(_client, link)
    else:
        doc = baixar_fn(None, link)
    if doc:
        with _lock:
            if len(_cache_docs) >= 30:
                _cache_docs.pop(next(iter(_cache_docs)))
            _cache_docs[link] = doc
    return doc


def documentos_para_pergunta(redator, pergunta: str, lista: list[dict], max_docs: int = 2, baixar_fn=None) -> tuple[list[dict], list[dict] | None]:
    """(documentos baixados, candidatos). Se a pergunta não deixa claro de qual evento se trata, os documentos vêm vazios
    e 'candidatos' traz os eventos possíveis, para o robô perguntar ao assessor. Falhas nunca derrubam a resposta."""
    candidatos = sorted([e for e in lista if e.get("link")], key=lambda e: e["data_entrega"] or "", reverse=True)[:15]
    if not candidatos:
        return [], None
    escolhidos = None
    if hasattr(redator, "escolher_eventos") and len(candidatos) > 1:
        try:
            r = redator.escolher_eventos(pergunta, candidatos, max_docs)
            if isinstance(r, list):
                r = {"ids": r, "claro": True}
            if r and r.get("claro") is False:
                dúvidas = [candidatos[i] for i in r.get("candidatos", []) if 0 <= i < len(candidatos)][:4] or candidatos[:4]
                return [], dúvidas
            if r and r.get("ids"):
                escolhidos = [candidatos[i] for i in r["ids"] if 0 <= i < len(candidatos)][:max_docs]
        except Exception:
            log.exception("Falha ao escolher documentos")
    if not escolhidos:
        escolhidos = candidatos[:1]  # sem seletor: o mais recente
    docs = []
    for e in escolhidos:
        try:
            doc = _baixar_cacheado(e["link"], baixar_fn)
        except Exception as erro:
            log.warning("Não foi possível baixar %s: %s", e["link"], erro)
            continue
        if doc:
            docs.append({"rotulo": f"{e['empresa']} - {e['data']} - {e['assunto']}", "tipo": doc[0], "conteudo": doc[1]})
    return docs, None


INSTRUCAO_AMBIGUA = (
    "Não ficou claro de qual evento o assessor está falando. NÃO responda com dados de um deles e NÃO chute. Faça uma pergunta curta "
    "de esclarecimento, listando em 'eventos_possiveis' (no máximo 4, cada um com data e assunto resumido) e peça que ele indique "
    "qual é, por exemplo pela data ou pelo nome da operação."
)


def contexto_documentos(docs: list[dict], duvidas: list[dict] | None = None) -> dict:
    if duvidas:
        return {"eventos_possiveis": [f"{e['data']}: {e['assunto'][:120]}" for e in duvidas], "instrucao_resposta_direta": INSTRUCAO_AMBIGUA}
    return {"documentos_anexados": [d["rotulo"] for d in docs], "instrucao_resposta_direta": INSTRUCAO_DOCUMENTOS}
