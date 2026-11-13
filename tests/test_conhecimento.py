from datetime import date

import conhecimento

FICHA = """---
empresa: Raízen
termos: raizen, raiz55, raiz16
validade: 2026-12-08
---
Preço de subscrição: R$ 0,25 por ação.
"""


def pasta(tmp_path, texto=FICHA):
    (tmp_path / "raizen.md").write_text(texto, encoding="utf-8")
    (tmp_path / "_modelo.md").write_text("---\nempresa: Modelo\ntermos: modelo\n---\nx", encoding="utf-8")
    return tmp_path


def test_acha_por_nome_e_por_ticker(tmp_path):
    p = pasta(tmp_path)
    assert conhecimento.buscar("Qual o preço da subscrição da Raízen?", hoje=date(2026, 11, 12), pasta=p)[0].empresa == "Raízen"
    assert conhecimento.buscar("e a RAIZ55?", hoje=date(2026, 11, 12), pasta=p)


def test_nao_acha_outra_empresa_nem_arquivo_modelo(tmp_path):
    p = pasta(tmp_path)
    assert conhecimento.buscar("prazo da Axia", hoje=date(2026, 11, 12), pasta=p) == []
    assert conhecimento.buscar("modelo", hoje=date(2026, 11, 12), pasta=p) == []


def test_ficha_vencida_e_ignorada(tmp_path):
    assert conhecimento.buscar("Raízen", hoje=date(2026, 12, 9), pasta=pasta(tmp_path)) == []


def test_seguimento_usa_a_pergunta_anterior(tmp_path):
    hist = [{"role": "user", "content": "subscrição de Raízen"}, {"role": "assistant", "content": "ok"}]
    assert conhecimento.buscar("e o prazo?", hist, hoje=date(2026, 11, 12), pasta=pasta(tmp_path))


def test_contexto_traz_instrucao_e_texto(tmp_path):
    f = conhecimento.buscar("raizen", hoje=date(2026, 11, 12), pasta=pasta(tmp_path))
    ctx = conhecimento.contexto(f)
    assert "R$ 0,25" in ctx["fichas_internas_eqi"][0] and "PRIORIDADE" in ctx["instrucao_fichas"]


def test_ficha_real_da_raizen_carrega():
    f = conhecimento.buscar("qual o prazo da subscrição de Raízen", hoje=date(2026, 11, 12))
    assert f and "08/12/2026" in f[0].texto
