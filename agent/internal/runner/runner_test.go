package runner

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
	"time"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
	"cabine.solidsd.com.br/agent/internal/reader"
)

// Cabine falso: devolve 2 antenas, guarda o status recebido e entrega 1 comando uma única vez.
type fake struct {
	mu        sync.Mutex
	status    []cloud.StatusReq
	resultado map[int]cloud.Resultado
	cmdDado   bool
}

func (f *fake) handler() http.Handler {
	mux := http.NewServeMux()
	auth := func(h http.HandlerFunc) http.HandlerFunc {
		return func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("X-Agent-Token") != "tok" {
				http.Error(w, "x", 401)
				return
			}
			h(w, r)
		}
	}
	mux.HandleFunc("/api/agente/config", auth(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(cloud.Config{Agente: "hotel", IntervaloSeg: 5, Antenas: []cloud.Antena{
			{ID: 1, Nome: "r700", Modelo: "ImpinjR700", IP: "10.0.0.1"}, {ID: 2, Nome: "edge", Modelo: "AcuraEdgeR30", IP: "10.0.0.2", Porta: 8080}}})
	}))
	mux.HandleFunc("/api/agente/status", auth(func(w http.ResponseWriter, r *http.Request) {
		var s cloud.StatusReq
		json.NewDecoder(r.Body).Decode(&s)
		f.mu.Lock()
		defer f.mu.Unlock()
		f.status = append(f.status, s)
		resp := cloud.StatusResp{OK: true}
		if !f.cmdDado {
			f.cmdDado = true
			c := `{"a":1}`
			resp.Comandos = []cloud.Comando{{ID: 7, AntenaID: 1, Metodo: "PUT", Caminho: "/api/v1/x", Corpo: &c}}
		}
		json.NewEncoder(w).Encode(resp)
	}))
	mux.HandleFunc("/api/agente/comandos/7/resultado", auth(func(w http.ResponseWriter, r *http.Request) {
		var res cloud.Resultado
		json.NewDecoder(r.Body).Decode(&res)
		f.mu.Lock()
		f.resultado = map[int]cloud.Resultado{7: res}
		f.mu.Unlock()
		w.Write([]byte(`{"ok":true}`))
	}))
	return mux
}

func TestCicloEnviaStatusExecutaComandoEHisterese(t *testing.T) {
	t.Setenv("CABINE_AGENT_DIR", t.TempDir())
	f := &fake{}
	srv := httptest.NewServer(f.handler())
	defer srv.Close()

	cfg := &config.Config{Server: srv.URL, Token: "tok", ReaderUser: "root", ReaderPass: "impinj"}
	r := New(cfg)
	r.Now = func() time.Time { return time.Unix(1000, 0) }
	r.Probe = func(_ *config.Config, a cloud.Antena) reader.Probe {
		if a.Nome == "edge" {
			return reader.Probe{Detalhe: "timeout"} // sempre fora
		}
		return reader.Probe{Online: true, LatenciaMs: 12, Detalhe: "ok", Extra: map[string]any{"status": "running"}}
	}
	var execCalls []string
	r.Exec = func(_ *config.Config, a cloud.Antena, m, p string, c *string) cloud.Resultado {
		execCalls = append(execCalls, m+" "+p+" "+*c+" @"+a.Nome)
		return cloud.Resultado{OK: true, HTTPStatus: 200, Resposta: "{}"}
	}

	st := r.Cycle() // 1º ciclo: a edge falhou UMA vez -> ainda "online" (histerese)
	if len(f.status) != 1 || len(f.status[0].Antenas) != 2 {
		t.Fatalf("status não enviado: %+v", f.status)
	}
	if !f.status[0].Antenas[1].Online {
		t.Fatalf("1ª falha não deveria derrubar a antena")
	}
	if len(execCalls) != 1 || execCalls[0] != `PUT /api/v1/x {"a":1} @r700` {
		t.Fatalf("comando não executado: %v", execCalls)
	}
	if !f.resultado[7].OK || f.resultado[7].HTTPStatus != 200 {
		t.Fatalf("resultado não reportado: %+v", f.resultado)
	}
	if !st.ServidorOK {
		t.Fatalf("servidor deveria estar ok: %s", st.Erro)
	}

	r.Cycle() // 2º ciclo: segunda falha seguida -> offline
	if f.status[1].Antenas[1].Online {
		t.Fatalf("2ª falha consecutiva deveria marcar offline")
	}
	if len(execCalls) != 1 {
		t.Fatalf("comando não pode ser reexecutado")
	}
	// estado local para o tray
	got, err := config.ReadState()
	if err != nil || len(got.Antenas) != 2 || got.Antenas[1].Online {
		t.Fatalf("state.json incorreto: %+v %v", got, err)
	}
	_ = os.Remove(config.StatePath())
}

func TestTokenInvalidoGeraErroNoEstado(t *testing.T) {
	t.Setenv("CABINE_AGENT_DIR", t.TempDir())
	srv := httptest.NewServer((&fake{}).handler())
	defer srv.Close()
	r := New(&config.Config{Server: srv.URL, Token: "errado"})
	st := r.Cycle()
	if st.ServidorOK || st.Erro == "" {
		t.Fatalf("esperava erro de token: %+v", st)
	}
}

func TestAntenaDeEvasaoFicaOfflineNaPrimeiraFalha(t *testing.T) {
	t.Setenv("CABINE_AGENT_DIR", t.TempDir())
	mux := http.NewServeMux()
	var enviado cloud.StatusReq
	mux.HandleFunc("/api/agente/config", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(cloud.Config{IntervaloSeg: 5, Antenas: []cloud.Antena{{ID: 1, Nome: "saida", Modelo: "AcuraEdgeR30", IP: "10.0.0.2", Evasao: true}}})
	})
	mux.HandleFunc("/api/agente/status", func(w http.ResponseWriter, r *http.Request) {
		json.NewDecoder(r.Body).Decode(&enviado)
		w.Write([]byte(`{"ok":true,"comandos":[]}`))
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()
	r := New(&config.Config{Server: srv.URL, Token: "x"})
	r.Probe = func(*config.Config, cloud.Antena) reader.Probe { return reader.Probe{Detalhe: "timeout"} }
	r.Cycle()
	if len(enviado.Antenas) != 1 || enviado.Antenas[0].Online {
		t.Fatalf("evasão deveria ficar offline já na 1ª falha: %+v", enviado.Antenas)
	}
	if got := enviado.Antenas[0].Detalhe; got != "[evasão] timeout" {
		t.Fatalf("detalhe: %q", got)
	}
}
