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

- Codigo pronto (`agent/`, ver `docs/infra/agente-hotel.md`). Falta: teste no Windows real (servico + tray), validar API do
  R700, auto-atualizacao assinada, repositorio proprio, MSI, assinatura de codigo.

## Ideias

- Sessao de leitura continua: pronta, opt-in (`SESSAO_AUTO=1`), desligada em producao.
- CI/CD: workflow criado (testes + deploy por secrets); falta configurar secrets e validar. GHCR nao adotado.
- Cadastrar o projeto em `erpsolid/docs/projetos/` (indice central de repos).

- Evasao: antenas .32 e .104 marcadas como de evasao (so monitoramento por ora). Pendente: mapear protocolo da porta 8080 do .104, descobrir por que a 8080 do .32 esta fechada, senha de administracao dos Acura.
