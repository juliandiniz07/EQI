"""Leitura dos comunicados: baixa o documento de cada evento e guarda um resumo no banco.

O assistente passa a responder com o conteúdo do comunicado (opções do investidor, prazos, fatores),
e não só com o título. A leitura roda uma vez por documento, logo após a coleta.

Uso manual (recupera eventos já coletados):
  python resumos.py              # últimos 60 dias, até 40 documentos
  python resumos.py --dias 120 --limite 100
  python resumos.py --empresa inter --dias 400   # só eventos de uma empresa, em qualquer época
  python resumos.py --empresa axia --dias 400 --refazer   # lê de novo e substitui os resumos já feitos
"""

import argparse
import logging
import os
import re
import time
from datetime import date, timedelta

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import eventos

log = logging.getLogger("resumos")

MAX_BYTES = 15 * 1024 * 1024
MAX_TENTATIVAS = 3
INTERVALO_SEG = float(os.getenv("RESUMO_INTERVALO_SEG", "7"))  # a cota gratuita do Gemini aceita poucas chamadas por minuto
ESPERA_429_SEG = 30
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

PROMPT = """\
Você lê comunicados oficiais de empresas listadas (CVM/B3) para assessores de investimento que precisam \
orientar clientes. Escreva em português do Brasil, SOMENTE com o que está no documento, neste formato exato \
(omita a seção inteira quando o documento não trouxer a informação):

**O que é:** uma ou duas frases.
**Opções do investidor:**
- cada alternativa em uma linha, com as condições (fator, razão, preço, tratamento tributário, se constarem)
**O que o cliente precisa fazer:**
- como e onde manifestar a opção (corretora, custodiante, canal indicado), documentos ou autorizações exigidos
- o que acontece se o cliente NÃO fizer nada (opção padrão ou consequência)
**Prazos e datas:**
- período de manifestação (início e fim, com horário), data-base, data de crédito, pagamento ou negociação
**Valores e números:** fatores, preços, quantidades, percentuais.
**Atenção:** restrições, riscos ou impactos tributários citados no documento.

Regras: seja completo e concreto (até cerca de 350 palavras); copie datas, horários, tickers e valores exatamente como \
estão no documento; nunca invente nem complete com conhecimento externo; sem opinião e sem recomendação de compra ou venda. \
Se o documento for apenas administrativo e não trouxer nenhuma informação útil, responda exatamente: SEM_CONTEUDO."""


def baixar(client: httpx.Client, link: str) -> tuple[str, bytes | str] | None:
    """('pdf', bytes) | ('texto', str) | None se não foi possível obter."""
    r = client.get(link)
    r.raise_for_status()
    corpo, tipo = r.content, r.headers.get("content-type", "")
    if len(corpo) > MAX_BYTES:
        log.warning("Documento grande demais (%d bytes): %s", len(corpo), link)
        return None
    if b"%PDF-" in corpo[:1024]:  # alguns PDFs vêm com bytes antes do cabeçalho
        return "pdf", corpo[corpo.index(b"%PDF-"):]
    if "html" in tipo or "text" in tipo or corpo[:1] == b"<":
        texto = re.sub(r"\s+", " ", BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True))
        if len(texto) > 200:
            return "texto", texto[:60000]
    log.warning("Documento sem conteúdo aproveitável (tipo=%s, %d bytes, início=%r) em %s", tipo, len(corpo), corpo[:120], link)
    return None


class ResumidorGemini:
    def __init__(self, api_key: str, modelos: list[str]):
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._modelos = modelos
        self._ultima = 0.0

    def _ritmo(self):
        falta = INTERVALO_SEG - (time.monotonic() - self._ultima)
        if falta > 0:
            time.sleep(falta)
        self._ultima = time.monotonic()

    def resumir(self, empresa: str, assunto: str, tipo: str, conteudo) -> str | None:
        from google.genai import errors, types

        parte = types.Part.from_bytes(data=conteudo, mime_type="application/pdf") if tipo == "pdf" else types.Part.from_text(text=conteudo)
        cabecalho = types.Part.from_text(text=f"Empresa: {empresa}\nAssunto: {assunto}")
        config = types.GenerateContentConfig(system_instruction=PROMPT, temperature=0.1, max_output_tokens=1800)
        for modelo in self._modelos:
            for tentativa in (1, 2):
                self._ritmo()
                try:
                    resp = self._client.models.generate_content(
                        model=modelo, contents=[types.Content(role="user", parts=[cabecalho, parte])], config=config
                    )
                    if resp.text and resp.text.strip():
                        return resp.text.strip()
                    break
                except errors.APIError as e:
                    codigo = getattr(e, "code", "?")
                    log.warning("Resumo: falha no modelo %s (%s)", modelo, codigo)
                    if codigo == 429 and tentativa == 1:
                        log.info("Limite de uso do Gemini: aguardando %ds...", ESPERA_429_SEG)
                        time.sleep(ESPERA_429_SEG)
                        continue
                    break
                except Exception:
                    log.exception("Resumo: erro inesperado no modelo %s", modelo)
                    break
        return None


