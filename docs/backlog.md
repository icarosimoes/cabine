# Backlog — Cabine RFID

## Riscos conhecidos (nao resolvidos)

- **Janela de "Registrar leitura" (15s) nao validada com o leitor fisico.** Se tags paradas nao forem
  relidas nesse intervalo, ou a antena pegar tags de fora da cabine, a quantidade do lote sai errada.
- **Leitor de saida sem parser**: protocolo do Acura EdgeR30 desconhecido; `bridge-saida/` so captura
  bytes. `/api/saida` esta pronta mas ninguem a chama de verdade.
- **Sem heartbeat do leitor**: o Kuma avisa "sem eventos ha X min", que pode ser so ausencia de movimento.
- **Sem backup automatico do Postgres** (so o volume `impinj_pgdata`). Definir `pg_dump` agendado.

## Ideias

- Resumo de leituras por hora (retido para sempre) + export CSV, com `RETENTION_DAYS` menor para eventos
  brutos (rascunho no stash `wip-main-py-resumo-hora-csv`).
- Sessao de leitura continua por cabine (modelo eTrace) em vez de clique unico.
- CI/CD (GitHub Actions + GHCR) no padrao erpsolid/hotspot, no lugar do deploy manual por rsync.
- Login com usuarios/papeis em vez de Basic Auth unico, se mais de uma pessoa operar.
- Testes automatizados (pytest contra Postgres descartavel); hoje nao ha suite.
- Cadastrar o projeto em `erpsolid/docs/projetos/` (indice central de repos).
