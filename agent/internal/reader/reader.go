// Package reader testa o estado dos leitores RFID e executa comandos na API REST deles.
//
// R700 (Impinj): API REST em https://<ip>/api/v1/... com autenticação Basic (padrão de fábrica root/impinj) e
// certificado autoassinado. Outros leitores (ex.: Acura EdgeR30, protocolo ainda não mapeado): só verificamos
// se a porta responde.
package reader

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
)

type Probe struct {
	Online     bool
	LatenciaMs int
	Detalhe    string
	Extra      map[string]any
}

func isR700(a cloud.Antena) bool { return strings.Contains(strings.ToLower(a.Modelo), "r700") }

func creds(cfg *config.Config, nome string) (string, string) {
	if c, ok := cfg.Readers[nome]; ok && c.User != "" {
		return c.User, c.Pass
	}
	return cfg.ReaderUser, cfg.ReaderPass
}

func httpClient(cfg *config.Config, timeout time.Duration) *http.Client {
	return &http.Client{
		Timeout: timeout,
		Transport: &http.Transport{
			TLSClientConfig:   &tls.Config{InsecureSkipVerify: !cfg.StrictTLSReaders}, //nolint:gosec // leitores usam certificado autoassinado
			DisableKeepAlives: true,
		},
	}
}

// BaseURL monta a URL base do leitor. R700 usa HTTPS (443 por padrão); os demais, HTTP na porta cadastrada.
func BaseURL(a cloud.Antena) string {
	if isR700(a) {
		if a.Porta > 0 && a.Porta != 443 {
			return fmt.Sprintf("https://%s:%d", a.IP, a.Porta)
		}
		return "https://" + a.IP
	}
	p := a.Porta
	if p == 0 {
		p = 80
	}
	return fmt.Sprintf("http://%s:%d", a.IP, p)
}

// Check mede a latência e coleta o que o leitor expuser.
func Check(cfg *config.Config, a cloud.Antena) Probe {
	if a.IP == "" {
		return Probe{Detalhe: "sem IP cadastrado"}
	}
	if !isR700(a) {
		return tcpCheck(a)
	}
	user, pass := creds(cfg, a.Nome)
	t0 := time.Now()
	req, _ := http.NewRequest("GET", BaseURL(a)+"/api/v1/status", nil)
	req.SetBasicAuth(user, pass)
	resp, err := httpClient(cfg, 6*time.Second).Do(req)
	lat := int(time.Since(t0).Milliseconds())
	if err != nil {
		return Probe{Detalhe: shortErr(err)}
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	switch {
	case resp.StatusCode == 401 || resp.StatusCode == 403:
		// o leitor respondeu: está no ar, mas a credencial configurada no agente não confere
		return Probe{Online: true, LatenciaMs: lat, Detalhe: "no ar, mas credencial do leitor recusada (HTTP " + strconv.Itoa(resp.StatusCode) + ")"}
	case resp.StatusCode >= 500:
		return Probe{Online: false, LatenciaMs: lat, Detalhe: "leitor respondeu HTTP " + strconv.Itoa(resp.StatusCode)}
	}
	p := Probe{Online: true, LatenciaMs: lat, Detalhe: "ok"}
	var extra map[string]any
	if json.Unmarshal(body, &extra) == nil && len(extra) > 0 {
		p.Extra = extra
		if st, ok := extra["status"].(string); ok {
			p.Detalhe = "ok — " + st
		}
	}
	return p
}

func tcpCheck(a cloud.Antena) Probe {
	port := a.Porta
	if port == 0 {
		port = 80
	}
	t0 := time.Now()
	d := net.Dialer{Timeout: 4 * time.Second}
	conn, err := d.DialContext(context.Background(), "tcp", net.JoinHostPort(a.IP, strconv.Itoa(port)))
	lat := int(time.Since(t0).Milliseconds())
	if err != nil {
		return Probe{Detalhe: shortErr(err)}
	}
	_ = conn.Close()
	return Probe{Online: true, LatenciaMs: lat, Detalhe: fmt.Sprintf("porta %d respondendo", port)}
}

var caminhoOK = regexp.MustCompile(`^/api/v1/[A-Za-z0-9_\-/.?=&]*$`)

// Exec executa um comando vindo do servidor. Defesa em profundidade: o agente revalida método e caminho.
func Exec(cfg *config.Config, a cloud.Antena, metodo, caminho string, corpo *string) cloud.Resultado {
	metodo = strings.ToUpper(metodo)
	if metodo != "GET" && metodo != "PUT" && metodo != "POST" {
		return cloud.Resultado{Resposta: "método não permitido: " + metodo}
	}
	if !caminhoOK.MatchString(caminho) || strings.Contains(caminho, "..") {
		return cloud.Resultado{Resposta: "caminho não permitido: " + caminho}
	}
	if !isR700(a) {
		return cloud.Resultado{Resposta: "configuração remota só é suportada em leitores Impinj R700"}
	}
	var body io.Reader
	if corpo != nil && *corpo != "" {
		body = strings.NewReader(*corpo)
	}
	u, err := url.Parse(BaseURL(a) + caminho)
	if err != nil {
		return cloud.Resultado{Resposta: err.Error()}
	}
	req, _ := http.NewRequest(metodo, u.String(), body)
	req.Header.Set("Content-Type", "application/json")
	user, pass := creds(cfg, a.Nome)
	req.SetBasicAuth(user, pass)
	resp, err := httpClient(cfg, 25*time.Second).Do(req)
	if err != nil {
		return cloud.Resultado{Resposta: shortErr(err)}
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 256<<10))
	return cloud.Resultado{OK: resp.StatusCode < 300, HTTPStatus: resp.StatusCode, Resposta: string(raw)}
}

func shortErr(err error) string {
	s := err.Error()
	if i := strings.LastIndex(s, ": "); i >= 0 && len(s) > 120 {
		s = s[i+2:]
	}
	if len(s) > 160 {
		s = s[:160]
	}
	return s
}
