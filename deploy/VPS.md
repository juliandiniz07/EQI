# Subir o assistente numa VPS

Resultado no fim: o site funcionando num endereço `https://`, a coleta da CVM rodando sozinha todo dia,
e o robô conectado ao WhatsApp em modo sombra. Tudo ligado 24 horas, sem depender do seu computador.

Tempo: cerca de 40 minutos, quase todos esperando o servidor baixar as coisas.

---

## Antes de começar

| O que | Detalhe |
|---|---|
| VPS contratada | 4 GB de memória, 2 vCPU, **Ubuntu 24.04 LTS**. Guarde o IP e a senha de root. |
| Endereço | Um domínio apontando para o IP, ou um gratuito em https://www.duckdns.org |
| Chip do robô | Número dedicado, com o celular à mão |
| Chave do Gemini | A mesma que você já usa, de https://aistudio.google.com/apikey |

> **O endereço precisa estar apontando para o IP antes do passo 4.** O HTTPS é emitido na hora de subir,
> e ele falha se o endereço ainda não responder pelo IP certo.

---

> **Depois de qualquer mudança, use `docker compose up -d --force-recreate api`.** É o único comando que
> sempre funciona. Os outros dois enganam: `restart` recarrega o código mas não relê o `.env`; `up -d` relê
> o `.env` mas, se nada na configuração mudou, deixa o processo antigo rodando com o código velho.

## 1. Entrar no servidor

No seu computador, no cmd:

```
ssh root@SEU_IP
```

Na primeira vez ele pergunta se confia no servidor: responda `yes`. Depois peça a senha que a hospedagem enviou.

## 2. Preparar o servidor

Cole o bloco inteiro de uma vez:

```bash
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
apt install -y git ufw
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

Isso atualiza o sistema, instala o Docker e o Git, e fecha o firewall deixando abertas só três portas:
a de acesso ao servidor e as duas do site.

## 3. Baixar o projeto

```bash
git clone https://github.com/juliandiniz07/EQI.git ~/eqi
cd ~/eqi
git checkout claude/ai-agent-corporate-events-4tp0xt
cp .env.example .env
```

Se o repositório for privado, o Git vai pedir usuário e senha. A senha é um token do GitHub, não a sua senha
da conta: crie em Settings > Developer settings > Personal access tokens, com permissão de leitura de repositório.

## 4. Preencher as configurações

```bash
nano .env
```

Preencha, no mínimo:

```
ENDERECO=seu-endereco.duckdns.org
PG_SENHA=            (gere com o comando abaixo)
GEMINI_API_KEY=      (a sua chave)
APP_SENHA=           (senha de entrada no site; escolha uma forte)
SECRET_KEY=          (gere com o comando abaixo)
EVOLUTION_APIKEY=    (gere com o comando abaixo)
GRUPOS_WEBHOOK_SEGREDO=  (gere com o comando abaixo)
GRUPOS_MODO=sombra
```

Para gerar os valores aleatórios, abra outro terminal ou saia do nano (Ctrl+O, Enter, Ctrl+X) e rode:

```bash
for n in PG_SENHA SECRET_KEY EVOLUTION_APIKEY GRUPOS_WEBHOOK_SEGREDO; do
  echo "$n=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
done
```

Copie cada linha para o lugar certo dentro do `.env`.

> `GRUPOS_MODO=sombra` é o que garante que nada é enviado aos grupos. Só mude para `ativo` quando decidir soltar.

## 5. Subir tudo

```bash
cd ~/eqi
docker compose up -d --build
```

A primeira vez demora alguns minutos. Para acompanhar:

```bash
docker compose ps
```

As seis linhas devem aparecer como `running`. Agora abra **https://seu-endereco** no navegador: a tela de
login do assistente deve aparecer, já com o cadeado de seguro.

## 6. Carregar os dados

A coleta automática roda nos horários programados, mas na primeira vez vale puxar tudo na mão:

```bash
docker compose exec api python fetch_events.py --cvm
docker compose exec api python resumos.py --dias 90 --limite 120
```

O segundo comando demora, por causa do limite da IA. Pode deixar rodando.

## 7. Conectar o WhatsApp

```bash
cd ~/eqi
alias eqi="docker compose exec api python"
eqi grupos_setup.py criar
```

O QR Code aparece **desenhado no próprio terminal**. Leia com o celular do chip do robô, em
WhatsApp > Configurações > Dispositivos conectados > Conectar dispositivo.

Se preferir não usar QR Code, preencha `NUMERO_ROBO` no `.env` com o número do chip (só dígitos, com DDI),
recrie o contêiner com `docker compose up -d --force-recreate api` e rode `eqi grupos_setup.py qr`:
em vez do QR, vem um código de 8 dígitos para digitar no celular.

Confira a conexão:

```bash
eqi grupos_setup.py status
```

## 8. Autorizar os grupos e ligar o webhook

Adicione o número do robô a um ou dois grupos e rode:

```bash
eqi grupos_setup.py grupos
```

Copie a linha `GRUPOS_PERMITIDOS=...` para o `.env` (`nano .env`), e então:

```bash
docker compose up -d --force-recreate api coletor
eqi grupos_setup.py webhook
```

Procure no registro a linha "Robô de grupos habilitado":

```bash
docker compose logs api | grep -i grupos
```

## 9. Ver o que o robô faria

```bash
eqi grupos_log.py
eqi grupos_log.py --respondidas
eqi grupos_log.py --resumo
```

Em modo sombra, cada linha mostra a pergunta do grupo e o texto que ele teria enviado. Nada foi enviado.

---

## O dia a dia

**Atualizar** (fichas novas e correções):

```bash
cd ~/eqi && git pull && docker compose up -d --force-recreate api coletor
```

Ficha nova vale na hora: o robô relê a pasta a cada pergunta. O `--force-recreate` só é necessário quando mudar código ou o `.env`.

**Ver se está tudo de pé:**

```bash
docker compose ps
docker compose logs -f api
```

(`Ctrl+C` sai do acompanhamento dos registros, sem derrubar nada.)

**Soltar o robô nos grupos**, quando decidir: troque para `GRUPOS_MODO=ativo` no `.env` e rode
`docker compose up -d --force-recreate api`.

**Backup do que importa:** o banco de eventos é reconstruível a partir da CVM, então o que não pode
se perder são as fichas (que estão no GitHub) e o `.env` (que não está em lugar nenhum). Guarde uma cópia
do `.env` num lugar seguro, fora do servidor.

---

## Quando algo dá errado

**O site não abre e o navegador reclama do certificado.** O endereço não estava apontando para o IP quando
você subiu. Corrija o apontamento, espere alguns minutos e rode
`docker compose restart caddy`.

**"Robô de grupos habilitado" não aparece nos registros.** Faltou `GRUPOS_WEBHOOK_SEGREDO` ou
`GRUPOS_PERMITIDOS` no `.env`, ou faltou recriar o contêiner com `docker compose up -d --force-recreate api` depois de editar.

**O WhatsApp desconectou.** Rode `eqi grupos_setup.py qr` e leia o código de novo.

**A IA respondendo 429.** É a cota gratuita do Gemini. O robô espera e tenta de novo sozinho. Se for
constante, é hora de ativar cobrança na conta do Google com um limite de gasto.

**Começar do zero**, sem perder o `.env`:

```bash
cd ~/eqi && docker compose down -v
docker compose up -d --build
```

`down -v` apaga também o banco de eventos e a sessão do WhatsApp, então será preciso refazer os passos 6 e 7.
