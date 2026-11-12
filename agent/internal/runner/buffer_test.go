package runner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
	"cabine.solidsd.com.br/agent/internal/dvr"
)

// Cabine falso com 1 câmera em modo buffer (Mibo) e, se pedir, 1 clipe dela.
type bufSrv struct {
	mu      sync.Mutex
	cameras []cloud.CameraCfg
	clipes  []cloud.Clipe
	enviado []byte
	erro    string
	srv     *httptest.Server
}

func novoBufSrv(t *testing.T) *bufSrv {
	b := &bufSrv{cameras: []cloud.CameraCfg{{ID: 4, Nome: "Mibo saída", IP: "192.168.30.70", Canal: 1}}}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/agente/config", func(w http.ResponseWriter, r *http.Request) {
		b.mu.Lock()
		defer b.mu.Unlock()
		json.NewEncoder(w).Encode(cloud.Config{IntervaloSeg: 5, Cameras: b.cameras})
	})
	mux.HandleFunc("/api/agente/status", func(w http.ResponseWriter, r *http.Request) {
		b.mu.Lock()
		defer b.mu.Unlock()
		json.NewEncoder(w).Encode(cloud.StatusResp{OK: true, Clipes: b.clipes})
		b.clipes = nil
	})
	mux.HandleFunc("/api/agente/clipes/5/arquivo", func(w http.ResponseWriter, r *http.Request) {
		buf := make([]byte, 64)
		n, _ := r.Body.Read(buf)
		b.mu.Lock()
		b.enviado = buf[:n]
		b.mu.Unlock()
		w.Write([]byte(`{"ok":true}`))
	})
	mux.HandleFunc("/api/agente/clipes/5/erro", func(w http.ResponseWriter, r *http.Request) {
		var m map[string]string
		json.NewDecoder(r.Body).Decode(&m)
		b.mu.Lock()
		b.erro = m["mensagem"]
		b.mu.Unlock()
		w.Write([]byte(`{"ok":true}`))
	})
	b.srv = httptest.NewServer(mux)
	t.Cleanup(b.srv.Close)
	return b
}

func runnerBuf(t *testing.T, b *bufSrv, cfg config.Config) (*Runner, *[]string, *sync.Mutex) {
	t.Setenv("CABINE_AGENT_DIR", t.TempDir())
	cfg.Server, cfg.Token, cfg.FFmpeg = b.srv.URL, "tok", "ffmpeg-falso"
	r := New(&cfg)
	var mu sync.Mutex
	var urls []string
	r.Record = func(ctx context.Context, rec *dvr.Recorder) {
		mu.Lock()
		urls = append(urls, rec.URL)
		mu.Unlock()
		<-ctx.Done()
	}
	t.Cleanup(func() { r.StopRecorders(); r.WaitRecorders() })
	return r, &urls, &mu
}

func TestGravadorSobeUmaVezEPara(t *testing.T) {
	b := novoBufSrv(t)
	r, urls, mu := runnerBuf(t, b, config.Config{DVRUser: "admin", DVRPass: "s@nha"})
	r.Cycle()
	r.Cycle()
	r.Cycle()
	time.Sleep(50 * time.Millisecond)
	mu.Lock()
	if len(*urls) != 1 || (*urls)[0] != "rtsp://admin:s%40nha@192.168.30.70:554/cam/realmonitor?channel=1&subtype=0" {
		t.Fatalf("gravador: %v", *urls)
	}
	mu.Unlock()
	b.mu.Lock()
	b.cameras = nil
	b.mu.Unlock() // câmera removida no painel
	r.Cycle()
	r.recMu.Lock()
	n := len(r.recorders)
	r.recMu.Unlock()
	if n != 0 {
		t.Fatalf("gravador não parou: %d", n)
	}
	r.WaitRecorders() // retorna porque o contexto foi cancelado
}

func TestGravadorTrocaDeCaminhoReinicia(t *testing.T) {
	b := novoBufSrv(t)
	r, urls, mu := runnerBuf(t, b, config.Config{DVRUser: "admin", DVRPass: "x"})
	r.Cycle()
	b.mu.Lock()
	b.cameras[0].RTSPPath = "/stream{canal}"
	b.mu.Unlock()
	r.Cycle()
	time.Sleep(50 * time.Millisecond)
	mu.Lock()
	defer mu.Unlock()
	if len(*urls) != 2 || !strings.HasSuffix((*urls)[1], "/stream1") {
		t.Fatalf("urls: %v", *urls)
	}
}

func TestSemCredencialNaoGravaEClipeAvisaErro(t *testing.T) {
	b := novoBufSrv(t)
	r, urls, mu := runnerBuf(t, b, config.Config{}) // sem dvrUser/dvrPass
	b.clipes = []cloud.Clipe{{ID: 5, CameraID: 4, Tipo: "rtsp", Camera: "Mibo saída", Inicio: 1000, Fim: 1070}}
	r.Cycle()
	r.WaitClipes()
	mu.Lock()
	defer mu.Unlock()
	if len(*urls) != 0 {
		t.Fatal("não deveria gravar sem credencial")
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if !strings.Contains(b.erro, "falta usuário/senha") {
		t.Fatalf("erro: %q", b.erro)
	}
}

func TestClipeRtspUsaOBufferDoGravador(t *testing.T) {
	b := novoBufSrv(t)
	r, _, _ := runnerBuf(t, b, config.Config{Cameras: map[string]config.ReaderCred{"Mibo saída": {User: "u", Pass: "p"}}})
	var gotName string
	var gotIni, gotFim time.Time
	r.Build = func(ctx context.Context, rec *dvr.Recorder, o dvr.Options, i, f time.Time, out string) error {
		gotName, gotIni, gotFim = rec.Name, i, f
		return os.WriteFile(out, []byte("\x00\x00\x00\x18ftypmp42"), 0o600)
	}
	r.Cycle() // sobe o gravador
	b.mu.Lock()
	b.clipes = []cloud.Clipe{{ID: 5, CameraID: 4, Tipo: "rtsp", Camera: "Mibo saída", Inicio: 1000, Fim: 1070}}
	b.mu.Unlock()
	r.Cycle()
	r.WaitClipes()
	b.mu.Lock()
	defer b.mu.Unlock()
	if gotName != "Mibo saída" || gotIni.Unix() != 1000 || gotFim.Unix() != 1070 {
		t.Fatalf("build: %q %v %v", gotName, gotIni.Unix(), gotFim.Unix())
	}
	if string(b.enviado) != "\x00\x00\x00\x18ftypmp42" {
		t.Fatalf("enviado: %q", b.enviado)
	}
}
