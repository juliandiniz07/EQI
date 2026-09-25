import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Protocol

TIPOS_EVENTO = (
    "DIVIDENDO",
    "JCP",
    "RENDIMENTO",
    "DESDOBRAMENTO",
    "GRUPAMENTO",
    "BONIFICACAO",
    "SUBSCRICAO",
    "OUTRO",
)

TIPOS_EM_DINHEIRO = {"DIVIDENDO", "JCP", "RENDIMENTO"}
TIPOS_EM_ACOES = {"DESDOBRAMENTO", "GRUPAMENTO", "BONIFICACAO"}


class ProvedorIndisponivel(Exception):
    """Falha ao consultar a fonte externa de eventos (rede, credencial, ticker inexistente...)."""


@dataclass
class EventoCorporativo:
    ticker: str
    tipo: str
    data_aprovacao: date | None = None
    data_com: date | None = None
    data_pagamento: date | None = None
    valor_por_acao: float | None = None
    # Multiplicador sobre a quantidade de ações (ex.: desdobramento 1:2 -> 2.0; grupamento 10:1 -> 0.1;
    # bonificação de 10% -> 1.1). None quando a fonte não traz um fator interpretável.
    multiplicador_acoes: float | None = None
    fator_descricao: str | None = None
    preco_subscricao: float | None = None
    descricao: str | None = None
    fonte: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, date):
                d[k] = v.isoformat()
        return d


class ProvedorEventos(Protocol):
    nome: str

    def buscar_eventos(self, ticker: str) -> list[EventoCorporativo]:
        """Todos os eventos conhecidos do ticker (qualquer data). Levanta ProvedorIndisponivel em falha."""
        ...


_FATOR_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(?:para|:|/)\s*(\d+(?:[.,]\d+)?)\s*$", re.IGNORECASE)


def multiplicador_de_fator(texto: str | None) -> float | None:
    """Interpreta fatores no formato "1 para 2", "1:2" ou "10/1" como multiplicador da posição."""
    if not texto:
        return None
    m = _FATOR_RE.match(texto)
    if not m:
        return None
    antes, depois = (float(x.replace(",", ".")) for x in m.groups())
    if antes <= 0:
        return None
    return depois / antes
