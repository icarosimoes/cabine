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

- **Impinj R700**: `https://<ip>/api/v1/status` com Basic (padrao de fabrica `root`/`impinj` — confirmado; trocar em `config.json`:
  `readerUser`/`readerPass` ou `readers.<nome-da-antena>`). Certificado autoassinado aceito. 401 = "no ar, credencial recusada".
  Configuracao: tela Antenas > **Configurar rádio** (GET status/presets, PUT/POST livre). **Os caminhos exatos da API do R700
  ainda precisam ser validados com um leitor real.**
- **Acura EdgeR30**: protocolo nao mapeado; so teste de porta TCP (status). Sem configuracao remota.

## Teste na LAN real (2026-11-07, da maquina de desenvolvimento em 192.168.30.0/24)

| Equipamento | IP | Resultado |
|---|---|---|
| R700 (impinj15f21c, "radio da cabine") | 192.168.30.90 | Online. HTTPS 443 (nginx, Basic). **Usuario `root` / senha `impinj` (padrao de fabrica)** funciona. `running`, preset ativo **`etrace`**, `eventWebhookStatus` = active/HTTP 200, MQTT conectado (usado pelo eTrace). fw 8.1.7.240 |
| Acura EDGE30R recepcao | 192.168.30.32 | Online: pagina de status na **porta 80** (fw 4.1.0, "OK - Modo de leitura automatica", uptime ~27d). **Porta 8080 (cadastrada no eTrace) FECHADA** |
| Acura EDGE30R servico | 192.168.30.104 | Online: fw 4.2.0, status OK, uptime ~120d. Porta 8080 aberta |

Caminhos da API do R700 **confirmados** (GET): `/api/v1/status`, `/api/v1/system`, `/api/v1/system/image`, `/api/v1/system/region`,
`/api/v1/system/time`, `/api/v1/profiles`, `/api/v1/profiles/inventory/presets` (`default`, `etrace`),
`/api/v1/profiles/inventory/presets/{id}`. Preset `etrace`: antenas 1-3 a 2000 cdBm (20 dBm), rfMode 1002, sessao 3, relatorio a cada 3s;
evento com `epc` (base64), `tid`, antena, RSSI, hostname. Preset `default`: antena 1 a 30 dBm.
**Escrita (PUT preset, start/stop) ainda nao testada** — para mudar o preset ativo: `POST /api/v1/profiles/stop`, `PUT` do preset,
`POST /api/v1/profiles/inventory/presets/{id}/start`.

O agente le a pagina de status do Acura (porta 80) e checa a porta de comunicacao cadastrada a parte. No R700 mostra preset ativo e o status do
webhook de eventos; **webhook com falha (status != active ou HTTP >= 400) gera alerta de WhatsApp** (`webhook_falha`).
Teste manual: `LIVE=1 READER_USER=.. READER_PASS=.. go test ./internal/reader -run Live -v`.

## Antenas de evasao (decisao de 2026-11-07)

- As antenas **192.168.30.32 (recepcao)** e **192.168.30.104 (servico)** sao as de **saida/evasao**. No cadastro (tela Antenas) ha o campo
  **"Antena de evasao (saida)"** (`antenas.evasao`), editavel; vai no `GET /api/agente/config` e muda o comportamento:
  1 falha ja marca offline (sem esperar a 2a), porta de comunicacao fechada conta como offline, alerta de WhatsApp com
  prefixo "🚨 EVASAO" e sai mesmo com "alerta" desligado.
- **Por enquanto so monitoramento (estado), sem leitura de tags**: o agente nao le nenhum fluxo de dados desses leitores; o
  `bridge-saida/` (que le a porta 8080 do `.104`) continua parado ate mapearmos o protocolo. Checagem do agente: pagina de status
  (porta 80). **Desde o agente 0.1.5 ele NAO sonda mais a porta de dados (8080)**: "online" = o host responde na rede.
  (`skipCommPort` ficou sem efeito.)
- **Por que a 8080 da `.32` parecia fechada (2026-11-07):** a pagina de status mostra *Modo Ethernet: Leitor como Servidor* e
  "Conexao TCP #1 e #2 (8080): Conectada", as duas de **192.168.30.18**; a 9090 (configuracao) fica "Desconectada". O Acura aceita
  so 2 clientes na 8080 — com as vagas ocupadas, nova conexao e recusada. Scan 1-10000 na `.32`: so 80 e 9090 abertas.
  Por isso o agente nao deve abrir a 8080 (poderia tomar a vaga do cliente real). Idea futura: ler "Conexao TCP #1/#2" da pagina de status
  e alertar quando nao houver cliente conectado.
