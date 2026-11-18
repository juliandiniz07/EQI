"""Simula o robô nos grupos sem WhatsApp: você cola a pergunta e vê o que ele responderia.

    python grupos_simulador.py                      # conversa: digite perguntas, uma por vez
    python grupos_simulador.py --arquivo perguntas.txt   # uma pergunta por linha do arquivo

Usa exatamente o mesmo caminho do robô de verdade: a mesma triagem (responde ou fica quieto),
as mesmas fichas e os mesmos eventos da CVM. Nada é enviado a lugar nenhum.
"""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PASTA = Path(__file__).resolve().parent
load_dotenv(PASTA / ".env")
logging.basicConfig(level=os.getenv("LOG_NIVEL", "WARNING"), format="%(levelname)s %(name)s: %(message)s")

import api  # noqa: E402
import conhecimento  # noqa: E402  (depois do load_dotenv, para a chave do Gemini já estar no ambiente)
import grupos  # noqa: E402
from consulta import Interpretador  # noqa: E402

LARGURA = 78


class SemEnvio:
    """Entra no lugar do enviador de verdade: garante que nada sai daqui."""

    def enviar(self, grupo: str, texto: str) -> None:
        raise AssertionError("o simulador nunca envia mensagens")


def montar_robo() -> grupos.RoboDeGrupos:
    cfg = grupos.Config()
    cfg.modo = "sombra"  # trava: o simulador nunca envia
    chave = os.getenv("GEMINI_API_KEY")
    modelos = [m.strip() for m in os.getenv(
        "GEMINI_MODELOS", "gemini-3.5-flash-lite,gemini-3.8-flash,gemini-flash-lite-latest,gemini-flash-latest"
    ).split(",") if m.strip()]
    redator = api.RedatorGemini(chave, modelos) if chave else None
    classificador = grupos.ClassificadorGemini(chave, modelos) if chave else None
    if not chave:
        print("AVISO: GEMINI_API_KEY não está no .env. As respostas sairão sem IA, bem mais pobres.\n")
    return grupos.RoboDeGrupos(
        cfg, Interpretador(), redator, classificador, SemEnvio(), api.resposta_sem_ia, hoje=date.today,
    )


def responder(robo: grupos.RoboDeGrupos, pergunta: str, contexto: list[str]) -> None:
    print("-" * LARGURA)
    print(f"PERGUNTA NO GRUPO: {pergunta}")
    relevante, camada = robo._relevante(pergunta, contexto)
    if not relevante:
        print(f"DECISÃO: ficaria quieto (não é assunto dele) [{camada}]")
        return
    resposta, mostrados, camada_resp = robo._montar_resposta(pergunta, contexto)
    print(f"DECISÃO: responderia [{camada}/{camada_resp}]")
    print("RESPOSTA:")
    for linha in resposta.splitlines():
        print("   " + linha)
    fichas = conhecimento.buscar(pergunta, [{"role": "user", "content": c} for c in contexto], hoje=robo.hoje())
    print("FONTE:")
    for f in fichas:
        print(f"   ficha interna: {f.arquivo} ({f.empresa})")
    for e in mostrados[:5]:
        print(f"   evento CVM: {e.get('data', '')} {e.get('empresa', '')} — {str(e.get('assunto', ''))[:60]}")
    if not fichas and not mostrados:
        print("   (nenhuma: confira se a resposta não afirma nada que não tenha de onde sair)")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Simula o robô nos grupos, sem WhatsApp")
    p.add_argument("--arquivo", help="arquivo de texto com uma pergunta por linha")
    a = p.parse_args()

    robo = montar_robo()
    contexto: list[str] = []

    if a.arquivo:
        caminho = Path(a.arquivo)
        if not caminho.is_file():
            print(f"Arquivo não encontrado: {caminho.resolve()}")
            sys.exit(1)
        perguntas = [l.strip() for l in caminho.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        for pergunta in perguntas:
            responder(robo, pergunta, [])  # cada linha é uma pergunta solta, sem conversa anterior
        print("-" * LARGURA)
        print(f"{len(perguntas)} pergunta(s) simulada(s).")
        return

    print("=" * LARGURA)
    print("Simulador do robô de grupos. Escreva uma pergunta como ela chegaria no WhatsApp.")
    print("As perguntas seguintes continuam a mesma conversa. Digite 'limpar' para recomeçar,")
    print("ou 'sair' para encerrar.")
    print("=" * LARGURA)
    while True:
        try:
            pergunta = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not pergunta:
            continue
        if pergunta.lower() in {"sair", "exit", "quit"}:
            return
        if pergunta.lower() == "limpar":
            contexto = []
            print("Conversa reiniciada.")
            continue
        responder(robo, pergunta, contexto)
        contexto = (contexto + [pergunta])[-4:]


if __name__ == "__main__":
    main()
