// Package cloud fala com o Cabine (servidor): só conexões de saída, autenticadas por X-Agent-Token.
package cloud

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	Server string
	Token  string
	HTTP   *http.Client
}

func New(server, token string) *Client {
	return &Client{Server: server, Token: token, HTTP: &http.Client{Timeout: 20 * time.Second}}
}

type Antena struct {
	ID     int    `json:"id"`
	Nome   string `json:"nome"`
	Modelo string `json:"modelo"`
	IP     string `json:"ip"`
	Porta  int    `json:"porta"`
	Modo   string `json:"modo"`
	Alerta bool   `json:"alerta"`
}

type Config struct {
	Agente       string   `json:"agente"`
	IntervaloSeg int      `json:"intervalo_seg"`
	Antenas      []Antena `json:"antenas"`
}

type StatusAntena struct {
	ID         int            `json:"id"`
	Online     bool           `json:"online"`
	LatenciaMs int            `json:"latencia_ms"`
	Detalhe    string         `json:"detalhe"`
	Extra      map[string]any `json:"extra,omitempty"`
}

type StatusReq struct {
	Versao  string         `json:"versao"`
	Host    string         `json:"host"`
	Antenas []StatusAntena `json:"antenas"`
}

type Comando struct {
	ID       int     `json:"id"`
	AntenaID int     `json:"antena_id"`
	Metodo   string  `json:"metodo"`
	Caminho  string  `json:"caminho"`
	Corpo    *string `json:"corpo"`
}

type StatusResp struct {
	OK       bool      `json:"ok"`
	Comandos []Comando `json:"comandos"`
}

type Resultado struct {
	OK         bool   `json:"ok"`
	HTTPStatus int    `json:"http_status,omitempty"`
	Resposta   string `json:"resposta"`
}

func (c *Client) do(method, path string, in, out any) error {
	var body io.Reader
	if in != nil {
		b, _ := json.Marshal(in)
		body = bytes.NewReader(b)
	}
	req, err := http.NewRequest(method, c.Server+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("X-Agent-Token", c.Token)
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode == 401 {
		return fmt.Errorf("token recusado pelo servidor (401) — gere outro em Antenas > Agentes")
	}
	if resp.StatusCode >= 300 {
		return fmt.Errorf("servidor respondeu HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	if out != nil {
		return json.Unmarshal(raw, out)
	}
	return nil
}

func (c *Client) GetConfig() (*Config, error) {
	var cfg Config
	return &cfg, c.do("GET", "/api/agente/config", nil, &cfg)
}

func (c *Client) PostStatus(s *StatusReq) (*StatusResp, error) {
	var r StatusResp
	return &r, c.do("POST", "/api/agente/status", s, &r)
}

func (c *Client) PostResultado(id int, r *Resultado) error {
	return c.do("POST", fmt.Sprintf("/api/agente/comandos/%d/resultado", id), r, nil)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
