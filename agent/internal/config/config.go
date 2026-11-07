// Package config guarda a configuração do agente (servidor, token, credenciais dos leitores) e o estado público
// que o tray lê. O config.json fica numa pasta restrita (contém o token); o state.json numa subpasta legível.
package config

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

var Version = "0.1.0"

const (
	ServiceName   = "CabineAgent"
	DisplayName   = "Cabine RFID Agent"
	DefaultServer = "https://cabine.solidsd.com.br"
)

type ReaderCred struct {
	User string `json:"user"`
	Pass string `json:"pass"`
}

type Config struct {
	Server string `json:"server"`
	Token  string `json:"token"`
	// Credenciais padrão da API REST dos leitores Impinj (padrão de fábrica do R700: root/impinj).
	ReaderUser string `json:"readerUser"`
	ReaderPass string `json:"readerPass"`
	// Sobrescreve por nome da antena (opcional).
	Readers map[string]ReaderCred `json:"readers,omitempty"`
	// Leitores usam certificado autoassinado: por padrão não validamos.
	StrictTLSReaders bool `json:"strictTlsReaders,omitempty"`
	// Não abrir conexão na porta de dados dos Acura (ex.: 8080), nem rápida: monitora só a página de status (porta 80).
	SkipCommPort bool `json:"skipCommPort,omitempty"`
}

func DataDir() string {
	if runtime.GOOS == "windows" {
		pd := os.Getenv("PROGRAMDATA")
		if pd == "" {
			pd = `C:\ProgramData`
		}
		return filepath.Join(pd, "CabineAgent")
	}
	if d := os.Getenv("CABINE_AGENT_DIR"); d != "" {
		return d
	}
	return "/var/lib/cabine-agent"
}

func ConfigPath() string { return filepath.Join(DataDir(), "config.json") }
func PublicDir() string  { return filepath.Join(DataDir(), "public") }
func StatePath() string  { return filepath.Join(PublicDir(), "state.json") }
func LogPath() string    { return filepath.Join(PublicDir(), "agent.log") }

func Load(path string) (*Config, error) {
	if path == "" {
		path = ConfigPath()
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Config
	if err := json.Unmarshal(b, &c); err != nil {
		return nil, err
	}
	c.Server = strings.TrimRight(strings.TrimSpace(c.Server), "/")
	if c.Server == "" || c.Token == "" {
		return nil, errors.New("config.json sem server/token")
	}
	if c.ReaderUser == "" {
		c.ReaderUser = "root"
	}
	if c.ReaderPass == "" {
		c.ReaderPass = "impinj"
	}
	return &c, nil
}

func (c *Config) Save(path string) error {
	if path == "" {
		path = ConfigPath()
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	b, _ := json.MarshalIndent(c, "", "  ")
	return os.WriteFile(path, b, 0o600)
}

// AntenaState é o que o tray mostra de cada antena.
type AntenaState struct {
	Nome       string `json:"nome"`
	IP         string `json:"ip"`
	Online     bool   `json:"online"`
	Latencia   int    `json:"latencia_ms"`
	Detalhe    string `json:"detalhe"`
	Verificado int64  `json:"verificado"`
}

// State é gravado pelo serviço a cada ciclo e lido pelo tray (processo separado, no usuário logado).
type State struct {
	Versao     string        `json:"versao"`
	ServidorOK bool          `json:"servidor_ok"`
	Erro       string        `json:"erro"`
	Server     string        `json:"server"`
	Ciclo      int64         `json:"ciclo"` // unix do último ciclo
	Antenas    []AntenaState `json:"antenas"`
}

func WriteState(s *State) error {
	if err := os.MkdirAll(PublicDir(), 0o755); err != nil {
		return err
	}
	b, _ := json.Marshal(s)
	tmp := StatePath() + ".tmp"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, StatePath())
}

func ReadState() (*State, error) {
	b, err := os.ReadFile(StatePath())
	if err != nil {
		return nil, err
	}
	var s State
	return &s, json.Unmarshal(b, &s)
}
