---
name: jarvis-layout-crud
description: Padroes de layout, menu e CRUD do painel Cabine RFID (sidebar navy/teal igual erpsolid/hotspot). Use antes de criar tela, item de menu ou formulario novo, ou ao revisar consistencia visual. TRIGGER em "tela nova", "menu", "padrao do sistema", "como as outras telas", "Jarvis-Layout".
---

Voce e o Jarvis-Layout do Cabine RFID. O painel e um unico `app/static/index.html` (CSS+JS puro, sem build).

## Regras

- **Menu**: sidebar fixa 220px (tokens `--navy #0f1623`, `--teal #14b8a6`; tema claro via
  `data-theme="light"` + `localStorage.tema`), igual `erpsolid/admin-layout.tsx` e `hotspot/admin/_base.html`.
  Itens agrupados por tarefa com `.nav-section` (Visao geral, Operacao, Controle, Cadastros, Leitor).
  Item novo entra no grupo da tarefa dele, nao no fim da lista. No celular vira drawer (`.topbar` + `.backdrop`).
- **Tela nova** = `<div class="view" data-view="x" hidden>` + `<a class="nav-item" data-nav="x" href="#x">` +
  entrada em `TITULOS` e `LOADERS` no JS. O polling (3s) so roda o loader da view aberta.
- Dentro da view: `<section>` com `<h2>`, filtros em `.row`, tabela em `.scroll > table`, mensagens em `.msg.ok|.bad`.
- Estados: sempre tratar vazio (`<tr><td colspan=N>nenhum ...`) e erro (mensagem da API em `.msg.bad`).
- Texto em pt-BR; escapar tudo que vem da API com `esc()`; nunca `innerHTML` com dado cru.
- Badge no menu so para algo que exige acao (hoje: saidas nao documentadas).
- Nao inventar dado: sem dado, mostrar vazio ou "–".
- Ao mudar o menu, atualizar a tabela de menu em `docs/mapa.md`.
