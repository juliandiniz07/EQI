"""Robô nos grupos de WhatsApp (Evolution API).

Fluxo: Evolution API (evento MESSAGES_UPSERT) -> POST /evolution/webhook/<segredo> -> filtros -> classificador de relevância ->
busca no banco de eventos -> resposta no mesmo grupo.

Segurança e custo:
- só age em grupos da lista GRUPOS_PERMITIDOS (os demais são ignorados sem gravar texto);
- modo "sombra" (GRUPOS_MODO=sombra, padrão): decide e registra, mas NÃO envia nada;
- limite de respostas por grupo e por pessoa;
- pré-filtro por regra antes de gastar IA; se a IA cair, decide por regra.
"""

import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import date, timedelta

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

import atendimento
import conhecimento
import eventos
from consulta import Interpretador

log = logging.getLogger("grupos")

MAX_EVENTOS_GRUPO = 5
TEMA_VALE_SEG = 15 * 60  # por quanto tempo "e o prazo?" ainda se refere ao que o robô respondeu
DIAS_AMPLIACAO = 365

# Termos que indicam, só por regra, que a mensagem pode tratar de evento corporativo.
_RE_TEMA = re.compile(
    r"\bopas?\b|oferta publica|fechamento de capital|\bfus(?:ao|oes)\b|incorpora|aquisic|\bcis(?:ao|oes)\b"
    r"|recuperac|falenc|\brj\b|reestrutura|cancelamento|deslist|subscric|aumento de capital|direito de preferencia"
    r"|\bsobras?\b|bonificac|desdobr|grupament|\bsplit\b|\binplit\b|emiss(?:ao|oes)|follow ?on|\bbdr\b|resgate"
    r"|conversao|fato relevante|comunicado ao mercado|evento corporativo|liquidac|\bfiis?\b|oferta de cotas"
    r"|permuta|leilao|assembleia|consulta formal|incorporac|dilui|homologac"
)
_RE_TICKER = re.compile(r"\b[a-z]{4}\d{1,2}\b")
# Palavras de ação: sozinhas não dizem nada, mas junto de um ticker ou de uma empresa com ficha, sim.
_RE_ACAO = re.compile(
    r"orientac|recomendac|\bprazo\b|ader(?:ir|e|iu|imos)|\bades(?:ao|oes)\b|\bvot(?:ar|o|ou|acao)\b|manifestac"
    r"|o que fazer|o que (?:eu |a gente )?fa[czg]|como (?:faz|fazer|proced|participa)|participar|preciso fazer"
    r"|data limite|ate quando|vence|pendencia|preco medio|custo medio|o que (?:e|significa|aconteceu)"
    r"|faze?r\b|\bfaco\b|subscrev|troca[r]?\b|vender os direitos"
)

PROMPT_CLASSIFICADOR = """\
Você filtra mensagens de grupos de WhatsApp de assessores de investimento. Responda SOMENTE um JSON \
{"relevante": true|false}.
relevante=true quando a mensagem pede ou pergunta algo que um banco de EVENTOS CORPORATIVOS da CVM \
poderia responder: OPA, fusão/incorporação/aquisição, desdobramento/grupamento, recuperação judicial, \
subscrição/aumento de capital/direito de preferência, cancelamento de registro, BDR/resgate/conversão de \
ações, fatos relevantes e emissões de FIIs, ou "teve algo da empresa/ativo X?".
relevante=false para cotação/preço-alvo, análise ou recomendação de investimento, política, piadas, \
cumprimentos, assuntos operacionais, ou quando a mensagem é só uma resposta/comentário de alguém."""


