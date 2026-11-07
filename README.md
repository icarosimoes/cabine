# Cabine — leitor RFID Impinj

Recebe o webhook de Event Reporting do leitor Impinj (R700), guarda as leituras de tags (SQLite) e
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

`.env` só na VPS (chmod 600): `WEBHOOK_USER`, `WEBHOOK_PASS`, `UI_USER`, `UI_PASS`, `KUMA_PUSH_URL` (opcional),
`STALE_MINUTES` (padrão 30), `RETENTION_DAYS` (padrão 30). DNS: `cabine.solidsd.com.br` → `95.111.250.4` no Cloudflare.

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
- **Destinos**: cadastrados manualmente na tela ("Destinos cadastrados"), com tipo `setor` ou
  `lavanderia`. O banco já nasce com um setor `Governança`. Destinos podem ser desativados (ficam no
  histórico, mas somem da lista de seleção para novas transferências).
- **Relatório/auditoria**: a seção "Transferências" lista todo o histórico, filtrável por tipo,
  destino e período, com a lista de EPCs de cada lote disponível para conferência.

### API (todas exigem basic auth `UI_USER`/`UI_PASS`, exceto `/ingest`)

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/lotes/registrar?janela_segundos=15` | Cria um lote com as EPCs distintas lidas na janela. 400 se nenhuma tag foi lida. |
| `GET` | `/api/lotes?status=&limit=` | Lista lotes (filtro opcional por `aguardando`/`transferido`). |
| `GET` | `/api/lotes/{id}` | Detalhe do lote: EPCs e transferência (se houver). |
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
