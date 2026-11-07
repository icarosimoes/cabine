# Cabine — leitor RFID Impinj

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

`.env` só na VPS (chmod 600): `WEBHOOK_USER`, `WEBHOOK_PASS`, `UI_USER`, `UI_PASS`, `POSTGRES_PASSWORD`,
`KUMA_PUSH_URL` (opcional), `STALE_MINUTES` (padrão 30), `RETENTION_DAYS` (padrão 30).
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
| `POST` | `/api/itens` | Body `{epc, tipo, fabricante?, tamanho?, composicao?, cor?}`. Upsert por EPC (substitui tudo). |
| `GET` | `/api/itens?q=&limit=` | Lista o catálogo (busca por EPC/tipo), já com `localizacao`/`estado` calculados. |
| `GET` | `/api/itens/opcoes` | Valores distintos pra preencher os filtros da tela (tipos, fabricantes, tamanhos, composições, cores, localizações, estados). |
| `GET` | `/api/inventario?localizacao=&estado=&tipo=&fabricante=&tamanho=&composicao=&cor=` | `{total, resumo:[{tipo, quantidade}]}` — contagem agrupada por tipo, com todos os filtros combináveis. |
| `GET` | `/api/estados` | Lista os estados de inventário cadastrados. |
| `POST` | `/api/estados` | Body `{nome, descricao?, comportamento: "ativo"\|"inativo"}`. 409 se nome já existe. |
| `PATCH` | `/api/itens/{epc}/estado` | Body `{estado_id}`. Define o estado de um item do catálogo. 404 se item não cadastrado, 400 se estado inválido. |

## Limitações e riscos conhecidos

- O leitor só envia evento quando lê tag, não há heartbeat. O Kuma avisa "sem eventos há mais de
  `STALE_MINUTES`", o que pode ser só ausência de movimento.
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
