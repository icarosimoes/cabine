---
tipo: registro
status: vivo
resumo: "Postgres de Cabine (Impinj) atualizado para 16.16 em 15/11/2026 (CVEs de 12/11/2026), com dump antes."
tags: [infra, postgres, seguranca]
---
# Postgres 16.15 → 16.16 (15/11/2026)

**Por quê:** lançamento de segurança do PostgreSQL de 12/11/2026 (CVEs de nota alta em plpgsql/FDW/dblink, `pg_restore`/`pg_dump` e RLS; fonte: postgresql.org/support/security — conferir os números nas notas da versão). Só versão menor: sem migração de dados.

**O que foi feito (VPS principal, serviço `impinj_postgres`):**
- Dump verificado antes: `/root/backups-pg/impinj_postgres-antes-16.16-20261115.dump` (`pg_dump -Fc`, conferido com `pg_restore -l`).
- Troca da imagem **por digest** (`docker service update --image`), `stop-first`, `CHECKPOINT` antes; cerca de 1 minuto fora do ar.
- Depois: `postgres --version` = 16.16, consulta ao banco OK, serviços dependentes sem erro nos logs (erros de DNS do banco só durante a troca).

**Rollback:** `docker service update --image postgres@sha256:aa6cb33cf4c19c765cefcf48d96176258800870a9a1e930ad126200f9d825139 impinj_postgres` (versão 16.15; mesmo formato de dados). Se houver dano de dados: `pg_restore` do dump acima.

**Imagem nova:** `sha256:81e77a4833487b8132fe4b00bedd39dc3f74b8641e387f19312a48f7de218c2e`.

**Backup:** o banco passou a entrar no backup diário da principal (`backup_all_projects.sh`, 03:00, 14 dias, copiado para fora da VPS pelo `espelhar-dumps.sh`); antes não tinha backup automático.

**Não testado:** fluxo de uso do aplicativo (só saúde do banco e logs dos serviços).

Plano completo, inventário dos outros bancos e backups: `~/dev/solid/docs/seguranca/PLANO-ATUALIZACAO-POSTGRES-2026-11-15.md`.
