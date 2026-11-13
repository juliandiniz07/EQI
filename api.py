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

import atendimento
import conhecimento
import eventos
import grupos
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
6. Não faça recomendação de investimento própria (comprar, vender, manter). Exceção: se uma ficha interna trouxer uma 'Orientação', apresente-a sempre que o assessor perguntar por recomendação, orientação, sugestão, o que fazer, se deve vender, comprar ou subscrever, ou se vale a pena, sempre como orientação e sem acrescentar nada além do que a ficha diz, lembrando de avaliar o suitability do cliente.
7. Nunca mencione que é uma IA, nem nomes de modelos ou empresas de tecnologia. Você é o \
"Assistente EQI".
8. Não cumprimente nem se apresente: nada de "Olá", "Oi", "Bom dia" ou "Sou o Assistente EQI" no início ou no fim. Comece direto pela resposta, e use o histórico da conversa em vez de recomeçar. Só responda a uma saudação, de forma breve e uma única vez, quando a mensagem do assessor for apenas uma saudação.
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

PROMPT_MENSAGEM = """\
Você transforma o material de um evento corporativo em UMA mensagem pronta para o assessor de investimento enviar ao cliente \
pelo WhatsApp. Escreva em português do Brasil, em texto simples, SEM emojis, SEM markdown (sem **, sem #, sem tabelas), \
com linhas curtas e títulos de seção em uma linha própria. Use SOMENTE o que está no material; nunca invente valores, datas ou regras.

Estrutura (omita a seção inteira quando o material não trouxer a informação):
1) Primeira linha: Empresa (TICKER) | tipo e situação do evento (ex.: Subscrição de ações aberta)
2) Uma linha com a posição do BTG Pactual Research, só se o material trouxer.
3) Prazos (para reservar ou manifestar): lista com "- ", cada prazo com data e horário, destacando a data mais restritiva.
4) O que está acontecendo?: 2 a 4 frases simples, para o cliente entender o contexto.
5) Os ativos ou direitos envolvidos, com código, fator e preço, em lista "- ", e um exemplo numérico se o material trouxer.
6) Quais são as opções?: opções numeradas (1), 2), 3)...), cada uma em uma linha com a consequência. Inclua "não fazer nada" e seu efeito, se constar.
7) Qualquer observação didática do material (ex.: sobre uma classe de ação) em uma ou duas frases.
8) Atenção à tela: confusões comuns de cotação ou de sistema, se houver.
9) Importante: lista "- " com restrições, riscos, irrevogabilidade, devolução de dinheiro etc.
Se o material trouxer o caminho no sistema para aprovar, inclua uma linha "Como aprovar: ...". Se trouxer um passo a passo do cliente, inclua uma seção "Passo a passo do cliente" com o caminho no aplicativo, fiel ao material.
Não recomende compra, venda ou subscrição por conta própria. Se o material trouxer uma 'Orientação', inclua-a em uma seção chamada Orientação, fiel ao material. Seja completo, mas enxuto (até ~330 palavras)."""


