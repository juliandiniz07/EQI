# Como testar o Agente de Eventos Corporativos

Guia passo a passo para rodar o agente no seu computador e conferir se as respostas estão certas.
Tempo estimado: 20 a 30 minutos na primeira vez.

---

## Parte 1 — Preparar o computador (só na primeira vez)

### 1.1 Instalar o Python

Você precisa do Python **3.10 ou mais novo**.

**Windows**
1. Acesse https://www.python.org/downloads/ e clique em **Download Python 3.x**.
2. Execute o instalador. **Na primeira tela, marque a caixa "Add python.exe to PATH"** (sem isso, os
   comandos abaixo não funcionam).
3. Clique em **Install Now** e aguarde.
4. Abra o **PowerShell** (tecla Windows → digite `PowerShell` → Enter) e confira:
   ```powershell
   python --version
   ```
   Deve aparecer algo como `Python 3.12.x`.

**Mac**
1. Abra o **Terminal** (Cmd + Espaço → digite `Terminal` → Enter).
2. Confira se já tem Python:
   ```bash
   python3 --version
   ```
3. Se aparecer versão 3.10 ou maior, pule para o passo 1.2. Senão, instale por
   https://www.python.org/downloads/ (arquivo `.pkg`) e confira de novo.

### 1.2 Criar a chave da API da Anthropic

É ela que permite ao agente usar o Claude. As chamadas são cobradas na conta da Anthropic
(cada pergunta de teste custa poucos centavos de dólar).

1. Acesse https://console.anthropic.com e faça login (ou crie a conta).
2. Se a conta for nova, cadastre um meio de pagamento em **Settings → Billing** e adicione créditos
   (US$ 5 são suficientes para muitos testes).
3. Vá em **Settings → API Keys → Create Key**. Dê um nome, por exemplo `agente-eventos-teste`.
4. **Copie a chave agora** (começa com `sk-ant-`) e guarde num lugar seguro. Ela não aparece de novo.

> Não compartilhe essa chave nem a coloque em arquivos que vão para o GitHub.

### 1.3 (Opcional) Criar o token da brapi.dev

Só é preciso para testar com **dados reais** (Parte 4). Para o primeiro teste, com dados fictícios,
pule este passo.

1. Acesse https://brapi.dev e crie uma conta gratuita.
2. No painel, copie o seu **token**.

---

## Parte 2 — Baixar e instalar o agente

### 2.1 Baixar o código

**Opção A — pelo navegador (mais simples)**
1. Acesse https://github.com/juliandiniz07/EQI/tree/claude/ai-agent-corporate-events-4tp0xt
   (faça login no GitHub se o repositório for privado).
2. Clique no botão verde **Code → Download ZIP**.
3. Descompacte o arquivo numa pasta fácil de achar, por exemplo `Documentos\EQI` (Windows) ou
   `Documentos/EQI` (Mac).

**Opção B — com git (se você já usa)**
```bash
git clone -b claude/ai-agent-corporate-events-4tp0xt https://github.com/juliandiniz07/EQI.git
```

### 2.2 Entrar na pasta pelo terminal

Abra o PowerShell (Windows) ou o Terminal (Mac) e vá até a pasta do projeto — a que contém o arquivo
`requirements.txt`. Exemplo:

**Windows**
```powershell
cd $HOME\Documents\EQI
```
**Mac**
```bash
cd ~/Documents/EQI
```

> Dica: se o ZIP criou uma subpasta com nome comprido (ex.: `EQI-claude-ai-agent-...`), entre nela.
> Para conferir, rode `dir` (Windows) ou `ls` (Mac): deve aparecer `requirements.txt` e a pasta `app`.

### 2.3 Criar o ambiente e instalar as dependências

**Windows**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
Se o segundo comando der erro de "execução de scripts desabilitada", rode uma vez
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, responda `S` e repita o comando.

**Mac**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Depois de ativar, o início da linha do terminal passa a mostrar `(.venv)`. A instalação leva
1 a 2 minutos.

### 2.4 Conferir que está tudo certo (testes automáticos)

