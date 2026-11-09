# Cabine — leitor RFID Impinj

Recebe o webhook de Event Reporting do leitor Impinj (R700), guarda as leituras (PostgreSQL) e oferece
um painel para lotes de enxoval, transferencias, inventario e alertas de evasao. Heartbeat opcional para
o Uptime Kuma.

**Documentacao completa em [`docs/`](docs/README.md)** (mapa, memoria, registro, backlog, deploy, skills).

- Webhook: `POST https://cabine.solidsd.com.br/ingest` (basic auth `WEBHOOK_USER`/`WEBHOOK_PASS`)
- Painel: `https://cabine.solidsd.com.br/` (login com usuarios e perfis: `painel` so dashboard, `leitura`, `operador`, `admin`; o par `UI_USER`/`UI_PASS` e admin de ambiente). Instalavel como PWA.
- Agente do hotel (Go, Windows): monitora os radios da LAN e conversa com o servidor; ver [`docs/infra/agente-hotel.md`](docs/infra/agente-hotel.md)
- Alertas por WhatsApp (Evolution API): configurados no painel (Alertas WhatsApp)
- Producao: VPS principal (Swarm, stack `impinj`) — passos em [`docs/infra/deploy.md`](docs/infra/deploy.md)

## Rodar localmente

```bash
docker run -d --name pg -e POSTGRES_USER=impinj -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=impinj -p 5432:5432 postgres:16-alpine
pip install fastapi==0.120.4 starlette==0.49.3 "uvicorn[standard]==0.34.0" "psycopg[binary]"
DATABASE_URL=postgresql://impinj:dev@localhost:5432/impinj WEBHOOK_USER=w WEBHOOK_PASS=w \
  UI_USER=u UI_PASS=u SAIDA_USER=s SAIDA_PASS=s SESSION_SECRET=$(openssl rand -hex 32) uvicorn app.main:app --port 8000
# SESSION_SECRET e obrigatorio (>= 32 caracteres); o app nao sobe sem ele.
# Testes: DATABASE_URL apontando para um Postgres DESCARTAVEL (apagam as tabelas) -> pytest -q
```

Dados de exemplo: `scripts/seed_exemplo.py`. Config do leitor: Enable Webhook Output, Server URL
`https://cabine.solidsd.com.br/ingest`, porta 443, Verify TLS, usuario/senha do webhook, Enable Retry.

## API

Rotas em [`docs/mapa.md`](docs/mapa.md); detalhes em [`docs/api.md`](docs/api.md). Exigem login (cookie ou Basic) com a permissao da rota (perfis em `docs/api.md`), exceto
`/ingest` (`WEBHOOK_*`), `POST /api/saida` (`SAIDA_*`), `/api/agente/*` (`X-Agent-Token`), `/health`, `/login` e os arquivos do PWA.

## Aviso

Nao versionar `.env` nem segredos. `main` -> producao nao e automatico (ver `docs/infra/deploy.md`).
