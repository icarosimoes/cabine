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
	htmlpkg "html"
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
		p.Detalhe = detalheR700(extra)
	}
	return p
}

var (
	reTags   = regexp.MustCompile(`(?s)<[^>]*>`)
	reSpaces = regexp.MustCompile(`\s+`)
	reFirm   = regexp.MustCompile(`Vers[ãa]o:\s*([0-9][0-9.]*)`)
	reNome   = regexp.MustCompile(`Nome:\s*(\S+)`)
	reUptime = regexp.MustCompile(`Tempo de atividade:\s*(\S+(?: \S+)?)`)
	reStatus = regexp.MustCompile(`Status:\s*(.+?)\s+SN:`)
	reSerial = regexp.MustCompile(`SN:\s*(\d+)`)
)

// ParseAcuraPage extrai firmware, nome, uptime, status e série da página inicial do EDGE30R (porta 80).
func ParseAcuraPage(page string) map[string]any {
	t := reTags.ReplaceAllString(htmlpkg.UnescapeString(page), " ")
	t = reSpaces.ReplaceAllString(strings.ReplaceAll(t, "\u00a0", " "), " ")
	out := map[string]any{}
	for k, re := range map[string]*regexp.Regexp{"firmware": reFirm, "nome": reNome, "uptime": reUptime, "status": reStatus, "serial": reSerial} {
		if m := re.FindStringSubmatch(t); m != nil {
			out[k] = strings.TrimSpace(m[1])
		}
	}
	return out
}

func portOpen(ip string, port int) (bool, int, string) {
	t0 := time.Now()
	conn, err := (&net.Dialer{Timeout: 4 * time.Second}).DialContext(context.Background(), "tcp", net.JoinHostPort(ip, strconv.Itoa(port)))
	lat := int(time.Since(t0).Milliseconds())
	if err != nil {
		return false, lat, shortErr(err)
	}
	_ = conn.Close()
	return true, lat, ""
}

// tcpCheck (leitores que não são R700): Acura EDGE30R tem página de status na porta 80 (firmware, uptime, status);
// a "porta de comunicação" cadastrada (ex.: 8080) é verificada à parte. Outros modelos: só a porta cadastrada.
// detalheR700 resume /api/v1/status: situação, preset ativo e se o webhook de eventos está entregando.
func detalheR700(extra map[string]any) string {
	d := "ok"
	if st, ok := extra["status"].(string); ok {
		d = st
	}
	if ap, ok := extra["activePreset"].(map[string]any); ok {
		if id, ok := ap["id"].(string); ok {
			d += " · preset " + id
		}
	}
	if w, ok := extra["eventWebhookStatus"].(map[string]any); ok {
		st, _ := w["status"].(string)
		code, _ := w["httpStatusCode"].(float64)
		d += fmt.Sprintf(" · webhook %s (HTTP %d)", st, int(code))
	}
	return d
}

func tcpCheck(a cloud.Antena) Probe {
	if !strings.Contains(strings.ToLower(a.Modelo), "acura") && !strings.Contains(strings.ToLower(a.Modelo), "edge") {
		port := a.Porta
		if port == 0 {
			port = 80
		}
		ok, lat, e := portOpen(a.IP, port)
		if !ok {
			return Probe{Detalhe: e}
		}
		return Probe{Online: true, LatenciaMs: lat, Detalhe: fmt.Sprintf("porta %d respondendo", port)}
	}
	t0 := time.Now()
	resp, err := (&http.Client{Timeout: 6 * time.Second}).Get(fmt.Sprintf("http://%s:80/", a.IP))
	lat := int(time.Since(t0).Milliseconds())
	if err != nil {
		return Probe{Detalhe: "página de status (porta 80) sem resposta: " + shortErr(err)}
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 128<<10))
	extra := ParseAcuraPage(string(body))
	detalhe := "ok"
	if st, ok := extra["status"].(string); ok {
		detalhe = st
	}
	if fw, ok := extra["firmware"].(string); ok {
		detalhe += " · fw " + fw
	}
	if a.Porta > 0 && a.Porta != 80 {
		if ok, _, _ := portOpen(a.IP, a.Porta); !ok {
			detalhe += fmt.Sprintf(" · ATENÇÃO: porta de comunicação %d FECHADA", a.Porta)
			extra["porta_comunicacao"] = "fechada"
		}
	}
	return Probe{Online: resp.StatusCode < 500, LatenciaMs: lat, Detalhe: detalhe, Extra: extra}
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
