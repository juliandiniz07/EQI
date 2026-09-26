# Assistente EQI — Eventos Corporativos

Chat web, protegido por senha, em que o assessor pergunta em linguagem natural ("Teve OPA essa
semana?", "O que aconteceu com a Petro?", "Quais FIIs fizeram emissão de cotas?") e recebe uma
resposta redigida como a de um analista, com cartões dos fatos relevantes e link para o documento
na CVM.

Custo: **R$ 0**. Usa a VPS gratuita da Oracle, a camada gratuita do Google Gemini, HTTPS gratuito
(Caddy + Let's Encrypt) e um endereço gratuito (DuckDNS). A Vercel é opcional.

---

## Como funciona

```
                 a cada 30 min (dias úteis, 7h-22h)
Fundamentus ─┐   + 1x por dia
CVM (oficial)┴──► fetch_events.py ──► fatos.db (SQLite)
                                          │
Assessor ──HTTPS──► Caddy ──► api.py ─────┤
 (navegador)                   │  1. entende a pergunta (empresa, tipo, período) por regras
                               │  2. busca no banco (máx. 10 eventos)
                               └─ 3. Gemini redige a resposta só com esses eventos
```

| Arquivo | Função |
|---|---|
| `fetch_events.py` | Coletor: lê o Fundamentus (ações e FIIs) e o arquivo oficial da CVM, filtra os eventos relevantes e grava sem duplicar |
| `scheduler.py` | Agendador (APScheduler): coleta ao iniciar, a cada 30 min nos dias úteis das 7h às 22h, e a CVM uma vez por dia |
| `api.py` | API FastAPI (porta 8001): `/login`, `/consultar`, `/health` e a página do chat em `/` |
| `consulta.py` | Entende a pergunta: apelidos ("Petro", "Magalu", "MXRF11", "Maxi Renda"), tipo de evento e período |
| `eventos.py` | Banco, filtro de relevância, classificação (OPA, Fusão, Recuperação...) e busca |
| `apelidos.json` | Lista editável de apelidos de empresas e FIIs |
| `web/index.html` | Frontend completo (login + chat), arquivo único |
| `deploy/` | Serviços systemd e configuração do Caddy |

### O que mudou em relação à ideia original, e por quê

| Ideia original | Como ficou | Motivo |
|---|---|---|
| Senha `eqi2026` verificada no navegador | Senha verificada **no servidor**, que devolve uma sessão assinada; a API recusa quem não fez login | No navegador, qualquer pessoa lê a senha no código-fonte e chama a API direto, gastando sua cota de IA. O site é público, então a proteção precisa estar no servidor |
| Gemini para interpretar **e** para redigir (2 chamadas) | Interpretação por regras, Gemini só redige (1 chamada) | A camada gratuita tem limite de pedidos por dia; com 1 chamada por pergunta cabem o dobro de perguntas. As regras também são mais previsíveis para datas ("essa semana", "em março") |
| `gemini-1.5-flash` | `gemini-flash-lite-latest`, com `gemini-flash-latest` de reserva (configurável) | O 1.5 Flash foi descontinuado pelo Google. O Flash-Lite tem a maior cota gratuita diária |
| Sem IA disponível = erro | Se a cota acabar ou a IA falhar, o chat responde com um resumo automático dos eventos | O chat nunca fica fora do ar por causa da cota |
| Só Fundamentus | Fundamentus + arquivo oficial da CVM (1x por dia) | O Fundamentus traz os fatos em quase tempo real; a CVM é a fonte oficial, carrega o histórico do ano inteiro no primeiro dia ("desdobramentos esse ano" já funciona) e cobre o que a página do Fundamentus perder |
| Janela 7h–22h em UTC | 7h–22h no **horário de Brasília** (configurável) | 7h–22h UTC equivale a 4h–19h em Brasília e perderia os fatos publicados depois do fechamento do mercado, que são muitos |
| Frontend na Vercel | A própria API já serve o chat no mesmo endereço; Vercel continua possível | Um site na Vercel (HTTPS) não consegue chamar um backend sem HTTPS; como a VPS precisa de HTTPS de qualquer jeito, servir tudo dali é mais simples e evita configurar CORS |
| — | Limite de tentativas de senha e de perguntas por minuto por IP | Proteção contra força bruta e contra alguém esgotar sua cota |

O campo `enviado` da tabela foi mantido. Fatos novos do Fundamentus entram com `enviado=0`; a carga
histórica da CVM entra com `enviado=1`. Assim, se você reaproveitar o banco de um bot antigo que
envia mensagens, ele não dispara o histórico todo.

---

## 1. Testar no seu computador (opcional, 10 min)

Precisa de Python 3.10+.

```bash
git clone -b claude/ai-agent-corporate-events-4tp0xt https://github.com/juliandiniz07/EQI.git
cd EQI
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # edite: GEMINI_API_KEY, DB_PATH=fatos.db, SECRET_KEY
python fetch_events.py --cvm   # primeira coleta (Fundamentus + histórico do ano na CVM)
python api.py
```

Abra http://localhost:8001 e entre com a senha do `.env`.

Testes automáticos: `pytest` (não usam internet nem a chave).

---

## 2. Chave gratuita do Gemini (5 min)

1. Acesse https://aistudio.google.com/apikey e entre com uma conta Google.
2. Clique em **Create API key** e copie a chave.
3. Não cadastre cartão: sem faturamento ativo, o uso fica sempre na camada gratuita (sem cobrança).

Sobre a camada gratuita:
- Ela tem limite de pedidos por minuto e por dia. Para um time de assessores costuma bastar; se
  estourar, o chat continua respondendo com o resumo automático até a cota renovar.
- Pelos termos do Google, o conteúdo enviado na camada gratuita pode ser usado para melhorar os
  produtos deles. Aqui só vão perguntas e fatos públicos da CVM; **oriente os assessores a não
  colocar dados de clientes nas perguntas**.

Para conferir quais modelos sua chave enxerga:

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=SUA_CHAVE" | grep '"name"'
```

Se algum nome em `GEMINI_MODELOS` não aparecer na lista, troque por um que apareça (prefira os
"flash-lite").

---

## 3. Deploy na VPS Oracle (Ubuntu 22.04)

### 3.1 Liberar as portas 80 e 443

São duas camadas de firewall na Oracle; as duas precisam ser abertas.

**No painel da Oracle Cloud:** Networking → Virtual Cloud Networks → sua VCN → Security Lists →
Default Security List → **Add Ingress Rules**:
- Source CIDR `0.0.0.0/0`, IP Protocol TCP, Destination Port Range `80`
- Repita para a porta `443`

**Dentro da VPS** (a imagem Ubuntu da Oracle bloqueia tudo no iptables):

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

Não abra a porta 8001: a API fica escondida atrás do Caddy.

### 3.2 Endereço gratuito (DuckDNS)

1. Acesse https://www.duckdns.org e entre (Google/GitHub).
2. Crie um subdomínio, ex.: `eqi-eventos` → fica `eqi-eventos.duckdns.org`.
3. No campo **current ip**, coloque o IP público da VPS e clique em **update ip**.

Se a empresa tiver domínio próprio, pode usar um subdomínio dele (ex.: `eventos.suaempresa.com.br`)
com um registro DNS tipo A apontando para o IP da VPS.

### 3.3 Instalar o sistema

```bash
ssh ubuntu@IP_DA_VPS

sudo apt update && sudo apt install -y python3-venv python3-pip git
git clone -b claude/ai-agent-corporate-events-4tp0xt https://github.com/juliandiniz07/EQI.git ~/eqi-eventos
cd ~/eqi-eventos
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
```

No `.env`, preencha no mínimo:
- `GEMINI_API_KEY` — a chave do passo 2
- `APP_SENHA` — **troque** a senha padrão (ela é a única proteção do site)
- `SECRET_KEY` — gere com `python3 -c "import secrets; print(secrets.token_hex(32))"`
- `DB_PATH` — deixe `/home/ubuntu/eqi-eventos/fatos.db`, ou aponte para o banco antigo
  (`/home/ubuntu/bot/fatos-relevantes-bot/fatos.db`) se quiser aproveitar o que ele já coletou

Salve com Ctrl+O, Enter, Ctrl+X.

### 3.4 Validar a leitura do Fundamentus (importante)

O layout das páginas do Fundamentus não pôde ser conferido durante o desenvolvimento. Antes de
ligar tudo, rode:

```bash
.venv/bin/python fetch_events.py --diagnostico
```

Para cada página deve aparecer o `mapeamento` das colunas (empresa, assunto, data...) e 3
`exemplos` lidos corretamente. Se aparecer `erro: não identifiquei as colunas...`, copie a linha
`cabecalhos` e ajuste a lista `_COLUNAS` em `fetch_events.py` (ou peça o ajuste mandando essa
saída).

Depois faça a primeira carga (inclui o histórico do ano da CVM):

```bash
.venv/bin/python fetch_events.py --cvm
```

O log mostra `lidos=... relevantes=... NOVOS INSERIDOS=...` para cada fonte. Para carregar também
o ano anterior: `.venv/bin/python fetch_events.py --cvm-ano 2025`.

### 3.5 Deixar rodando como serviço

```bash
sudo cp deploy/eqi-api.service deploy/eqi-coletor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eqi-api eqi-coletor
systemctl status eqi-api eqi-coletor --no-pager
curl -s localhost:8001/health
```

Os dois serviços sobem sozinhos quando a VPS reinicia.

### 3.6 HTTPS com Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile      # troque SEU-ENDERECO.duckdns.org pelo seu endereço
sudo systemctl reload caddy
```

Em até 1 minuto o certificado é emitido. Acesse **https://seu-endereco.duckdns.org** — o chat já
está no ar. Pronto: essa é a URL para passar aos assessores.

Se o certificado não sair, confira `sudo journalctl -u caddy -n 50`: quase sempre é porta 80/443
fechada (passo 3.1) ou o DuckDNS apontando para o IP errado.

---

## 4. (Opcional) Frontend na Vercel

Só é necessário se você quiser o endereço `*.vercel.app`. O backend continua na VPS, com HTTPS
(passo 3.6 é obrigatório: um site HTTPS não consegue chamar uma API sem HTTPS).

1. Em `web/index.html`, altere a linha de configuração:
   ```js
   const API_URL = "https://seu-endereco.duckdns.org";
   ```
   Faça commit e push.
2. Em https://vercel.com → **Add New → Project** → importe o repositório `EQI`.
3. Em **Root Directory**, escolha `web`. Framework Preset: **Other**. Clique em **Deploy**.
4. No `.env` da VPS, restrinja o CORS ao endereço da Vercel e reinicie a API:
   ```
   CORS_ORIGINS=https://seu-projeto.vercel.app
   ```
   ```bash
   sudo systemctl restart eqi-api
   ```

---

## Operação do dia a dia

| Tarefa | Comando |
|---|---|
| Ver log da coleta | `journalctl -u eqi-coletor -f` |
| Ver log da API | `journalctl -u eqi-api -f` |
| Status do sistema | `curl -s localhost:8001/health` (total no banco, evento mais recente, última coleta e erro, se houver) |
| Coletar agora | `cd ~/eqi-eventos && .venv/bin/python fetch_events.py` |
| Trocar a senha | edite `APP_SENHA` no `.env` e `sudo systemctl restart eqi-api` |
| Atualizar o sistema | `cd ~/eqi-eventos && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart eqi-api eqi-coletor` |
| Backup do banco | `sqlite3 fatos.db ".backup fatos-$(date +%F).db"` |
| Adicionar apelido | edite `apelidos.json` e `sudo systemctl restart eqi-api` |

### Perguntas que o chat entende bem

- **Período:** hoje, ontem, essa semana, semana passada, este mês, mês passado, em março,
  esse ano, 2025, últimos 15 dias. Sem período: 30 dias (ou 365 quando cita uma empresa).
  Se não achar nada, amplia sozinho para 12 meses e avisa.
- **Tipo:** OPA, fusão/incorporação/aquisição/cisão, recuperação judicial/extrajudicial/falência,
  cancelamento de registro/ações, desdobramento/grupamento, emissão/oferta de cotas/subscrição,
  liquidação de fundo.
- **Empresa:** nome, apelido ("Petro", "Magalu", "BB") ou ticker (PETR4, MXRF11). Empresas fora da
  lista de apelidos são achadas pelo nome como aparece nos fatos (ex.: "Lupatech").
- **Continuação:** "mais detalhes", "e da Vale?" aproveitam o contexto da pergunta anterior.

## Limitações conhecidas

- A leitura do Fundamentus depende do layout do site; se ele mudar, o `/health` mostra o erro da
  última coleta e o `--diagnostico` ajuda a ajustar. A carga diária da CVM continua funcionando
  para ações.
- O arquivo da CVM cobre companhias abertas (ações). FIIs vêm só do Fundamentus.
- O "assunto" dos fatos é curto; a resposta se limita ao que está nele e no link do documento.
  O assistente não lê o PDF.
- Senha única para todos. Se precisar de login individual ou de saber quem perguntou o quê, é o
  próximo passo.
