package runner

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
	"time"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
	"cabine.solidsd.com.br/agent/internal/dvr"
)

// Cabine falso que entrega 1 pedido de clipe (e o repete no ciclo seguinte, como o servidor faria se o download ainda corresse).
func servidorDeClipe(t *testing.T) (*httptest.Server, *sync.Mutex, *[]byte, *string) {
	var mu sync.Mutex
	var recebido []byte
	var erro string
	mux := http.NewServeMux()
	mux.HandleFunc("/api/agente/config", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(cloud.Config{IntervaloSeg: 5})
	})
	mux.HandleFunc("/api/agente/status", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(cloud.StatusResp{OK: true, Clipes: []cloud.Clipe{
			{ID: 9, Inicio: 1000, Fim: 1070, Camera: "dvr-saida", IP: "10.0.0.50", Porta: 80, Canal: 3}}})
	})
	mux.HandleFunc("/api/agente/clipes/9/arquivo", func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		mu.Lock()
		recebido = b
		mu.Unlock()
		w.Write([]byte(`{"ok":true}`))
	})
	mux.HandleFunc("/api/agente/clipes/9/erro", func(w http.ResponseWriter, r *http.Request) {
		var b map[string]string
		json.NewDecoder(r.Body).Decode(&b)
		mu.Lock()
		erro = b["mensagem"]
		mu.Unlock()
		w.Write([]byte(`{"ok":true}`))
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv, &mu, &recebido, &erro
}

func novo(t *testing.T, srv *httptest.Server, cfg config.Config) *Runner {
	t.Setenv("CABINE_AGENT_DIR", t.TempDir())
	cfg.Server, cfg.Token = srv.URL, "tok"
	return New(&cfg)
}

func TestClipeBuscadoNoDVREEnviado(t *testing.T) {
	srv, mu, recebido, _ := servidorDeClipe(t)
	r := novo(t, srv, config.Config{DVRUser: "geral", DVRPass: "x", Cameras: map[string]config.ReaderCred{"dvr-saida": {User: "saida", Pass: "y"}}})
	var chamadas int
	var cam dvr.Camera
	var ini, fim time.Time
	r.Fetch = func(ctx context.Context, c dvr.Camera, o dvr.Options, i, f time.Time, out string) error {
		mu.Lock()
		chamadas++
		cam, ini, fim = c, i, f
		mu.Unlock()
		return os.WriteFile(out, []byte("\x00\x00\x00\x18ftypmp42"), 0o600)
	}
	r.Cycle()
	r.Cycle() // o servidor repete o pedido; o 2º ciclo não pode baixar de novo se o 1º ainda correr, mas ao fim pode repetir — só exigimos que o envio chegue
	r.WaitClipes()
	mu.Lock()
	defer mu.Unlock()
	if string(*recebido) != "\x00\x00\x00\x18ftypmp42" {
		t.Fatalf("servidor não recebeu o mp4: %q", *recebido)
	}
	if chamadas < 1 || cam.Host != "10.0.0.50" || cam.Canal != 3 || cam.HTTPPort != 80 {
		t.Fatalf("câmera errada: %+v (chamadas=%d)", cam, chamadas)
	}
	if cam.User != "saida" || cam.Pass != "y" { // credencial por câmera vence a geral
		t.Fatalf("credencial: %+v", cam)
	}
	if ini.Unix() != 1000 || fim.Unix() != 1070 {
		t.Fatalf("janela: %v → %v", ini.Unix(), fim.Unix())
	}
	if ents, _ := os.ReadDir(config.DataDir() + "/clipes"); len(ents) != 0 {
		t.Fatalf("arquivo temporário não foi apagado: %v", ents)
	}
}

func TestClipeComFalhaAvisaOServidor(t *testing.T) {
	srv, mu, recebido, erro := servidorDeClipe(t)
	r := novo(t, srv, config.Config{DVRUser: "u", DVRPass: "p"})
	r.Fetch = func(context.Context, dvr.Camera, dvr.Options, time.Time, time.Time, string) error {
		return errors.New("sem gravação no canal 3")
	}
	r.Cycle()
	r.WaitClipes()
	mu.Lock()
	defer mu.Unlock()
	if *erro != "sem gravação no canal 3" || len(*recebido) != 0 {
		t.Fatalf("erro=%q recebido=%q", *erro, *recebido)
	}
}

func TestClipeNaoDuplicaEnquantoBaixa(t *testing.T) {
	srv, mu, _, _ := servidorDeClipe(t)
	r := novo(t, srv, config.Config{DVRUser: "u", DVRPass: "p"})
	solta := make(chan struct{})
	var chamadas int
	r.Fetch = func(ctx context.Context, c dvr.Camera, o dvr.Options, i, f time.Time, out string) error {
		mu.Lock()
		chamadas++
		mu.Unlock()
		<-solta
		return os.WriteFile(out, []byte("\x00\x00\x00\x18ftypmp42"), 0o600)
	}
	r.Cycle()
	r.Cycle()
	r.Cycle() // servidor repete o pedido com o download ainda em curso
	close(solta)
	r.WaitClipes()
	mu.Lock()
	defer mu.Unlock()
	if chamadas != 1 {
		t.Fatalf("baixou %d vezes o mesmo clipe", chamadas)
	}
}
