"""Diagnóstico do download da CVM: python diag_cvm.py  -> gera diag_cvm.txt para enviar ao suporte."""
import sys
import httpx

LINK = sys.argv[1] if len(sys.argv) > 1 else "https://www.rad.cvm.gov.br/ENETWeb/frmDownloadDocumento.aspx?Tela=ext&numProtocolo=1588822&descTipo=IPE&CodigoInstituicao=1"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
saida = []


def mostrar(nome, r):
    saida.append(f"=== {nome}: {r.status_code} tipo={r.headers.get('content-type')} bytes={len(r.content)} url_final={r.url}")
    for h in ("location", "refresh", "content-disposition", "content-length"):
        if h in r.headers:
            saida.append(f"  {h}: {r.headers[h]}")
    saida.append(f"  cookies recebidos: {[c for c in r.cookies.keys()]}")
    saida.append(f"  historico: {[(x.status_code, str(x.url)) for x in r.history]}")
    saida.append("  inicio do corpo: " + repr(r.content[:200]))


with httpx.Client(headers=UA, timeout=60, follow_redirects=True) as c:
    for i in (1, 2, 3):
        try:
            r = c.get(LINK)
            mostrar(f"tentativa {i}", r)
            if r.content[:1] == b"<":
                open(f"cvm_{i}.html", "w", encoding="utf-8").write(r.text)
        except Exception as e:
            saida.append(f"tentativa {i}: ERRO {e}")
    for base in ("https://www.rad.cvm.gov.br/ENETWeb/", "https://www.rad.cvm.gov.br/ENET/"):
        try:
            mostrar("raiz " + base, c.get(base))
        except Exception as e:
            saida.append(f"raiz {base}: ERRO {e}")
open("diag_cvm.txt", "w", encoding="utf-8").write("\n".join(saida))
print("\n".join(saida))
