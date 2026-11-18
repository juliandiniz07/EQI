"""Ajudante para ligar o robô nos grupos de WhatsApp (Evolution API), sem precisar de curl.

Ordem de uso, com a Evolution já rodando:

    python grupos_setup.py criar       # cria a instância e salva o QR Code em qrcode.png
    python grupos_setup.py status      # diz se o WhatsApp já conectou
    python grupos_setup.py grupos      # lista os grupos e os IDs para o GRUPOS_PERMITIDOS
    python grupos_setup.py webhook     # aponta a Evolution para a API local
    python grupos_setup.py teste       # confere se a API responde no endereço do webhook

Lê EVOLUTION_URL, EVOLUTION_APIKEY, EVOLUTION_INSTANCE e GRUPOS_WEBHOOK_SEGREDO do .env.
"""

import argparse
import base64
import os
import secrets
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

PASTA = Path(__file__).resolve().parent
ARQ_QR = PASTA / "qrcode.png"


class Evolution:
    def __init__(self):
        load_dotenv(PASTA / ".env")
        self.url = os.getenv("EVOLUTION_URL", "http://localhost:8080").rstrip("/")
        self.apikey = os.getenv("EVOLUTION_APIKEY", "").strip()
        self.instancia = os.getenv("EVOLUTION_INSTANCE", "").strip() or "eqi"
        self.segredo = os.getenv("GRUPOS_WEBHOOK_SEGREDO", "").strip()
        if not self.apikey:
            sair("Falta EVOLUTION_APIKEY no .env. Use a mesma chave com que a Evolution foi iniciada.")

    def chamar(self, metodo: str, caminho: str, corpo: dict | None = None) -> dict:
        try:
            r = httpx.request(
                metodo, f"{self.url}{caminho}", json=corpo,
                headers={"apikey": self.apikey, "Content-Type": "application/json"}, timeout=60,
            )
        except httpx.HTTPError as e:
            sair(f"Não foi possível falar com a Evolution em {self.url}.\n"
                 f"Confira se ela está rodando (no Docker) e se o EVOLUTION_URL está certo.\nDetalhe: {e}")
        if r.status_code == 401:
            sair("A Evolution recusou a chave (401). O EVOLUTION_APIKEY do .env precisa ser o mesmo da Evolution.")
        if r.status_code >= 400:
            sair(f"A Evolution respondeu {r.status_code}:\n{r.text[:800]}")
        try:
            return r.json()
        except ValueError:
            return {}


def sair(msg: str) -> None:
    print("\n" + msg + "\n")
    sys.exit(1)


def _salvar_qr(dados: dict) -> bool:
    """Guarda o QR Code como imagem. Devolve False se a resposta não trouxe QR (normalmente já está conectado)."""
    qr = dados.get("qrcode") or dados.get("qr") or {}
    b64 = (qr.get("base64") or "").split(",")[-1] if isinstance(qr, dict) else ""
    if not b64:
        return False
    ARQ_QR.write_bytes(base64.b64decode(b64))
    print(f"QR Code salvo em: {ARQ_QR}")
    print("Abra esse arquivo e leia o código no WhatsApp do chip do robô:")
    print("  WhatsApp > Configurações > Dispositivos conectados > Conectar dispositivo")
    print("O código expira em cerca de 40 segundos. Se expirar, rode de novo: python grupos_setup.py qr")
    return True


def cmd_criar(e: Evolution) -> None:
    d = e.chamar("POST", "/instance/create", {
        "instanceName": e.instancia, "qrcode": True, "integration": "WHATSAPP-BAILEYS",
    })
    print(f"Instância '{e.instancia}' criada.")
    if not _salvar_qr(d):
        print("A Evolution não devolveu QR Code agora. Rode: python grupos_setup.py qr")


def cmd_qr(e: Evolution) -> None:
    if not _salvar_qr(e.chamar("GET", f"/instance/connect/{e.instancia}")):
        print("Sem QR Code na resposta. Rode 'python grupos_setup.py status': pode já estar conectado.")


def cmd_status(e: Evolution) -> None:
    d = e.chamar("GET", f"/instance/connectionState/{e.instancia}")
    estado = ((d.get("instance") or {}).get("state")) or d.get("state") or "desconhecido"
    legenda = {"open": "CONECTADO ao WhatsApp", "connecting": "conectando (leia o QR Code)",
               "close": "DESCONECTADO (rode: python grupos_setup.py qr)"}
    print(f"Instância '{e.instancia}': {legenda.get(estado, estado)}")


def cmd_grupos(e: Evolution) -> None:
    d = e.chamar("GET", f"/group/fetchAllGroups/{e.instancia}?getParticipants=false")
    grupos = d if isinstance(d, list) else (d.get("groups") or [])
    if not grupos:
        sair("Nenhum grupo encontrado. Adicione o número do robô a um grupo e rode de novo.")
    print(f"\n{len(grupos)} grupo(s) em que o robô está:\n")
    ids = []
    for g in grupos:
        jid = g.get("id") or ""
        print(f"  {g.get('subject') or '(sem nome)'}\n    {jid}")
        ids.append(jid)
    print("\nPara autorizar UM grupo, copie a linha abaixo com o ID dele para o .env:")
    print(f"\n  GRUPOS_PERMITIDOS={ids[0]}\n")
    print("Para mais de um, separe por vírgula. O robô ignora qualquer grupo fora dessa lista.")


def cmd_webhook(e: Evolution) -> None:
    if not e.segredo:
        sair("Falta GRUPOS_WEBHOOK_SEGREDO no .env.\n"
             f"Use este valor, que acabei de sortear:\n\n  GRUPOS_WEBHOOK_SEGREDO={secrets.token_urlsafe(24)}\n")
    porta = os.getenv("PORT", "8001")
    # host.docker.internal: de dentro do contêiner, é como a Evolution enxerga este computador
    destino = os.getenv("GRUPOS_WEBHOOK_URL", f"http://host.docker.internal:{porta}/evolution/webhook/{e.segredo}")
    e.chamar("POST", f"/webhook/set/{e.instancia}", {"webhook": {
        "enabled": True, "url": destino, "webhookByEvents": False, "events": ["MESSAGES_UPSERT"],
    }})
    print("Webhook configurado. A Evolution vai avisar a API a cada mensagem nova dos grupos.")
    print(f"Destino: {destino.replace(e.segredo, '***')}")


def cmd_teste(e: Evolution) -> None:
    porta = os.getenv("PORT", "8001")
    alvo = f"http://127.0.0.1:{porta}/evolution/webhook/{e.segredo or 'sem-segredo'}"
    try:
        r = httpx.post(alvo, json={"event": "teste"}, timeout=15)
    except httpx.HTTPError:
        sair(f"A API não respondeu em http://127.0.0.1:{porta}. Rode 'python api.py' numa outra janela e tente de novo.")
    if r.status_code == 404:
        sair("A API respondeu 404: o segredo do .env não confere com o que o robô carregou.\n"
             "Reinicie o 'python api.py' depois de editar o .env.")
    r.raise_for_status()
    print("A API respondeu certo no endereço do webhook. O caminho está aberto.")
    print("Agora escreva uma pergunta no grupo e rode: python grupos_log.py")


COMANDOS = {"criar": cmd_criar, "qr": cmd_qr, "status": cmd_status,
            "grupos": cmd_grupos, "webhook": cmd_webhook, "teste": cmd_teste}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Liga o robô nos grupos de WhatsApp (Evolution API)")
    p.add_argument("comando", choices=list(COMANDOS))
    COMANDOS[p.parse_args().comando](Evolution())


if __name__ == "__main__":
    main()
