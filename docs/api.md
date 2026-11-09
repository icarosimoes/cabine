# API, fluxos e referencia detalhada — Cabine RFID

> Conteudo movido do README da raiz em 2026-11-07 (padrao: README curto, detalhe em `docs/`).


Recebe o webhook de Event Reporting do leitor Impinj (R700), guarda as leituras de tags (PostgreSQL) e
mostra um painel ao vivo. Opcionalmente manda heartbeat pro Uptime Kuma (monitor Push).

- Webhook: `POST https://cabine.solidsd.com.br/ingest` (basic auth `WEBHOOK_USER`/`WEBHOOK_PASS`)
- Painel: `https://cabine.solidsd.com.br/` (basic auth `UI_USER`/`UI_PASS`)
- Roda na **VPS principal** (Swarm, stack `impinj`, `impinj-stack.yml`) por enquanto.

## Deploy

```bash
rsync -a --exclude .git ./ root@95.111.250.4:/opt/impinj/
ssh root@95.111.250.4
cd /opt/impinj && docker build -t impinj-rfid:local .
bash -c 'set -a; . ./.env; set +a; docker stack deploy -c impinj-stack.yml impinj'
```

`.env` só na VPS (chmod 600): `WEBHOOK_USER`, `WEBHOOK_PASS`, `UI_USER`, `UI_PASS`, `SAIDA_USER`,
`SAIDA_PASS`, `POSTGRES_PASSWORD`, `KUMA_PUSH_URL` (opcional), `STALE_MINUTES` (padrão 30),
`RETENTION_DAYS` (padrão 30), `SAIDA_JANELA_MINUTOS` (padrão 60).
DNS: `cabine.solidsd.com.br` → `95.111.250.4` no Cloudflare.

### Banco de dados (PostgreSQL)

A stack sobe um serviço `postgres:16-alpine` próprio (volume `impinj_pgdata`, só acessível pela rede
interna `impinj-internal`, sem porta exposta pra fora). O app se conecta via `DATABASE_URL`, montada
automaticamente pelo `impinj-stack.yml` a partir de `POSTGRES_PASSWORD`. Para rodar local (fora da
stack), suba um Postgres e exporte `DATABASE_URL=postgresql://usuario:senha@host:5432/banco` — o app
cria as tabelas sozinho no primeiro start (`init_db()`), não precisa rodar migration à parte.

**Risco se já existe um deploy anterior rodando em SQLite**: esta mudança troca o motor de banco —
não existe migração automática de dados do SQLite antigo (`/data/impinj.db`) pro Postgres novo. Se a
VPS já tiver histórico real de leituras em SQLite, ele fica pra trás a menos que seja exportado antes
do primeiro deploy desta versão. Antes de rodar `docker stack deploy` com essa stack nova em cima de
um ambiente que já tinha dados, confirme se isso importa.

## Config no leitor (Event Reporting → Webhook)

Enable Webhook Output, Server URL `https://cabine.solidsd.com.br/ingest`, Port `443`, Verify TLS ligado,
Username/Password = `WEBHOOK_USER`/`WEBHOOK_PASS`. Ligar Enable Retry. O leitor só precisa de saída pra internet.

## Lotes e transferências

O painel permite registrar manualmente a leitura de um lote de enxoval colocado na cabine e depois
transferi-lo para um setor interno (ex. Governança) ou para a lavanderia (fornecedor externo):

- **Registrar leitura**: ao clicar, o sistema pega as EPCs distintas lidas nos últimos N segundos
  (padrão 15s, ajustável na tela) e cria um lote com essa quantidade. Não cria lote se nenhuma tag
  foi lida na janela.
- **Transferir**: cada lote fica "aguardando" até ser transferido para um único destino (setor ou
  lavanderia). Depois de transferido não pode ser transferido de novo — é um registro de auditoria,
  não um estoque editável.
- **Identificadas / Novas**: ao registrar, as EPCs do lote são separadas entre as que já têm cadastro
  no catálogo ("identificadas", com tipo/fabricante/etc. à mostra) e as que não têm ("novas"). Dá pra
  cadastrar uma tag nova direto nessa tela — ela entra no catálogo e migra pra lista de identificadas
  sem recarregar a página. (Inspirado na tela "Cabine" do eTrace, que faz o mesmo em fluxo de sessão
  contínua; aqui ainda é por clique único — ver limitação abaixo.)
