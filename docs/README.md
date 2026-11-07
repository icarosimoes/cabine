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
- [infra/deploy.md](infra/deploy.md): deploy de producao (Swarm), migracao SQLite -> Postgres, rollback.
- [agentes/README.md](agentes/README.md): skills Jarvis do projeto.
- [padroes/documentacao-projeto.md](padroes/documentacao-projeto.md): padrao de documentacao.
- [_templates/](_templates): modelos de ADR, incidente e registro de trabalho.
- [../bridge-saida/README.md](../bridge-saida/README.md): ponte local em Go para o leitor de saida.

## Estado atual (07/11/2026)

- **Producao**: VPS principal (`95.111.250.4`, Docker Swarm), stack `impinj`, dominio
  `cabine.solidsd.com.br` (Traefik + Let's Encrypt).
- **Banco**: PostgreSQL 16 (servico `postgres` da propria stack). Era SQLite ate 07/11/2026.
- **Painel**: shell de sidebar (navy/teal) no padrao do `erpsolid`/`hotspot`, menu agrupado por tarefa.
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
