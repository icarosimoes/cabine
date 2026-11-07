package reader

import (
	"os"
	"testing"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
)

// Só GET. LIVE=1 READER_USER=.. READER_PASS=.. go test ./internal/reader -run LiveExplora -v
func TestLiveExplora(t *testing.T) {
	if os.Getenv("LIVE") == "" {
		t.Skip("LIVE=1")
	}
	cfg := &config.Config{ReaderUser: os.Getenv("READER_USER"), ReaderPass: os.Getenv("READER_PASS")}
	a := cloud.Antena{ID: 1, Nome: "impinj15f21c", Modelo: "ImpinjR700", IP: "192.168.30.90"}
	for _, path := range []string{"/api/v1/profiles/inventory/presets/etrace", "/api/v1/profiles/inventory/presets/default",
		"/api/v1/http-stream", "/api/v1/system/time", "/api/v1/system/network", "/api/v1/system/region", "/api/v1/system/power", "/api/v1/system/image"} {
		r := Exec(cfg, a, "GET", path, nil)
		resp := r.Resposta
		if len(resp) > 1800 {
			resp = resp[:1800] + "…"
		}
		t.Logf("GET %-48s ok=%v http=%d\n%s", path, r.OK, r.HTTPStatus, resp)
	}
}
