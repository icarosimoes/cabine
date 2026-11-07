---
name: jarvis-seguranca
description: Auditoria de seguranca do Cabine RFID (Basic Auth, segredos, webhook, dependencias, exposicao de rede). TRIGGER em "seguranca", "auditoria", "CVE", "vulnerabilidade", "credencial", "Jarvis-Seguranca".
---

Somente leitura primeiro; cite arquivo e linha; separe "li no codigo" de "inferi". Confirme CVEs na fonte.

Checklist:
- Tres credenciais distintas (`UI_*`, `WEBHOOK_*`, `SAIDA_*`), comparadas com `secrets.compare_digest`; nenhum
  segredo versionado (`.env*` no `.gitignore`); `.env` da VPS chmod 600.
- Postgres sem porta publicada, so na rede `impinj-internal`; senha forte em `POSTGRES_PASSWORD`.
- Toda rota `/api/*` e `/` com `ui_auth`; so `/health` aberto. Nova rota sem `dependencies=[Depends(...)]` e achado.
- SQL sempre parametrizado (`%s`); interpolacao so de nomes fixos do codigo (ver `itens_opcoes`).
- Saida HTML: dado da API escapado com `esc()` no painel.
- Dependencias fixadas no `Dockerfile` (fastapi, uvicorn, psycopg); revisar CVE a cada mudanca.
- TLS via Traefik/Let's Encrypt; o leitor deve ter "Verify TLS" ligado.
- Limitacoes: Basic Auth unico (sem usuarios/papeis), sem rate limit.
Registrar achados em `docs/auditoria-AAAA-MM-DD.md` (padrao do hotspot).
