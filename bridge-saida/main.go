// Ponte local entre um leitor RFID de saída (ex. Acura EdgeR30, 192.168.30.x) e a
// API da Cabine (POST /api/saida). Roda num computador na mesma rede local do
// leitor — a VPS da Cabine não alcança essa rede privada.
//
// HONESTIDADE SOBRE O QUE ESTE PROGRAMA FAZ HOJE: o protocolo do leitor na porta
// configurada ainda não é conhecido (sem SDK/manual do fabricante disponível no
// momento em que isso foi escrito). parseEvent() abaixo é um stub que não decodifica
// nada — o programa só conecta no leitor e loga os bytes crus recebidos, em hex,
// com timestamp. Isso serve de ferramenta de captura caso Wireshark/tcpdump não
// seja viável na máquina local. Assim que o protocolo real for conhecido (via
// captura de tráfego ou documentação do fabricante), parseEvent() é a única
// função que precisa mudar — o resto (reconexão, logging, envio pro Cabine) já
// funciona.
//
// Se a conexão for recusada (connection refused) ao tentar discar pro leitor,
// isso é um sinal de que o leitor espera conexão NO SENTIDO CONTRÁRIO (ele disca
// pra algo, não aceita conexões) — nesse caso este programa precisa virar um
// listener (net.Listen) em vez de um dialer. O log abaixo avisa isso quando
// acontece.
package main

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"
)

type config struct {
	readerAddr     string // ex. "192.168.30.104:8080"
	cabineURL      string // ex. "https://cabine.solidsd.com.br"
	saidaUser      string
	saidaPass      string
	destinoID      int
	dialTimeout    time.Duration
	reconnectDelay time.Duration
}

func loadConfig() (config, error) {
	get := func(name string) (string, error) {
		v := os.Getenv(name)
		if v == "" {
			return "", fmt.Errorf("variável de ambiente %s é obrigatória", name)
		}
		return v, nil
	}
	readerAddr, err := get("READER_ADDR")
	if err != nil {
		return config{}, err
	}
	cabineURL, err := get("CABINE_URL")
	if err != nil {
		return config{}, err
	}
	saidaUser, err := get("SAIDA_USER")
	if err != nil {
		return config{}, err
	}
	saidaPass, err := get("SAIDA_PASS")
	if err != nil {
		return config{}, err
	}
	destinoIDStr, err := get("DESTINO_ID")
	if err != nil {
		return config{}, err
	}
	destinoID, err := strconv.Atoi(destinoIDStr)
	if err != nil {
		return config{}, fmt.Errorf("DESTINO_ID deve ser um número inteiro (id do destino no Cabine, ex. o id de \"Saída Serviço\"): %w", err)
	}
	dialTimeout := envDurationSeconds("DIAL_TIMEOUT_SECONDS", 10)
	reconnectDelay := envDurationSeconds("RECONNECT_DELAY_SECONDS", 5)
	return config{
		readerAddr:     readerAddr,
		cabineURL:      cabineURL,
		saidaUser:      saidaUser,
		saidaPass:      saidaPass,
		destinoID:      destinoID,
		dialTimeout:    dialTimeout,
		reconnectDelay: reconnectDelay,
	}, nil
}

func envDurationSeconds(name string, def int) time.Duration {
	if v := os.Getenv(name); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return time.Duration(n) * time.Second
		}
	}
	return time.Duration(def) * time.Second
}

// cabineClient manda eventos de saída confirmados pro endpoint POST /api/saida
// do Cabine. Essa parte já funciona de verdade — não depende do protocolo do leitor.
type cabineClient struct {
	baseURL string
	user    string
	pass    string
	http    *http.Client
}

func (c *cabineClient) reportarSaida(ctx context.Context, epc string, destinoID int) error {
	body, err := json.Marshal(map[string]any{"epc": epc, "destino_id": destinoID})
	if err != nil {
		return fmt.Errorf("montar corpo da requisição: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/saida", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("montar requisição: %w", err)
	}
	req.SetBasicAuth(c.user, c.pass)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("enviar pro Cabine: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		b, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("cabine respondeu %d: %s", resp.StatusCode, string(b))
	}
	return nil
}

// parseEvent é o único ponto deste programa que depende do protocolo real do
// leitor. Ainda não sabemos qual é — ver o comentário no topo do arquivo.
// Troque esta função quando tiver uma captura de tráfego real pra basear o
// parsing; o resto do programa não precisa mudar.
func parseEvent(raw []byte) (epc string, ok bool) {
	return "", false
}

func conectarEOuvir(ctx context.Context, cfg config, logger *slog.Logger, client *cabineClient) error {
	dialer := net.Dialer{Timeout: cfg.dialTimeout}
	conn, err := dialer.DialContext(ctx, "tcp", cfg.readerAddr)
	if err != nil {
		return fmt.Errorf("conectar em %s: %w — se for \"connection refused\", o leitor pode esperar que ALGUÉM DISQUE PRA ELE em vez do contrário; confirme com Wireshark/tcpdump qual lado inicia a conexão antes de insistir aqui", cfg.readerAddr, err)
	}
	defer conn.Close()
	logger.Info("conectado ao leitor", "endereco", cfg.readerAddr)

	buf := make([]byte, 4096)
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		conn.SetReadDeadline(time.Now().Add(30 * time.Second))
		n, err := conn.Read(buf)
		if err != nil {
			if errors.Is(err, os.ErrDeadlineExceeded) {
				continue
			}
			return fmt.Errorf("leitura da conexão com %s: %w", cfg.readerAddr, err)
		}
		raw := append([]byte(nil), buf[:n]...)
		logger.Info("bytes recebidos do leitor (protocolo ainda não decodificado — isto é uma captura, não um evento interpretado)",
			"tamanho", n, "hex", hex.EncodeToString(raw))

		epc, ok := parseEvent(raw)
		if !ok {
			continue
		}
		if err := client.reportarSaida(ctx, epc, cfg.destinoID); err != nil {
			logger.Error("falha ao reportar saída pro Cabine", "epc", epc, "erro", err)
			continue
		}
		logger.Info("saída reportada ao Cabine", "epc", epc)
	}
}

func main() {
	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))

	cfg, err := loadConfig()
	if err != nil {
		logger.Error("configuração inválida", "erro", err)
		os.Exit(1)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	client := &cabineClient{
		baseURL: cfg.cabineURL,
		user:    cfg.saidaUser,
		pass:    cfg.saidaPass,
		http:    &http.Client{Timeout: 10 * time.Second},
	}

	logger.Info("iniciando ponte de saída", "leitor", cfg.readerAddr, "cabine", cfg.cabineURL, "destino_id", cfg.destinoID)

	for {
		if ctx.Err() != nil {
			logger.Info("encerrando (sinal recebido)")
			return
		}
		if err := conectarEOuvir(ctx, cfg, logger, client); err != nil && ctx.Err() == nil {
			logger.Error("conexão com o leitor caiu, tentando de novo", "erro", err, "espera", cfg.reconnectDelay)
			select {
			case <-time.After(cfg.reconnectDelay):
			case <-ctx.Done():
				return
			}
		}
	}
}
