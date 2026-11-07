# Backlog — Cabine RFID

## Riscos conhecidos (nao resolvidos)

- **Janela de "Registrar leitura" (15s) nao validada com o leitor fisico.** Se tags paradas nao forem
  relidas nesse intervalo, ou a antena pegar tags de fora da cabine, a quantidade do lote sai errada.
- **Leitor de saida sem parser**: protocolo do Acura EdgeR30 desconhecido; `bridge-saida/` so captura
  bytes. `/api/saida` esta pronta mas ninguem a chama de verdade.
- **Sem heartbeat do leitor**: o Kuma avisa "sem eventos ha X min", que pode ser so ausencia de movimento.
- **Sem backup automatico do Postgres** (so o volume `impinj_pgdata`). Definir `pg_dump` agendado.

- **Inventario importado precisa de revisao** (2026-11-07): 8 EPCs fora do padrao hex (`3`, `300F`,
  `???`, um com dois EPCs colados, truncados) e 85 tags lidas pelo leitor que nao estao no CSV
  (aparecem como "novas" na leitura). Localizacao (`Lavanderia`, `Apartamentos`...) e `Data_Compra` do CSV
  nao foram importadas; decidir se vale um campo de data de compra.
- **Falta validacao do formato de EPC na API**: `/api/itens` e `/api/saida` aceitam qualquer texto; um EPC
  digitado em minusculo ou base64 nao casa com as leituras. Considerar normalizar com `epc_hex()`.

## Ideias

- Resumo de leituras por hora (retido para sempre) + export CSV, com `RETENTION_DAYS` menor para eventos
  brutos (rascunho no stash `wip-main-py-resumo-hora-csv`).
- Sessao de leitura continua por cabine (modelo eTrace) em vez de clique unico.
- CI/CD (GitHub Actions + GHCR) no padrao erpsolid/hotspot, no lugar do deploy manual por rsync.
- Login com usuarios/papeis em vez de Basic Auth unico, se mais de uma pessoa operar.
- Testes automatizados (pytest contra Postgres descartavel); hoje nao ha suite.
- Cadastrar o projeto em `erpsolid/docs/projetos/` (indice central de repos).
