# Registro de Trabalho — Cabine RFID

## 2026-11-07

### Receptor de webhook + painel (commits `74456cb`, `95b2efa`)

- Webhook Impinj R700, painel ao vivo, heartbeat Uptime Kuma, dominio `cabine.solidsd.com.br`.

### Lotes, inventario, evasao, Postgres (branch `claude/leitura-cabine-enxoval-1gu2i7`)

- Lotes de leitura, destinos, transferencias com auditoria; catalogo/inventario com estados proprios;
  `POST /api/saida` + `bridge-saida/` (Go); migracao SQLite -> PostgreSQL. Mesclada em `main`.

### Revisao + padronizacao

- Revisao do codigo mesclado. Correcoes: `init_db` com retry (Postgres sobe depois do app no Swarm);
  `/ingest` grava em thread (nao bloqueia o event loop); duplo clique em "Transferir" devolve 409 em vez
  de 500; `/api/saida` rejeita EPC vazio.
- Painel reorganizado em sidebar agrupada (padrao erpsolid/hotspot), tema claro/escuro, responsivo.
  Validado com screenshot headless contra Postgres local (painel e alertas com dados reais).
- `docs/`, `.claude/skills/` e `scripts/migrar_sqlite.py` criados no padrao dos outros sistemas.
- Alteracoes locais antigas nao commitadas (resumo por hora, export CSV, `TZ_OFFSET_HOURS`, retencao de
  7 dias) estavam pela metade e baseadas em SQLite; ficaram em `git stash`
  (`wip-main-py-resumo-hora-csv`). Retomar sobre o codigo Postgres se ainda forem desejadas.
- Producao tinha 20.937 eventos / 236 EPCs em SQLite (ver `infra/deploy.md` para a migracao).
- **Aberto:** validar com material real a janela de leitura da cabine; sem suite de testes.
