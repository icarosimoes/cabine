# Mapa do Projeto Cabine RFID

Data de referencia: 2026-11-08.

## Visao geral

Recebe o webhook de Event Reporting do leitor Impinj R700 (`POST /ingest`), guarda as leituras de tags
e oferece um painel para: registrar a leitura de um lote de enxoval na cabine, transferi-lo a um destino
(setor ou lavanderia), manter catalogo/inventario de itens e detectar saidas nao documentadas.

```text
Leitor R700 --webhook--> /ingest --> events (PostgreSQL)
Painel (navegador) --basic auth--> /api/*  --> lotes, transferencias, itens, estados, destinos
Ponte local (Go) --basic auth SAIDA--> /api/saida --> eventos_saida (cruza com transferencias)
Agente do hotel (Go, Windows) --X-Agent-Token--> /api/agente/* --> antena_status, comandos (status e config dos radios na LAN)
Video da evasao: /api/saida -> clipes -> agente (status) -> DVR Intelbras (ffmpeg/RTSP) -> PUT /api/agente/clipes/{id}/arquivo -> player na tela Alertas de evasao
Alertas: avaliar_alertas/avaliar_agentes --> Evolution API (WhatsApp) --> grupo/numeros cadastrados
PWA: /manifest.webmanifest, /sw.js, /offline.html, /icons/* (publicos; o painel e a API continuam com login)
```

## Estrutura do repositorio

```text
app/main.py               FastAPI: webhook, API, lifespan (init_db, heartbeat Kuma, retencao)
app/static/index.html     Painel (sidebar + views por hash, JS puro, responsivo, PWA)
app/static/login.html     Tela de login
app/static/{manifest.webmanifest,sw.js,offline.html,icons/}  PWA (icones gerados por scripts/gerar_icones.py)
agent/                    Agente do hotel em Go (servico + tray + instalador); build: agent/build.sh (versao em agent/VERSION)
bridge-saida/             Ponte Go (captura de bytes do leitor de saida; ainda sem parser)
tests/                    pytest contra Postgres descartavel (conftest exige DATABASE_URL)
.github/workflows/ci.yml  Testes em todo push/PR; deploy na VPS so com os secrets VPS_HOST/VPS_SSH_KEY
scripts/seed_exemplo.py   Dados de exemplo via HTTP
scripts/migrar_sqlite.py  Copia events do SQLite antigo para o Postgres
scripts/normalizar_epc.py Converte EPCs base64 ja gravados em events para hex
scripts/seed_fera_palace.py  Cadastros do cliente Fera Palace (ambientes, antenas, acomodacoes)
scripts/importar_inventario_csv.py  Importa CSV do eTrace (Item;Localizacao;RfidTag;...) para itens
scripts/seed_usuarios_fera.py  Usuarios do cliente
scripts/gerar_icones.py   Gera os icones do PWA (Pillow)
scripts/rotacionar-chave-ci.sh  Cria/troca a chave SSH do CI e os secrets do GitHub (ver infra/deploy.md)
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
| Operacao | Conferencia de estoque | `#conferencia` |
| Operacao | Transferencias (auditoria) | `#transferencias` |
| Controle | Alertas de evasao (badge com nao documentados) | `#evasao` |
| Cadastros | Inventario | `#inventario` |
| Cadastros | Catalogo de itens | `#catalogo` |
| Cadastros | Destinos (ambientes, com alerta) | `#destinos` |
| Cadastros | Antenas (leitor -> ambiente) | `#antenas` |
| Cadastros | Acomodações (enxoval por UH) | `#acomodacoes` |
| Cadastros | Estados de inventario | `#estados` |
| Leitor | Tags lidas | `#tags` |
| Leitor | Eventos ao vivo | `#eventos` |
| Administracao (admin) | Usuarios | `#usuarios` |
| Administracao (admin) | Alertas WhatsApp (config, destinatarios, grupos, historico) | `#alertaswa` |

O perfil `painel` ve so o item Painel. Em telas estreitas (<640px) as tabelas do Painel viram cartoes (`table.rt` + `data-l`).

Cada tela carrega so os dados dela (polling de 3s apenas da view aberta + resumo/badge).

## Modelo de dados (PostgreSQL)

