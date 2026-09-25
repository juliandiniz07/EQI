# Agente de Eventos Corporativos

Assistente de IA (Claude) que responde aos assessores sobre eventos corporativos: dividendos, JCP,
rendimentos de FIIs, desdobramentos, grupamentos, bonificações e direitos de subscrição.

O assessor pergunta em linguagem natural ("Cliente tem 1.500 ITUB4, quanto recebe de JCP e como fica
a posição depois da bonificação?") e o agente:

1. consulta a API de mercado (ferramenta `buscar_eventos_corporativos`);
2. faz os cálculos de forma determinística em Python (ferramenta `simular_impacto_na_posicao`):
   valor bruto, IR retido estimado sobre JCP, valor líquido, nova quantidade e frações;
3. responde em português, citando data com, data de pagamento, valor/fator e a fonte.

Datas e valores sempre vêm das ferramentas, nunca da memória do modelo. O agente não faz
recomendação de investimento.

## Estrutura

```
app/
  main.py            API FastAPI (POST /api/chat) + página de chat em /
  agent.py           Loop do agente com o Claude (prompt de sistema, ferramentas, histórico por sessão)
  tools.py           Definição e execução das ferramentas
  config.py          Configuração via variáveis de ambiente
  providers/
    base.py          Modelo EventoCorporativo e interface ProvedorEventos
    brapi.py         Provedor da API externa brapi.dev
    mock.py          Provedor com dados fictícios (dev/teste)
  data/eventos_exemplo.json
  static/index.html  Chat web
tests/
```

## Como rodar

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencha ANTHROPIC_API_KEY e BRAPI_TOKEN
set -a && source .env && set +a
uvicorn app.main:criar_app --factory --reload
```

Abra http://localhost:8000. Para testar sem a API de mercado, use `PROVEDOR_EVENTOS=mock`
(os dados de exemplo são fictícios e o agente avisa isso na resposta).

API:

```bash
curl -s localhost:8000/api/chat -H 'Content-Type: application/json' \
  -d '{"mensagem": "Quais os próximos proventos de PETR4?"}'
# -> {"sessao_id": "...", "resposta": "..."}  (envie o sessao_id de volta para manter o contexto)
```

Testes: `pytest`

Passo a passo detalhado de teste (inclusive para quem não é desenvolvedor): [COMO_TESTAR.md](COMO_TESTAR.md)

## Configuração

| Variável | Padrão | Descrição |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Chave da API da Anthropic |
| `AGENTE_MODELO` | `claude-opus-5` | Modelo do Claude |
| `AGENTE_ESFORCO` | `medium` | Profundidade de raciocínio (`low`…`max`). `medium` equilibra qualidade e tempo de resposta para chat; suba para `high` se as respostas ficarem rasas |
| `PROVEDOR_EVENTOS` | `brapi` | `brapi` ou `mock` |
| `BRAPI_TOKEN` | — | Token da brapi.dev |
| `ALIQUOTA_IR_JCP` | `0.15` | Alíquota de IR retido sobre JCP nas simulações — **confirme o valor vigente com a área tributária** |

O agente usa *fallback* automático do lado da Anthropic (`fallbacks: "default"`): se o modelo principal
recusar uma pergunta por engano, a mesma requisição é refeita em outro modelo.

## Trocando a fonte de dados

A brapi.dev foi usada como primeira API externa. Para usar outro fornecedor (B3, Economatica,
Quantum, feed interno), crie uma classe com `nome` e `buscar_eventos(ticker) -> list[EventoCorporativo]`
em `app/providers/` e registre em `criar_provedor` (`app/providers/__init__.py`).

## Antes de ir para produção

- Validar o mapeamento da brapi com respostas reais (principalmente `completeFactor` de desdobramentos/
  grupamentos/bonificações) e conferir amostras contra os avisos aos acionistas.
- O histórico das conversas fica em memória: reiniciar o servidor apaga as sessões, e com mais de uma
  instância é preciso guardar o histórico num store compartilhado (ex.: Redis).
- Adicionar autenticação (SSO corporativo) na frente da API.
- Rever regras tributárias além do JCP (ex.: retenção sobre dividendos acima de limites mensais).
