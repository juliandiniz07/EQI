"""API do Assistente EQI de Eventos Corporativos.

POST /login      {"senha"}                 -> {"token", "expira_em"}
POST /consultar  {"pergunta", "historico"} -> {"resposta", "eventos", "meta"}   (Authorization: Bearer <token>)
GET  /health                               -> status do sistema
GET  /                                     -> chat web (web/index.html)
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import eventos
from consulta import Interpretacao, Interpretador

load_dotenv()
log = logging.getLogger("api")

MAX_EVENTOS_RESPOSTA = 10
DIAS_AMPLIACAO = 365
INDEX_HTML = Path(__file__).resolve().parent / "web" / "index.html"

SYSTEM_PROMPT = """\
Você é o Assistente EQI de Eventos Corporativos. Conversa com assessores de investimento e fala \
como um analista sênior de mercado de capitais brasileiro: objetivo, claro, com contexto, \
cordial e sem soar robótico.

Regras:
1. Use APENAS os eventos do bloco DADOS enviado junto com a pergunta. Nunca invente empresas, \
datas, valores, tickers, partes envolvidas ou desdobramentos que não estejam lá. Você pode \
explicar em termos gerais o que um tipo de evento significa (ex.: o que é uma OPA), sem \
atribuir isso como fato a uma empresa.
2. Cite empresa e data (dd/mm) de cada evento mencionado. Se houver ticker em DADOS, use-o \
entre parênteses. Se o total encontrado for maior que o mostrado, diga isso.
3. Se a lista de eventos vier vazia, diga que não encontrou nada no período consultado e sugira, \
de forma útil, ampliar o período ou reformular (outro nome da empresa, outro tipo de evento).
4. Se "periodo_ampliado" for verdadeiro, avise que no período pedido não havia nada e que os \
eventos mostrados são de um período maior.
5. Responda em português do Brasil, em texto corrido e curto (até ~150 palavras), sem tabelas \
e sem markdown pesado; os detalhes de cada evento aparecem em cartões abaixo da sua resposta. \
Quando fizer sentido, termine oferecendo mais detalhes.
6. Não faça recomendação de investimento (comprar, vender, manter).
7. Nunca mencione que é uma IA, nem nomes de modelos ou empresas de tecnologia. Você é o \
"Assistente EQI".
"""


# ---------------------------------------------------------------------------
# Autenticação e limite de uso
# ---------------------------------------------------------------------------

class Autenticador:
    def __init__(self, senha: str, segredo: str, horas: int = 12):
        self._senha = senha.encode()
        self._segredo = segredo.encode()
        self._validade = horas * 3600

    def senha_confere(self, senha: str) -> bool:
        return hmac.compare_digest(senha.encode(), self._senha)

    def emitir(self) -> tuple[str, int]:
        expira = int(time.time()) + self._validade
        assinatura = hmac.new(self._segredo, str(expira).encode(), hashlib.sha256).hexdigest()
        return f"{expira}.{assinatura}", expira

    def valido(self, token: str) -> bool:
        expira, _, assinatura = token.partition(".")
        if not expira.isdigit() or int(expira) < time.time():
            return False
        esperado = hmac.new(self._segredo, expira.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(assinatura, esperado)


class LimiteDeUso:
    """Janela deslizante em memória por chave (IP)."""

    def __init__(self, maximo: int, janela_segundos: int):
        self._maximo = maximo
        self._janela = janela_segundos
        self._eventos: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def _fila(self, chave: str) -> deque:
        fila = self._eventos[chave]
        agora = time.monotonic()
        while fila and agora - fila[0] > self._janela:
            fila.popleft()
        return fila

    def excedido(self, chave: str) -> bool:
        with self._lock:
            return len(self._fila(chave)) >= self._maximo

    def registrar(self, chave: str) -> None:
        with self._lock:
            self._fila(chave).append(time.monotonic())

    def permitir(self, chave: str) -> bool:
        """Registra o uso e diz se ainda estava dentro do limite."""
        with self._lock:
            fila = self._fila(chave)
            if len(fila) >= self._maximo:
                return False
            fila.append(time.monotonic())
            return True


# ---------------------------------------------------------------------------
# Redação da resposta (Gemini)
# ---------------------------------------------------------------------------

class RedatorGemini:
    def __init__(self, api_key: str, modelos: list[str]):
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._modelos = modelos

    def redigir(self, pergunta: str, historico: list[dict], dados: dict) -> str | None:
        """Texto da resposta, ou None se nenhum modelo respondeu (cota esgotada, erro...)."""
        from google.genai import errors, types

        conteudos = []
        for m in historico[-10:]:
            papel = "user" if m["role"] == "user" else "model"
            if not conteudos and papel == "model":
                continue  # a conversa enviada à IA precisa começar pelo usuário
            conteudos.append(types.Content(role=papel, parts=[types.Part.from_text(text=m["content"][:3000])]))
        texto = f"DADOS:\n{json.dumps(dados, ensure_ascii=False, indent=1)}\n\nPERGUNTA DO ASSESSOR: {pergunta}"
        conteudos.append(types.Content(role="user", parts=[types.Part.from_text(text=texto)]))
        config = types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, temperature=0.4, max_output_tokens=2048)

        for modelo in self._modelos:
            try:
                resp = self._client.models.generate_content(model=modelo, contents=conteudos, config=config)
                if resp.text and resp.text.strip():
                    return resp.text.strip()
                log.warning("Modelo %s devolveu resposta vazia", modelo)
            except errors.APIError as e:
                log.warning("Falha no modelo %s (%s): %s", modelo, getattr(e, "code", "?"), e)
            except Exception:
                log.exception("Erro inesperado ao chamar o modelo %s", modelo)
        return None


def resposta_sem_ia(interp: Interpretacao, eventos_mostrados: list[dict], total: int, ampliado: bool) -> str:
    """Resposta montada por regra — usada quando a IA não está disponível."""
    alvo = f" sobre {interp.empresa}" if interp.empresa else ""
    if not eventos_mostrados:
        return (
            f"Não encontrei eventos relevantes{alvo} no período ({interp.descricao_periodo}). "
            "Tente ampliar o período (ex.: \"este ano\") ou reformular com outro nome da empresa ou tipo de evento."
        )
    inicio = (
        f"No período pedido não houve nada{alvo}, mas nos últimos {DIAS_AMPLIACAO} dias encontrei {total} evento(s)."
        if ampliado
        else f"Encontrei {total} evento(s){alvo} ({interp.descricao_periodo})."
    )
    if total > len(eventos_mostrados):
        inicio += f" Seguem os {len(eventos_mostrados)} mais recentes."
    linhas = [f"• {e['empresa']} ({e['data']}): {e['assunto']}" for e in eventos_mostrados[:5]]
    return inicio + "\n\n" + "\n".join(linhas)


# ---------------------------------------------------------------------------
# Modelos da API
# ---------------------------------------------------------------------------

class Mensagem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=6000)


class Consulta(BaseModel):
    pergunta: str = Field(min_length=1, max_length=1000)
    historico: list[Mensagem] = Field(default_factory=list, max_length=40)


class Login(BaseModel):
    senha: str = Field(max_length=200)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def criar_app(redator=None, interpretador: Interpretador | None = None, hoje=None) -> FastAPI:
    senha = os.getenv("APP_SENHA", "eqi2026")
    segredo = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    if senha == "eqi2026":
        log.warning("APP_SENHA está com o valor padrão. Troque no .env antes de publicar o site.")
    if not os.getenv("SECRET_KEY"):
        log.warning("SECRET_KEY não definida: os logins caem sempre que a API reiniciar.")

    if redator is None and os.getenv("GEMINI_API_KEY"):
        modelos = [m.strip() for m in os.getenv("GEMINI_MODELOS", "gemini-flash-lite-latest,gemini-flash-latest").split(",") if m.strip()]
        redator = RedatorGemini(os.environ["GEMINI_API_KEY"], modelos)
    if redator is None:
        log.warning("GEMINI_API_KEY não definida: respostas serão montadas sem IA.")

    auth = Autenticador(senha, segredo, horas=int(os.getenv("SESSAO_HORAS", "12")))
    limite_login = LimiteDeUso(maximo=10, janela_segundos=15 * 60)  # senhas erradas por IP
    limite_consulta = LimiteDeUso(maximo=int(os.getenv("LIMITE_POR_MINUTO", "20")), janela_segundos=60)
    interpretador = interpretador or Interpretador()
    hoje = hoje or date.today

    app = FastAPI(title="Assistente EQI — Eventos Corporativos", docs_url=None, redoc_url=None)
    origens = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origens, allow_methods=["GET", "POST"], allow_headers=["*"])

    def exigir_login(authorization: str = Header(default="")):
        token = authorization.removeprefix("Bearer ").strip()
        if not token or not auth.valido(token):
            raise HTTPException(401, "Sessão expirada. Entre novamente.")

    @app.get("/")
    def index():
        return FileResponse(INDEX_HTML)

    @app.post("/login")
    def login(dados: Login, request: Request):
        if limite_login.excedido(_ip(request)):
            raise HTTPException(429, "Muitas tentativas. Aguarde alguns minutos.")
        if not auth.senha_confere(dados.senha):
            limite_login.registrar(_ip(request))  # só erros contam: escritório inteiro pode sair pelo mesmo IP
            raise HTTPException(401, "Senha incorreta.")
        token, expira = auth.emitir()
        return {"token": token, "expira_em": expira}

    @app.get("/health")
    def health():
        with eventos.conectar() as con:
            banco = eventos.status(con)
        return {"status": "ok", "ia_configurada": redator is not None, **banco}

    @app.post("/consultar", dependencies=[Depends(exigir_login)])
    def consultar(dados: Consulta, request: Request):
        if not limite_consulta.permitir(_ip(request)):
            raise HTTPException(429, "Muitas perguntas em sequência. Aguarde um minuto.")

        historico = [m.model_dump() for m in dados.historico]
        with eventos.conectar() as con:
            interp = interpretador.interpretar(dados.pergunta, historico, hoje=hoje(), nomes_no_banco=eventos.empresas_conhecidas(con))
            encontrados = eventos.buscar(con, interp.filtro())
            ampliado = False
            if not encontrados and (interp.termos_empresa or interp.tipos) and interp.dias < DIAS_AMPLIACAO:
                filtro = interp.filtro()
                filtro.desde = interp.ate - timedelta(days=DIAS_AMPLIACAO - 1)
                encontrados = eventos.buscar(con, filtro)
                ampliado = bool(encontrados)

        mostrados = encontrados[:MAX_EVENTOS_RESPOSTA]
        meta = {**interp.meta(), "total": len(encontrados), "periodo_ampliado": ampliado}
        contexto = {
            "hoje": hoje().strftime("%d/%m/%Y"),
            "filtros_entendidos": meta,
            "total_encontrado": len(encontrados),
            "mostrando": len(mostrados),
            "periodo_ampliado": ampliado,
            "eventos": [
                {k: e[k] for k in ("empresa", "data", "assunto", "tipo_evento_rotulo", "categoria", "tipo", "tipo_ativo")}
                for e in mostrados
            ],
        }
        if interp.ticker:
            contexto["ticker_da_empresa"] = interp.ticker

        texto = redator.redigir(dados.pergunta, historico, contexto) if redator else None
        if not texto:
            texto = resposta_sem_ia(interp, mostrados, len(encontrados), ampliado)
        return {"resposta": texto, "eventos": mostrados, "meta": meta}

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = criar_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8001")),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
