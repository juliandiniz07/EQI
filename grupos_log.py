"""Mostra o que o robô fez (ou faria) nos grupos de WhatsApp.

    python grupos_log.py                 # as 20 últimas mensagens analisadas
    python grupos_log.py --limite 50     # mais mensagens
    python grupos_log.py --respondidas   # só as que geraram resposta
    python grupos_log.py --resumo        # contagem por decisão

Em modo sombra, a coluna "decisão" mostra `sombra` e a resposta é o texto que teria sido enviado.
"""

import argparse
import sys

import eventos

DECISOES = {
    "sombra": "RESPONDERIA (modo sombra: nada foi enviado)",
    "respondido": "RESPONDEU no grupo",
    "ignorado:limite": "ignorou (limite de respostas atingido)",
    "ignorado:humano_respondeu": "ignorou (um colega respondeu antes)",
}


def _rotulo(decisao: str) -> str:
    if decisao in DECISOES:
        return DECISOES[decisao]
    if decisao.startswith("ignorado:"):
        return f"ignorou ({decisao.split(':', 1)[1]})"
    return decisao


def linhas(limite: int, so_respondidas: bool) -> list:
    sql = "SELECT quando, grupo, remetente, texto, decisao, camada, resposta, enviado FROM grupos_log"
    if so_respondidas:
        sql += " WHERE decisao IN ('sombra', 'respondido')"
    sql += " ORDER BY id DESC LIMIT ?"
    with eventos.conectar() as con:
        return list(con.execute(sql, (limite,)))


def resumo() -> list:
    with eventos.conectar() as con:
        return list(con.execute("SELECT decisao, COUNT(*) FROM grupos_log GROUP BY decisao ORDER BY 2 DESC"))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):  # acentos no terminal do Windows
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Log do robô nos grupos de WhatsApp")
    p.add_argument("--limite", type=int, default=20)
    p.add_argument("--respondidas", action="store_true", help="só as mensagens que geraram resposta")
    p.add_argument("--resumo", action="store_true", help="contagem por decisão")
    a = p.parse_args()

    if a.resumo:
        total = 0
        for decisao, n in resumo():
            print(f"{n:5d}  {_rotulo(decisao)}")
            total += n
        print(f"{total:5d}  TOTAL")
        return

    registros = linhas(a.limite, a.respondidas)
    if not registros:
        print("Nada registrado ainda. O robô só grava quando chega mensagem de um grupo autorizado.")
        return
    for r in reversed(registros):  # mais antigas primeiro, como numa conversa
        print("=" * 78)
        print(f"{r['quando']}  |  grupo {r['grupo']}  |  de {r['remetente']}")
        print(f"PERGUNTA: {r['texto']}")
        print(f"DECISÃO:  {_rotulo(r['decisao'])}" + (f"  [{r['camada']}]" if r["camada"] else ""))
        if r["resposta"]:
            print("RESPOSTA:")
            for linha in r["resposta"].splitlines():
                print("   " + linha)
    print("=" * 78)
    print(f"{len(registros)} mensagem(ns). Use --limite N para ver mais, --resumo para a contagem.")


if __name__ == "__main__":
    main()
