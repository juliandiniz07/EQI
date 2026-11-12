"""Busca e montagem do contexto para responder uma pergunta (usado pelo chat web e pelos grupos de WhatsApp).

Regra geral, válida para qualquer empresa: quando a pergunta cita uma empresa, buscamos TODOS os eventos dela no
período e deixamos a IA julgar, pelo título e pelo resumo, o que responde à pergunta. O tipo de evento só serve
para ordenar (os do tipo pedido aparecem primeiro) e nunca esconde nada.
"""

from dataclasses import dataclass
from datetime import timedelta

import eventos

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
