# Cabine — leitor RFID Impinj

Recebe o webhook de Event Reporting do leitor Impinj (R700), guarda as leituras de tags (SQLite) e
mostra um painel ao vivo. Opcionalmente manda heartbeat pro Uptime Kuma (monitor Push).

- Webhook: `POST https://rfid.solidsd.com.br/ingest` (basic auth `WEBHOOK_USER`/`WEBHOOK_PASS`)
- Painel: `https://rfid.solidsd.com.br/` (basic auth `UI_USER`/`UI_PASS`)
- Roda na **VPS principal** (Swarm, stack `impinj`, `impinj-stack.yml`) por enquanto.

## Deploy

```bash
rsync -a --exclude .git ./ root@95.111.250.4:/opt/impinj/
ssh root@95.111.250.4
cd /opt/impinj && docker build -t impinj-rfid:local .
bash -c 'set -a; . ./.env; set +a; docker stack deploy -c impinj-stack.yml impinj'
```

`.env` só na VPS (chmod 600): `WEBHOOK_USER`, `WEBHOOK_PASS`, `UI_USER`, `UI_PASS`, `KUMA_PUSH_URL` (opcional),
`STALE_MINUTES` (padrão 30), `RETENTION_DAYS` (padrão 30). DNS: `rfid.solidsd.com.br` → `95.111.250.4` no Cloudflare.

## Config no leitor (Event Reporting → Webhook)

Enable Webhook Output, Server URL `https://rfid.solidsd.com.br/ingest`, Port `443`, Verify TLS ligado,
Username/Password = `WEBHOOK_USER`/`WEBHOOK_PASS`. Ligar Enable Retry. O leitor só precisa de saída pra internet.

## Limitação

O leitor só envia evento quando lê tag, não há heartbeat. O Kuma avisa "sem eventos há mais de `STALE_MINUTES`",
o que pode ser só ausência de movimento.
