import grupos_log


def test_rotulo_traduz_decisoes():
    assert "RESPONDERIA" in grupos_log._rotulo("sombra")
    assert "RESPONDEU" in grupos_log._rotulo("respondido")
    assert grupos_log._rotulo("ignorado:regra") == "ignorou (regra)"
    assert grupos_log._rotulo("ignorado:limite") == "ignorou (limite de respostas atingido)"
    assert grupos_log._rotulo("qualquer") == "qualquer"


def test_le_o_log_e_resume(banco, capsys):
    import eventos

    with eventos.conectar() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS grupos_log (id INTEGER PRIMARY KEY AUTOINCREMENT, quando TEXT DEFAULT CURRENT_TIMESTAMP,"
            " grupo TEXT, remetente TEXT, texto TEXT, decisao TEXT, camada TEXT, resposta TEXT, enviado INTEGER DEFAULT 0)"
        )
        con.executemany(
            "INSERT INTO grupos_log (grupo, remetente, texto, decisao, camada, resposta, enviado) VALUES (?,?,?,?,?,?,?)",
            [("g1", "551199", "prazo da Randoncorp?", "sombra", "ficha", "Até 16/12/2026.", 0),
             ("g1", "551188", "bom dia", "ignorado:regra", "regra", "", 0)],
        )
    assert len(grupos_log.linhas(10, so_respondidas=False)) == 2
    so_resp = grupos_log.linhas(10, so_respondidas=True)
    assert len(so_resp) == 1 and so_resp[0]["texto"] == "prazo da Randoncorp?"
    assert dict(map(tuple, grupos_log.resumo())) == {"sombra": 1, "ignorado:regra": 1}
