"""Ferramentas que o agente pode chamar. Todo número que chega ao assessor sai daqui, não do modelo."""

import json
import math
from datetime import date

from app.providers.base import (
    TIPOS_EM_ACOES,
    TIPOS_EM_DINHEIRO,
    TIPOS_EVENTO,
    EventoCorporativo,
    ProvedorEventos,
    ProvedorIndisponivel,
)

MAX_EVENTOS = 40

_FILTROS = {
    "ticker": {"type": "string", "description": "Código de negociação na B3, ex.: PETR4, ITUB4, HGLG11."},
    "tipos": {
        "type": "array",
        "items": {"type": "string", "enum": list(TIPOS_EVENTO)},
        "description": "Filtra por tipo de evento. Omita para trazer todos.",
    },
    "data_inicio": {
        "type": "string",
        "format": "date",
        "description": "AAAA-MM-DD. Traz eventos com data com, pagamento ou aprovação a partir desta data.",
    },
    "data_fim": {
        "type": "string",
        "format": "date",
        "description": "AAAA-MM-DD. Traz eventos com data com, pagamento ou aprovação até esta data.",
    },
}

DEFINICOES = [
    {
        "name": "buscar_eventos_corporativos",
        "description": (
            "Consulta a fonte de mercado e retorna os eventos corporativos de um ativo: dividendos, JCP, "
            "rendimentos de FII, desdobramentos, grupamentos, bonificações e subscrições, com data de "
            "aprovação, data com (último dia com direito), data de pagamento, valor por ação e fator. "
            "Use sempre antes de afirmar qualquer data ou valor. Chame uma vez por ticker; para vários "
            "ativos, faça as chamadas em paralelo."
        ),
        "input_schema": {
            "type": "object",
            "properties": _FILTROS,
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "name": "simular_impacto_na_posicao",
        "description": (
            "Calcula o efeito dos eventos de um ativo sobre uma posição: valor bruto e líquido estimado de "
            "proventos em dinheiro (com IR retido sobre JCP) e a nova quantidade de ações em desdobramentos, "
            "grupamentos e bonificações (com frações). Use quando o assessor informar uma quantidade de ações. "
            "Aceita os mesmos filtros de buscar_eventos_corporativos."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_FILTROS,
                "quantidade": {"type": "integer", "minimum": 1, "description": "Quantidade de ações/cotas na data com."},
            },
            "required": ["ticker", "quantidade"],
            "additionalProperties": False,
        },
    },
]


class ErroFerramenta(Exception):
    """Erro mostrado ao modelo como tool_result com is_error=True."""


def _data_param(valor, nome: str) -> date | None:
    if valor in (None, ""):
        return None
    try:
        return date.fromisoformat(str(valor))
    except ValueError:
        raise ErroFerramenta(f"{nome} inválida: {valor!r}. Use o formato AAAA-MM-DD.")


def _filtrar(eventos: list[EventoCorporativo], entrada: dict) -> list[EventoCorporativo]:
    tipos = set(entrada.get("tipos") or [])
    invalidos = tipos - set(TIPOS_EVENTO)
    if invalidos:
        raise ErroFerramenta(f"Tipos inválidos: {sorted(invalidos)}. Válidos: {list(TIPOS_EVENTO)}.")
    inicio = _data_param(entrada.get("data_inicio"), "data_inicio")
    fim = _data_param(entrada.get("data_fim"), "data_fim")

    def no_periodo(e: EventoCorporativo) -> bool:
        if inicio is None and fim is None:
            return True
        datas = [d for d in (e.data_com, e.data_pagamento, e.data_aprovacao) if d]
        return any((inicio is None or d >= inicio) and (fim is None or d <= fim) for d in datas)

    selecionados = [e for e in eventos if (not tipos or e.tipo in tipos) and no_periodo(e)]
    selecionados.sort(key=lambda e: e.data_com or e.data_aprovacao or date.min, reverse=True)
    return selecionados


class Ferramentas:
    def __init__(self, provedor: ProvedorEventos, aliquota_ir_jcp: float):
        self._provedor = provedor
        self._aliquota_ir_jcp = aliquota_ir_jcp

    def executar(self, nome: str, entrada: dict) -> str:
        """Executa a ferramenta e devolve JSON. Levanta ErroFerramenta para erros que o modelo deve ver."""
        if nome == "buscar_eventos_corporativos":
            return json.dumps(self.buscar_eventos(entrada), ensure_ascii=False)
        if nome == "simular_impacto_na_posicao":
            return json.dumps(self.simular_impacto(entrada), ensure_ascii=False)
        raise ErroFerramenta(f"Ferramenta desconhecida: {nome}")

    def _eventos(self, entrada: dict) -> tuple[str, list[EventoCorporativo]]:
        ticker = str(entrada.get("ticker") or "").strip().upper()
        if not ticker:
            raise ErroFerramenta("Informe o ticker.")
        try:
            eventos = self._provedor.buscar_eventos(ticker)
        except ProvedorIndisponivel as e:
            raise ErroFerramenta(str(e)) from e
        return ticker, _filtrar(eventos, entrada)

    def buscar_eventos(self, entrada: dict) -> dict:
        ticker, eventos = self._eventos(entrada)
        return {
            "ticker": ticker,
            "fonte": self._provedor.nome,
            "total_encontrado": len(eventos),
            "eventos": [e.to_dict() for e in eventos[:MAX_EVENTOS]],
            "truncado": len(eventos) > MAX_EVENTOS,
        }

    def simular_impacto(self, entrada: dict) -> dict:
        quantidade = entrada.get("quantidade")
        if not isinstance(quantidade, int) or isinstance(quantidade, bool) or quantidade < 1:
            raise ErroFerramenta("quantidade deve ser um inteiro positivo.")
        ticker, eventos = self._eventos(entrada)

        simulacoes = []
        for e in eventos[:MAX_EVENTOS]:
            item = {"evento": e.to_dict()}
            if e.tipo in TIPOS_EM_DINHEIRO and e.valor_por_acao is not None:
                bruto = round(quantidade * e.valor_por_acao, 2)
                aliquota = self._aliquota_ir_jcp if e.tipo == "JCP" else 0.0
                ir = round(bruto * aliquota, 2)
                item.update(valor_bruto=bruto, aliquota_ir=aliquota, ir_retido_estimado=ir, valor_liquido_estimado=round(bruto - ir, 2))
            elif e.tipo in TIPOS_EM_ACOES and e.multiplicador_acoes:
                nova = quantidade * e.multiplicador_acoes
                inteira = math.floor(nova + 1e-9)
                item.update(
                    quantidade_antes=quantidade,
                    quantidade_depois=inteira,
                    fracao_de_acao=round(nova - inteira, 6),
                    observacao="Frações costumam ser agrupadas e leiloadas pela companhia, com o valor creditado depois.",
                )
            else:
                item["observacao"] = "A fonte não traz dados suficientes para calcular o impacto deste evento."
            simulacoes.append(item)

        return {
            "ticker": ticker,
            "quantidade": quantidade,
            "fonte": self._provedor.nome,
            "simulacoes": simulacoes,
            "aviso": "Valores estimados. IR sobre dividendos e rendimentos pode variar conforme o investidor e a legislação vigente.",
        }
