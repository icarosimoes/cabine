---
name: jarvis-deploy
description: Deploy do Cabine RFID na VPS Swarm (rsync + build local + stack deploy), migracao de banco, verificacao e rollback. TRIGGER em "deploy", "subir pra producao", "publicar", "rollback", "Jarvis-Deploy".
---

Siga `docs/infra/deploy.md`. Resumo operacional:

1. Antes: `git status` limpo e commit feito; confira `.env` da VPS com os nomes de variavel que o
   `impinj-stack.yml` exige (`${VAR:?}` derruba o deploy se faltar). Mostre so os NOMES, nunca valores.
2. Mudanca de banco/dado: faca backup primeiro (volume `impinj_impinj_data` e/ou `pg_dump`). Nunca
   `docker volume prune` nem remova volumes.
3. `rsync` (sem `.git`, `.claude`, `.env`) -> `docker build -t impinj-rfid:local .` na VPS ->
   `docker stack deploy -c impinj-stack.yml impinj`.
4. Verificar: `docker service ps impinj_impinj impinj_postgres`, `/health` pelo dominio, `GET /api/summary` com auth.
5. Nao tocar em outras stacks do Swarm. Registrar o deploy em `docs/registro-trabalho.md`.
6. Deploy e acao com efeito externo: so execute com pedido explicito do usuario na sessao.