def processar_pendentes(resumidor, dias: int = 60, limite: int = 40, empresa: str | None = None, refazer: bool = False, client: httpx.Client | None = None, baixar_fn=baixar) -> dict:
    """Resume os eventos recentes que ainda não têm resumo. Nunca levanta exceção para o chamador."""
    contagem = {"ok": 0, "sem_conteudo": 0, "erro": 0}
    corte = date.today() - timedelta(days=dias)
    client = client or httpx.Client(headers=HEADERS, timeout=45, follow_redirects=True)
    with eventos.conectar() as con:
        candidatos = []
        filtro_sql = (
            "link LIKE 'http%'" if refazer else
            "resumo IS NULL AND link LIKE 'http%' AND COALESCE(resumo_status,'') != 'sem_conteudo' AND COALESCE(resumo_tentativas,0) < ?"
        )
        for r in con.execute(
            f"SELECT protocolo, empresa, assunto, tipo, categoria, link, data_entrega FROM fatos WHERE {filtro_sql}",
            () if refazer else (MAX_TENTATIVAS,),
        ):
            q = eventos.parse_data(r["data_entrega"])
            if not q or q.date() < corte or not eventos.deve_resumir(r["assunto"], r["tipo"], r["categoria"]):
                continue
            if empresa and f" {eventos.termo(empresa)} " not in f" {eventos.termo(r['empresa'])} {eventos.termo(r['assunto'])} ":
                continue
            candidatos.append((q, r))
        candidatos.sort(key=lambda x: x[0], reverse=True)

        for _, r in candidatos[:limite]:
            status, resumo = "erro", None
            try:
                doc = baixar_fn(client, r["link"])
                if doc:
                    texto = resumidor.resumir(r["empresa"] or "", r["assunto"] or "", doc[0], doc[1])
                    if texto and texto.strip() == "SEM_CONTEUDO":
                        status = "sem_conteudo"
                    elif texto:
                        status, resumo = "ok", texto
            except Exception as e:
                log.warning("Resumo: falha em %s (%s): %s", r["empresa"], r["protocolo"], e)
            contagem["ok" if status == "ok" else "sem_conteudo" if status == "sem_conteudo" else "erro"] += 1
            if refazer and status == "erro":
                continue  # não apaga o resumo antigo se a nova leitura falhou
            con.execute(
                "UPDATE fatos SET resumo=COALESCE(?, resumo), resumo_status=?, resumo_tentativas=COALESCE(resumo_tentativas,0)+1 WHERE protocolo=?",
                (resumo, status, r["protocolo"]),
            )
            con.commit()
    log.info("Resumos: %s", contagem)
    return contagem


def resumidor_do_ambiente() -> ResumidorGemini | None:
    chave = os.getenv("GEMINI_API_KEY")
    if not chave:
        return None
    modelos = [m.strip() for m in os.getenv("GEMINI_MODELOS", "gemini-flash-lite-latest,gemini-flash-latest").split(",") if m.strip()]
    return ResumidorGemini(chave, modelos)


def executar(dias: int = 60, limite: int = 40, empresa: str | None = None, refazer: bool = False) -> dict | None:
    try:
        resumidor = resumidor_do_ambiente()
        if resumidor is None:
            log.warning("GEMINI_API_KEY não definida: documentos não serão lidos.")
            return None
        return processar_pendentes(resumidor, dias=dias, limite=limite, empresa=empresa, refazer=refazer)
    except Exception:
        log.exception("Erro na leitura de documentos")
        return None


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dias", type=int, default=60)
    p.add_argument("--limite", type=int, default=40)
    p.add_argument("--refazer", action="store_true", help="lê de novo e substitui os resumos já existentes")
    p.add_argument("--empresa", help="só eventos dessa empresa (ex.: inter, brava)")
    a = p.parse_args()
    executar(a.dias, a.limite, a.empresa, a.refazer)