- Achados: `.104` com 8080 aberta (fluxo bruto, sem HTTP).
  Senha `acura@autoid` **recusada** na pagina de administracao dos dois (login volta para o index).
- Antes de capturar o protocolo na 8080 do `.104`: pode haver so 1 conexao por vez (o eTrace pode usa-la); fazer fora do horario de pico.

## Vídeo da evasão (DVR Intelbras e câmera Wi-Fi Mibo) — agente 0.1.8

Quando uma antena de evasão tem câmera (painel: Antenas > Câmeras), o servidor pede ao agente o trecho `[−10 s, +60 s]` da detecção
(`clipes[]` na resposta de `POST /api/agente/status`). O agente roda em segundo plano (até 2 downloads simultâneos; não atrasa o status):

1. Lê a hora do DVR por HTTP (`/cgi-bin/global.cgi?action=getCurrentTime`, digest ou basic) e corrige o deslocamento do relógio.
2. Roda o **ffmpeg** no playback RTSP `rtsp://<ip>:554/cam/playback?channel=N&subtype=0&starttime=AAAA_MM_DD_hh_mm_ss&endtime=...` (TCP),
   sem áudio, gravando mp4. O DVR entrega em **tempo real** (um trecho de 70 s leva ~70 s).
3. Envia o mp4 (`PUT /api/agente/clipes/{id}/arquivo`) ou a mensagem de erro (`POST .../erro`). O arquivo temporário é apagado.

**Requisitos no PC do hotel:** `ffmpeg.exe` na mesma pasta do `cabine-agent.exe` (ou no PATH, ou `ffmpegPath`), e, no `config.json`
(pasta restrita a SYSTEM/Administradores), as credenciais do DVR — de preferência um usuário **somente leitura/reprodução** criado no DVR:

```json
{ "dvrUser": "cabine", "dvrPass": "...", "cameras": { "DVR recepção - saída": {"user": "x", "pass": "y"} },
  "rtspPort": 554, "ffmpegPath": "", "videoCodec": "copy" }
```

### Hikvision (playback no aparelho, agente 0.1.8)

`tipo=hikvision`: o agente le a hora via `GET http://IP:porta/ISAPI/System/time` (digest) e roda o ffmpeg em
`rtsp://usuario:senha@IP:554/Streaming/tracks/<canal>01?starttime=..&endtime=..` (horario **UTC**; tenta o formato compacto e depois o ISO). Mesmas credenciais
do `config.json` (`dvrUser/dvrPass` ou `cameras.<nome>`). Opcoes de modelos, cuidados (RTSP/criptografia, firmware que ignora o horario) e plano B (`tipo=rtsp` com
`/Streaming/Channels/{canal}01`) em [../video-evasao.md](../video-evasao.md).

### Câmera Wi-Fi Intelbras Mibo (modo "buffer", agente 0.1.8)

As Mibo (iM3/iM4/iM5, iMX...) têm RTSP ao vivo (`rtsp://usuário:senha@IP:554/cam/realmonitor?channel=1&subtype=0`) e ONVIF perfil S, mas **não achei API
local para tocar o que está no cartão SD**; por isso o agente não "pede o trecho depois": ele mantém um gravador por câmera cadastrada com
tipo *Câmera Wi-Fi Mibo* (painel: Antenas > Câmeras):

1. `ffmpeg` lê o ao vivo (RTSP por TCP, sem reencodar) e grava segmentos de ~5 s em `ProgramData\CabineAgent\buffer\<id>\AAAAMMDD_hhmmss.ts`;
   cai? reinicia em 5 s. Segmentos com mais de ~6 min são apagados (uns poucos MB por câmera).
2. Na evasão, o agente junta os segmentos de `[−10 s, +60 s]` (`ffmpeg -f concat -c copy`, corte com `-ss`) em mp4 e envia, como no DVR.

**Consequências:** o PC do agente precisa estar **ligado e na rede** (o "10 s antes" só existe se o gravador já estava rodando; use o agente da
máquina 24 h), o RTSP da câmera tem que estar ativo e com usuário/senha conhecidos (app Mibo Cam; fixe o IP da câmera no roteador), e o consumo
é contínuo (~1–4 Mbps por câmera, por Wi-Fi). Sem buffer (agente recém-iniciado), o erro do clipe explica. Em *Testar* o servidor pede os últimos 30 s.
O caminho RTSP é configurável por câmera (`rtsp_path`) caso o modelo/firmware use outro. **Não confirmado:** o modelo "Mibo iX" (não achei na documentação da Intelbras; as linhas
iM e iMX seguem o padrão acima) — confirme o RTSP no manual do seu modelo e teste com VLC antes.