- **Destinos**: cadastrados manualmente na tela ("Destinos cadastrados"), com tipo `setor` ou
  `lavanderia`. O banco já nasce com um setor `Governança`. Destinos podem ser desativados (ficam no
  histórico, mas somem da lista de seleção para novas transferências).
- **Relatório/auditoria**: a seção "Transferências" lista todo o histórico, filtrável por tipo,
  destino e período, com a lista de EPCs de cada lote disponível para conferência.

### API (todas exigem basic auth `UI_USER`/`UI_PASS`, exceto `/ingest`)

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/lotes/registrar?janela_segundos=15` | Cria um lote com as EPCs distintas lidas na janela. Retorna também `identificados` (já no catálogo) e `novos` (EPCs sem cadastro). 400 se nenhuma tag foi lida. |
| `GET` | `/api/lotes?status=&limit=` | Lista lotes (filtro opcional por `aguardando`/`transferido`). |
| `GET` | `/api/lotes/{id}` | Detalhe do lote: EPCs, `identificados`/`novos` e transferência (se houver). |
| `POST` | `/api/lotes/{id}/transferir` | Body `{destino_id, observacao?}`. 409 se já transferido, 400 se destino inválido/inativo. |
| `GET` | `/api/destinos?tipo=&ativo=` | Lista destinos. |
| `POST` | `/api/destinos` | Body `{tipo: "setor"\|"lavanderia", nome}`. 409 se nome já existe. |
| `PATCH` | `/api/destinos/{id}` | Body `{ativo: bool}` — ativa/desativa (não apaga histórico). |
| `GET` | `/api/transferencias?destino_id=&tipo=&desde=&ate=&limit=` | Relatório de transferências (`desde`/`ate` em epoch seconds). |

### Modelo de dados

`lotes` (um registro por clique em "Registrar leitura") → `lote_itens` (EPCs daquele lote, N:1) →
no máximo uma `transferencias` por lote (1:1, com `destino_id`). Tudo é *append-only*: nada é
sobrescrito, o que existe é o rastro completo de leitura → transferência. `destinos` é a lista de
setores/lavanderias cadastrados manualmente pela tela (ver `/api/destinos`).

## Inventário e catálogo de itens

Inspirado numa tela de referência (eTrace/Fera Hotéis): mostra o estoque atual agrupado por tipo de
item, com filtros por Localização, Estado, Tipo, Fabricante, Tamanho, Composição e Cor.

- **Catálogo de itens**: o RFID só dá a EPC (um id opaco); pra ter tipo/fabricante/tamanho/composição/cor
  é preciso cadastrar isso manualmente na seção "Catálogo de itens", vinculando esses atributos à EPC.
  Cadastro é *upsert* por EPC: recadastrar uma EPC substitui todos os campos pelo que foi enviado
  dessa vez (campo deixado em branco vira `null`, não mantém o valor anterior). Uma EPC lida pelo
  leitor mas nunca cadastrada no catálogo não aparece no Inventário.
- **Localização** não é campo editável — é calculada a partir da última transferência de cada EPC
  (nome do destino, ou `null`/"–" se o item nunca foi transferido). Mesma lógica de antes, inalterada.
- **Estado** é uma entidade própria e independente de localização (`estados_inventario`), igual ao
  conceito "Estados de Inventário" do eTrace — **não** é mais derivado de "para onde foi a última
  transferência". Um item pode estar "Em manutenção" fisicamente na Rouparia Geral, por exemplo. Todo
  item novo nasce no estado marcado como `padrao` (semeado como "Em uso"); trocar o estado é uma ação
  manual separada de "Movimentar" (botão "Definir" na seção "Catálogo de itens", equivalente ao
  "DEFINIR ESTADO" do eTrace), e recadastrar o item (mudar tipo/fabricante/etc.) não reseta o estado.
  Estados são cadastráveis na seção "Estados de inventário cadastrados" (nome, descrição,
  comportamento `ativo`/`inativo`); o banco já nasce com os 4 do eTrace: Em uso, Em manutenção,
  Vendido, Descartado.

### Dados de exemplo

`scripts/seed_exemplo.py` popula destinos (Apartamentos, Rouparia Geral, Piscina) e catálogo (9
itens com tipos/atributos vistos nos prints de referência do eTrace) via HTTP, contra qualquer
instância rodando — local ou já deployada. Simula leitura + transferência de dois grupos de itens
(deixa o resto como "Sem leitura" de propósito, pra mostrar o inventário com estados variados).
Idempotente: pode rodar de novo sem duplicar destino ou item de catálogo (cada rodada só soma mais
histórico de leitura, que é esperado).

```bash
BASE_URL=http://localhost:8000 UI_USER=... UI_PASS=... WEBHOOK_USER=... WEBHOOK_PASS=... \
  python3 scripts/seed_exemplo.py
