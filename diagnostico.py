"""Mostra o que o banco tem de uma empresa: eventos, tipo, e se o documento já foi resumido.

Uso:  python diagnostico.py axia
"""

import sys

from dotenv import load_dotenv

import eventos


def main():
    load_dotenv()
    if len(sys.argv) < 2:
        print("Uso: python diagnostico.py <empresa>")
        return
    alvo = eventos.termo(sys.argv[1])
    linhas = []
    with eventos.conectar() as con:
        for r in con.execute("SELECT * FROM fatos"):
            if f" {alvo} " in f" {eventos.termo(r['empresa'])} {eventos.termo(r['assunto'])} ":
                quando = eventos.parse_data(r["data_entrega"])
                linhas.append((quando, r))
    linhas.sort(key=lambda x: x[0] or eventos.datetime.min, reverse=True)
    print(f"{len(linhas)} evento(s) com '{sys.argv[1]}'\n")
    for quando, r in linhas:
        tipo = eventos.ROTULOS.get(eventos.classificar(r["assunto"], r["tipo"]) or "", "-")
        situacao = r["resumo_status"] or "pendente"
        print(f"{quando.strftime('%d/%m/%Y') if quando else '?':10} [{tipo}] resumo={situacao}")
        print(f"           {(r['assunto'] or '')[:110]}")
        if r["resumo"]:
            print(f"           >> {r['resumo'][:110].replace(chr(10), ' ')}...")


if __name__ == "__main__":
    main()
