# Agente do hotel (cabine-agent) — Windows

Programa em Go (`agent/`) que roda **na LAN do hotel** e faz so conexoes de saida para o Cabine. Funcoes:

1. Busca no Cabine as antenas que tem IP (`GET /api/agente/config`) e testa cada uma a cada ~15s.
2. Envia o status (online, latencia, detalhe) — `POST /api/agente/status` — que aparece em Antenas > Status e gera
   alerta de WhatsApp (**antena offline** e **agente sem contato**, um aviso por transicao).
3. Recebe comandos de configuracao de radio (fila `comandos`), executa na API REST do leitor e devolve o resultado.
4. Tray (icone na bandeja): verde = tudo no ar, vermelho = antena offline / sem servidor / servico parado, cinza = sem dados.

## Decisoes

- Linguagem: Go (um `.exe`, compila no Linux via Docker, mesmo padrao do SOLID Guardian). Agente **separado** do Guardian.
- Instalacao inicial: `.exe` auto-instalavel + `instalar.cmd`. MSI fica para depois (exige maquina Windows).
- Seguranca: token por agente (hash SHA-256 no banco, mostrado uma vez); so HTTPS de saida; comandos limitados a
  `GET/PUT/POST` em `/api/v1/...` (validado no servidor **e** no agente); config.json (token) em pasta restrita a
  SYSTEM/Administradores; o tray le apenas `public\state.json`.
- Histerese: antena so vira offline apos **2 falhas seguidas** (evita alarme por 1 pacote perdido).

## Leitores

- **Impinj R700**: `https://<ip>/api/v1/status` com Basic (padrao de fabrica `root`/`impinj`, trocar em `config.json`:
  `readerUser`/`readerPass` ou `readers.<nome-da-antena>`). Certificado autoassinado aceito. 401 = "no ar, credencial recusada".
  Configuracao: tela Antenas > **Configurar rádio** (GET status/presets, PUT/POST livre). **Os caminhos exatos da API do R700
  ainda precisam ser validados com um leitor real.**
- **Acura EdgeR30**: protocolo nao mapeado; so teste de porta TCP (status). Sem configuracao remota.

## Teste na LAN real (2026-11-07, feito da maquina de desenvolvimento em 192.168.30.0/24)

| Equipamento | IP | Resultado |
|---|---|---|
| R700 (impinj15f21c, "radio da cabine") | 192.168.30.90 | Online. HTTPS 443 (nginx, Basic "Reader Access"); 80 redireciona (307). **401 com admin/impinj** — credencial da API ainda a confirmar; ate la o agente reporta "no ar, credencial recusada". Portas 8080/5084 fechadas |
| Acura EDGE30R recepcao | 192.168.30.32 | Online: pagina de status na **porta 80** (fw 4.1.0, EDGE30R-57D65E, SN 6480592, "OK - Modo de leitura automatica", uptime ~27d). **Porta 8080 (cadastrada no eTrace) FECHADA** |
| Acura EDGE30R servico | 192.168.30.104 | Online: fw 4.2.0, EDGE30R-5826DE, SN 6480951, uptime ~120d. Porta 8080 aberta |

O agente le a pagina de status do Acura (porta 80) para firmware/uptime/status e checa a porta de comunicacao cadastrada a parte
(aviso "porta de comunicacao FECHADA"). Teste manual: `LIVE=1 READER_USER=.. READER_PASS=.. go test ./internal/reader -run Live -v`.

## Instalar (teste inicial)

1. Painel > Antenas > *Agente do hotel* > **Gerar token** (copie; nao aparece de novo). Cadastre IP/porta das antenas.
2. Build: `agent/build.sh` -> `agent/dist/cabine-agent.exe` + `instalar.cmd` (+ `.sha256`).
3. No PC Windows do hotel: copiar os dois arquivos, dois cliques em `instalar.cmd` (pede Administrador), colar a URL e o token.
   Instala em `C:\Program Files\Cabine Agent`, servico `CabineAgent` (automatico, reinicia sozinho se cair), tray no logon.
4. Conferir: `cabine-agent.exe status`, icone na bandeja, painel > Antenas > Status. Log: `C:\ProgramData\CabineAgent\public\agent.log`.
5. Remover: `cabine-agent.exe uninstall` (Administrador).

## Proximos passos

- Auto-atualizacao assinada (Ed25519; manifesto servido pelo proprio Cabine) — chave publica embutida no agente.
- Repositorio proprio do agente + CI que compila, assina e publica.
- MSI (WiX) em maquina Windows; assinatura de codigo (evita aviso do SmartScreen/antivirus).
- Validar API do R700 e comandos prontos (potencia por antena, iniciar/parar leitura).
