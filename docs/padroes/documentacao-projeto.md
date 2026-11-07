# Padrao de Documentacao por Projeto

Todo projeto da Solid deve ter documentacao propria dentro do proprio repositorio. A documentacao central pode existir em outro lugar (ex: `bisolid`), mas cada sistema precisa carregar sua memoria, mapa e operacao junto do codigo.

## Estrutura obrigatoria

```text
docs/
  README.md
  mapa.md
  memoria-projeto.md
  registro-trabalho.md
  agentes/       (se o projeto usar agentes Jarvis proprios)
  infra/         (se o projeto tiver deploy Swarm/producao)
```

## Responsabilidade de cada arquivo

| Arquivo | Responsabilidade |
| --- | --- |
| `docs/README.md` | Indice da documentacao do projeto e atalhos principais. |
| `docs/mapa.md` | Mapa do sistema, modulos, servicos, dominios, modelo de dados e rotas. |
| `docs/memoria-projeto.md` | Decisoes tecnicas, padroes adotados, historico de arquitetura e contexto importante. |
| `docs/registro-trabalho.md` | Registro cronologico das mudancas feitas, deploys, incidentes e pendencias. |
| `docs/agentes/` | Agentes usados pelo projeto (Jarvis Layout/CRUD, Jarvis Financeiro etc), quando existirem. |
| `docs/infra/` | Operacao de VPS, Docker, Swarm, DNS, deploy, backup e rollback, quando existir producao. |

## Regra de ouro

Se uma decisao ajuda a operar, manter, migrar ou evoluir o projeto, ela deve estar no `docs/` do proprio projeto.

Exemplos:

- caminho padrao de deploy;
- dominios e subdominios;
- nome da stack;
- comandos de deploy;
- secrets esperados no CI;
- padroes de menu, layout e CRUD;
- regras financeiras importantes;
- pendencias conhecidas;
- migracoes feitas ou planejadas;
- bugs sistemicos encontrados e a correcao aplicada.

## README da raiz

O `README.md` da raiz deve ser curto e apontar para `docs/`.

Ele deve conter:

- resumo do projeto;
- como rodar localmente;
- link para documentacao completa;
- comandos principais;
- aviso para nao versionar segredos.

Detalhes longos devem ir para `docs/`.

## Padrao para novos projetos

Ao iniciar ou assumir qualquer projeto:

1. Verificar se existe `docs/`.
2. Se nao existir, criar a estrutura obrigatoria.
3. Criar `memoria-projeto.md` com as decisoes conhecidas.
4. Criar `mapa.md` com servicos, modulos, modelo de dados e rotas.
5. Criar `registro-trabalho.md` e registrar a data inicial.
6. Se houver agentes reutilizaveis, colocar em `docs/agentes/`.
7. Se houver deploy/producao, colocar operacao em `docs/infra/`.
8. Atualizar o `README.md` da raiz apontando para `docs/README.md`.

## Padrao de atualizacao

Atualize a documentacao quando:

- mudar deploy, DNS, infra ou pipeline;
- criar modulo importante;
- alterar padrao visual, financeiro, CRUD ou menu;
- resolver incidente;
- migrar servico;
- adicionar agente;
- encontrar e corrigir um bug sistemico (contrato quebrado entre frontend/backend, migration mal formada etc);
- criar uma decisao que voce precisaria lembrar daqui a algumas semanas.
