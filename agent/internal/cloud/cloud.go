// Package cloud fala com o Cabine (servidor): só conexões de saída, autenticadas por X-Agent-Token.
package cloud

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
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
	Evasao bool   `json:"evasao"` // antena de saída: monitoramento prioritário
}

// CameraCfg é uma câmera em modo buffer (sem playback no aparelho, ex. Intelbras Mibo): o agente grava o ao vivo.
type CameraCfg struct {
	ID       int    `json:"id"`
	Nome     string `json:"nome"`
	IP       string `json:"ip"`
	Canal    int    `json:"canal"`
	RTSPPath string `json:"rtsp_path"` // vazio = padrão das Mibo/VIP: /cam/realmonitor?channel={canal}&subtype=0
}

type Config struct {
	Agente       string      `json:"agente"`
	IntervaloSeg int         `json:"intervalo_seg"`
	Antenas      []Antena    `json:"antenas"`
	Cameras      []CameraCfg `json:"cameras"`
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

// Clipe é um pedido de vídeo: trecho [Inicio, Fim] (unix, segundos) de um canal do DVR.
type Clipe struct {
	ID       int     `json:"id"`
	CameraID int     `json:"camera_id"`
	Tipo     string  `json:"tipo"` // dvr (Intelbras/Dahua) | hikvision (playback no aparelho) | rtsp (buffer do ao vivo)
	RTSPPath string  `json:"rtsp_path"`
	Inicio   float64 `json:"inicio"`
	Fim      float64 `json:"fim"`
	Camera   string  `json:"camera"`
	IP       string  `json:"ip"`
	Porta    int     `json:"porta"` // porta HTTP do DVR
	Canal    int     `json:"canal"`
	Gravacao string  `json:"gravacao"`
}

type StatusResp struct {
	OK       bool      `json:"ok"`
	Comandos []Comando `json:"comandos"`
	Clipes   []Clipe   `json:"clipes"`
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

// PutClipe envia o mp4 do clipe (corpo bruto). O prazo é folgado: o arquivo tem dezenas de MB.
func (c *Client) PutClipe(id int, path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	fi, err := f.Stat()
	if err != nil {
		return err
	}
	req, err := http.NewRequest("PUT", fmt.Sprintf("%s/api/agente/clipes/%d/arquivo", c.Server, id), f)
	if err != nil {
		return err
	}
	req.ContentLength = fi.Size()
	req.Header.Set("X-Agent-Token", c.Token)
	req.Header.Set("Content-Type", "video/mp4")
	resp, err := (&http.Client{Timeout: 10 * time.Minute}).Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode >= 300 {
		return fmt.Errorf("servidor recusou o clipe (HTTP %d): %s", resp.StatusCode, truncate(string(raw), 200))
	}
	return nil
}

func (c *Client) PostClipeErro(id int, msg string) error {
	return c.do("POST", fmt.Sprintf("/api/agente/clipes/%d/erro", id), map[string]string{"mensagem": msg}, nil)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
