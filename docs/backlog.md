# Backlog — Cabine RFID

## Riscos conhecidos (nao resolvidos)

- **Janela de "Registrar leitura" (15s) nao validada com o leitor fisico.** Se tags paradas nao forem
  relidas nesse intervalo, ou a antena pegar tags de fora da cabine, a quantidade do lote sai errada.
- **Leitor de saida sem parser**: protocolo do Acura EdgeR30 desconhecido; `bridge-saida/` so captura
  bytes. `/api/saida` esta pronta mas ninguem a chama de verdade.
- **Sem heartbeat do leitor**: o Kuma avisa "sem eventos ha X min", que pode ser so ausencia de movimento.
- **Sem backup automatico do Postgres** (so o volume `impinj_pgdata`). Definir `pg_dump` agendado.

- **Inventario importado: revisar** (2026-11-07): 8 EPCs fora do padrao hex e 85 tags lidas que nao estao no CSV.
  Agora ha a tela **Revisao do inventario** (EPC fora do padrao, tags lidas sem cadastro) para corrigir; `Data_Compra`
  passou a ser importada (`data_compra`). Localizacao do CSV segue nao importada (e derivada de transferencias).
- [x] **Validacao de EPC na API** (2026-11-08): `/api/itens` e `/api/saida` exigem hex, tamanho par, 4-64 caracteres (400 se invalido).

## Seguranca (auditoria 2026-11-07, ver `docs/auditoria-2026-11-07.md`)

- [x] **Alta**: Starlette 0.49.3 + FastAPI 0.120.4 (CVE-2025-62727). Corrigido no codigo (2026-11-07); **falta deploy**.
- [x] `SESSION_SECRET` obrigatorio (>=32 chars, app nao sobe sem). **Antes do deploy: gerar no `.env` da VPS (`openssl rand -hex 32`)**; sessoes atuais caem.
- [x] Rate limit: IP do ultimo hop do X-Forwarded-For, tambem no Basic/webhook/saida, expurgo de `_falhas` (corrigido, falta deploy).
- [ ] Incluir `pg_dump` do Cabine no `espelhar-dumps.sh` (nao coberto hoje) e testar restore.
- [x] Agente 0.1.4: tray so abre http(s) vindo do `state.json`. [ ] Ainda: tirar escrita de Users em `state.json`/pasta publica (risco de link no tmp do servico).
- [x] Container sem root (uid 10001). [ ] `govulncheck` no agente; assinatura do instalador; restringir `wa_url` a https/publico.

## Dashboard (2026-11-07)

- Feito: monitoramento das antenas/agentes, alertas recentes, inventario por estado, cabine e conferencia (`GET /api/dashboard`).
- Feito: perfil de acesso `painel` (so dashboard). Feito (2026-11-09): quem nao tem a permissao `sistema` nao recebe IP, host, versao nem detalhe tecnico no `/api/dashboard`. Ideia: mais perfis (ex.: lavanderia so ve transferencias; ja da para criar na tela Usuarios).
- Feito (2026-11-09): "Pecas paradas na lavanderia" (ultima transferencia para lavanderia ha mais de `PARADO_DIAS`, padrao 7, e nao devolvidas) e "Mais nao encontrados na conferencia" (30 dias). Divergencia da cabine nao e gravada (so avisa por WhatsApp), entao o ranking usa as conferencias.
- Ideias: grafico de leituras por hora no Painel; historico de disponibilidade das antenas (hoje so ha o ultimo estado; precisaria de tabela de checagens para "uptime %"); temperatura do Acura/R700; link do card de antena para "Configurar radio"; gravar divergencias da cabine para ranking.
- Feito (2026-11-09): tela **Monitoramento** (uptime das antenas, quedas, latencia, agentes, radio, comandos, alertas, banco, seguranca). Ideias para ela: status da instancia Evolution (conectada/desconectada), alerta quando o uptime 24h cair abaixo de X%, temperatura do R700/Acura (depende de validar a API no hardware).

## Responsivo e PWA (2026-11-07)

