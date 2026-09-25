import logging
import threading
import uuid
from datetime import date

import anthropic

from app.config import Config
from app.tools import DEFINICOES, ErroFerramenta, Ferramentas

log = logging.getLogger(__name__)

MAX_ITERACOES = 8
MAX_TOKENS = 16000
BETA_FALLBACK = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
Você é o assistente de Eventos Corporativos da EQI Investimentos. Quem conversa com você são \
assessores de investimento que precisam responder rapidamente a dúvidas de clientes sobre \
dividendos, juros sobre capital próprio (JCP), rendimentos de FIIs, desdobramentos, grupamentos, \
bonificações e direitos de subscrição.

Como trabalhar:
- Toda data, valor ou fator que você citar precisa vir das ferramentas. Consulte \
buscar_eventos_corporativos antes de responder sobre um ativo; se a ferramenta não trouxer o \
dado, diga que não encontrou na fonte em vez de estimar ou usar memória.
- Quando o assessor mencionar uma quantidade de ações do cliente, use simular_impacto_na_posicao \
para os cálculos. Não faça contas de cabeça.
- Deixe claro quem tem direito: quem estiver posicionado ao fim do pregão da data com recebe; \
a partir do dia seguinte a ação negocia "ex".
- Responda em português, de forma direta, no formato que o assessor possa repassar ao cliente: \
comece pela resposta, depois os detalhes (tipo, data com, data de pagamento, valor por ação, \
fator). Use tabela quando houver vários eventos.
- Informe a fonte dos dados. Quando a fonte for "dados de exemplo (fictícios)", avise que os \
números não são reais.
- Valores líquidos são estimativas; para dúvidas tributárias específicas do cliente, oriente a \
procurar a área tributária.
- Você não faz recomendação de investimento (comprar, vender, manter). Se pedirem, explique que \
foge do seu escopo e ofereça os dados do evento.
- Se o ticker for ambíguo ou estiver faltando, pergunte antes de consultar.
"""


class Agente:
    """Mantém uma conversa por sessão e executa o loop de ferramentas com o Claude."""

    def __init__(self, config: Config, ferramentas: Ferramentas, client: anthropic.Anthropic | None = None):
        self._config = config
        self._ferramentas = ferramentas
        self._client = client or anthropic.Anthropic()
        self._conversas: dict[str, list] = {}
        self._lock = threading.Lock()

    def nova_sessao(self) -> str:
        return uuid.uuid4().hex

    def responder(self, sessao_id: str, pergunta: str, hoje: date | None = None) -> str:
        with self._lock:
            historico = self._conversas.setdefault(sessao_id, [])
            tamanho_original = len(historico)
        hoje = hoje or date.today()
        historico.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"[Data de hoje: {hoje.isoformat()}]"},
                    {"type": "text", "text": pergunta},
                ],
            }
        )
        try:
            return self._loop(historico)
        except Exception:
            # Não deixa um turno pela metade no histórico: a próxima pergunta recomeça limpa.
            del historico[tamanho_original:]
            raise

    def _loop(self, historico: list) -> str:
        for _ in range(MAX_ITERACOES):
            resposta = self._client.beta.messages.create(
                model=self._config.modelo,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=DEFINICOES,
                messages=historico,
                thinking={"type": "adaptive"},
                output_config={"effort": self._config.esforco},
                cache_control={"type": "ephemeral"},
                betas=[BETA_FALLBACK],
                fallbacks="default",
            )

            if resposta.stop_reason == "refusal":
                raise RespostaRecusada()

            historico.append({"role": "assistant", "content": resposta.content})

            if resposta.stop_reason != "tool_use":
                texto = "\n\n".join(b.text for b in resposta.content if b.type == "text").strip()
                if resposta.stop_reason == "max_tokens":
                    texto += "\n\n_(Resposta interrompida por limite de tamanho. Peça para continuar.)_"
                return texto or "Não consegui gerar uma resposta. Pode reformular a pergunta?"

            resultados = [self._executar(b) for b in resposta.content if b.type == "tool_use"]
            historico.append({"role": "user", "content": resultados})

        raise LimiteIteracoes()

    def _executar(self, bloco) -> dict:
        try:
            conteudo = self._ferramentas.executar(bloco.name, bloco.input)
            return {"type": "tool_result", "tool_use_id": bloco.id, "content": conteudo}
        except ErroFerramenta as e:
            return {"type": "tool_result", "tool_use_id": bloco.id, "content": str(e), "is_error": True}
        except Exception:
            log.exception("Falha inesperada na ferramenta %s", bloco.name)
            return {
                "type": "tool_result",
                "tool_use_id": bloco.id,
                "content": "Erro interno ao executar a ferramenta.",
                "is_error": True,
            }


class RespostaRecusada(Exception):
    pass


class LimiteIteracoes(Exception):
    pass
