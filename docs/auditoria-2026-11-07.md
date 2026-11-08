# Auditoria de segurança — Cabine RFID (2026-11-07)

Escopo: `app/main.py`, `app/static/index.html`, `Dockerfile`, `impinj-stack.yml`, agente Go (`agent/`) e a VPS
95.111.250.4 (somente leitura; nenhum valor de segredo foi lido, só nomes, tamanhos e permissões).
Legenda: **[código]** = li no arquivo; **[VPS]** = observado por comando; **[inferi]** = dedução, não testada.

## Status das correcoes (atualizado no fim do dia)

Corrigidos e em producao (08/11): #1 Starlette 0.49.3/FastAPI 0.120.4, #2 `SESSION_SECRET` obrigatorio (>=32), #3 rate limit (ultimo hop do XFF; Basic, webhook e saida incluidos; expurgo), #6 container uid 10001, parte do #5 (tray so abre http/https; agente 0.1.5+). Depois da auditoria: perfil `painel` fechado por padrao (teste percorre as rotas), PWA sem cache de API.
Abertos: #4 backup do banco do Cabine, #5 ACL da pasta publica do agente, #7 `wa_url` (https/IP publico), #8 revogacao de sessao, #9 assinatura do instalador; `govulncheck` nao rodado.
Obs.: ao diagnosticar o deploy, o fim do `.env` apareceu numa saida de comando (~38 caracteres de uma senha); trocar essa senha se quiser.

## Resumo

| # | Severidade | Achado |
|---|-----------|--------|
| 1 | Alta | Starlette 0.41.3 vulnerável a CVE-2025-62727 e `/` e `/login` (sem auth) usam `FileResponse` |
| 2 | Média | `SESSION_SECRET` não definido na VPS: a chave do cookie deriva de `UI_PASS` |
| 3 | Média | Rate limit do login usa `X-Forwarded-For` (contornável) e não cobre Basic Auth |
| 4 | Média | Backup do Postgres do Cabine não existe (cron só cobre outros projetos) |
| 5 | Média | Agente: `state.json` e pasta pública graváveis por qualquer usuário local |
| 6 | Baixa | Container roda como root |
| 7 | Baixa | Chave da Evolution em texto puro na tabela `config`; `wa_url` editável (SSRF só admin) |
| 8 | Baixa | Sessão sem revogação; `_falhas` cresce sem limite |
| 9 | Info | Instalador do agente sem assinatura; hash vem do mesmo servidor |

## Checklist do skill

- **Três credenciais distintas**: OK [VPS] — WEBHOOK/UI/SAIDA existem, nenhuma repete a outra; 24/32/48 caracteres.
  Comparação com `secrets.compare_digest` [código] `app/main.py:252,283` e `hmac.compare_digest` `:277,299`.
- **Segredos versionados**: OK [código] `.gitignore` tem `.env*`; `git ls-files` não lista `.env`/chaves.
- **`.env` na VPS**: OK [VPS] modo 600, dono root.
- **Postgres sem porta publicada**: OK [VPS] nenhum serviço `impinj_*` publica porta; 5432/55432 não escutam no host;
  postgres só na rede interna.
- **Toda rota `/api/*` autenticada**: OK [código] mapa completo das rotas: sem auth só `/health`, `/api/login`,
  `/api/logout`, `/login`, `/`. Rotas do agente usam `X-Agent-Token` (`:1401`); ingest/saída usam Basic próprio.
  Download do agente: admin (`:1418`), com allowlist de nomes (sem path traversal).
- **SQL parametrizado**: OK [código]. Interpolações em f-string são só nomes fixos: `ALTER TABLE ... {col}` (`:196`)
  vem de lista no código; `SELECT DISTINCT {col}` (`:1199`) — confirme que `col` passa por allowlist (não reverifiquei
  a origem de `col` linha a linha; tratar como **[inferi]** até conferir); `{where}` montado com `%s` + `args`.
- **`esc()` no painel**: OK nos pontos amostrados [código] (`index.html:604-625`, comprovante). Campos numéricos
  (`l.id`, `c.total`) entram sem `esc`, aceitável por serem inteiros do servidor. Não auditei as 30 ocorrências de
  `innerHTML` uma a uma.
