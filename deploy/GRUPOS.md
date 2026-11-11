# Colocando o robô nos grupos — passo a passo

1. **Evolution rodando** (VPS): `docker compose -f evolution-compose.yml --env-file .env.evolution up -d`
2. **Criar a instância e ler o QR** (chip dedicado):
   `curl -X POST localhost:8080/instance/create -H "apikey: $KEY" -H "Content-Type: application/json" -d '{"instanceName":"eqi","qrcode":true,"integration":"WHATSAPP-BAILEYS"}'`
3. **Adicionar o número do robô** nos grupos (começar com 1 ou 2 grupos).
4. **Listar os grupos e copiar os JIDs**: `curl localhost:8080/group/fetchAllGroups/eqi?getParticipants=false -H "apikey: $KEY"`
5. **.env do projeto**: preencher EVOLUTION_*, GRUPOS_WEBHOOK_SEGREDO (`python3 -c "import secrets;print(secrets.token_urlsafe(24))"`), GRUPOS_PERMITIDOS (JIDs), manter `GRUPOS_MODO=sombra`.
6. **Reiniciar a API** (`systemctl restart eqi-api`) e conferir no log: "Robô de grupos habilitado".
7. **Webhook da instância** (precisa de URL pública com HTTPS, via Caddy):
   `curl -X POST localhost:8080/webhook/set/eqi -H "apikey: $KEY" -H "Content-Type: application/json" -d '{"webhook":{"enabled":true,"url":"https://SEU_DOMINIO/evolution/webhook/SEGREDO","webhookByEvents":false,"events":["MESSAGES_UPSERT"]}}'`
   (se a Evolution e a API estão na mesma máquina, pode usar `http://127.0.0.1:8001/evolution/webhook/SEGREDO`, sem HTTPS.)
8. **Testar em sombra**: escreva no grupo e confira `sqlite3 fatos.db "select quando,decisao,texto,resposta from grupos_log order by id desc limit 10"`.
9. Depois de alguns dias sem erros: `GRUPOS_MODO=ativo`, reiniciar, e ampliar para os demais grupos.
