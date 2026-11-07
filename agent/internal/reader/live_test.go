package reader

import (
	"os"
	"testing"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
)

// Teste manual contra equipamentos reais da LAN: LIVE=1 go test ./internal/reader -run Live -v
func TestLiveLAN(t *testing.T) {
	if os.Getenv("LIVE") == "" {
		t.Skip("defina LIVE=1 para testar contra os leitores reais")
	}
	cfg := &config.Config{ReaderUser: os.Getenv("READER_USER"), ReaderPass: os.Getenv("READER_PASS")}
	for _, a := range []cloud.Antena{
		{ID: 1, Nome: "impinj15f21c", Modelo: "ImpinjR700", IP: "192.168.30.90"},
		{ID: 2, Nome: "edge-recepcao (porta 8080 do eTrace)", Modelo: "AcuraEdgeR30", IP: "192.168.30.32", Porta: 8080},
		{ID: 4, Nome: "edge-servico (porta 8080 do eTrace)", Modelo: "AcuraEdgeR30", IP: "192.168.30.104", Porta: 8080},
	} {
		p := Check(cfg, a)
		t.Logf("%-40s online=%-5v %4dms  %s  %v", a.Nome, p.Online, p.LatenciaMs, p.Detalhe, p.Extra)
	}
}