```bash
pytest
```
O resultado esperado é algo como **`24 passed`**. Esses testes não usam a sua chave nem internet.

---

## Parte 3 — Primeiro teste, com dados fictícios

Nesta etapa o agente usa uma base de exemplo embutida (com **valores inventados**), para você
validar o comportamento sem depender da brapi.

### 3.1 Informar a chave e subir o servidor

Troque `sk-ant-...` pela sua chave.

**Windows**
```powershell
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:PROVEDOR_EVENTOS="mock"
uvicorn app.main:criar_app --factory
```

**Mac**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export PROVEDOR_EVENTOS=mock
uvicorn app.main:criar_app --factory
```

Quando aparecer `Uvicorn running on http://127.0.0.1:8000`, o servidor está no ar.
**Deixe essa janela aberta** — fechar a janela desliga o agente.

### 3.2 Abrir o chat

No navegador, acesse **http://localhost:8000**. Deve aparecer a tela
"Assistente de Eventos Corporativos".

### 3.3 Roteiro de perguntas

Faça as perguntas abaixo e compare com o resultado esperado. A base fictícia só tem os ativos
**PETR4, ITUB4, WEGE3, MGLU3, HGLG11 e BBAS3**. As respostas podem variar na redação; o que
importa são os números e o comportamento.

| # | Pergunta | O que deve aparecer |
|---|---|---|
| 1 | Quais os proventos de PETR4? | Dividendo de R$ 0,45/ação (data com 21/08/2026, pagamento 20/10/2026) e JCP de R$ 0,30/ação (data com 21/08/2026, pagamento 20/11/2026). Deve avisar que os dados são **fictícios**. |
| 2 | Cliente tem 1.000 PETR4. Quanto ele recebe? | Dividendo: R$ 450,00 (sem IR). JCP: bruto R$ 300,00, IR R$ 45,00, líquido R$ 255,00. |
| 3 | Cliente tem 1.505 ITUB4. Quanto recebe de JCP e como fica a posição após a bonificação? | JCP: bruto R$ 30,10, IR R$ 4,52, líquido R$ 25,58. Bonificação de 10%: 1.505 → 1.655 ações + fração de 0,5 ação. |
| 4 | Tenho um cliente com 200 WEGE3, o que muda com o desdobramento? | Desdobramento 1 para 2: passa a ter 400 ações; data com 01/10/2026. |
| 5 | E um cliente com 1.234 MGLU3? | Grupamento 10 para 1: fica com 123 ações + fração de 0,4 (frações costumam ir a leilão). |
| 6 | Quanto rende 100 cotas de HGLG11 este mês? | Rendimento de R$ 1,10/cota → R$ 110,00; pagamento 14/10/2026. |
| 7 | Tem subscrição de BBAS3? | Direito de subscrição de 5% a R$ 22,50; negociação de 15/10 a 30/10. |
| 8 | Quais os proventos de VALE3? | Deve dizer que **não encontrou** eventos na fonte (VALE3 não está na base fictícia). **Não pode inventar valores.** |
| 9 | Devo comprar WEGE3 antes do desdobramento? | Deve **recusar a recomendação** (fora do escopo) e oferecer os dados do evento. |
| 10 | Quando é a data com? | Deve **perguntar de qual ativo** (pergunta ambígua). |
| 11 | Depois da pergunta 3: "e se fossem 3.000 ações?" | Deve lembrar que é ITUB4 (contexto da conversa) e recalcular: JCP bruto R$ 60,00; 3.000 → 3.300 ações. |

Anote qualquer resposta diferente do esperado (com o texto da pergunta e da resposta) para eu corrigir.

### 3.4 Desligar

Volte à janela do terminal e aperte **Ctrl + C**.

---

## Parte 4 — Teste com dados reais (brapi.dev)

Aqui o agente consulta a API de mercado de verdade. **Confira as respostas contra fontes oficiais**
(aviso aos acionistas / fato relevante no site de RI da empresa ou na B3), porque o mapeamento dos
dados da brapi ainda não foi validado com respostas reais.

### 4.1 Subir com a brapi

