"""Provedor de eventos corporativos via brapi.dev (API de mercado brasileiro).

Usa GET /quote/{ticker}?dividends=true, que retorna em `dividendsData`:
  - cashDividends:  proventos em dinheiro (dividendos, JCP, rendimentos)
  - stockDividends: eventos em ações (desdobramento, grupamento, bonificação)
  - subscriptions:  direitos de subscrição
Para trocar de fornecedor (B3, Economatica, Quantum...), implemente outro ProvedorEventos.
"""

from datetime import date, datetime

import httpx

from app.providers.base import EventoCorporativo, ProvedorIndisponivel, multiplicador_de_fator

_MAPA_TIPOS = {
    "DIVIDENDO": "DIVIDENDO",
    "JCP": "JCP",
    "JUROS SOBRE CAPITAL PROPRIO": "JCP",
    "RENDIMENTO": "RENDIMENTO",
    "DESDOBRAMENTO": "DESDOBRAMENTO",
    "GRUPAMENTO": "GRUPAMENTO",
    "BONIFICACAO": "BONIFICACAO",
    "SUBSCRICAO": "SUBSCRICAO",
}


def _tipo(label: str | None, padrao: str) -> str:
    if not label:
        return padrao
    chave = label.strip().upper().replace("Ç", "C").replace("Ã", "A").replace("Ó", "O")
    return _MAPA_TIPOS.get(chave, "OUTRO")


def _data(valor) -> date | None:
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _float(valor) -> float | None:
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


class ProvedorBrapi:
    nome = "brapi.dev"

    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = client or httpx.Client(timeout=15)

    def buscar_eventos(self, ticker: str) -> list[EventoCorporativo]:
        ticker = ticker.strip().upper()
        params = {"dividends": "true"}
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            resp = self._client.get(f"{self._base_url}/quote/{ticker}", params=params, headers=headers)
        except httpx.HTTPError as e:
            raise ProvedorIndisponivel(f"Erro de rede ao consultar {self.nome}: {e}") from e
        if resp.status_code == 404:
            raise ProvedorIndisponivel(f"Ticker {ticker} não encontrado em {self.nome}.")
        if resp.status_code >= 400:
            raise ProvedorIndisponivel(f"{self.nome} respondeu HTTP {resp.status_code} para {ticker}.")

        resultados = resp.json().get("results") or []
        if not resultados:
            return []
        dados = resultados[0].get("dividendsData") or {}
        return self._converter(ticker, dados)

    def _converter(self, ticker: str, dados: dict) -> list[EventoCorporativo]:
        eventos: list[EventoCorporativo] = []
        for d in dados.get("cashDividends") or []:
            eventos.append(
                EventoCorporativo(
                    ticker=ticker,
                    tipo=_tipo(d.get("label"), "DIVIDENDO"),
                    data_aprovacao=_data(d.get("approvedOn")),
                    data_com=_data(d.get("lastDatePrior")),
                    data_pagamento=_data(d.get("paymentDate")),
                    valor_por_acao=_float(d.get("rate")),
                    descricao=d.get("remarks") or d.get("relatedTo"),
                    fonte=self.nome,
                )
            )
        for d in dados.get("stockDividends") or []:
            fator = d.get("completeFactor")
            eventos.append(
                EventoCorporativo(
                    ticker=ticker,
                    tipo=_tipo(d.get("label"), "OUTRO"),
                    data_aprovacao=_data(d.get("approvedOn")),
                    data_com=_data(d.get("lastDatePrior")),
                    multiplicador_acoes=multiplicador_de_fator(fator),
                    fator_descricao=fator or (str(d["factor"]) if d.get("factor") is not None else None),
                    descricao=d.get("remarks"),
                    fonte=self.nome,
                )
            )
        for d in dados.get("subscriptions") or []:
            eventos.append(
                EventoCorporativo(
                    ticker=ticker,
                    tipo="SUBSCRICAO",
                    data_aprovacao=_data(d.get("approvedOn")),
                    data_com=_data(d.get("lastDatePrior")),
                    preco_subscricao=_float(d.get("priceUnit")),
                    fator_descricao=f"{d['percentage']}%" if d.get("percentage") is not None else None,
                    descricao=" | ".join(
                        x for x in (d.get("remarks"), d.get("tradingPeriod") and f"Negociação: {d['tradingPeriod']}") if x
                    )
                    or None,
                    fonte=self.nome,
                )
            )
        return eventos