class Config:
    def __init__(self):
        e = os.getenv
        self.url = e("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
        self.apikey = e("EVOLUTION_APIKEY", "")
        self.instancia = e("EVOLUTION_INSTANCE", "")
        self.segredo = e("GRUPOS_WEBHOOK_SEGREDO", "")
        self.permitidos = {_id_grupo(g) for g in e("GRUPOS_PERMITIDOS", "").split(",") if g.strip()}
        self.modo = e("GRUPOS_MODO", "sombra").strip().lower()  # sombra | ativo
        self.espera_seg = int(e("GRUPOS_ESPERA_SEG", "20"))  # espera antes de responder (0 = imediato)
        self.max_por_grupo = int(e("GRUPOS_MAX_POR_GRUPO_10MIN", "6"))
        self.max_por_pessoa = int(e("GRUPOS_MAX_POR_PESSOA_10MIN", "3"))

    @property
    def ativo(self) -> bool:
        return bool(self.segredo and self.permitidos)


def _id_grupo(valor: str | None) -> str:
    """Normaliza o ID do grupo: '1203...@g.us', '1203...-group' e '1203...' viram '1203...'."""
    v = (valor or "").strip()
    return re.sub(r"(-group|@g\.us)$", "", v)


_RE_PERGUNTA = re.compile(r"\?|^(?:como|quando|onde|qual|quais|quem|quanto|quantos|por ?que|pq|sera|tem|teve|alguem|da pra|e possivel)\b")


def _parece_pergunta(texto: str) -> bool:
    """Mensagem que pergunta algo — e portanto não é resposta à pergunta de outra pessoa."""
    return bool(_RE_PERGUNTA.search(eventos.termo(texto)))


def _limpar(texto: str) -> str:
    return " ".join((texto or "").split())


# ---------------------------------------------------------------------------
# Peças substituíveis (nos testes entram versões falsas)
# ---------------------------------------------------------------------------

class ClassificadorGemini:
    def __init__(self, api_key: str, modelos: list[str]):
        from google import genai

        self._client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
        self._modelos = modelos

    def relevante(self, texto: str, contexto: list[str]) -> bool | None:
        """True/False, ou None se nenhuma IA respondeu (quem chama decide por regra)."""
        from google.genai import errors, types

        entrada = ("Mensagens anteriores do grupo:\n" + "\n".join(contexto) + "\n\n" if contexto else "") + f"MENSAGEM: {texto}"
        config = types.GenerateContentConfig(
            system_instruction=PROMPT_CLASSIFICADOR, temperature=0, max_output_tokens=20,
            response_mime_type="application/json",
        )
        for modelo in self._modelos:
            try:
                resp = self._client.models.generate_content(model=modelo, contents=entrada, config=config)
                return bool(json.loads(resp.text)["relevante"])
            except errors.APIError as e:
                log.warning("Classificador: falha no modelo %s (%s)", modelo, getattr(e, "code", "?"))
            except Exception:
                log.exception("Classificador: erro inesperado no modelo %s", modelo)
        return None


class EnviadorEvolution:
    def __init__(self, cfg: Config):
        self._cfg = cfg

    def enviar(self, grupo: str, texto: str, citar: dict | None = None) -> None:
        c = self._cfg
        corpo = {"number": grupo, "text": texto, "delay": 2000}
        if citar:  # responde citando a pergunta, para ficar claro a quem o robô respondeu
            corpo["quoted"] = citar
        r = httpx.post(
            f"{c.url}/message/sendText/{c.instancia}",
            json=corpo, headers={"apikey": c.apikey}, timeout=30,
        )
        r.raise_for_status()


class Limites:
    """Janela deslizante simples por chave."""

    def __init__(self, janela_seg: int = 600):
        self._j = janela_seg
        self._f: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, chave: str, maximo: int) -> bool:
        with self._lock:
            f, agora = self._f[chave], time.monotonic()
            while f and agora - f[0] > self._j:
                f.popleft()
            if len(f) >= maximo:
                return False
            f.append(agora)
            return True


# ---------------------------------------------------------------------------
# Núcleo
# ---------------------------------------------------------------------------

