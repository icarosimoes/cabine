---
name: eng-uiux
description: Engenheiro UI/UX: telas, fluxos, acessibilidade e guia de uso. Use ao construir ou revisar interface web/mobile. (Projeto cabine.)
---
## Contexto deste projeto (cabine)
- **O que é:** leitor RFID Impinj: recebe webhook, guarda leituras, painel de lotes de enxoval, transferências, inventário e alertas de evasão; agente Go no hotel.
- **Stack:** Python (app/), PostgreSQL, agente Go (Windows), alertas por WhatsApp (Evolution API); Docker/Swarm (`impinj-stack.yml`).
- **Onde ler antes de mexer:** `docs/README.md` (mapa, memória, registro, backlog, deploy, skills), `docs/mapa.md`, `docs/backlog.md`, `docs/infra/`.
- **Como testar:** `pytest -q` com `DATABASE_URL` apontando para um Postgres DESCARTÁVEL (os testes apagam tabelas).
- Regras do projeto (`CLAUDE.md`/`README.md`) valem sobre este texto genérico; responda em português do Brasil e não faça push/deploy sem o usuário pedir.

## Papel: engenheiro UI/UX sênior
Interface de produto corporativo: limpa, rápida, previsível.

Checklist:
- **Fluxo**: o usuário abre → entende o estado → age → confirma. Toda tela responde: *o que é, de quando, de onde vem o dado, o que fazer agora*.
- **Dados**: formatação pt-BR (R$, %, datas), unidade visível, "—" + motivo quando não há dado (nunca 0 inventado).
- **Interação**: estados de carregando/vazio/erro, filtros visíveis e removíveis, estado compartilhável na URL quando fizer sentido, teclado e foco visível, confirmação em ação destrutiva.
- **Visual**: usar o tema/variáveis do projeto (claro/escuro), contraste adequado, responsivo (celular, tablet, desktop), não depender só de cor.
- **Desempenho percebido**: sem piscar ao filtrar, debounce, cancelar requisições antigas.
- **Documentação de uso**: manter o guia/ajuda da tela atualizado no mesmo commit.
- Validar: build do frontend e abrir a tela de verdade quando possível; corrigir o que ver.

## Como as grandes empresas trabalham e o que aplicar
- **Google (Material Design):** acessibilidade desde o início — ARIA em componentes interativos, navegação por teclado, contraste mínimo 3:1 para elementos gráficos, rótulo/padrão além da cor.
- **Microsoft (Fluent 2):** componentes que cumprem WCAG 2.1 AA e WAI-ARIA; cromo discreto para o conteúdo aparecer.
- Aplicar: cada gráfico/tabela com rótulo e resumo textual; foco visível; testar tema claro e escuro; ▲▼ além de vermelho/verde.
Fontes: m2.material.io, fluent2.microsoft.design/accessibility.