class RedatorGemini:
    def __init__(self, api_key: str, modelos: list[str]):
        from google import genai

        self._client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
        self._modelos = modelos

    def escolher_eventos(self, pergunta: str, candidatos: list[dict], maximo: int = 2) -> dict | None:
        """Índices dos comunicados que melhor respondem à pergunta (empate: o mais recente). None se a IA não respondeu."""
        from google.genai import errors, types

        lista = "\n".join(f"{i} | {e['data']} | {e['categoria']} | {e['assunto'][:160]}" for i, e in enumerate(candidatos))
        instrucao = (
            "Você escolhe quais comunicados de uma empresa devem ser abertos para responder à pergunta de um assessor. "
            "A lista está do mais recente para o mais antigo. Regras: se houver várias operações ou edições da mesma natureza "
            "(ex.: 5ª e 6ª operação), escolha SEMPRE a mais recente; prefira o comunicado que traz a informação pedida "
            "(ex.: 'período de manifestação' para prazos, 'resultado' para o que foi decidido). "
            'Se a pergunta cita a natureza do evento (subscrição, resgate, OPA, desdobramento, pendência, bonificação, etc.), '
            'ela É clara: escolha o comunicado MAIS RECENTE dessa natureza, mesmo que haja outros de natureza diferente na lista. '
            'Só responda {"claro": false, "candidatos": [até 4 índices]} quando a pergunta não traz nenhuma pista da natureza do evento '
            '(ex.: "qual o prazo?") e há eventos de natureza diferente. Caso contrário, responda '
            f'{{"claro": true, "ids": [até {maximo} índices]}}.'
        )
        config = types.GenerateContentConfig(system_instruction=instrucao, temperature=0, max_output_tokens=100, response_mime_type="application/json")
        for modelo in self._modelos:
            try:
                resp = self._client.models.generate_content(model=modelo, contents=f"PERGUNTA: {pergunta}\n\nCOMUNICADOS:\n{lista}", config=config)
                r = json.loads(resp.text)
                if r.get("claro") is False:
                    return {"claro": False, "candidatos": [int(i) for i in r.get("candidatos", [])]}
                return {"claro": True, "ids": [int(i) for i in r["ids"]]}
            except errors.APIError as e:
                log.warning("Seleção de documentos: falha no modelo %s (%s)", modelo, getattr(e, "code", "?"))
            except Exception:
                log.exception("Seleção de documentos: erro inesperado no modelo %s", modelo)
        return None

    def mensagem(self, fonte: str, empresa: str, assunto: str) -> str | None:
        """Mensagem de WhatsApp montada a partir de uma ficha ou resumo. None se nenhum modelo respondeu."""
        from google.genai import errors, types

        config = types.GenerateContentConfig(system_instruction=PROMPT_MENSAGEM, temperature=0.2, max_output_tokens=2048)
        conteudo = f"EMPRESA: {empresa}\nASSUNTO DO EVENTO: {assunto}\n\nMATERIAL:\n{fonte}"
        for modelo in self._modelos:
            try:
                resp = self._client.models.generate_content(model=modelo, contents=conteudo, config=config)
                if resp.text and resp.text.strip():
                    return resp.text.strip()
            except errors.APIError as e:
                log.warning("Mensagem: falha no modelo %s (%s)", modelo, getattr(e, "code", "?"))
            except Exception:
                log.exception("Mensagem: erro inesperado no modelo %s", modelo)
        return None

    def redigir(self, pergunta: str, historico: list[dict], dados: dict, documentos: list[dict] | None = None) -> str | None:
        """Texto da resposta, ou None se nenhum modelo respondeu (cota esgotada, erro...)."""
        from google.genai import errors, types

        conteudos = []
        for m in historico[-10:]:
            papel = "user" if m["role"] == "user" else "model"
            if not conteudos and papel == "model":
                continue  # a conversa enviada à IA precisa começar pelo usuário
            conteudos.append(types.Content(role=papel, parts=[types.Part.from_text(text=m["content"][:3000])]))
        texto = f"DADOS:\n{json.dumps(dados, ensure_ascii=False, indent=1)}\n\nPERGUNTA DO ASSESSOR: {pergunta}"
        partes = []
        for d in documentos or []:
            partes.append(types.Part.from_text(text=f"DOCUMENTO ANEXADO: {d['rotulo']}"))
            partes.append(
                types.Part.from_bytes(data=d["conteudo"], mime_type="application/pdf") if d["tipo"] == "pdf" else types.Part.from_text(text=d["conteudo"])
            )
        conteudos.append(types.Content(role="user", parts=[*partes, types.Part.from_text(text=texto)]))
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


