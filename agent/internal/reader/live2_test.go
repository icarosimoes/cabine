package reader

import (
	"os"
	"testing"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
)

// Só leituras (GET) no R700 real: LIVE=1 READER_USER=... READER_PASS=... go test ./internal/reader -run LiveR700 -v
func TestLiveR700(t *testing.T) {
	if os.Getenv("LIVE") == "" {
		t.Skip("LIVE=1")
	}
	cfg := &config.Config{ReaderUser: os.Getenv("READER_USER"), ReaderPass: os.Getenv("READER_PASS")}
	a := cloud.Antena{ID: 1, Nome: "impinj15f21c", Modelo: "ImpinjR700", IP: "192.168.30.90"}
	p := Check(cfg, a)
	t.Logf("CHECK online=%v %dms detalhe=%q extra=%v", p.Online, p.LatenciaMs, p.Detalhe, p.Extra)
	for _, path := range []string{"/api/v1/status", "/api/v1/system", "/api/v1/system/rfid/interface", "/api/v1/profiles", "/api/v1/profiles/inventory/presets", "/api/v1/profiles/inventory"} {
		r := Exec(cfg, a, "GET", path, nil)
		resp := r.Resposta
		if len(resp) > 700 {
			resp = resp[:700] + "…"
		}
		t.Logf("GET %-40s ok=%v http=%d\n%s", path, r.OK, r.HTTPStatus, resp)
	}
}
