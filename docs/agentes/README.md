# Jarvis — Skills do Cabine RFID

Skills Claude Code do projeto, no mesmo formato do `erpsolid` (`docs/agentes/*.md` = fonte versionada;
a copia que o Claude Code carrega fica em `.claude/skills/<nome>/SKILL.md` — ao editar uma, replique na outra).

| Tarefa | Skill |
| --- | --- |
| Tela nova, item de menu, formulario, consistencia visual | `/jarvis-layout-crud` |
| Deploy na VPS, migracao de banco, rollback | `/jarvis-deploy` |
| Auditoria de seguranca, credenciais, dependencias | `/jarvis-seguranca` |
| Backlog, registro de trabalho, tickets | `/jarvis-backlog` |

Diferencas em relacao ao erpsolid: sem multi-tenant/RLS, sem Next.js (HTML/JS puro), sem CI/GHCR (deploy
manual), sem skills financeiras.