- Feito: PWA instalavel (manifest, service worker so de icones/offline, icones) e Painel responsivo (cartoes no celular).
- [x] **Restante das telas responsivo (2026-11-09)**: todas as tabelas viram cartoes no celular (rotulos automaticos pelo cabecalho), botao grande de Registrar leitura/Movimentar/Transferir, formularios em largura total, catalogo com selecao no canto do cartao. Atalhos do PWA: Dashboard, Leitura da cabine, Conferencia. (Texto original do item:) tornar o restante responsivo, uma tela por vez, na ordem de uso em celular/tablet: Leitura da cabine (botao grande de registrar leitura, lista agrupada), Alertas de evasao, Transferencias, Conferencia, Antenas, Inventario/Catalogo. Reusar `table.rt` (+ `data-l` nas celulas) e os dialogos `<dialog>` em tela cheia no celular.
- Ideias PWA: notificacoes push (Web Push com VAPID) para os mesmos alertas do WhatsApp; atalho "Leitura da cabine" no manifest; cache de leitura do Painel com carimbo "dados de HH:MM" (hoje sem rede mostra so a pagina offline, de proposito).
- Fora do escopo: app nativo; PWA nao substitui o agente Windows (que fala com os radios na LAN).

## Aprendido com o eTrace (nao feito ainda)

- [x] Conferencia de estoque: usuario, exportar CSV e imprimir (2026-11-08). Usuario em cada movimentacao: feito.
- [x] **Leitor manual** (colar EPCs, ver cadastrados/novos/invalidos e cadastrar em lote) e **edicao em lote** no Catalogo (2026-11-08).
- **Monitor de antenas de controle ao vivo** (intervalo, som, pausa, relatorio) e uso do `alerta` do ambiente
  para disparar aviso; mapear `hostname` do evento -> antena -> ambiente.
- **Estacoes de controle** (clientes que recebem alerta, com heartbeat) e **atributos/categorias com pai**
  configuraveis.
- Itens de acomodacao: so "Piscina", "Solteiro" e "King" do Fera Palace; Usuarios do eTrace nao foram lidos.

## Decisoes recentes

- (2026-11-07) `/ingest` so grava eventos durante leitura solicitada (`POST /api/leitura/armar` ou conferencia em
  andamento); fora disso descarta e guarda so o "ultimo sinal" em memoria (indicador "radio: enviando sinal").
  Consequencia: Tags lidas, Eventos ao vivo e Leituras por hora refletem apenas leituras solicitadas.
  Feito (2026-11-09): botao "Capturar" (30 s a 10 min) na tela Eventos ao vivo. Pendente: validar `/ingest` com leitor real.

## Agente do hotel (2026-11-07)

- **Proximo: `.msi`** (ver `docs/infra/agente-hotel.md` > Proximos passos): aparece em Aplicativos instalados, atualiza por cima, token por parametro.
- Agente 0.1.5: online = host responde (nao sonda 8080 do Acura). 0.1.6: reinstalacao fecha servico/tray e mantem o token.
- **Falso alarme noturno**: o agente do PC da governanca desliga no fim do expediente e dispara "agente sem contato" todo dia. Opcoes: horario de funcionamento por agente (ex.: 07h-19h) ou nao alertar quando outro agente cobre todas as antenas dele. Aguardando decisao.
- Idea: alertas extras vindos do agente — R700 fora de `running`, preset diferente do esperado, temperatura alta, comando de config com falha, Acura sem cliente na 8080 (lendo a pagina de status).
- Validado em producao (2026-11-07): agente 0.1.6 no Windows, 3 antenas reportando, alertas de offline/voltou entregues no grupo de WhatsApp. Proxima: `.msi`.
- Ideia: cadastrar numero individual como destinatario alem do grupo (o grupo pode ficar silenciado/enterrado).
- Codigo pronto (`agent/`, ver `docs/infra/agente-hotel.md`). Falta: teste no Windows real (servico + tray), validar API do
  R700, auto-atualizacao assinada, repositorio proprio, MSI, assinatura de codigo.

## Ideias

- Sessao de leitura continua: pronta, opt-in (`SESSAO_AUTO=1`), desligada em producao.
- CI/CD: workflow criado (testes + compila agente + deploy por secrets); **falta rodar `./scripts/rotacionar-chave-ci.sh`** (cria chave + secrets `VPS_HOST`/`VPS_SSH_KEY`; bloqueado para o assistente) e validar com `gh workflow run ci`. GHCR nao adotado.
- Cadastrar o projeto em `erpsolid/docs/projetos/` (indice central de repos).

- Evasao: antenas .32 e .104 so monitoramento de rede (online = pagina de status responde; agente 0.1.5 nao sonda a 8080). O Acura aceita 2 clientes na 8080 (hoje ocupadas por 192.168.30.18). Pendente: mapear protocolo da 8080, senha de administracao dos Acura.
