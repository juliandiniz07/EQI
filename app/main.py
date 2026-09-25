import logging
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent import Agente, LimiteIteracoes, RespostaRecusada
from app.config import Config
from app.providers import criar_provedor
from app.tools import Ferramentas

log = logging.getLogger(__name__)
_STATIC = Path(__file__).resolve().parent / "static"


class Pergunta(BaseModel):
    mensagem: str = Field(min_length=1, max_length=4000)
    sessao_id: str | None = None


class Resposta(BaseModel):
    sessao_id: str
    resposta: str


def criar_app(agente: Agente | None = None) -> FastAPI:
    if agente is None:
        config = Config.from_env()
        agente = Agente(config, Ferramentas(criar_provedor(config), config.aliquota_ir_jcp))

    app = FastAPI(title="Agente de Eventos Corporativos")

    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/chat", response_model=Resposta)
    def chat(p: Pergunta):
        sessao_id = p.sessao_id or agente.nova_sessao()
        try:
            texto = agente.responder(sessao_id, p.mensagem)
        except RespostaRecusada:
            texto = "Não posso ajudar com essa solicitação. Reformule a pergunta sobre o evento corporativo."
        except LimiteIteracoes:
            texto = "A consulta ficou longa demais. Tente perguntar sobre menos ativos por vez."
        except anthropic.RateLimitError:
            raise HTTPException(503, "Muitas requisições no momento. Tente novamente em instantes.")
        except anthropic.APIConnectionError:
            raise HTTPException(503, "Sem conexão com o serviço de IA. Tente novamente.")
        except anthropic.APIStatusError as e:
            log.error("Erro da API Anthropic (%s): %s", e.status_code, e.message)
            raise HTTPException(502, "Erro no serviço de IA.")
        return Resposta(sessao_id=sessao_id, resposta=texto)

    return app
