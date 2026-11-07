# Memoria do Projeto Cabine RFID

Data de referencia: 2026-11-07.

## Decisoes principais

- **Sem build de frontend**: o painel e um `index.html` unico (CSS+JS puro) servido pelo FastAPI. Mesmo
  espirito do `hotspot` (CSS puro, sem Tailwind). Nao introduzir framework so para o menu.
- **Auth = HTTP Basic com tres credenciais separadas** (`UI_*`, `WEBHOOK_*`, `SAIDA_*`). Se a maquina da
  ponte for comprometida, nao da acesso ao painel nem ao ingest da cabine. Nao reaproveitar credencial.
- **PostgreSQL desde 2026-11-07** (era SQLite). Servico `postgres` na propria stack, rede interna
  `impinj-internal`, sem porta publicada. `init_db()` cria as tabelas sozinho (sem Alembic) e tenta ate
  60s esperar o Postgres subir (Swarm nao garante ordem).
- **Lotes/transferencias sao append-only**: transferencia e 1:1 com lote (`UNIQUE lote_id`); corrida de
  duplo clique vira 409, nao 500. O que existe e trilha de auditoria, nada e editado depois.
- **Estado de inventario independe de localizacao** (como "Estados de Inventario" do eTrace). Item novo
  nasce no estado `padrao`; recadastrar item nao reseta estado.
- **Evasao**: saida e `documentado` se a EPC foi transferida ao mesmo destino dentro de
  `SAIDA_JANELA_MINUTOS` (60). O protocolo do leitor de saida (Acura EdgeR30) e desconhecido; a ponte so
  loga bytes crus, sem parser especulativo.
- **Deploy manual** (rsync + build local na VPS + `docker stack deploy`), sem registry nem CI, diferente
  de erpsolid/hotspot (GHCR + GitHub Actions). Ver `infra/deploy.md` e o backlog.
- Sem dado ficticio no painel: onde nao ha dado a tela mostra vazio/"–".

- **EPC canonico = hex maiusculo** (desde 2026-11-07). O R700 manda `epc` em base64 (bytes do EPC) e
  nao envia `epcHex`; `epc_hex()` em `app/main.py` converte no `/ingest` (24 hex = 16 chars base64).
  Catalogo (`itens`), lotes e eventos de saida usam sempre hex. Qualquer entrada nova de EPC (ponte de
  saida, CSV, API) deve passar pelo mesmo formato, senao nao casa com o catalogo.
- **Localizacao nao e campo editavel**: vem da ultima transferencia. Importacoes de inventario nao
  fabricam lotes/transferencias para "carregar" localizacao (poluiria a auditoria).

- **Referencia = eTrace (Fera Palace Hotel)**, lido em 2026-11-07 (somente leitura). Modelo dele: Ambientes
  (com sino de alerta), Antenas (nome, modelo, ambiente), Estacoes de Controle (PCs que recebem alerta
  `NTFY-...`, com heartbeat), Categorias (com pai), Atributos (Tamanho/Composicao/Cor/Fabricante),
  Acomodacoes (tipo, UHs, enxoval por UH com **qtd** e **fluxo**), Estados, Conferencia de Estoque,
  Movimentacoes (com usuario), Leitor Manual, Antenas de Controle (monitor ao vivo). O teste do Cabine usa
  exatamente os cadastros desse cliente (`scripts/seed_fera_palace.py`).
- **Esperado = UHs x qtd por UH x fluxo.** `fluxo` e o multiplicador de jogos em circulacao (3 = quarto +
  lavanderia + rouparia; 1,1 = item que quase nao roda). Piscina usa qtd total (89/162/35) com 1 UH.
- **Alerta por ambiente**: ligado onde a peca nao deveria cruzar antena de controle (Apartamentos, Rouparia
  Geral...); desligado em Lavanderia, Relavar, Recepcao, Secagem. Ainda nao dispara nada: so e cadastro.

- **Inventario no modelo do eTrace**: filtro de estado padrao "Em uso", resumo com total, colunas Esperado e
  Diferenca (so quando o unico filtro e o estado) e botao "Iniciar conferencia de inventario".
- **Conferencia de estoque**: ao iniciar, guarda o snapshot dos EPCs que casam com os filtros (so uma em
  andamento por vez). "Conferido" = tag lida pelo leitor depois do inicio (calculado ao vivo a partir de
  `events`); ao finalizar, grava `conferido_em` por item. Nao ha usuario ainda (depende do login com papeis).

- **Gravacao sob demanda (2026-11-07):** o leitor R700 envia continuamente, mas `/ingest` so persiste em
  `events` durante leitura solicitada (`/api/leitura/armar` ou conferencia `andamento`); o resto e descartado
  (resposta `stored: 0`). O "ultimo sinal" fica em memoria (`state["ultimo_sinal"]`) para o indicador e o Kuma.
- **Auth (2026-11-07):** Basic separado para webhook/saida; painel usa cookie de sessao (HMAC, scrypt) com
  papeis painel<leitura<operador<admin (painel = so dashboard: `/api/dashboard`, `/api/summary`, `/api/me`; todo o resto 403); admin de ambiente via `UI_USER/UI_PASS`. Env: `SESSION_SECRET`,
  `SESSION_HORAS`, `TZ_OFFSET_HOURS`, `SESSAO_AUTO`. CI em `.github/workflows/ci.yml` (secrets `VPS_HOST`, `VPS_SSH_KEY`).

## Convencoes

- Textos de UI em pt-BR; tabelas/rotas de dominio em pt-BR; termos do leitor/evento em ingles (Impinj).
- Timestamps em epoch seconds (`DOUBLE PRECISION`); a UI formata em pt-BR no navegador.
- Imagem `impinj-rfid:local` buildada na VPS; servico `impinj_impinj` (nome legado mantido para nao
  quebrar stack/volume/Traefik).

## PWA

- `app/static/`: `manifest.webmanifest`, `sw.js`, `offline.html`, `icons/` (gerados por `scripts/gerar_icones.py`). Rotas publicas em `app/main.py` (`pwa_*`). O SW nunca cacheia `/api/*` nem HTML do painel — so icones e offline. Mudou o SW? Incremente `VERSAO` em `sw.js`.
- Tabelas responsiveis: classe `rt` na `<table>` e `data-l="Rotulo"` nas `<td>` (a primeira celula e o titulo do cartao).