`videoCodec`: `copy` (padrão, não reencoda) ou `h264` (use se o DVR grava em H.265 e o navegador não reproduz; exige CPU). A senha nunca vai
ao servidor e é mascarada nas mensagens de erro. **Pendente:** validar com o modelo real do DVR (RTSP de playback, formato do CGI, codec).

## Baixar e testar no Windows

**Onde baixar:** painel do Cabine (logado como admin) > **Antenas** > secao **Agente do hotel (Windows)** > links
`cabine-agent.exe`, `instalar.cmd` e `SHA-256` (rotas `/api/agente/download/...`; so admin). Os arquivos vem de `agent/dist/`
(gerados por `agent/build.sh`) e entram na imagem no deploy. Sem deploy ainda, copie de `agent/dist/` no repositorio.

**Passo a passo:**
1. No painel: Antenas > *Agente do hotel* > nome > **Gerar token** (copie o token).
2. No PC Windows (na mesma rede 192.168.30.x): crie uma pasta, ponha `cabine-agent.exe` e `instalar.cmd` juntos. Confira o hash:
   `certutil -hashfile cabine-agent.exe SHA256` deve bater com o `.sha256`.
3. Dois cliques em `instalar.cmd` > Permitir (Administrador) > URL (Enter) > colar o token. Se o Windows/SmartScreen avisar ("editor
   desconhecido"): *Mais informacoes > Executar assim mesmo* (o exe ainda nao e assinado digitalmente).
4. Resultado esperado: janela "Instalado com sucesso", icone na bandeja (verde, vermelho ou cinza), servico `CabineAgent` em
   `services.msc` (Automatico, Em execucao).
5. Conferir: `"C:\Program Files\Cabine Agent\cabine-agent.exe" status`; painel Antenas > coluna **Status (agente)** em ~15s;
   botao **Configurar rádio** > *Ler status* / *Ler preset ativo* (so leitura). Log: `C:\ProgramData\CabineAgent\public\agent.log`.
6. Testar o alerta: desligue um Acura/cabo de rede; em ~30s o grupo do WhatsApp recebe "antena ... OFFLINE" (evasao: com prefixo).
7. Remover: `cabine-agent.exe uninstall` (Administrador).

**Atualizar / reinstalar (a partir da 0.1.6):** rode o `instalar.cmd` novo por cima. O instalador para o servico, fecha o icone da
bandeja e qualquer outro `cabine-agent.exe`, tenta copiar ate 10x e **reaproveita o token** se voce deixar em branco (ENTER).
Se a instalacao instalada for anterior a 0.1.6 (nao fecha o app) e der "acesso negado" ao copiar, feche uma vez a mao, num Prompt
como Administrador: `net stop CabineAgent` e `taskkill /F /IM cabine-agent.exe`.
Nao aparece em "Aplicativos instalados" (nao e MSI): services.msc ("Cabine RFID Agent") ou
`"C:\Program Files\Cabine Agent\cabine-agent.exe" uninstall`; manual: `sc delete CabineAgent`,
`reg delete HKLM\Software\Microsoft\Windows\CurrentVersion\Run /v CabineAgentTray /f`, `rmdir /s /q "C:\Program Files\Cabine Agent"`.

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
- **MSI (proximo trabalho, decidido 2026-11-07):** gerar `CabineAgent.msi` no `build.sh` (wixl/msitools ou WiX via Docker, sem
  precisar de Windows para gerar). Instala em Program Files, registra o servico (auto start + reinicio em falha), aparece em
  *Aplicativos instalados* com Desinstalar, atualiza por cima fechando servico e tray, recebe `TOKEN=`/`SERVER=` por parametro
  (`msiexec /i CabineAgent.msi TOKEN=xxx`) e reaproveita o comando `install` do agente numa acao personalizada (config.json + ACLs).
  O painel passa a oferecer o `.msi`. Risco: so testavel no Windows do usuario (1-2 rodadas de ajuste); SmartScreen segue avisando
  sem assinatura de codigo. Assinatura de codigo (evita aviso do SmartScreen/antivirus).
- Validar API do R700 e comandos prontos (potencia por antena, iniciar/parar leitura).
