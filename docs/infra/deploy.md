# Deploy — Cabine RFID em producao

## Topologia

- VPS `95.111.250.4` (manager `vmi3286141`), Swarm compartilhado com os outros projetos Solid.
- Stack `impinj` (`impinj-stack.yml`): servicos `impinj_impinj` (app, 128M) e `impinj_postgres` (256M),
  ambos fixados no manager. Traefik (`traefik-public`) publica `cabine.solidsd.com.br`.
- Imagem `impinj-rfid:local` buildada na propria VPS (sem registry). **Nao ha `git pull` na VPS**: o
  codigo chega por `rsync` para `/opt/impinj/`.
- `.env` so na VPS (`/opt/impinj/.env`, chmod 600): `WEBHOOK_USER`, `WEBHOOK_PASS`, `UI_USER`, `UI_PASS`,
  `SAIDA_USER`, `SAIDA_PASS`, `POSTGRES_PASSWORD`; opcionais `KUMA_PUSH_URL`, `STALE_MINUTES`,
  `RETENTION_DAYS`, `SAIDA_JANELA_MINUTOS`.

## Deploy de rotina

```bash
rsync -a --exclude .git --exclude .claude --exclude .env ./ root@95.111.250.4:/opt/impinj/
ssh root@95.111.250.4
cd /opt/impinj && docker build -t impinj-rfid:local .
bash -c 'set -a; . ./.env; set +a; docker stack deploy -c impinj-stack.yml impinj'
docker service update --force impinj_impinj   # se so a imagem mudou
```

Verificar: `docker service ps impinj_impinj` e `curl -s https://cabine.solidsd.com.br/health`.

## Migracao SQLite -> PostgreSQL (2026-11-07)

O volume antigo `impinj_impinj_data` (`impinj.db`) **nao e apagado** pelo deploy: fica como backup.
1. Backup: copiar `impinj.db` do volume para `/root/backup/impinj-AAAAMMDD.db`.
2. Adicionar `SAIDA_USER`, `SAIDA_PASS`, `POSTGRES_PASSWORD` ao `.env`.
3. Deploy (acima). O app cria as tabelas no primeiro start.
4. Copiar eventos: `scripts/migrar_sqlite.py` em container na rede `impinj_impinj-internal`, montando o
   volume antigo somente leitura (`SQLITE_PATH`, `DATABASE_URL`). E idempotente.
5. Conferir a contagem no log do script e em `GET /api/summary`.

## Rollback

`git checkout <commit anterior>`, rsync, build e deploy. O SQLite antigo segue intacto no volume; eventos
recebidos so no Postgres depois da migracao nao voltam para ele.

## Cuidados

- O R700 usa retry: durante o redeploy (segundos) ele reenvia, nao perde leituras.
- Nao rodar `docker volume prune` na VPS sem checar `impinj_impinj_data` e `impinj_pgdata`.
- Nao mexer em outras stacks do Swarm.
