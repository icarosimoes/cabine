# Video da evasao (camera/DVR) — guia

Data de referencia: 2026-11-12. Estado: **implementado e testado com DVR/ffmpeg simulados; nao validado com DVR/camera reais.**
Branch de desenvolvimento: `claude/dvr-camera-evasion-detection-qhep0r`.

## O que faz

Quando uma antena de evasao detecta uma peca (`POST /api/saida`), o servidor anexa ao evento um **clipe** da camera daquela saida:
**10 s antes e 60 s depois** da deteccao. O clipe aparece na tela *Alertas de evasao* (botao "▶ assistir") e fica guardado 30 dias.

> **Pre-requisito que nao e deste modulo:** hoje nenhuma evasao real chama `/api/saida` — o `bridge-saida/` ainda nao le os Acura de saida
> (protocolo da porta 8080 desconhecido). Ate la, o clipe so e gerado por evento simulado ou pelo botao **Testar** da camera.

## Arquitetura

```text
Acura (saida) -> bridge-saida -> POST /api/saida --> eventos_saida + clipes (aguardando)
                                                            |
Agente do hotel  <---- POST /api/agente/status (a cada ~15 s) ---- clipes[] quando a janela fecha (+5 s)
   |  tipo dvr : DVR/NVR toca o trecho (RTSP playback)  --\
   |  tipo rtsp: buffer local do ao vivo (Mibo)          ---> ffmpeg -> mp4
   v
PUT /api/agente/clipes/{id}/arquivo  ->  /srv/clipes/{id}.mp4  ->  GET /api/clipes/{id}/video  ->  player no painel
```

O servidor (VPS) nao alcanca a LAN do hotel; so o **agente** (conexoes de saida) fala com as cameras. Credenciais das cameras ficam **so no
`config.json` do agente**, nunca no banco nem no servidor.

## Tipos de camera

| | **DVR/NVR Intelbras** (`tipo=dvr`) | **Hikvision** (`tipo=hikvision`) | **So ao vivo / buffer** (`tipo=rtsp`): Mibo iM/iMX, Hikvision sem SD... |
|---|---|---|---|
| Quem guarda o video | o aparelho (HD) | o aparelho (HD do DVR/NVR ou cartao SD da camera IP) | **o agente** (buffer de ~6 min no PC) |
| Como o trecho sai | RTSP playback `/cam/playback?channel=N&starttime=..&endtime=..` (tempo real: 70 s = ~70 s) | RTSP playback `/Streaming/tracks/<canal>01?starttime=..&endtime=..`, **horario em UTC** (tenta formato compacto `20261112T130000Z` e, se falhar, ISO `2026-11-12T13:00:00Z`) | segmentos `.ts` de 5 s gravados do RTSP ao vivo, juntados com `ffmpeg -f concat` |
| Correcao de relogio | le a hora do DVR (`global.cgi?action=getCurrentTime`, digest) | le a hora do aparelho (`GET /ISAPI/System/time`, digest, `<localTime>`) e aplica a diferenca | nao precisa (usa o relogio do PC) |
| "10 s antes" | se o canal grava continuo | se o canal grava continuo | **so se o gravador ja estava rodando** |
| Custo | nenhum extra | nenhum extra | PC ligado 24 h + 1–4 Mbps por camera |
| URL | (playback acima) | (playback acima) | ao vivo: Mibo `/cam/realmonitor?channel=1&subtype=0` (padrao); Hikvision `rtsp_path` = `/Streaming/Channels/{canal}01` |

### Opcoes de camera Hikvision (o que usar)

1. **DVR/NVR Hikvision** com o canal em gravacao continua → `tipo=hikvision`. Melhor opcao: o "10 s antes" vem do HD e o PC nao precisa gravar nada.
2. **Camera IP Hikvision com cartao microSD** (ex.: serie DS-2CD; ha relato de playback por SD em uma DS-2CD2186G2-I) → `tipo=hikvision` com o canal 1 (`/Streaming/tracks/101`). Gravar no cartao em modo continuo.
3. **Camera Hikvision sem SD / sem gravar** (ou se o playback do firmware ignorar o horario) → `tipo=rtsp` com `rtsp_path = /Streaming/Channels/{canal}01`
   (buffer no agente, mesmas consequencias da Mibo). Serve tambem como plano B se o playback falhar.

**Cuidados na Hikvision (fontes de campo, validar no seu modelo/firmware):**
- Habilitar RTSP e conferir a autenticacao (digest); relatos de `RTSP permission denied` num DVR enquanto o app (Hik-Connect) tocava normal — vale
  criar um usuario com permissao de **reproducao remota** e checar se ha **criptografia de video/chave** ativa (bloqueia o RTSP).