- **TLS**: [inferi] terminado no Traefik (https://cabine.solidsd.com.br responde); cookie só é `secure` se
  `x-forwarded-proto == https` (`:1721`).
- **Comandos ao rádio**: OK [código] `:1527-1547` método GET/PUT/POST, caminho validado por regex `CMD_CAMINHO` +
  bloqueio de `..`, corpo JSON, só admin; o agente revalida (allowlist em `reader.Exec`).

## Achados

### 1. Starlette 0.41.3 — CVE-2025-62727 (Alta)
- [VPS] `pip list` no container: starlette 0.41.3 (puxado por fastapi 0.115.6).
- Fonte: CVE-2025-62727 / GHSA-7f5h-v6xp-fcq8 — afeta Starlette 0.39.0 a antes de 0.49.1; header `Range`
  malicioso causa processamento quadrático em `FileResponse` (CPU, DoS). CVSS 7.5. Corrigido em 0.49.1.
- [código] `FileResponse` em `/login` (`:1795`) e `/` (`:1863`) — **sem autenticação** — e no download (`:1426`).
  Logo, qualquer pessoa na internet pode esgotar CPU do único worker (`--workers 1`) e derrubar o painel **e o
  `/ingest`** (o rádio perde eventos).
- Correção: subir fastapi para versão que exija starlette ≥ 0.49.1 (fixar `starlette==` junto) e rodar a suíte.
  Alternativa imediata: Traefik remover/limitar o header `Range` nessas rotas.

### 2. `SESSION_SECRET` ausente (Média)
- [VPS] a variável não está no `.env`. [código] `:33` usa `sha256(UI_PASS + "|cabine-sessao")`.
- Impacto [inferi]: qualquer cookie emitido dá material para tentar descobrir `UI_PASS` offline (HMAC com chave
  derivada da senha). Com 24 caracteres o risco prático é baixo, mas troca de `UI_PASS` também invalida sessões
  de forma acidental e o par senha/chave fica acoplado.
- Correção: gerar `SESSION_SECRET` aleatório (≥32 bytes) no `.env` da VPS e passar no stack; falhar no start se vazio.

### 3. Rate limit do login (Média)
- [código] `:1708-1712` usa o primeiro valor de `X-Forwarded-For`, que o cliente controla; atrás do Traefik, o IP
  real é o último valor adicionado pelo proxy. Um atacante rotaciona o header e ignora o limite de 8/5min.
- [código] `autentica()` também é chamado via Basic Auth (`usuario_opt`, `:316-319`) sem nenhum limite — dá para
  tentar senhas em qualquer `/api/*`. `webhook_auth` e `saida_auth` também não limitam.
- Correção: usar o IP do último hop confiável; contar falhas também no caminho Basic; limitar `_falhas` (dict sem
  expurgo de chaves antigas: cresce com IPs distintos).

### 4. Sem backup do banco do Cabine (Média)
- [VPS] cron `espelhar-dumps.sh` existe (03:30); os dumps listados são do gestaointeligente. Não achei menção a
  impinj/rfid/cabine no script. Já estava no backlog; agora há dados de operação (lotes, transferências, alertas).
- Correção: incluir `pg_dump` do `impinj_postgres` no script, com retenção e teste de restore.

### 5. Agente Windows — pasta pública gravável (Média, [inferi])
- [código] `install_windows.go:69` concede `Users:(OI)(CI)M` em `PublicDir`; `config.go:122` escreve `state.json`
  (0644) ali; o tray lê `st.Server` e abre com `rundll32 url.dll,FileProtocolHandler` (`tray_windows.go:44-50`).
- Cenário: usuário local sem privilégio edita `state.json` (campo `server`) e, quando um admin clicar em "Painel"
  no tray, abre URL/arquivo arbitrário no contexto do admin. Além disso, o serviço (SYSTEM) cria/renomeia
  arquivos temporários nessa pasta: risco de ataque por link/junction.
- Correção: tray não deve usar a URL de `state.json` (usar o `DefaultServer` compilado ou ler o `config.json`
  restrito); manter só o log em pasta separada gravável, e `state.json` somente-leitura para Users.
- Positivo [código]: `config.json` com ACL restrita (`:68`) e 0600; token do agente guardado só como sha256 no
  servidor (`:1397`), gerado com `secrets.token_urlsafe(32)`.

### 6. Container como root (Baixa)
- [VPS] `uid=0`. Adicionar `USER` não privilegiado no Dockerfile e `read_only: true` onde der.

### 7. Chave da Evolution e `wa_url` (Baixa)
- [código] `wa_apikey` guardada em texto puro em `config`; o GET devolve só `wa_apikey_definida` (`:723`) — bom.
- `wa_url` é editável pelo admin e o servidor envia a `apikey` para ela: admin pode apontar para host arbitrário
  (SSRF/exfiltração interna). Aceitável com admin confiável; considerar restringir a https e bloquear IPs privados.

### 8. Sessão e memória (Baixa)
- Token sem identificador de sessão: logout só apaga o cookie (`:1725`); roubo de cookie vale até expirar (12h).
  Desativar o usuário revoga na hora (`:305`), exceto o usuário do `.env` (`e=True`).
- `_falhas` (`:265`) não remove chaves vazias.

### 9. Distribuição do agente (Info)
- `cabine-agent.exe` sem assinatura de código; o `.sha256` é servido pelo mesmo servidor, então não protege contra
  o próprio servidor comprometido. SmartScreen vai alertar. Já previsto no backlog (assinatura + atualização assinada).
- `agent` poll desliga a verificação TLS dos leitores (`reader.go:47`, `InsecureSkipVerify`) por serem
  autoassinados — restrito à LAN, com opção `StrictTLSReaders`.

## Outros pontos verificados
- Dependências Go do agente: **não** verifiquei CVEs (`govulncheck` não foi rodado). Pendente.
- fastapi 0.115.6, uvicorn 0.34.0, psycopg 3.2.3: só o CVE do Starlette foi confirmado em fonte; os demais não
  foram pesquisados individualmente.
- Evolution API/credencial do rádio (`root/impinj`) e senha padrão do R700: a senha default do fabricante segue
  ativa [inferi pela configuração do agente] — trocar no rádio.

## Ordem sugerida
1. Atualizar Starlette/FastAPI (achado 1) e redeploy.
2. `SESSION_SECRET` (2) e corrigir rate limit (3).
3. Backup do banco (4).
4. Ajustar permissões do agente antes do rollout além do teste (5).
5. Usuário não-root, assinatura do instalador, `govulncheck`.
