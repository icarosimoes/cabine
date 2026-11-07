---
name: jarvis-backlog
description: Atualiza docs/backlog.md e docs/registro-trabalho.md do Cabine, compara com o eTrace/referencias e detalha tickets. TRIGGER em "backlog", "o que falta", "registrar trabalho", "Jarvis-Backlog".
---

- `docs/backlog.md`: separe "Riscos conhecidos" (nao resolvidos) de "Ideias". Ao concluir algo, mova para
  `docs/registro-trabalho.md` com data, commit e como foi validado; nao deixe item feito no backlog.
- Todo item com: o que, por que, como validar. Sem prometer o que depende de hardware nao testado
  (janela da cabine, protocolo do leitor de saida).
- Mantenha `docs/README.md` ("Estado atual") coerente com o registro.
- Use `docs/_templates/` (ADR, incidente, registro).