- Alguns firmwares **ignoram o horario** e comecam pela gravacao mais antiga: o clipe sairia com a imagem errada. **Conferir sempre** a imagem do teste
  contra a hora (ex.: passe algo na frente da camera e use *Testar*). Se acontecer, use o plano B (buffer).
- Relogio do aparelho fora de hora: o agente corrige pela diferenca lida no ISAPI; mantenha NTP ligado mesmo assim.
- Nao ha parser de ISAPI de busca/download (`/ISAPI/ContentMgmt/...`) neste modulo — so RTSP playback; ISAPI aqui e so para a hora.

Camera Wi-Fi Mibo: nao ha API local conhecida para tocar o cartao SD, por isso o buffer. **"Mibo iX" nao foi confirmado** na documentacao
da Intelbras (as linhas iM e iMX tem RTSP e ONVIF perfil S): confirme no manual do modelo e teste a URL no VLC.

## Ciclo de vida do clipe

`aguardando` (ainda gravando os 60 s) → `enviado` (agente buscando) → `ok` | `erro`.

- Janela `[t0 − 10, t0 + 60]`; o pedido so vai ao agente apos `fim + 5 s`.
- Varias leituras da mesma camera em ate 30 s dividem **um** clipe (uma pessoa com 20 pecas = 1 video).
- `enviado` por mais de 15 min → `erro` ("o agente nao concluiu"). `aguardando` por mais de 6 h sem agente → `erro` (o DVR sobrescreve).
- Retencao: apaga linha e arquivo apos 30 dias; o evento de evasao permanece.

## Implantacao (checklist)

1. **Deploy do servidor** com este branch (cria as tabelas sozinho; volume `impinj_clipes` em `/srv/clipes`; ver `infra/deploy.md`).
2. **Agente 0.1.8** no PC do hotel: `agent/build.sh`, copiar `cabine-agent.exe` + `instalar.cmd`, reinstalar. (Para camera Wi-Fi use o PC que fica ligado 24 h.)
3. **ffmpeg**: colocar `ffmpeg.exe` na mesma pasta do `cabine-agent.exe` (ou no PATH, ou `ffmpegPath` no config).
4. **Credenciais** em `C:\ProgramData\CabineAgent\config.json` (pasta restrita a SYSTEM/Administradores) — no DVR, prefira um usuario **somente leitura/reproducao**:

   ```json
   { "dvrUser": "cabine", "dvrPass": "...",
     "cameras": { "Mibo saida servico": { "user": "admin", "pass": "..." } },
     "rtspPort": 554, "ffmpegPath": "", "videoCodec": "copy" }
   ```

   `cameras.<nome>` (nome igual ao do painel) vence `dvrUser/dvrPass`. `videoCodec`: `copy` (padrao) ou `h264` (reencoda; use se a camera grava H.265 e o navegador nao toca).
5. **Painel > Antenas > Cameras**: cadastrar (nome, tipo, IP, porta HTTP so no DVR, canal, gravacao) e depois, no cadastro da antena de evasao, escolher a **Camera (DVR)**. Uma camera por antena.
6. Clicar **Testar** na camera. DVR: leva 1–3 min. Wi-Fi: espere ~1 min depois de subir o agente (o buffer precisa existir). Deve aparecer "✔ camera OK" e o player.
7. Rede: fixar o IP da camera/DVR no roteador; liberar 554 (RTSP) e 80 (CGI do DVR) entre o PC do agente e a camera.

## Configuracao do servidor (variaveis opcionais)

| Variavel | Padrao | Efeito |
|---|---|---|
| `CLIPE_ANTES_SEGUNDOS` / `CLIPE_DEPOIS_SEGUNDOS` | 10 / 60 | janela do clipe |
| `CLIPE_MARGEM_SEGUNDOS` | 5 | espera apos o fim da janela (o DVR grava em blocos) |
| `CLIPE_AGRUPAR_SEGUNDOS` | 30 | leituras da mesma camera nessa janela dividem o clipe |
| `CLIPE_TIMEOUT_SEGUNDOS` | 900 | agente pegou o pedido e nao concluiu |
| `CLIPE_MAX_MB` | 100 | tamanho maximo do mp4 enviado |
| `CLIPES_RETENCAO_DIAS` | 30 | apaga clipes mais antigos |
| `CLIPES_DIR` | `/srv/clipes` | onde ficam os mp4 |

## Problemas comuns (mensagem do clipe → o que fazer)

