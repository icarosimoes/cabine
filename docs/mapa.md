# Mapa do Projeto Cabine RFID

Data de referencia: 2026-11-07.

## Visao geral

Recebe o webhook de Event Reporting do leitor Impinj R700 (`POST /ingest`), guarda as leituras de tags
e oferece um painel para: registrar a leitura de um lote de enxoval na cabine, transferi-lo a um destino
(setor ou lavanderia), manter catalogo/inventario de itens e detectar saidas nao documentadas.

```text
Leitor R700 --webhook--> /ingest --> events (PostgreSQL)
Painel (navegador) --basic auth--> /api/*  --> lotes, transferencias, itens, estados, destinos
Ponte local (Go) --basic auth SAIDA--> /api/saida --> eventos_saida (cruza com transferencias)
```

## Estrutura do repositorio

```text
app/main.py               FastAPI: webhook, API, lifespan (init_db, heartbeat Kuma, retencao)
app/static/index.html     Painel (sidebar + views por hash, JS puro)
bridge-saida/             Ponte Go (captura de bytes do leitor de saida; ainda sem parser)
scripts/seed_exemplo.py   Dados de exemplo via HTTP
scripts/migrar_sqlite.py  Copia events do SQLite antigo para o Postgres
scripts/normalizar_epc.py Converte EPCs base64 ja gravados em events para hex
scripts/importar_inventario_csv.py  Importa CSV do eTrace (Item;Localizacao;RfidTag;...) para itens
impinj-stack.yml          Stack Swarm (postgres + impinj)
docs/                     Esta documentacao
.claude/skills/           Skills Jarvis (copia de docs/agentes)
```

## Menu do painel (`#hash`)

Padrao de sidebar do `erpsolid`/`hotspot` (tokens navy `#0f1623` / teal `#14b8a6`, tema claro/escuro,
topbar + drawer no celular). Itens agrupados por tarefa, nao em lista unica:

| Grupo | Item | Hash |
| --- | --- | --- |
| Visao geral | Painel | `#painel` |
| Operacao | Leitura da cabine (registrar + lotes aguardando) | `#leitura` |
| Operacao | Transferencias (auditoria) | `#transferencias` |
| Controle | Alertas de evasao (badge com nao documentados) | `#evasao` |
| Cadastros | Inventario | `#inventario` |
| Cadastros | Catalogo de itens | `#catalogo` |
| Cadastros | Destinos | `#destinos` |
| Cadastros | Estados de inventario | `#estados` |
| Leitor | Tags lidas | `#tags` |
| Leitor | Eventos ao vivo | `#eventos` |

Cada tela carrega so os dados dela (polling de 3s apenas da view aberta + resumo/badge).

## Modelo de dados (PostgreSQL)

`events` (leituras brutas, expiram por `RETENTION_DAYS`) · `destinos` (setor|lavanderia) ·
`lotes` -> `lote_itens` (EPCs) -> 0..1 `transferencias` (append-only) · `estados_inventario` ·
`itens` (catalogo por EPC) · `eventos_saida` (deteccoes nas saidas, `documentado` 0/1).
Localizacao do item = destino da ultima transferencia do lote que o continha (`ITENS_VIEW_SQL`).

## Rotas

Auth: `/ingest` = `WEBHOOK_*`; `POST /api/saida` = `SAIDA_*`; demais `/api/*` e `/` = `UI_*`; `/health` aberto.

| Area | Rotas |
| --- | --- |
| Leitor | `POST /ingest`, `GET /api/summary`, `/api/tags`, `/api/events` |
| Lotes | `POST /api/lotes/registrar`, `GET /api/lotes`, `GET /api/lotes/{id}`, `POST /api/lotes/{id}/transferir` |
| Destinos | `GET/POST /api/destinos`, `PATCH /api/destinos/{id}` |
| Transferencias | `GET /api/transferencias` |
| Itens/estados | `POST/GET /api/itens`, `GET /api/itens/opcoes`, `PATCH /api/itens/{epc}/estado`, `GET/POST /api/estados`, `GET /api/inventario` |
| Evasao | `POST /api/saida`, `GET /api/saida` |

Payloads, codigos de erro e fluxos: [api.md](api.md) e `app/main.py`.