Com o terminal ainda na pasta do projeto e o `(.venv)` ativo (se fechou o terminal, repita o
`cd` do passo 2.2 e a linha de ativação do 2.3):

**Windows**
```powershell
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:PROVEDOR_EVENTOS="brapi"
$env:BRAPI_TOKEN="seu-token-da-brapi"
uvicorn app.main:criar_app --factory
```

**Mac**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export PROVEDOR_EVENTOS=brapi
export BRAPI_TOKEN="seu-token-da-brapi"
uvicorn app.main:criar_app --factory
```

Abra de novo **http://localhost:8000** (se já estava aberto, recarregue a página para começar uma
conversa nova).

### 4.2 O que conferir

1. **Proventos em dinheiro** — pergunte os últimos proventos de 2 ou 3 ativos que você conhece
   (ex.: PETR4, ITUB4, TAEE11, um FII como HGLG11). Compare **tipo, valor por ação, data com e data
   de pagamento** com o aviso aos acionistas.
2. **Eventos em ações** — pergunte sobre um ativo que teve desdobramento, grupamento ou bonificação
   recente. Confira se o **fator** está certo e se a simulação com uma quantidade (ex.: 1.000 ações)
   dá o número esperado. **Este é o ponto de maior risco.** Se o agente disser que não conseguiu
   calcular o impacto, anote o ativo e o evento.
3. **Ticker inexistente** — pergunte sobre `XPTO3`. Deve dizer que não encontrou, sem inventar.
4. **Fonte** — toda resposta deve citar `brapi.dev` como fonte.

---

## Parte 5 — Testar a API (opcional, para quem vai integrar com outros sistemas)

Com o servidor rodando, abra **outro** terminal:

**Mac / Linux**
```bash
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"mensagem": "Quais os proventos de PETR4?"}'
```

**Windows (PowerShell)**
```powershell
Invoke-RestMethod -Uri http://localhost:8000/api/chat -Method Post -ContentType "application/json" `
  -Body '{"mensagem": "Quais os proventos de PETR4?"}'
```

A resposta traz `sessao_id` e `resposta`. Para continuar a mesma conversa, envie o `sessao_id`
recebido junto com a próxima mensagem.

Também há uma documentação interativa da API em **http://localhost:8000/docs**.

---

## Problemas comuns

| Sintoma | Causa provável | Como resolver |
|---|---|---|
| `python` / `python3` não é reconhecido | Python não instalado ou fora do PATH | Reinstale marcando **Add python.exe to PATH** (Windows) e abra um terminal novo. |
| `No such file or directory: requirements.txt` | Terminal na pasta errada | Refaça o passo 2.2; `dir`/`ls` deve mostrar `requirements.txt`. |
| `uvicorn` não é reconhecido | Ambiente virtual não ativado | Rode a linha de ativação do passo 2.3 (deve aparecer `(.venv)`). |
| Chat responde "Erro no serviço de IA" e o terminal mostra erro 401 | Chave da Anthropic errada ou não informada | Confira o `ANTHROPIC_API_KEY` (passo 3.1). As variáveis valem só para aquela janela do terminal: se abriu outra, informe de novo. |
| Erro 400 falando de crédito / billing | Conta da Anthropic sem créditos | Adicione créditos em console.anthropic.com → Settings → Billing. |
| "Muitas requisições no momento" | Limite de uso da conta | Aguarde um minuto e tente de novo. |
| Com brapi, o agente diz que houve erro ao consultar a fonte | Token da brapi ausente/errado, limite do plano gratuito ou ticker inexistente | Confira o `BRAPI_TOKEN` e o plano na brapi.dev. |
| `Address already in use` | Já tem um servidor rodando na porta 8000 | Feche a outra janela, ou rode com `--port 8001` e acesse http://localhost:8001. |
| Página abre sem formatação nas respostas (tabelas aparecem como texto) | Sem acesso ao CDN que formata o texto | Não afeta o conteúdo; pode ignorar no teste. |

Se travar em algum passo, mande o **print ou o texto do erro que aparece no terminal**.
