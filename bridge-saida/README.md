# Ponte de saída (Go)

Roda num computador local, na mesma rede do leitor RFID de saída (ex. Acura EdgeR30,
`192.168.30.104:8080`) — a VPS da Cabine (`95.111.250.4`) não alcança essa rede privada.
Conecta no leitor, interpreta os eventos de tag detectada e reporta pro Cabine
(`POST /api/saida`), que cruza com o histórico de transferências pra decidir se aquela
saída foi documentada ou não.

## Estado atual: protocolo do leitor desconhecido

**Isso ainda não lê o leitor de verdade.** Não existe documentação do fabricante
disponível até agora, então `parseEvent()` em `main.go` é um stub que não decodifica
nada. O que o programa já faz, testado de ponta a ponta:

- Conecta no leitor via TCP, reconecta sozinho se a conexão cair.
- Loga em hex, com timestamp, todo byte bruto recebido — serve de captura de
  tráfego caso não dê pra rodar Wireshark/tcpdump na máquina local.
- Nunca chama a API do Cabine enquanto `parseEvent()` não souber decodificar nada
  (confirmado em teste: nenhuma requisição HTTP é feita com o stub atual).
- Se a conexão for recusada (`connection refused`), avisa no log que o leitor pode
  esperar ser discado NO SENTIDO CONTRÁRIO — nesse caso o programa precisaria virar
  um listener (`net.Listen`) em vez de um dialer, e isso só se descobre testando
  contra o hardware real.

## Próximo passo

1. Rodar isso (ou Wireshark/tcpdump) apontado pro leitor real, capturar uma leitura de
   tag de verdade.
2. Mandar a captura (hex do log, ou um `.pcap`) de volta.
3. `parseEvent()` é reescrita a partir disso — é a única função que muda; reconexão,
   logging e envio pro Cabine já funcionam.

## Configuração (variáveis de ambiente, todas obrigatórias exceto as duas últimas)

| Variável | Exemplo | Descrição |
| --- | --- | --- |
| `READER_ADDR` | `192.168.30.104:8080` | IP:porta do leitor na rede local. |
| `CABINE_URL` | `https://cabine.solidsd.com.br` | Base da API do Cabine. |
| `SAIDA_USER` / `SAIDA_PASS` | — | Credencial própria pra `/api/saida` (não é a mesma do webhook nem da UI). |
| `DESTINO_ID` | `7` | Id do destino no Cabine que esse leitor de saída representa (ex. "Saída Serviço", ver `GET /api/destinos`). |
| `DIAL_TIMEOUT_SECONDS` | `10` (padrão) | Timeout pra conectar no leitor. |
| `RECONNECT_DELAY_SECONDS` | `5` (padrão) | Espera antes de tentar reconectar. |

## Rodar

```bash
cd bridge-saida
go build -o bridge-saida .
READER_ADDR=192.168.30.104:8080 CABINE_URL=https://cabine.solidsd.com.br \
  SAIDA_USER=... SAIDA_PASS=... DESTINO_ID=7 ./bridge-saida
```

Sem dependências externas (só biblioteca padrão do Go).