| Mensagem (coluna Video / Testar) | Causa provavel | Acao |
|---|---|---|
| `usuario/senha do DVR nao configurados no config.json do agente` | faltam `dvrUser/dvrPass` ou `cameras.<nome>` | preencher o config.json e reiniciar o servico `CabineAgent` |
| `DVR ... nao respondeu a leitura da hora` | IP/porta HTTP errados, DVR fora do ar, firewall | conferir IP e porta 80 a partir do PC do agente |
| `usuario ou senha do DVR recusados (401)` | credencial errada | corrigir no config.json |
| `aparelho Hikvision ... nao respondeu a leitura da hora via ISAPI` | ISAPI desativado, autenticacao so digest/basic diferente, porta HTTP errada, usuario sem permissao | conferir porta HTTP (80), usuario e ISAPI no aparelho |
| `sem video do canal N ... 404 / permission denied` (Hikvision) | RTSP bloqueado (permissao/criptografia), canal sem gravacao | usuario com reproducao remota; desativar criptografia; ou usar `tipo=rtsp` (buffer) |
| `sem video do canal N entre .. (hora do DVR)` | canal sem gravacao no intervalo (so movimento?), canal errado, caminho de playback diferente no firmware | gravacao continua no DVR; conferir o canal; testar a URL RTSP no VLC |
| `ffmpeg nao encontrado` | falta o ffmpeg | colocar `ffmpeg.exe` ao lado do agente |
| `o gravador nao esta rodando` (Mibo) | camera fora do ar, IP/senha/caminho RTSP errados, sem credencial | testar `rtsp://...` no VLC; conferir `cameras.<nome>` |
| `o gravador subiu ha Ns` (Mibo) | agente recem-iniciado: o buffer so guarda o que passou depois | esperar ~1 min e repetir o teste |
| `... este agente nao esta gravando o ao vivo dela` | camera Wi-Fi sem credencial no agente, ou agente anterior a 0.1.8 | atualizar o agente e preencher o config |
| `nenhum agente buscou o trecho a tempo` | agente desligado por horas | manter o agente em PC 24 h |
| `o arquivo nao e um mp4 valido` / HTTP 413 | arquivo corrompido ou maior que `CLIPE_MAX_MB` | repetir; reduzir via `videoCodec`/substream |
| video baixa mas nao toca no Chrome | camera em H.265 | `"videoCodec": "h264"` |

Logs do agente: `C:\ProgramData\CabineAgent\public\agent.log` (`[clipe #N]`, `[gravador NOME]`; senhas mascaradas).

## Seguranca e limites

- Senha da camera/DVR so no `config.json` do agente (pasta restrita), mascarada nas mensagens de erro. O painel nunca a mostra.
- O mp4 so e servido a quem tem a permissao `consultar`; o nome do arquivo vem do id numerico (sem caminho do usuario). O upload valida `ftyp` e o limite de tamanho e exige `X-Agent-Token`.
- Qualquer agente em contato pega o pedido (como nos comandos de radio): com varios hoteis/agentes, ligar camera a agente esta no backlog.
- Sem audio (`-an`). Imagem de pessoas: avaliar LGPD (retencao de 30 dias e acesso por perfil sao os controles atuais).
- Nao validado: modelo real do DVR (caminho RTSP, formato do CGI, codec), camera Mibo e Hikvision reais (formato do horario do playback, ISAPI), estabilidade do Wi-Fi sob buffer continuo.

## Codigo e testes

| Parte | Onde |
|---|---|
| Servidor: tabelas, rotas, fila, retencao | `app/main.py` (`cameras`, `clipes`, `_clipe_da_saida`, `_clipes_para_agente`, `purgar_clipes`) |
| Painel | `app/static/index.html` (secao Cameras em Antenas; coluna Video e player em Alertas de evasao) |
| Agente: DVR (hora, RTSP playback, digest) | `agent/internal/dvr/dvr.go` |
| Agente: Hikvision (hora ISAPI, URLs de playback) | `agent/internal/dvr/hikvision.go` |
| Agente: buffer (gravador, segmentos, montagem) | `agent/internal/dvr/buffer.go` |
| Agente: orquestracao | `agent/internal/runner/runner.go` (`startClipe`, `syncRecorders`) |
| Testes | `tests/test_clipes.py` (servidor, 13), `agent/internal/dvr/*_test.go` (inclui ffmpeg real e ISAPI/ffmpeg simulados), `agent/internal/runner/{clipes,buffer}_test.go` |

API: ver [api.md](api.md) (secao *Video da evasao*). Agente: [infra/agente-hotel.md](infra/agente-hotel.md).
