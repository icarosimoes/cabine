# Memoria do Projeto Cabine RFID

Data de referencia: 2026-11-07.

## Decisoes principais

- **Sem build de frontend**: o painel e um `index.html` unico (CSS+JS puro) servido pelo FastAPI. Mesmo
  espirito do `hotspot` (CSS puro, sem Tailwind). Nao introduzir framework so para o menu.
- **Auth = HTTP Basic com tres credenciais separadas** (`UI_*`, `WEBHOOK_*`, `SAIDA_*`). Se a maquina da
  ponte for comprometida, nao da acesso ao painel nem ao ingest da cabine. Nao reaproveitar credencial.
- **PostgreSQL desde 2026-11-07** (era SQLite). Servico `postgres` na propria stack, rede interna
  `impinj-internal`, sem porta publicada. `init_db()` cria as tabelas sozinho (sem Alembic) e tenta ate
  60s esperar o Postgres subir (Swarm nao garante ordem).
- **Lotes/transferencias sao append-only**: transferencia e 1:1 com lote (`UNIQUE lote_id`); corrida de
  duplo clique vira 409, nao 500. O que existe e trilha de auditoria, nada e editado depois.
- **Estado de inventario independe de localizacao** (como "Estados de Inventario" do eTrace). Item novo
  nasce no estado `padrao`; recadastrar item nao reseta estado.
- **Evasao**: saida e `documentado` se a EPC foi transferida ao mesmo destino dentro de
  `SAIDA_JANELA_MINUTOS` (60). O protocolo do leitor de saida (Acura EdgeR30) e desconhecido; a ponte so
  loga bytes crus, sem parser especulativo.
- **Deploy manual** (rsync + build local na VPS + `docker stack deploy`), sem registry nem CI, diferente
  de erpsolid/hotspot (GHCR + GitHub Actions). Ver `infra/deploy.md` e o backlog.
- Sem dado ficticio no painel: onde nao ha dado a tela mostra vazio/"–".

## Convencoes

- Textos de UI em pt-BR; tabelas/rotas de dominio em pt-BR; termos do leitor/evento em ingles (Impinj).
- Timestamps em epoch seconds (`DOUBLE PRECISION`); a UI formata em pt-BR no navegador.
- Imagem `impinj-rfid:local` buildada na VPS; servico `impinj_impinj` (nome legado mantido para nao
  quebrar stack/volume/Traefik).