```

### API adicional

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/itens` | Body `{epc, tipo, fabricante?, tamanho?, composicao?, cor?, data_compra?}` (data AAAA-MM-DD ou DD/MM/AAAA). EPC deve ser hex, par, 4-64 caracteres (400 se não). Upsert por EPC (substitui tudo). |
| `POST` | `/api/itens/editar-lote` | Body `{epcs[], tipo?, fabricante?, tamanho?, composicao?, cor?, data_compra?, estado_id?}`. Só altera os campos enviados. Devolve `{alterados, nao_encontrados}`. Operador. |
| `POST` | `/api/itens/consulta` | Body `{epcs[]}` (até 2000). Devolve `{identificados, novos, invalidos}`. Leitor manual. |
| `POST` | `/api/itens/cadastrar-lote` | Body `{epcs[], tipo, ...atributos}`. Cadastra os novos; já existentes não mudam. Devolve `{cadastrados, ja_existiam, invalidos}`. Operador. |
| `GET` | `/api/inventario/revisao?dias=30` | EPCs cadastrados fora do padrão e tags lidas sem cadastro (`lidas_sem_cadastro`). |
| `GET` | `/api/conferencias/{id}/export.csv` | CSV da conferência (epc, item, situação, conferido_em). |
| `GET` | `/api/itens?q=&limit=` | Lista o catálogo (busca por EPC/tipo), já com `localizacao`/`estado` calculados. |
| `GET` | `/api/itens/opcoes` | Valores distintos pra preencher os filtros da tela (tipos, fabricantes, tamanhos, composições, cores, localizações, estados). |
| `GET` | `/api/inventario?localizacao=&estado=&tipo=&fabricante=&tamanho=&composicao=&cor=` | `{total, resumo:[{tipo, quantidade}]}` — contagem agrupada por tipo, com todos os filtros combináveis. |
| `GET` | `/api/estados` | Lista os estados de inventário cadastrados. |
| `POST` | `/api/estados` | Body `{nome, descricao?, comportamento: "ativo"\|"inativo"}`. 409 se nome já existe. |
| `PATCH` | `/api/itens/{epc}/estado` | Body `{estado_id}`. Define o estado de um item do catálogo. 404 se item não cadastrado, 400 se estado inválido. |

## Controle de evasão (saídas não documentadas)

Leitores RFID fixos nas saídas do prédio (fisicamente separados da cabine, ligados na rede local
`192.168.30.x`) detectam qualquer EPC passando por ali. Toda detecção é cruzada com o histórico de
`transferencias`: se a EPC tiver sido transferida pro mesmo destino (ex. "Saída Serviço") dentro da
janela de `SAIDA_JANELA_MINUTOS` (padrão 60min), a saída é `documentado`; senão, é um alerta de
possível evasão não documentada.

- **`bridge-saida/`**: programa em Go que roda localmente, na mesma rede dos leitores (a VPS não
  alcança `192.168.30.x`), e manda cada detecção pro Cabine via `POST /api/saida`. **O protocolo do
  leitor físico (ex. Acura EdgeR30) ainda não é conhecido** — não implementei um parser especulativo;
  o programa hoje só conecta, loga os bytes crus recebidos (serve de ferramenta de captura) e nunca
  chama a API enquanto não houver uma captura real pra basear o parsing. Ver `bridge-saida/README.md`.
- **Credencial própria** (`SAIDA_USER`/`SAIDA_PASS`), separada de `UI_USER` e `WEBHOOK_USER` — se a
  máquina local da ponte for comprometida, não dá acesso ao painel nem ao ingest do leitor da cabine.
- Tela "Alertas de evasão": lista `eventos_saida` (toda detecção, documentada ou não), filtrável por
  destino/período/status, com o não-documentado destacado em vermelho.

