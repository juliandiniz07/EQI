"""Provedor com dados FICTÍCIOS, para desenvolvimento e testes sem acesso à API externa."""

import json
from datetime import date
from pathlib import Path

from app.providers.base import EventoCorporativo

_ARQUIVO = Path(__file__).resolve().parent.parent / "data" / "eventos_exemplo.json"
_CAMPOS_DATA = ("data_aprovacao", "data_com", "data_pagamento")


class ProvedorMock:
    nome = "dados de exemplo (fictícios)"

    def __init__(self, arquivo: Path = _ARQUIVO):
        self._eventos = []
        for item in json.loads(arquivo.read_text(encoding="utf-8")):
            for campo in _CAMPOS_DATA:
                if item.get(campo):
                    item[campo] = date.fromisoformat(item[campo])
            self._eventos.append(EventoCorporativo(fonte=self.nome, **item))

    def buscar_eventos(self, ticker: str) -> list[EventoCorporativo]:
        ticker = ticker.strip().upper()
        return [e for e in self._eventos if e.ticker == ticker]