class Detalhe(BaseModel):
    protocolo: str = Field(min_length=1, max_length=120)


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
        modelos = [m.strip() for m in os.getenv("GEMINI_MODELOS", "gemini-3.5-flash-lite,gemini-3.8-flash,gemini-flash-lite-latest,gemini-flash-latest").split(",") if m.strip()]
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

    cfg_grupos = grupos.Config()
    if cfg_grupos.ativo:
        classificador = None
        if os.getenv("GEMINI_API_KEY"):
            modelos_c = [m.strip() for m in os.getenv("GEMINI_MODELOS", "gemini-3.5-flash-lite,gemini-3.8-flash,gemini-flash-lite-latest,gemini-flash-latest").split(",") if m.strip()]
            classificador = grupos.ClassificadorGemini(os.environ["GEMINI_API_KEY"], modelos_c)
        robo = grupos.RoboDeGrupos(
            cfg_grupos, interpretador, redator, classificador, grupos.EnviadorEvolution(cfg_grupos), resposta_sem_ia, hoje=hoje,
        )
        app.include_router(grupos.criar_router(robo))
        log.info("Robô de grupos habilitado (%d grupo(s), modo %s)", len(cfg_grupos.permitidos), cfg_grupos.modo)

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

    cache_mensagens: dict = {}

    @app.post("/detalhar", dependencies=[Depends(exigir_login)])
    def detalhar(dados: Detalhe, request: Request):
        """Mensagem pronta para WhatsApp de um evento: usa a ficha da EQI (se houver) ou o resumo do comunicado."""
        if not limite_consulta.permitir(_ip(request)):
            raise HTTPException(429, "Muitas solicitações em sequência. Aguarde um minuto.")
        with eventos.conectar() as con:
            r = con.execute("SELECT empresa, assunto, resumo FROM fatos WHERE protocolo = ?", (dados.protocolo,)).fetchone()
        if not r:
            raise HTTPException(404, "Evento não encontrado.")
        empresa, assunto = (r["empresa"] or "").strip(), (r["assunto"] or "").strip()
        fichas = conhecimento.buscar_do_evento(empresa, assunto, hoje=hoje())
        if fichas:
            fonte, origem = fichas[0].texto, "ficha"
        elif r["resumo"]:
            fonte, origem = r["resumo"], "resumo"
        else:
            return {"mensagem": None, "origem": None}
        chave = (dados.protocolo, hash(fonte))
        if chave not in cache_mensagens:
            texto = redator.mensagem(fonte, empresa, assunto) if redator else None
            if not texto:
                return {"mensagem": fonte, "origem": origem, "sem_ia": True}  # não guarda: tenta de novo na próxima vez
            if len(cache_mensagens) >= 200:
                cache_mensagens.pop(next(iter(cache_mensagens)))
            cache_mensagens[chave] = texto
        return {"mensagem": cache_mensagens[chave], "origem": origem}

    @app.post("/consultar", dependencies=[Depends(exigir_login)])
    def consultar(dados: Consulta, request: Request):
        if not limite_consulta.permitir(_ip(request)):
            raise HTTPException(429, "Muitas perguntas em sequência. Aguarde um minuto.")

        historico = [m.model_dump() for m in dados.historico]
        with eventos.conectar() as con:
            interp = interpretador.interpretar(dados.pergunta, historico, hoje=hoje(), nomes_no_banco=eventos.empresas_conhecidas(con))
            res = atendimento.buscar_para_pergunta(con, interp)

        encontrados, ampliado = res.eventos, res.ampliado
        mostrados, extra = atendimento.contexto_eventos(
            res, interp, MAX_EVENTOS_RESPOSTA, 3200, ("empresa", "data", "assunto", "tipo_evento_rotulo", "categoria", "tipo", "tipo_ativo")
        )
        meta = {**interp.meta(), "total": len(encontrados), "periodo_ampliado": ampliado, "tipo_relaxado": res.tipo_relaxado}
        contexto = {
            "hoje": hoje().strftime("%d/%m/%Y"),
            "filtros_entendidos": meta,
            "total_encontrado": len(encontrados),
            "mostrando": len(mostrados),
            "periodo_ampliado": ampliado,
            **extra,
        }
        if interp.ticker:
            contexto["ticker_da_empresa"] = interp.ticker

        documentos = []
        fichas = conhecimento.buscar(dados.pergunta, historico, hoje=hoje())
        if fichas:
            contexto.update(conhecimento.contexto(fichas))
        if redator and not fichas and atendimento.pergunta_especifica(dados.pergunta):
            documentos, duvidas = atendimento.documentos_para_pergunta(redator, dados.pergunta, encontrados)
            if documentos or duvidas:
                contexto.update(atendimento.contexto_documentos(documentos, duvidas))
        if redator and documentos:
            texto = redator.redigir(dados.pergunta, historico, contexto, documentos=documentos)
        else:
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
