# Documentacao Cabine RFID

Esta pasta centraliza a memoria tecnica e o historico de trabalho do Cabine (leitor RFID Impinj +
controle de enxoval). Segue o [padrao de documentacao por projeto](padroes/documentacao-projeto.md)
dos demais sistemas Solid (erpsolid, hotspot).

## Indice

- [api.md](api.md): API, fluxos (lotes, inventario, evasao), modelo de dados e limitacoes em detalhe.
- [mapa.md](mapa.md): servicos, modelo de dados, rotas da API e menu do painel.
- [memoria-projeto.md](memoria-projeto.md): decisoes tecnicas e contexto importante.
- [registro-trabalho.md](registro-trabalho.md): historico cronologico (mudancas, deploys, pendencias).
- [backlog.md](backlog.md): o que falta e riscos conhecidos.
- [infra/deploy.md](infra/deploy.md): deploy de producao (Swarm), migracao SQLite -> Postgres, rollback, CI e rotacao da chave.
- [infra/agente-hotel.md](infra/agente-hotel.md): agente Windows (instalar, atualizar, testes na LAN, evasao, MSI planejado).
- [auditoria-2026-11-07.md](auditoria-2026-11-07.md): auditoria de seguranca e status das correcoes.
- [referencia-etrace-cabine.md](referencia-etrace-cabine.md): fluxo do eTrace usado como referencia.
- [agentes/README.md](agentes/README.md): skills Jarvis do projeto.
- [padroes/documentacao-projeto.md](padroes/documentacao-projeto.md): padrao de documentacao.
- [_templates/](_templates): modelos de ADR, incidente e registro de trabalho.
- [../bridge-saida/README.md](../bridge-saida/README.md): ponte local em Go para o leitor de saida.

## Estado atual (08/11/2026)

- **Painel/PWA**: Painel com monitoramento das antenas, agentes, alertas, inventario por estado e cabine (`GET /api/dashboard`);
  responsivo (tabelas viram cartoes no celular) e instalavel; perfil `painel` ve so o dashboard.
- **Monitoramento**: agente Go no(s) PC(s) do hotel (varios agentes por antena: online de um vence offline de outro); alertas de
  WhatsApp (antena offline/online, agente sem contato, webhook do radio, sem sinal, falhas) com estado persistido no banco.
- **Seguranca**: auditoria feita e corrigida em producao (Starlette 0.49.3, `SESSION_SECRET`, rate limit, container nao-root);
  pendencias em [auditoria-2026-11-07.md](auditoria-2026-11-07.md).
- **Deploy**: manual (rsync + build + stack deploy); CI automatico pronto mas aguardando `scripts/rotacionar-chave-ci.sh`.
- **Pendencias principais**: backup do banco, falso alarme noturno do agente que desliga, `.msi`, responsivo das demais telas.

## Estado em 07/11/2026

- **Producao**: VPS principal (`95.111.250.4`, Docker Swarm), stack `impinj`, dominio
  `cabine.solidsd.com.br` (Traefik + Let's Encrypt).
- **Banco**: PostgreSQL 16 (servico `postgres` da propria stack). Era SQLite ate 07/11/2026.
- **Painel**: shell de sidebar (navy/teal) no padrao do `erpsolid`/`hotspot`, menu agrupado por tarefa.
- **Inventario**: 4.570 itens importados do CSV do eTrace em 07/11/2026; EPC normalizado para hex
  (ver [memoria-projeto.md](memoria-projeto.md)).
- **Pendente de validacao fisica**: janela de leitura da cabine e protocolo do leitor de saida
  (ver [backlog.md](backlog.md)).

## Caminhos principais

| Area | Caminho |
| --- | --- |
| API + webhook (FastAPI) | `app/main.py` |
| Painel (HTML/JS puro, sem build) | `app/static/index.html` |
| Stack Swarm | `impinj-stack.yml` |
| Ponte de saida (Go) | `bridge-saida/` |
| Scripts (seed, migracao) | `scripts/` |
