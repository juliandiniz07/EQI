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


def test_ficha_so_vale_para_o_assunto_declarado(tmp_path):
    p = pasta(tmp_path, FICHA.replace("validade:", "assunto: aumento de capital, subscricao\nvalidade:"))
    hoje = date(2026, 11, 12)
    assert conhecimento.buscar_do_evento("RAIZEN", "Aumento de Capital Privado, Instituição do Programa de Units", hoje, p)
    assert conhecimento.buscar_do_evento("RAIZEN", "Venda de Participação na Logum Logística S.A.", hoje, p) == []


def test_ficha_real_da_raizen_nao_vale_para_logum():
    hoje = date(2026, 11, 12)
    assert conhecimento.buscar_do_evento("RAIZEN", "Aumento de Capital Privado, Instituição do Programa de Units", hoje)
    assert conhecimento.buscar_do_evento("RAIZEN", "Venda de Participação na Logum Logística S.A.", hoje) == []


def test_instrucao_cobre_recomendacao_e_orientacao():
    ins = conhecimento.INSTRUCAO_FICHAS.lower()
    for palavra in ("recomendação", "orientação", "devo vender", "vale a pena", "o que fazer"):
        assert palavra in ins


def test_ficha_vencida_e_achada_por_buscar_vencidas(tmp_path):
    p = pasta(tmp_path)
    depois = date(2026, 12, 9)  # a ficha vale até 2026-12-08
    assert conhecimento.buscar("Raízen", hoje=depois, pasta=p) == []
    vencidas = conhecimento.buscar_vencidas("Raízen", hoje=depois, pasta=p)
    assert len(vencidas) == 1 and vencidas[0].vencida(depois)
    # e o contrário: enquanto vale, não aparece como vencida
    assert conhecimento.buscar_vencidas("Raízen", hoje=date(2026, 11, 17), pasta=p) == []


def test_contexto_de_vencida_manda_dizer_que_acabou(tmp_path):
    depois = date(2026, 12, 9)
    ctx = conhecimento.contexto_vencidas(conhecimento.buscar_vencidas("Raízen", hoje=depois, pasta=pasta(tmp_path)), depois)
    assert "prazo encerrado em 08/12/2026" in ctx["evento_encerrado"][0]
    assert "o prazo acabou" in ctx["instrucao_resposta_direta"] and "NUNCA trate o evento como aberto" in ctx["instrucao_resposta_direta"]


def test_instrucao_proibe_aviso_generico_de_suitability():
    ins = conhecimento.INSTRUCAO_FICHAS
    assert "NÃO encerre a resposta com aviso genérico" in ins and "suitability" in ins
    assert "complete com o lembrete de suitability" not in ins


def test_busca_por_tema_acha_sem_citar_a_empresa(tmp_path):
    (tmp_path / "r.md").write_text(
        "---\nempresa: Raízen\ntermos: raizen, raiz55\nassunto: aumento de capital, subscricao\nvalidade: 2026-12-08\n---\nPreço: R$ 0,25.\n",
        encoding="utf-8")
    hoje = date(2026, 11, 19)
    achadas = conhecimento.buscar_por_tema("quais empresas estão com aumentos de capital?", hoje, tmp_path)
    assert [f.empresa for f in achadas] == ["Raízen"]  # plural também casa
    assert conhecimento.buscar_por_tema("qual o CDI hoje?", hoje, tmp_path) == []
    assert conhecimento.buscar_por_tema("bom dia pessoal", hoje, tmp_path) == []


def test_busca_por_tema_ignora_ficha_vencida(tmp_path):
    (tmp_path / "r.md").write_text(
        "---\nempresa: Raízen\ntermos: raizen\nassunto: aumento de capital\nvalidade: 2026-01-01\n---\nx\n", encoding="utf-8")
    assert conhecimento.buscar_por_tema("aumento de capital", date(2026, 11, 19), tmp_path) == []


def test_contexto_tema_manda_usar_como_principal():
    ins = conhecimento.INSTRUCAO_TEMA
    assert "parte principal da resposta" in ins and "NÃO diga que a resposta veio de ficha" in ins


def test_pergunta_nova_nao_herda_o_assunto_de_quem_falou_antes(tmp_path):
    """No grupo, um assessor pergunta de RAIZ4 e outro muda de assunto: a ficha não pode vazar."""
    p = pasta(tmp_path)
    hoje = date(2026, 11, 12)
    hist = [{"role": "user", "content": "tão sabendo de algo sobre RAIZ55?"}]
    assert conhecimento.buscar("quais empresas pagam dividendos em dezembro?", hist, hoje, p) == []
    assert conhecimento.buscar("e o prazo?", hist, hoje, p)  # continuação continua herdando
