"""Interpretação da pergunta do assessor (empresa, tipo de evento, período) sem gastar chamadas de IA.

O domínio é estreito o bastante para regras funcionarem bem, e isso economiza a cota gratuita
da IA para o que ela faz melhor: redigir a resposta.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import eventos

ARQUIVO_APELIDOS = Path(__file__).resolve().parent / "apelidos.json"
DIAS_PADRAO = 30
DIAS_PADRAO_EMPRESA = 365

# Palavras da pergunta -> tipo de evento
_TIPOS_PERGUNTA = [
    ("OPA", r"\bopas?\b|oferta publica de aquisicao|fechamento de capital"),
    ("FUSAO", r"\bfus(?:ao|oes)\b|incorpora|aquisic|\bcis(?:ao|oes)\b|\bm ?& ?a\b|compra de empresa"),
    ("RECUPERACAO", r"recuperac|\bfalenc|\brj\b|reestrutura"),
    ("CANCELAMENTO", r"cancelamento|deslist|cancelou"),
    ("EMISSAO", r"emiss(?:ao|oes)|emitiu|emitiram|oferta (?:publica )?de cotas|subscric|follow ?on|novas cotas"),
    ("DESDOBRAMENTO", r"desdobr|grupament|\bsplit\b|\binplit\b"),
    ("LIQUIDACAO", r"liquidac"),
]
_RE_FII = re.compile(r"\bfiis?\b|fundos? imobiliarios?|fundo de investimento imobiliario")
_RE_TICKER = re.compile(r"\b([a-z]{4})(\d{1,2})\b")
_RE_SEGUIMENTO = re.compile(
    r"^(?:e|e a|e o|e da|e do|e de|e na|e no)\b|mais detalhes?|\bdetalh|sobre (?:isso|esse|essa|ele|ela)|\bdel[ae]s?\b|\bdess[ae]\b|\bdisso\b|\bnest[ae]\b"
)

# Palavras que nunca devem ser tratadas como nome de empresa
_STOPWORDS = set(
    """
    a o as os um uma de da do das dos e em no na nos nas com por para pra pro que qual quais quem como
    quando onde teve tem tiveram houve ha algum alguma alguns algumas algo novidade novidades noticia noticias
    hoje ontem semana semanas mes meses ano anos dia dias ultimo ultima ultimos ultimas passado passada
    esse essa este esta isso esses essas estes estas nesse nessa neste nesta desse dessa deste desta
    aconteceu acontecendo acontece rolou evento eventos fato fatos relevante relevantes recente recentes
    empresa empresas companhia companhias acao acoes papel papeis fundo fundos fii fiis cota cotas
    imobiliario imobiliarios mercado brasil sobre mais detalhe detalhes me fale falar diga mostre mostra
    lista liste quero saber ver fizeram fez fazer feito anunciou anunciaram publicou publicaram
    opa opas oferta ofertas publica publicas aquisicao aquisicoes fusao fusoes incorporacao cisao
    recuperacao judicial extrajudicial falencia cancelamento registro emissao emissoes subscricao
    desdobramento desdobramentos grupamento grupamentos liquidacao split
    bom boa dia tarde noite ola oi obrigado obrigada valeu
    """.split()
)

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


@dataclass
class Interpretacao:
    empresa: str | None = None  # rótulo para exibir
    ticker: str | None = None
    termos_empresa: list[str] = field(default_factory=list)
    tipos: list[str] = field(default_factory=list)
    tipo_ativo: str | None = None
    desde: date | None = None
    ate: date | None = None
    periodo_explicito: bool = False
    descricao_periodo: str = ""

    @property
    def dias(self) -> int:
        return (self.ate - self.desde).days + 1

    def filtro(self) -> eventos.Filtro:
        return eventos.Filtro(self.desde, self.ate, self.termos_empresa, self.tipos, self.tipo_ativo)

    def meta(self) -> dict:
        return {
            "empresa": self.empresa,
            "ticker": self.ticker,
            "tipo": [eventos.ROTULOS[t] for t in self.tipos] or None,
            "tipo_ativo": self.tipo_ativo,
            "dias": self.dias,
            "desde": self.desde.isoformat(),
            "ate": self.ate.isoformat(),
            "periodo": self.descricao_periodo,
        }


_termo = eventos.termo


class Interpretador:
    def __init__(self, arquivo_apelidos: Path = ARQUIVO_APELIDOS):
        dados = json.loads(arquivo_apelidos.read_text(encoding="utf-8"))
        self._empresas = dados["empresas"]
        self._por_ticker: dict[str, dict] = {}
        self._apelidos: list[tuple[re.Pattern, dict]] = []
        for e in self._empresas:
            if e.get("ticker"):
                self._por_ticker[e["ticker"].lower()[:4]] = e
            for a in e["apelidos"]:
                self._apelidos.append((re.compile(rf"\b{re.escape(_termo(a))}\b"), e))
        # Apelidos mais longos primeiro: "banco do brasil" antes de "brasil"
        self._apelidos.sort(key=lambda x: -len(x[0].pattern))

    # -- empresa ------------------------------------------------------------------

    def _empresa(self, texto: str, nomes_no_banco: list[str]) -> tuple[str | None, str | None, list[str], str | None]:
        for regex, e in self._apelidos:
            m = regex.search(texto)
            if m:
                citado = m.group(0).upper()
                ticker = citado if _RE_TICKER.fullmatch(m.group(0)) else e.get("ticker")
                return e["nome"], ticker, [_termo(b) for b in e["busca"]], e.get("tipo_ativo")

        m = _RE_TICKER.search(texto)
        if m:
            raiz, ticker = m.group(1), (m.group(1) + m.group(2)).upper()
            e = self._por_ticker.get(raiz)
            if e:
                return e["nome"], ticker, [_termo(b) for b in e["busca"]] + [ticker.lower()], e.get("tipo_ativo")
            return ticker, ticker, [ticker.lower()], "FII" if m.group(2) == "11" else None

        # Palavra da pergunta que aparece no nome de alguma empresa do banco (ex.: "Brava", "Lupatech")
        palavras = [p for p in texto.split() if len(p) >= 4 and p not in _STOPWORDS and not p.isdigit()]
        for p in palavras:
            achados = [n for n in nomes_no_banco if re.search(rf"\b{re.escape(p)}\b", _termo(n))]
            if achados:
                rotulo = min(achados, key=len).strip() if len(achados) == 1 else p.capitalize()
                return rotulo, None, [p], None
        return None, None, [], None

    # -- período ------------------------------------------------------------------

    @staticmethod
    def _periodo(texto: str, hoje: date) -> tuple[date, date, str] | None:
        if re.search(r"\bhoje\b", texto):
            return hoje, hoje, "hoje"
        if re.search(r"\bontem\b", texto):
            d = hoje - timedelta(days=1)
            return d, d, "ontem"
        m = re.search(r"ultim[oa]s? (\d{1,3}) (dias?|semanas?|mes|meses)", texto) or re.search(
            r"\b(\d{1,3}) (dias?|semanas?|mes|meses)\b", texto
        )
        if m:
            n = int(m.group(1))
            dias = n * {"d": 1, "s": 7, "m": 30}[m.group(2)[0]]
            return hoje - timedelta(days=dias - 1), hoje, f"últimos {n} {m.group(2)}"
        if re.search(r"semana passada|ultima semana", texto):
            inicio = hoje - timedelta(days=hoje.weekday() + 7)
            return inicio, inicio + timedelta(days=6), "semana passada"
        if re.search(r"\b(?:ess|est|nest|dest)a semana\b|\bda semana\b|\bna semana\b", texto):
            return hoje - timedelta(days=hoje.weekday()), hoje, "esta semana"
        if re.search(r"mes passado|ultimo mes", texto):
            fim = hoje.replace(day=1) - timedelta(days=1)
            return fim.replace(day=1), fim, "mês passado"
        if re.search(r"\b(?:ess|est|nest|dest)e mes\b|\bdo mes\b|\bno mes\b", texto):
            return hoje.replace(day=1), hoje, "este mês"
        m = re.search(r"\b(?:em|de) (" + "|".join(_MESES) + r")(?: de (\d{4}))?\b", texto)
        if m:
            mes = _MESES[m.group(1)]
            ano = int(m.group(2)) if m.group(2) else (hoje.year if mes <= hoje.month else hoje.year - 1)
            inicio = date(ano, mes, 1)
            fim = (date(ano + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1))
            return inicio, min(fim, hoje), f"{m.group(1)} de {ano}"
        if re.search(r"ano passado", texto):
            return date(hoje.year - 1, 1, 1), date(hoje.year - 1, 12, 31), f"{hoje.year - 1}"
        if re.search(r"\b(?:ess|est|nest|dest)e ano\b|\bdo ano\b|\bno ano\b", texto):
            return date(hoje.year, 1, 1), hoje, f"{hoje.year} (até hoje)"
        m = re.search(r"\b(20\d{2})\b", texto)
        if m:
            ano = int(m.group(1))
            return date(ano, 1, 1), min(date(ano, 12, 31), hoje), str(ano)
        return None

    # -- principal ------------------------------------------------------------------

    def interpretar(
        self, pergunta: str, historico: list[dict] | None = None, hoje: date | None = None, nomes_no_banco: list[str] | None = None
    ) -> Interpretacao:
        hoje = hoje or date.today()
        nomes_no_banco = nomes_no_banco or []
        texto = _termo(pergunta)
        r = Interpretacao()

        r.empresa, r.ticker, r.termos_empresa, ativo_empresa = self._empresa(texto, nomes_no_banco)
        r.tipos = [cod for cod, pad in _TIPOS_PERGUNTA if re.search(pad, texto)]
        r.tipo_ativo = "FII" if _RE_FII.search(texto) else ativo_empresa if ativo_empresa == "FII" else None
        periodo = self._periodo(texto, hoje)

        # Pergunta de seguimento ("e da Vale?", "mais detalhes") herda o que faltar da anterior
        if historico and _RE_SEGUIMENTO.search(texto):
            anteriores = [m["content"] for m in historico if m.get("role") == "user" and m.get("content")]
            if anteriores:
                ant = self.interpretar(anteriores[-1], historico=None, hoje=hoje, nomes_no_banco=nomes_no_banco)
                if not r.termos_empresa:
                    r.empresa, r.ticker, r.termos_empresa = ant.empresa, ant.ticker, ant.termos_empresa
                    r.tipos = r.tipos or ant.tipos
                r.tipo_ativo = r.tipo_ativo or ant.tipo_ativo
                if not periodo and ant.periodo_explicito:
                    periodo = (ant.desde, ant.ate, ant.descricao_periodo)

        if periodo:
            r.desde, r.ate, r.descricao_periodo = periodo
            r.periodo_explicito = True
        else:
            dias = DIAS_PADRAO_EMPRESA if r.termos_empresa else DIAS_PADRAO
            r.desde, r.ate = hoje - timedelta(days=dias - 1), hoje
            r.descricao_periodo = f"últimos {dias} dias"
        return r
