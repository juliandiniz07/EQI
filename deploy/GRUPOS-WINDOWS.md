# Teste em modo sombra no seu computador (Windows)

O objetivo deste teste é ver **o que o robô responderia** nos grupos, sem que ele envie nada.
Nada chega ao grupo. Tudo fica gravado num log que você lê quando quiser.

Para o teste de verdade (o robô respondendo no grupo), veja `GRUPOS.md`, que usa um servidor sempre ligado.

---

## Antes de começar

1. **Docker Desktop instalado e aberto.** Confira com `docker --version`.
   Se não tiver, baixe em https://www.docker.com/products/docker-desktop/ (pode exigir permissão de administrador).
2. **Um chip de WhatsApp dedicado ao robô.** Não use o seu número pessoal: o robô vira um "dispositivo conectado" dessa conta.
3. **O projeto já funcionando**, ou seja, o `python api.py` abrindo em http://localhost:8001.

Todos os comandos abaixo rodam na pasta do projeto (`cd` até a pasta EQI).
Se no seu computador o Python se chama `py`, troque `python` por `py`.

---

## 1. Subir a Evolution API

```
cd deploy
copy .env.evolution.example .env.evolution
notepad .env.evolution
```

Preencha as duas senhas (o próprio arquivo diz como gerar) e salve. Depois:

```
docker compose -f evolution-compose.yml --env-file .env.evolution up -d
cd ..
```

A primeira vez demora alguns minutos, porque o Docker baixa os programas.
Para conferir se subiu: `docker ps` deve listar três linhas (evolution, postgres, redis).

## 2. Preencher o .env do projeto

```
notepad .env
```

Preencha assim e salve:

```
EVOLUTION_URL=http://localhost:8080
EVOLUTION_APIKEY=  (a mesma chave do .env.evolution)
EVOLUTION_INSTANCE=eqi
GRUPOS_WEBHOOK_SEGREDO=  (gere um com o comando abaixo)
GRUPOS_PERMITIDOS=  (fica vazio por enquanto; preenchemos no passo 4)
GRUPOS_MODO=sombra
```

Para gerar o segredo:

```
python -c "import secrets; print(secrets.token_urlsafe(24))"
```

> `GRUPOS_MODO=sombra` é o que garante que nada é enviado. Só mude isso quando decidir ligar de verdade.

## 3. Conectar o WhatsApp do robô

```
python grupos_setup.py criar
```

Isso cria o arquivo `qrcode.png` na pasta. Abra o arquivo e, **no celular do chip do robô**, leia o código em
WhatsApp > Configurações > Dispositivos conectados > Conectar dispositivo.

O código expira em cerca de 40 segundos. Se expirar, rode `python grupos_setup.py qr` para gerar outro.

Confira a conexão:

```
python grupos_setup.py status
```

Deve dizer **CONECTADO ao WhatsApp**.

## 4. Autorizar os grupos

Adicione o número do robô a **um ou dois grupos** e rode:

```
python grupos_setup.py grupos
```

Ele lista os grupos com os respectivos IDs e já monta a linha pronta. Copie para o `.env`, em `GRUPOS_PERMITIDOS`,
separando por vírgula se for mais de um. O robô ignora qualquer grupo fora dessa lista.

## 5. Ligar o caminho entre a Evolution e a API

Abra **duas janelas** de terminal, as duas na pasta do projeto.

Na primeira, deixe a API rodando:

```
python api.py
```

Procure no texto que aparece a linha **"Robô de grupos habilitado (N grupo(s), modo sombra)"**.
Se ela não aparecer, o `.env` está incompleto: faltou o segredo ou o `GRUPOS_PERMITIDOS`.

Na segunda janela:

```
python grupos_setup.py webhook
python grupos_setup.py teste
```

O `teste` deve dizer que a API respondeu certo. Se disser 404, reinicie o `python api.py`, porque o `.env` mudou depois dele ter começado.

## 6. Ver o que o robô faria

Escreva uma pergunta no grupo, por exemplo "qual o prazo para trocar RAPT3?", e depois rode:

```
python grupos_log.py
```

Para cada mensagem, o log mostra a pergunta, a decisão (respondeu ou ignorou, e por quê) e, quando for o caso,
**o texto que teria sido enviado**. Outras opções:

```
python grupos_log.py --limite 50     # mais mensagens
python grupos_log.py --respondidas   # só as que gerariam resposta
python grupos_log.py --resumo        # contagem por decisão
```

Deixe rodando alguns dias e leia o log de vez em quando. O que avaliar:
- ele respondeu o que deveria ter respondido?
- ele ficou quieto nas conversas que não são do assunto dele?
- alguma resposta está errada ou desatualizada? Se sim, o ajuste costuma ser na ficha, não no código.

---

## Problemas comuns

**"Não foi possível falar com a Evolution"** — o Docker Desktop não está aberto, ou os contêineres caíram.
Confira com `docker ps` e suba de novo com o comando do passo 1.

**A Evolution recusou a chave (401)** — o `EVOLUTION_APIKEY` do `.env` do projeto está diferente do `.env.evolution`.

**O WhatsApp desconectou sozinho** — normal quando o computador dorme ou troca de rede.
Rode `python grupos_setup.py qr` e leia o código de novo. É exatamente esse incômodo que o servidor resolve.

**Nada aparece no `grupos_log.py`** — o robô só grava mensagens de grupos que estão no `GRUPOS_PERMITIDOS`.
Confira a lista e se o `python api.py` foi reiniciado depois da última mudança no `.env`.

**Desligar tudo no fim do dia** — feche a janela do `python api.py` e rode, na pasta `deploy`:
`docker compose -f evolution-compose.yml --env-file .env.evolution stop`
(use `start` no lugar de `stop` para voltar; nada é perdido, inclusive a conexão do WhatsApp).
