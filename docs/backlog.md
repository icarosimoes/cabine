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

## Seguranca (auditoria 2026-11-07, ver `docs/auditoria-2026-11-07.md`)

- [x] **Alta**: Starlette 0.49.3 + FastAPI 0.120.4 (CVE-2025-62727). Corrigido no codigo (2026-11-07); **falta deploy**.
- [x] `SESSION_SECRET` obrigatorio (>=32 chars, app nao sobe sem). **Antes do deploy: gerar no `.env` da VPS (`openssl rand -hex 32`)**; sessoes atuais caem.
- [x] Rate limit: IP do ultimo hop do X-Forwarded-For, tambem no Basic/webhook/saida, expurgo de `_falhas` (corrigido, falta deploy).
- [ ] Incluir `pg_dump` do Cabine no `espelhar-dumps.sh` (nao coberto hoje) e testar restore.
- [x] Agente 0.1.4: tray so abre http(s) vindo do `state.json`. [ ] Ainda: tirar escrita de Users em `state.json`/pasta publica (risco de link no tmp do servico).
- [x] Container sem root (uid 10001). [ ] `govulncheck` no agente; assinatura do instalador; restringir `wa_url` a https/publico.

## Dashboard (2026-11-07)

- Feito: monitoramento das antenas/agentes, alertas recentes, inventario por estado, cabine e conferencia (`GET /api/dashboard`).
- Ideias: grafico de leituras por hora no Painel; historico de disponibilidade das antenas (hoje so ha o ultimo estado; precisaria de tabela de checagens para "uptime %"); temperatura do Acura/R700; itens parados ha X dias fora da lavanderia; ranking de tipos com maior perda/divergencia; link do card de antena para "Configurar radio".

## Aprendido com o eTrace (nao feito ainda)

- Conferencia de estoque: feita (2026-11-07); falta registrar o usuario e exportar/imprimir o resultado.
- **Usuario em cada movimentacao** e impressao de movimentacoes (precisa de login com usuarios).
- **Leitor manual** (colar/ler tags avulsas e confirmar).
- **Monitor de antenas de controle ao vivo** (intervalo, som, pausa, relatorio) e uso do `alerta` do ambiente
  para disparar aviso; mapear `hostname` do evento -> antena -> ambiente.
- **Estacoes de controle** (clientes que recebem alerta, com heartbeat) e **atributos/categorias com pai**
  configuraveis.
- Itens de acomodacao: so "Piscina", "Solteiro" e "King" do Fera Palace; Usuarios do eTrace nao foram lidos.

## Decisoes recentes

- (2026-11-07) `/ingest` so grava eventos durante leitura solicitada (`POST /api/leitura/armar` ou conferencia em
  andamento); fora disso descarta e guarda so o "ultimo sinal" em memoria (indicador "radio: enviando sinal").
  Consequencia: Tags lidas, Eventos ao vivo e Leituras por hora refletem apenas leituras solicitadas.
  Pendente: botao "Capturar" na tela Eventos ao vivo e validar `/ingest` com leitor real.

## Agente do hotel (2026-11-07)

- **Proximo: `.msi`** (ver `docs/infra/agente-hotel.md` > Proximos passos): aparece em Aplicativos instalados, atualiza por cima, token por parametro.
- Agente 0.1.5: online = host responde (nao sonda 8080 do Acura). 0.1.6: reinstalacao fecha servico/tray e mantem o token.
- Idea: alertas extras vindos do agente — R700 fora de `running`, preset diferente do esperado, temperatura alta, comando de config com falha, Acura sem cliente na 8080 (lendo a pagina de status).
- Validado em producao (2026-11-07): agente 0.1.6 no Windows, 3 antenas reportando, alertas de offline/voltou entregues no grupo de WhatsApp. Proxima: `.msi`.
- Ideia: cadastrar numero individual como destinatario alem do grupo (o grupo pode ficar silenciado/enterrado).
- Codigo pronto (`agent/`, ver `docs/infra/agente-hotel.md`). Falta: teste no Windows real (servico + tray), validar API do
  R700, auto-atualizacao assinada, repositorio proprio, MSI, assinatura de codigo.

## Ideias

- Sessao de leitura continua: pronta, opt-in (`SESSAO_AUTO=1`), desligada em producao.
- CI/CD: workflow criado (testes + deploy por secrets); falta configurar secrets e validar. GHCR nao adotado.
- Cadastrar o projeto em `erpsolid/docs/projetos/` (indice central de repos).

- Evasao: antenas .32 e .104 so monitoramento de rede (online = pagina de status responde; agente 0.1.5 nao sonda a 8080). O Acura aceita 2 clientes na 8080 (hoje ocupadas por 192.168.30.18). Pendente: mapear protocolo da 8080, senha de administracao dos Acura.