### API da ponte de saída

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/saida` | Body `{epc, destino_id}`. Basic auth `SAIDA_USER`/`SAIDA_PASS`. Cruza com `transferencias` e grava em `eventos_saida`. |
| `GET` | `/api/saida?documentado=&destino_id=&desde=&ate=&limit=` | Lista eventos de saída (basic auth `UI_USER`/`UI_PASS`). |

## Limitações e riscos conhecidos

- O leitor só envia evento quando lê tag, não há heartbeat. O Kuma avisa "sem eventos há mais de
  `STALE_MINUTES`", o que pode ser só ausência de movimento.
- **`bridge-saida/` não lê o leitor físico de saída ainda.** O protocolo do leitor (Acura EdgeR30 na
  porta 8080, visto nos prints de referência) é desconhecido; o programa só loga bytes crus pra servir
  de captura. `/api/saida` e a tela de alertas funcionam e foram testados, mas ninguém chama eles de
  verdade até o parser ser escrito a partir de uma captura real de tráfego.
- **A janela de "Registrar leitura" (padrão 15s) não foi validada com o leitor físico.** Ela assume
  que tags paradas na cabine continuam sendo relidas nesse intervalo. Se isso não acontecer (ou se a
  antena capturar tags de fora da cabine), a quantidade do lote sai errada. Testar com material real
  antes de confiar no número antes de depender dele para auditoria.
- `main → deploy` não é automático: dar merge nesta branch só atualiza o histórico do git. Subir pra
  produção ainda exige os passos manuais da seção **Deploy** (rsync + build + `docker stack deploy`
  na VPS).
- **Modelo de leitura ainda é por clique único**, não sessão contínua automática como o eTrace (que
  detecta sozinho início/fim de sessão por cabine, classifica resultado — inclusive "leitor sem
  comunicação" — e mantém histórico de sessões). Migrar pra esse modelo é mudança de arquitetura, não
  feita ainda; ver conversa do projeto antes de decidir se vale a pena.

## Perfis, painel, agentes, alertas e PWA (adicionado em 2026-11-08)

### Perfis de acesso

`painel` < `leitura` < `operador` < `admin` (`PAPEIS` em `app/main.py`). `painel_auth` (qualquer usuario logado) vale so para `GET /api/dashboard`,
`/api/summary` e `/api/me`; `ui_auth` exige >= `leitura` (403 "seu perfil so acessa o Painel" para `painel`), `op_auth` >= `operador`, `admin_auth` = `admin`.
Rota nova em `/api/*` deve usar uma dessas dependencias; o teste `test_nenhuma_rota_get_da_api_abre_para_o_perfil_painel` falha se alguma rota GET abrir para o `painel`.
O papel vale na hora (lido do banco a cada requisicao); desativar o usuario derruba a sessao. Login: `POST /api/login` (cookie `cabine_sessao`, HMAC com `SESSION_SECRET`, 12 h) ou HTTP Basic.

### `GET /api/dashboard`

Uma chamada para o Painel: `antenas[]` (`nome, descricao, modelo, ip, ambiente, evasao, alerta, online` = `true|false|null` (null = sem informacao recente do agente),
`latencia_ms, detalhe, verificado_em, agente, extra` so com campos seguros: `firmware, uptime, serial, preset, webhook`), `agentes[]` (`online, versao, host, visto_em`),
`resumo` (contagens e `problemas[]`), `alertas` (`ativos`, `enviados_24h`, `falhas_24h`, `ultimos[]` sem numero de telefone), `cabine` (lotes/pecas aguardando, transferencias e pecas de hoje, ultimas),
`evasao` (24 h), `inventario` (total e por estado), `conferencia` (ultima). "Hoje" usa `TZ_OFFSET_HOURS`.

### Leitura sob demanda

O `/ingest` responde 200 sempre, mas so **grava** em `events` com leitura armada (`POST /api/leitura/armar?segundos=N`, usado por "Registrar leitura") ou conferencia em andamento
(`stored:0` caso contrario). Mesmo sem gravar, guarda o "ultimo sinal" em memoria (indicador "radio: enviando sinal" e alerta de sem sinal).

### Agente do hotel

- Admin cria o agente (`POST /api/agentes {nome}`): devolve o token **uma unica vez**; o servidor guarda so o SHA-256. Varios agentes podem existir; `DELETE /api/agentes/{id}` remove.
- `GET /api/agente/config` (header `X-Agent-Token`): intervalo, antenas com IP/porta/modelo/`evasao`. `POST /api/agente/status`: o agente envia `versao, host` e o estado de cada antena
  (`id, online, latencia_ms, detalhe, extra`) e recebe os comandos pendentes. Regra com varios agentes: o "online" de um agente em dia nao e sobrescrito pelo "offline" de outro;
  o offline so vale se o registro atual for do mesmo agente, ja offline, sem dono ou mais velho que `AGENTE_SEM_CONTATO_SEGUNDOS` (120).
- Comandos ao radio: `POST /api/antenas/{id}/comandos {metodo GET|PUT|POST, caminho /api/v1/..., corpo JSON}` (admin; caminho validado, sem `..`), o agente executa na LAN e devolve em
  `POST /api/agente/comandos/{id}/resultado`; consulta em `GET /api/comandos/{id}` (`pendente|enviado|ok|erro|expirado`; expira em 120 s). O agente revalida o allowlist.
- Checagem do agente: R700 = `GET /api/v1/status` (credencial `root`/senha configurada no agente); Acura EDGE30R = pagina de status na porta 80 (firmware, uptime, status). **A porta de dados (8080) nao e sondada**
  (o Acura aceita so 2 clientes). Online = o host responde na rede. Detalhes e testes: [infra/agente-hotel.md](infra/agente-hotel.md).

### Alertas por WhatsApp (Evolution API)

- Config no painel (admin): `GET/PUT /api/config/alertas` (`wa_url, wa_instance, wa_apikey` so de escrita, `ativo, sem_sinal_seg, falhas, repetir_min`; os padroes vem das variaveis `WHATSAPP_*` e `ALERTA_*`).
  Destinatarios (pessoa ou grupo `...@g.us`) em `/api/alertas/destinatarios` (+ `/{id}/teste`), busca de grupos em `/api/alertas/grupos` (+ `/atualizar`, em segundo plano), historico em `/api/alertas/envios`.
- Tipos: `sem_sinal`, `falhas` (so memoria), `antena_offline` (inclui a retomada "voltou"), `antena_online` (retomada de cobertura), `agente_offline`, `webhook_falha`, divergencia de movimentacao.
  Antenas de evasao: prefixo "🚨 EVASAO", 1 falha ja marca offline, alertam mesmo com o alerta desligado. Lembrete a cada `repetir_min`.
- Estado: `state["alertas"]` (memoria) espelhado em `alerta_estado` para as chaves `antena:`, `webhook:`, `agente:`, `cobertura:` — restart/deploy nao reenvia. Sem dado fresco do agente o estado e preservado
  (nada de "voltou" falso). `cobertura:ID` marca antena que ficou sem nenhum agente; quando algum volta a ve-la online sai "antena X esta ONLINE (agente Y)".
- "enviado" em `alerta_envios` significa que a Evolution aceitou a mensagem (nao garante leitura).

### PWA

Arquivos publicos em `app/static/`: `manifest.webmanifest` (standalone, start `/#painel`), `sw.js` (escopo `/`, `no-cache`), `offline.html`, `icons/` (`scripts/gerar_icones.py`).
O service worker **nao** guarda API nem paginas do painel — so icones e a pagina offline. Mudou o SW: incremente `VERSAO` em `sw.js`.

### Variaveis de ambiente (resumo)

Obrigatorias: `DATABASE_URL`, `WEBHOOK_USER/PASS`, `UI_USER/PASS`, `SAIDA_USER/PASS`, `SESSION_SECRET` (>= 32). Opcionais: `SESSION_HORAS`, `RETENTION_DAYS`, `TZ_OFFSET_HOURS`, `STALE_MINUTES`,
`KUMA_PUSH_URL`, `SAIDA_JANELA_MINUTOS`, `SESSAO_AUTO`, `WHATSAPP_API_URL/INSTANCE/APIKEY`, `ALERTA_SEM_SINAL_SEGUNDOS`, `ALERTA_FALHAS_INGEST`, `ALERTA_REPETIR_MINUTOS`, `AGENTE_SEM_CONTATO_SEGUNDOS`, `AGENTE_DIST`.
No `impinj-stack.yml` nao use parenteses nem espacos extras no texto de `${VAR:?mensagem}` (quebra a interpolacao do `docker stack deploy`).

| `GET` | `/api/auditoria/logins?usuario=&resultado=&dias=30&limit=300` | Admin. Tentativas de login (`ok`/`falha`/`bloqueado`) com IP e navegador, mais `resumo`. Versao CSV em `/api/auditoria/logins.csv`. Senha nunca e guardada. |