`events` (leituras brutas, expiram por `RETENTION_DAYS`) · `destinos` (setor|lavanderia) ·
`lotes` -> `lote_itens` (EPCs) -> 0..1 `transferencias` (append-only) · `estados_inventario` ·
`conferencias` -> `conferencia_itens` (snapshot dos EPCs esperados) · `antenas` (leitor -> ambiente) · `acomodacoes` -> `acomodacao_itens` (qtd por UH x fluxo) · `destinos.alerta` · `itens` (catalogo por EPC) · `usuarios` (papel painel|leitura|operador|admin) · `sessoes` · `leituras_hora` (agregado) · `config` (WhatsApp/limiares) · `alerta_destinatarios` · `alerta_envios` · `alerta_estado` (alertas de antena/agente ja enviados) · `agentes` (token so como hash) · `antena_status` (1 linha por antena) · `comandos` (fila para o agente) · `antenas.{descricao,modo,alerta,ip,porta,evasao}` · `eventos_saida` (deteccoes nas saidas, `documentado` 0/1, `antena_id`, `clipe_id`) · `cameras` (canal de DVR; sem senha) -> `clipes` (video 10 s antes/60 s depois de uma saida; mp4 em `CLIPES_DIR`) · `antenas.camera_id`.
Localizacao do item = destino da ultima transferencia do lote que o continha (`ITENS_VIEW_SQL`).

## Rotas

Auth: `/ingest` = `WEBHOOK_*`; `POST /api/saida` = `SAIDA_*`; `/api/agente/{config,status,comandos/..}` = `X-Agent-Token`;
demais `/api/*` e `/` = login (cookie de sessao ou Basic) com a **permissao** da rota (tabela `perfis`: dashboard, consultar, operar_cabine, excluir_lotes, catalogo_editar, conferencia, cadastros, exportar, alertas_whatsapp, usuarios, auditoria, sistema; o perfil `admin` e fixo e tem todas; `/api/summary` e `/api/me` valem para qualquer logado; detalhes em `docs/api.md`). Abertos: `/health`, `/login`, `/api/login`,
`/api/logout` e os arquivos PWA. Falhas de login/Basic/webhook entram no rate limit (8 em 5 min por IP; usa o ultimo IP do `X-Forwarded-For`).

| Area | Rotas |
| --- | --- |
| Leitor | `POST /ingest`, `GET /api/summary`, `/api/tags`, `/api/events` |
| Lotes | `POST /api/lotes/registrar`, `GET /api/lotes`, `GET /api/lotes/{id}`, `POST /api/lotes/{id}/transferir` |
| Destinos | `GET/POST /api/destinos`, `PATCH /api/destinos/{id}` |
| Transferencias | `GET /api/transferencias` |
| Itens/estados | `POST/GET /api/itens`, `GET /api/itens/opcoes`, `PATCH /api/itens/{epc}/estado`, `GET/POST /api/estados`, `GET /api/inventario` |
| Evasao | `POST /api/saida`, `GET /api/saida` |
| Conferencia | `POST /api/conferencias` (snapshot por filtros, 1 em andamento), `GET /api/conferencias`, `GET /api/conferencias/{id}?somente=`, `POST /api/conferencias/{id}/finalizar` |
| Antenas | `GET/POST /api/antenas` (upsert por nome) |
| Acomodacoes/par | `GET/POST /api/acomodacoes` (upsert por nome, substitui itens), `GET /api/enxoval/esperado` |

| Painel | `GET /api/dashboard` (antenas+status, agentes, alertas, inventario por estado, cabine, evasao, conferencia) |
| Leitura sob demanda | `POST /api/leitura/armar?segundos=N` (o `/ingest` so grava com leitura armada ou conferencia em andamento) |
| Lotes (extra) | `DELETE /api/lotes/{id}` (reiniciar), `POST /api/lotes/{id}/estado`, `POST /api/lotes/{id}/divergencia`, `GET /api/transferencias/{id}/comprovante` |
| Antenas (extra) | `PUT /api/antenas/{id}` (edita, inclui `evasao`), `POST /api/antenas/{id}/comandos`, `GET /api/comandos/{id}` |
| Agentes | `GET/POST/DELETE /api/agentes` (token aparece so na criacao), `GET /api/agente/download/{arquivo}` (admin), `GET /api/agente/config`, `POST /api/agente/status`, `POST /api/agente/comandos/{id}/resultado` |
| Alertas WhatsApp | `GET/PUT /api/config/alertas`, `GET/POST/PUT/DELETE /api/alertas/destinatarios`, `POST /api/alertas/destinatarios/{id}/teste`, `GET /api/alertas/grupos`, `POST /api/alertas/grupos/atualizar`, `GET /api/alertas/envios` |
| Usuarios/sessao | `POST /api/login`, `POST /api/logout`, `GET /api/me`, `GET/POST /api/usuarios`, `PATCH /api/usuarios/{id}`, `GET /api/sessoes` |
| Relatorios | `GET /api/leituras-hora`, `GET /api/export/{leituras,inventario,transferencias}.csv` |
| PWA (publicas) | `GET /manifest.webmanifest`, `/sw.js`, `/offline.html`, `/icons/{nome}` |

Payloads, codigos de erro e fluxos: [api.md](api.md) e `app/main.py`.