class RoboDeGrupos:
    def __init__(self, cfg, interpretador, redator, classificador, enviador, resposta_sem_ia, hoje=date.today, dormir=time.sleep):
        self.cfg, self.interp, self.redator = cfg, interpretador, redator
        self.classificador, self.enviador, self.resposta_sem_ia = classificador, enviador, resposta_sem_ia
        self.hoje, self.dormir = hoje, dormir
        self.limites = Limites()
        self._vistos: deque = deque(maxlen=2000)
        self._recentes: dict[str, deque] = defaultdict(lambda: deque(maxlen=8))  # (quando, quem, texto)
        self._tema: dict[str, tuple[float, str]] = {}  # último assunto respondido por grupo: (quando, pergunta)
        self._lock = threading.Lock()
        self._criar_tabela()

    def _criar_tabela(self):
        with eventos.conectar() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS grupos_log (id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT DEFAULT CURRENT_TIMESTAMP,"
                " grupo TEXT, remetente TEXT, texto TEXT, decisao TEXT, camada TEXT, resposta TEXT, enviado INTEGER DEFAULT 0)"
            )

    def _log(self, grupo, remetente, texto, decisao, camada="", resposta="", enviado=0):
        with eventos.conectar() as con:
            con.execute(
                "INSERT INTO grupos_log (grupo, remetente, texto, decisao, camada, resposta, enviado) VALUES (?,?,?,?,?,?,?)",
                (grupo, remetente, texto, decisao, camada, resposta, enviado),
            )

    # -- entrada: devolve o que será processado (ou o motivo de ignorar) ---------------

    def receber(self, p: dict) -> tuple[str, dict | None]:
        d = p.get("data") or {}
        key = d.get("key") or {}
        jid = key.get("remoteJid") or ""
        if not jid.endswith("@g.us") or key.get("fromMe"):
            return "ignorado:nao_e_mensagem_de_grupo", None
        grupo = _id_grupo(jid)
        if grupo not in self.cfg.permitidos:
            return "ignorado:grupo_nao_autorizado", None  # nada é gravado de grupos fora da lista
        msg = d.get("message") or {}
        texto = _limpar(msg.get("conversation") or (msg.get("extendedTextMessage") or {}).get("text") or "")
        if not texto:
            return "ignorado:sem_texto", None
        mid = key.get("id")
        quem = (key.get("participant") or "?").split("@")[0]
        with self._lock:
            if mid and mid in self._vistos:
                return "ignorado:duplicada", None
            self._vistos.append(mid)
            anteriores = [f"{q}: {t}" for _, q, t in self._recentes[grupo]][-4:]
            proprias = [t for _, q, t in self._recentes[grupo] if q == quem][-2:]
            self._recentes[grupo].append((time.monotonic(), quem, texto))
        return "ok", {"destino": jid, "grupo": grupo, "quem": quem, "texto": texto,
                      "contexto": anteriores, "proprias": proprias, "mid": mid,
                      "citar": {"key": key, "message": msg}}

    # -- decisão e resposta -----------------------------------------------------------

    def _no_assunto(self, texto: str) -> tuple[bool, bool]:
        """(tem tema de evento, aponta para um ativo nosso) — o ativo é um ticker ou uma empresa com ficha."""
        norm = eventos.termo(texto)
        hoje = self.hoje()
        tem_ficha = bool(conhecimento.buscar(texto, hoje=hoje) or conhecimento.buscar_vencidas(texto, hoje=hoje))
        return bool(_RE_TEMA.search(norm)), bool(_RE_TICKER.search(norm)) or tem_ficha

    def _relevante(self, texto: str, contexto: list[str]) -> tuple[bool, str]:
        norm = eventos.termo(texto)
        tema, alvo = self._no_assunto(texto)
        # Seguimento: "mas quando cai na conta?" não cita nada, mas continua a pergunta anterior
        seguimento = bool(
            not (tema or alvo) and contexto and "?" in texto
            and any(any(self._no_assunto(c)) for c in contexto[-2:])
        )
        if len(norm) < 8 or not (tema or alvo or seguimento):
            return False, "prefiltro"
        if self.classificador:
            r = self.classificador.relevante(texto, contexto)
            if r is not None:
                return r, "ia"
        # IA indisponível: decide por regra, e aí o seguimento não basta (sem IA não dá para julgar o contexto).
        return bool(tema or (alvo and _RE_ACAO.search(norm))), "regra"

    def _ancora(self, grupo: str, proprias: list[str], contexto: list[str]) -> list[str]:
        """De que assunto a pergunta trata, quando ela não diz.

        Primeiro o que ESSA pessoa perguntou antes: num grupo, quem faz um seguimento costuma estar continuando
        a própria conversa. Na falta disso, o último tema que o robô respondeu ali. A ordem importa: a
        interpretação herda do último item da lista."""
        tema = self._tema.get(grupo)
        recente = [tema[1]] if tema and time.monotonic() - tema[0] < TEMA_VALE_SEG else []
        ancora = [t for t in recente if t not in proprias] + list(proprias)
        return ancora or contexto

    def _montar_resposta(self, texto: str, contexto: list[str], ancora: list[str] | None = None) -> tuple[str, list[dict], str]:
        # O histórico serve para a INTERPRETAÇÃO resolver referências ("e o prazo?"). Ele NÃO vai para a IA como
        # turno de conversa: num grupo, duas perguntas seguidas de pessoas diferentes faziam a IA responder a
        # anterior usando os dados da atual. Para a IA, a mensagem anterior entra em DADOS, rotulada.
        anteriores = list(ancora if ancora is not None else contexto)
        historico = [{"role": "user", "content": c} for c in anteriores]
        with eventos.conectar() as con:
            it = self.interp.interpretar(texto, historico, hoje=self.hoje(), nomes_no_banco=eventos.empresas_conhecidas(con))
            res = atendimento.buscar_para_pergunta(con, it)
        achados, ampliado = res.eventos, res.ampliado
        mostrados, extra = atendimento.contexto_eventos(res, it, MAX_EVENTOS_GRUPO, 1500, ("empresa", "data", "assunto", "tipo_evento_rotulo", "tipo_ativo"))
        dados = {
            "canal": "grupo de WhatsApp: sem markdown. Prefira a resposta completa à resposta curta: não corte datas, valores, condições nem o prazo que o assessor precisa. Até ~130 palavras e 3 parágrafos curtos.",
            "hoje": self.hoje().strftime("%d/%m/%Y"),
            "total_encontrado": len(achados), "mostrando": len(mostrados), "periodo_ampliado": ampliado,
            **extra,
        }
        if it.ticker:
            dados["ticker_da_empresa"] = it.ticker
        if anteriores:
            dados["mensagem_anterior_do_grupo"] = anteriores[-1][:500]
            dados["instrucao_mensagem_anterior"] = (
                "Serve APENAS para entender referências da pergunta atual ('e o prazo?', 'mas quando vence?'). "
                "NUNCA responda a ela: responda sempre a PERGUNTA DO ASSESSOR."
            )
        documentos = []
        fichas = conhecimento.buscar(texto, historico, hoje=self.hoje())
        tema = conhecimento.buscar_por_tema(texto, hoje=self.hoje()) if not fichas else []
        vencidas = conhecimento.buscar_vencidas(texto, historico, hoje=self.hoje()) if not (fichas or tema) else []
        if fichas:
            dados.update(conhecimento.contexto(fichas))
        elif tema:
            dados.update(conhecimento.contexto_tema(tema))
        elif vencidas:
            dados.update(conhecimento.contexto_vencidas(vencidas, self.hoje()))
        if self.redator and not (fichas or tema or vencidas) and atendimento.pergunta_especifica(texto):
            documentos, duvidas = atendimento.documentos_para_pergunta(self.redator, texto, achados)
            if documentos or duvidas:
                dados.update(atendimento.contexto_documentos(documentos, duvidas))
        if self.redator and documentos:
            resp = self.redator.redigir(texto, [], dados, documentos=documentos)
        else:
            resp = self.redator.redigir(texto, [], dados) if self.redator else None
        camada = "ia"
        if not resp:
            resp, camada = self.resposta_sem_ia(it, mostrados, len(achados), ampliado), "regra"
            if vencidas and not mostrados:
                f = vencidas[0]
                resp = (f"O prazo de {f.empresa} se encerrou em {f.validade.strftime('%d/%m/%Y')}. "
                        "Tenho o material aqui, mas não consegui redigir os detalhes agora.")
                camada = "regra/vencida-sem-ia"
            elif fichas and not mostrados:  # há material interno; seria errado dizer que não encontramos nada
                resp = (f"Tenho o material de {fichas[0].empresa} aqui, mas não consegui redigir a resposta agora. "
                        "Tente de novo em alguns instantes ou consulte o assistente no navegador.")
                camada = "regra/ficha-sem-ia"
        return resp, mostrados, camada

    def processar(self, m: dict) -> str:
        grupo, quem, texto = m["grupo"], m["quem"], m["texto"]
        relevante, camada = self._relevante(texto, m["contexto"])
        if not relevante:
            self._log(grupo, quem, texto, f"ignorado:{camada}", camada)
            return "ignorado"
        if not self.limites.permitir(f"g:{grupo}", self.cfg.max_por_grupo) or not self.limites.permitir(f"p:{quem}", self.cfg.max_por_pessoa):
            self._log(grupo, quem, texto, "ignorado:limite", camada)
            return "limite"
        if self.cfg.espera_seg and self.cfg.modo == "ativo":
            self.dormir(self.cfg.espera_seg)
            with self._lock:
                desde = time.monotonic() - self.cfg.espera_seg - 1
                # Só conta como resposta o que NÃO é pergunta: uma pergunta nova de um colega é outra conversa,
                # e não motivo para deixar quem perguntou sem resposta.
                alguem_respondeu = any(
                    q != quem and not _parece_pergunta(t) for tempo, q, t in self._recentes[grupo] if tempo > desde
                )
            if alguem_respondeu:  # um colega já respondeu: o robô não atropela
                self._log(grupo, quem, texto, "ignorado:humano_respondeu", camada)
                return "humano"
        ancora = self._ancora(grupo, m.get("proprias") or [], m["contexto"])
        resposta, mostrados, camada_resp = self._montar_resposta(texto, m["contexto"], ancora)
        with self._lock:
            self._tema[grupo] = (time.monotonic(), texto)
        enviado = 0
        if self.cfg.modo == "ativo":
            try:
                self.enviador.enviar(m["destino"], resposta, m.get("citar"))
                enviado = 1
            except Exception:
                log.exception("Falha ao enviar resposta ao grupo")
        self._log(grupo, quem, texto, "respondido" if enviado else "sombra", f"{camada}/{camada_resp}", resposta, enviado)
        return "enviado" if enviado else "sombra"


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

def criar_router(robo: RoboDeGrupos) -> APIRouter:
    router = APIRouter()

    @router.post("/evolution/webhook/{segredo}")
    async def webhook(segredo: str, request: Request, fundo: BackgroundTasks):
        if not robo.cfg.segredo or segredo != robo.cfg.segredo:
            raise HTTPException(404)
        try:
            payload = await request.json()
        except Exception:
            return {"ok": True}
        motivo, msg = robo.receber(payload)
        if msg:
            fundo.add_task(robo.processar, msg)  # responde 200 à Evolution na hora; o resto roda depois
        return {"ok": True, "status": motivo}

    return router
