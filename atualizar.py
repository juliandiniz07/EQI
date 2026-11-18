"""Traz as novidades do projeto (fichas novas e correções) e diz se é preciso reiniciar a API.

    python atualizar.py          (ou clique duas vezes em atualizar.bat)

As fichas da pasta `conhecimento` são lidas a cada pergunta, então ficha nova vale na hora.
Mudança em código ou na tela só entra quando o `api.py` é reiniciado.
"""

import subprocess
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
RAMO = "claude/ai-agent-corporate-events-4tp0xt"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=PASTA, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if git("rev-parse", "--git-dir").returncode:
        print("Esta pasta não é o projeto (não encontrei o controle de versão). Rode de dentro da pasta EQI.")
        sys.exit(1)

    antes = git("rev-parse", "HEAD").stdout.strip()
    print("Buscando novidades...\n")
    r = git("pull", "--no-edit", "origin", RAMO)
    saida = (r.stdout + r.stderr).strip()
    if r.returncode:
        print(saida)
        print("\nNão consegui atualizar. Causas comuns:")
        print("  - sem internet ou GitHub fora do ar;")
        print("  - arquivos alterados à mão nesta pasta. Nesse caso rode:  git stash")
        print("    e depois rode este atualizador de novo.")
        sys.exit(1)

    depois = git("rev-parse", "HEAD").stdout.strip()
    if antes == depois:
        print("Já estava atualizado. Nada mudou.")
        return

    mudados = [l.strip() for l in git("diff", "--name-only", antes, depois).stdout.splitlines() if l.strip()]
    fichas = [m for m in mudados if m.startswith("conhecimento/") and m.endswith(".md")]
    codigo = [m for m in mudados if m.endswith((".py", ".html", ".json", ".txt"))]

    if fichas:
        print("Fichas atualizadas (já valendo, sem precisar reiniciar):")
        for f in fichas:
            print("   " + Path(f).name)
        print()
    outros = [m for m in mudados if m not in fichas]
    if outros:
        print(f"Outros arquivos atualizados: {len(outros)}")
        for m in outros[:8]:
            print("   " + m)
        if len(outros) > 8:
            print(f"   e mais {len(outros) - 8}")
        print()

    if codigo:
        print("ATENÇÃO: houve mudança de código. Reinicie o assistente para valer:")
        print("   na janela onde o api.py roda, aperte Ctrl+C e rode de novo:  py api.py")
    else:
        print("Pronto. Nada a reiniciar.")


if __name__ == "__main__":
    main()
