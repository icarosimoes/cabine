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
- **Agente do hotel (Go, Windows)**: servico + tray + instalador `.exe`/`instalar.cmd`; servidor ganhou `agentes`, `antena_status`, `comandos`, status/Configurar radio na tela Antenas e alertas de antena/agente offline. Testado ponta a ponta local (Linux). **Nao testado em Windows real.** Aberto: deploy (junto com cabine eTrace e fix do alarme).
- R700 .90 validado ao vivo (root/impinj): status, system, presets; Configurar radio ja le o preset ativo e deixa pronto para editar; alerta de webhook do radio com falha. Cadeia completa testada com os 3 leitores reais (somente leitura). Aberto: deploy; teste de escrita no R700.
- Antena de evasao (campo editavel na UI, reflete em /api/agente/config, alertas prioritarios, 1 falha = offline); download do instalador do agente pelo painel (`/api/agente/download/*`, imagem inclui `agent/dist`); `skipCommPort` no agente; guia de teste no Windows. 23 testes. Aberto: deploy.
- Deploy em producao do agente + antenas de evasao (stack convergiu, `/health` ok, download do agente sem login = 401). Update pontual: `evasao=TRUE` em recepcao/servico e ip 192.168.30.90:443 no R700.
- **Auditoria de seguranca (2026-11-07)**, somente leitura: `docs/auditoria-2026-11-07.md`. Achados: Starlette 0.41.3 com CVE-2025-62727 exploravel sem login via `/` e `/login`; `SESSION_SECRET` ausente na VPS; rate limit do login contornavel (X-Forwarded-For) e ausente no Basic; sem backup do banco do Cabine; pasta publica do agente gravavel por qualquer usuario; container root. Correcao no codigo (nao deployada): Starlette 0.49.3/FastAPI 0.120.4, `SESSION_SECRET` obrigatorio (e passado no stack), rate limit por ultimo hop do XFF cobrindo Basic/webhook/saida com expurgo, container uid 10001, tray do agente 0.1.4 so abre http(s). 25 testes. Pendentes: backup do banco, ACL da pasta publica do agente, govulncheck, assinatura.
- Antenas Acura: a `.32` aparecia offline por a porta 8080 recusar conexao — a pagina de status mostra "Leitor como Servidor" com as 2 vagas da 8080 ocupadas por 192.168.30.18 (porta 9090 de config livre; scan 1-10000 so achou 80 e 9090). Decisao do usuario: "online" = host responde na rede. Agente 0.1.5 nao sonda mais a 8080 (online = pagina de status responde; evita ocupar vaga do cliente real). Pendente: deploy + reinstalar o agente no Windows.
- Deploy da correcao de seguranca (Starlette 0.49.3, `SESSION_SECRET` obrigatorio, rate limit, uid 10001) com **queda de alguns minutos**: o texto `:?Informe SESSION_SECRET (openssl rand -hex 32)` com parenteses quebrou a interpolacao do `docker stack deploy` (app recebia valor de 7 chars e caia). Corrigido: mensagem do `:?` sem parenteses. Regra: nunca por parenteses/espacos extras no texto de `:?` do stack. Logins invalidados pelo segredo novo.
- Agente 0.1.6: reinstalacao para o servico, fecha o tray e demais `cabine-agent.exe`, copia com retentativas e reaproveita o token; `instalar.cmd` aceita token em branco. Decidido fazer `.msi` depois (documentado).
- **Validacao ponta a ponta em producao (2026-11-07):** agente 0.1.6 instalado no Windows do usuario; `.32` e `.104` online (so pagina de status); R700 .90 caiu e voltou durante o teste e os dois avisos (OFFLINE e "voltou a responder") chegaram no grupo de WhatsApp "TI | AJA". Instancia Evolution `icaro` conectada (`open`); o grupo existe (7 participantes). Teste direto por API para um numero individual tambem entregue. Obs.: status "enviado" em `alerta_envios` = a Evolution aceitou; o alerta das 18:34 tinha chegado ao grupo, so passou despercebido (considerar cadastrar numero individual alem do grupo).
- Branches `claude/leitura-cabine-enxoval-1gu2i7` e `archive/leitura-cabine-enxoval-superseded` (mesmo commit `9d8d014`, PR #2 fechado sem merge) **apagadas do GitHub**: o dashboard delas criava um `GET /api/antenas` que colidia com o cadastro de antenas do `main` e derivava status dos eventos (que agora so sao gravados sob demanda). Nada aproveitavel.
- **Dashboard (Painel) reformulado**: novo `GET /api/dashboard` (ui_auth) e tela com faixa de situacao (tudo certo / N problemas), cards (antenas e agentes online, alertas 24h, lotes aguardando, evasao, pecas hoje, itens), tabela **Monitoramento das antenas** (ambiente, IP, online/OFFLINE/sem informacao, detalhe com uptime e webhook, latencia, ha quanto verificou), Agentes do hotel (versao, ultimo contato), Alertas de WhatsApp recentes (tipo/envio, falhas 24h), Inventario por estado (barras), ultimas transferencias e ultima conferencia. Expoe so campos seguros do `extra` do radio (firmware, uptime, serial, preset, webhook). 26 testes.
- CI: o job de deploy passa a compilar o agente (`agent/build.sh`, versao unica em `agent/VERSION`) antes do rsync, porque `agent/dist/` nao e versionado.
- Dashboard em producao (deploy manual): `GET /api/dashboard` devolvendo os dados reais (3 antenas online, agente 0.1.6, 25 alertas enviados em 24h, 7 lotes/244 pecas aguardando, 4570 itens). A tela do Painel nao foi conferida visualmente em producao (so a sintaxe do JS e a API).
- CI automatico na VPS: workflow pronto, **secrets ainda nao criados**: o ambiente bloqueou gravar a chave no `authorized_keys` do servidor (acesso persistente). Passo a passo para o usuario em `docs/infra/deploy.md` > "Deploy automatico pelo CI".
- **Perfil de acesso "painel" (so dashboard)** — papeis agora: painel < leitura < operador < admin. `painel_auth` (qualquer usuario logado) vale so para `/api/dashboard`, `/api/summary` e `/api/me`; `ui_auth` passou a exigir >= leitura, entao toda rota existente e futura fica fechada ao perfil painel por padrao. Tela: usuario "painel" ve so o item Painel no menu, nao chama `/api/lotes`/`/api/saida` (cards vem do dashboard) e o hash e forcado a `#painel`. Constraint `usuarios_papel_check` migrada no start. Testes: acesso do painel, e um teste que percorre todas as rotas GET de `/api/*` e falha se alguma abrir para o painel. 28 testes. Obs.: o perfil painel enxerga IPs das antenas e nomes de agentes (campos do dashboard). Aberto: commit/deploy.
- **PWA + responsivo, comecando pelo Painel (2026-11-07)**: o app agora e instalavel (Chrome/Edge/Android: botao "Instalar app" no menu; iOS: Compartilhar > Adicionar a Tela de Inicio). Arquivos publicos (o navegador os busca sem cookie; sem dado sensivel): `/manifest.webmanifest` (standalone, start `/#painel`, 3 icones incl. maskable), `/sw.js` (escopo `/`, `no-cache`), `/offline.html`, `/icons/*` (lista fechada, sem path traversal). **O service worker NAO guarda API nem paginas do painel** (dados ao vivo e autenticados; evita mostrar dado velho/vazar em aparelho compartilhado): so icones e a pagina "Sem conexao". Icones gerados por `scripts/gerar_icones.py` (Pillow). Meta tags (theme-color, apple-touch-icon, viewport-fit=cover) em `index.html` e `login.html`.
  Responsivo do Painel: tabelas (antenas, agentes, alertas, transferencias) viram **cartoes** abaixo de 640px (`table.rt` + `data-l`), cards em 2 colunas (1 abaixo de 360px; o ultimo impar ocupa a linha), margens de seguranca do notch (`env(safe-area-inset-*)`), `grid2` sem estouro em telas estreitas. Verificado no Chrome com a app local (quadro de 390px): sem rolagem horizontal, SW ativo e manifest lido. 29 testes (inclui PWA). Aberto: deploy; telas operacionais (cabine, conferencia, inventario, antenas...) ainda nao foram adaptadas.
- **Alerta repetido apos deploy/restart corrigido**: o estado dos alertas de antena/agente/webhook (`state["alertas"]`) era so memoria; cada restart reenviava "agente sem contato" (visto 19:58 e 20:08, o segundo causado pelo deploy). Agora fica na tabela `alerta_estado` (chaves `antena:`, `webhook:`, `agente:`), carregada no start (`carregar_estado_alertas`) e gravada so quando muda (`salvar_estado_alertas`, que tambem descarta alertas de antena/agente removidos). A "retomada" (voltou) tambem funciona atraves do restart. `sem_sinal` e `falhas` continuam so em memoria de proposito (dependem de `ultimo_sinal`/falhas do `/ingest`, que zeram no restart; persisti-los geraria falso "voltou" apos cada deploy). 31 testes. Aberto: commit/deploy.
- Diagnostico "agente sem conexao" (2026-11-07 ~19:56): ultimo contato do agente FP11 as 19:56:15, entre dois deploys e antes do segundo; causa provavel: PC desligado/suspenso ou sem rede (nao confirmada).
- **Varios agentes na mesma antena + aviso de retomada de cobertura (2026-11-07)**: decisao do usuario (opcao "dois agentes": um no PC da governanca que desliga no fim do expediente e outro numa maquina 24h, `pcsom`). (1) `POST /api/agente/status`: o "online" de um agente em dia nao e derrubado pelo "offline" de outro (o offline so vale se o registro atual for do mesmo agente, ja offline, sem dono ou mais velho que `AGENTE_SEM_CONTATO_SEGUNDOS`). (2) Cobertura: antena com alerta/evasao sem nenhum agente reportando ganha a marca silenciosa `cobertura:ID` (persistida em `alerta_estado`); quando algum agente volta a ve-la **online**, sai "✅ antena X esta ONLINE (agente Y) — voltou a ser monitorada" (tipo `antena_online`), uma vez; se ja havia alerta de offline, vale so a retomada normal (sem duplicar). (3) Corrigido defeito antigo: quando o agente sumia com a antena offline, o servidor mandava "voltou a responder" falso; agora, sem dado fresco, o estado do alerta e preservado. Dashboard mostra qual agente reportou cada antena. 33 testes. Observacao: o alerta "agente sem contato" do PC que desliga a noite continua disparando (falso alarme esperado); opcoes futuras: horario de funcionamento por agente ou suprimir quando outro agente cobre as mesmas antenas.
