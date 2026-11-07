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
- **Deploy em producao (2026-11-07):** backup do SQLite em `/root/backup/impinj-20261107.db` e
  `impinj-final-20261107.db` (+ copia do `.env`); `SAIDA_USER`/`SAIDA_PASS`/`POSTGRES_PASSWORD` adicionados ao
  `.env` da VPS; rsync + build + `docker stack deploy`. 25.626 eventos migrados para o Postgres. O app
  esperou ~10s o Postgres subir (retry funcionou); houve ~1-2 min de `/ingest` indisponivel durante a
  troca (leitor tem retry). Verificado: `/health` 200, UI 200, sem auth 401, leitor gravando.
  Volume SQLite antigo `impinj_impinj_data` mantido como backup.
- **Aberto:** validar com material real a janela de leitura da cabine; sem suite de testes.

### Normalizacao de EPC + importacao do inventario (2026-11-07)

- O R700 manda `epc` em base64 (sem `epcHex`); o app guardava assim e nada casava com o catalogo (hex).
  `epc_hex()` em `app/main.py` converte para hex maiusculo no `/ingest`; `scripts/normalizar_epc.py`
  converteu 237 EPCs ja gravados em `events`.
- `scripts/importar_inventario_csv.py`: 4.570 itens do CSV do eTrace importados (951 como Descartado,
  3.619 Em uso). Localizacao e Data_Compra nao importadas (localizacao e derivada de transferencias).
  8 EPCs fora do padrao hex foram importados mesmo assim (ex.: `3`, `300F`, `???`) a pedido do usuario.
- Conferencia: 152 dos 237 EPCs lidos pelo leitor existem no catalogo; 85 aparecem como "novas".
  **Aberto:** revisar os 8 EPCs fora do padrao e as 85 tags lidas que nao estao no CSV.

### Cadastros do Fera Palace + par de enxoval (2026-11-07)

- Lido o eTrace de referencia (Chrome, somente leitura). Implementado: `destinos.alerta`, tabelas `antenas`,
  `acomodacoes`/`acomodacao_itens`, `GET /api/enxoval/esperado` e telas Antenas, Acomodacoes, coluna Alerta
  em Destinos e "Enxoval esperado x atual" em Inventario. `scripts/seed_fera_palace.py` carrega os cadastros
  (11 ambientes, 3 antenas, 3 acomodacoes / 20 itens de enxoval). Testado local contra Postgres.
- Tipo do item (catalogo) agora aparece em Eventos ao vivo e Tags lidas.
- **Deploy em producao feito** (rsync + build + service update) e seed executado: 12 destinos (11 do eTrace +
  Governanca), 3 antenas, 3 acomodacoes, 20 itens de enxoval. `/health` ok, leitor gravando, `esperado` responde.
- **Aberto:** o CSV nao traz estado, entao o "atual" do Cabine (3.619 Em uso) fica acima do eTrace (3.232 Em uso):
  ~387 itens estao em outros estados la (manutencao/vendido). Ajustar os estados para o esperado x atual bater.

### Inventario no padrao eTrace + Conferencia de estoque (2026-11-07)

- Inventario: estado padrao "Em uso", resumo com total, Esperado/Diferenca na mesma tabela, botao
  "Iniciar conferencia de inventario". Nova tela Conferencia de estoque (lista, detalhe com esperados/
  conferidos/nao conferidos/%/duracao, filtro "so nao conferidos", finalizar). Testado local (3 de 9 = 33%).
- **Aberto:** deploy em producao (aguardando autorizacao).

### Login, relatorios, sessoes, CI e gravacao sob demanda (2026-11-07)

- Login com usuarios/papeis (leitura < operador < admin), cookie assinado, tela `/login`, tela Usuarios.
  `scripts/seed_usuarios_fera.py` cadastra os usuarios vistos no eTrace (jucimara, artur, daniela,
  supervisoras, sg) como operador, **sem senha** (admin define na tela Usuarios).
- Relatorio Leituras por hora (+CSV), sessao continua opt-in (`SESSAO_AUTO=1`), testes pytest (12) e CI.
- **Gravacao sob demanda:** `/ingest` so grava com leitura armada (`POST /api/leitura/armar`, usado por
  "Registrar leitura") ou conferencia em andamento; fora disso descarta e guarda so o ultimo sinal em memoria.
  Indicador do painel agora diz "radio: enviando sinal / sem sinal ha X min (· gravando)".

### Alerta por WhatsApp (2026-11-07)

- Codigo pronto (`avaliar_alertas`/`checar_alertas`, `POST /api/alertas/teste`, testes). **Aberto:** configurar
  instancia/chave/numero da Evolution (`evo.solidsd.com.br`, stack `evolution-api` ja existe na VPS) e deploy.
- Configuracao do alerta pela UI (tela Alertas WhatsApp, tabela `config`) e **Antenas com edicao** e campos do eTrace
  (descricao, modelo, localizacao, modo automatico/manual, alerta, IP, porta; `PUT /api/antenas/{id}`). Seed das 3 antenas
  atualizado com IPs 192.168.30.32 / .104 porta 8080. Aberto: deploy.

- Monitor do radio em tempo quase real: limite "sem sinal" agora em **segundos** (padrao 60, configuravel na tela
  Alertas WhatsApp, minimo 10), checagem de alerta a cada 10s (laco proprio) e indicador do painel usa o mesmo limite.
  Observacao: o deploy do alerta quebrou 1x por default aninhado `${A:-${B:-30}}` no `impinj-stack.yml` (nao suportado
  pelo `docker stack deploy`); corrigido. Nunca usar default aninhado no stack.
- Cadastro de destinatarios do alerta (padrao gestaointeligente): tabelas `alerta_destinatarios`/`alerta_envios`, envio por
  pessoa conforme o tipo, teste individual, historico (30 dias). Aberto: deploy.
- Destinatario pode ser grupo de WhatsApp (ID @g.us) + botao Buscar grupos (Evolution fetchAllGroups). Aberto: deploy.
- Busca de grupos da Evolution em segundo plano (instancia com 352 grupos leva ~130s): POST /api/alertas/grupos/atualizar + GET /api/alertas/grupos, com filtro por nome na tela. Aberto: deploy.
- Corrigido alarme falso de 'sem sinal' logo apos cada restart/deploy (ultimo evento do banco e antigo por causa da gravacao sob demanda; agora conta a partir da subida do app).
- Cabine no padrao eTrace: lista agrupada com ultima localizacao, Reiniciar leitura, confirmar movimentacao, Definir estado em lote, comprovante imprimivel com assinatura, aviso de divergencia por WhatsApp. 19 testes. Aberto: deploy (junto com o fix do alarme falso).
