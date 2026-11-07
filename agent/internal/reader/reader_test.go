package reader

import (
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
)

func antenaDe(t *testing.T, srvURL, modelo string) cloud.Antena {
	u, _ := url.Parse(srvURL)
	host, port, _ := net.SplitHostPort(u.Host)
	p, _ := strconv.Atoi(port)
	return cloud.Antena{ID: 1, Nome: "a", Modelo: modelo, IP: host, Porta: p}
}

func TestR700OnlineCredencialEErro(t *testing.T) {
	var gotUser, gotPass string
	ok := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUser, gotPass, _ = r.BasicAuth()
		if r.URL.Path != "/api/v1/status" {
			http.NotFound(w, r)
			return
		}
		w.Write([]byte(`{"status":"running","serialNumber":"37xx"}`))
	}))
	defer ok.Close()
	cfg := &config.Config{ReaderUser: "root", ReaderPass: "impinj"}
	p := Check(cfg, antenaDe(t, ok.URL, "ImpinjR700"))
	if !p.Online || !strings.Contains(p.Detalhe, "running") || p.Extra["serialNumber"] != "37xx" {
		t.Fatalf("probe: %+v", p)
	}
	if gotUser != "root" || gotPass != "impinj" {
		t.Fatalf("credenciais não enviadas: %q %q", gotUser, gotPass)
	}

	unauth := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Error(w, "no", 401) }))
	defer unauth.Close()
	p = Check(cfg, antenaDe(t, unauth.URL, "ImpinjR700"))
	if !p.Online || !strings.Contains(p.Detalhe, "credencial") {
		t.Fatalf("401 deve ser online com aviso de credencial: %+v", p)
	}

	if p := Check(cfg, cloud.Antena{Nome: "x", Modelo: "ImpinjR700", IP: "127.0.0.1", Porta: 1}); p.Online {
		t.Fatalf("porta fechada não pode ficar online")
	}
}

const paginaAcura = `<html><title>EDGE30R Autoid</title><body><div>Edge-30R AutoID Normal</div>
<div>Vers&atilde;o: &nbsp; 4.1.0 </div><div>Nome:&nbsp; EDGE30R-57D65E </div><div>Tempo de atividade:&nbsp; 26d 23:46:15 </div>
<div>Status:&nbsp; OK - Modo de leitura autom&aacute;tica </div><div>SN:&nbsp; 6480592 </div></body></html>`

func TestParseAcuraPage(t *testing.T) {
	e := ParseAcuraPage(paginaAcura)
	if e["firmware"] != "4.1.0" || e["nome"] != "EDGE30R-57D65E" || e["uptime"] != "26d 23:46:15" || e["serial"] != "6480592" ||
		e["status"] != "OK - Modo de leitura automática" {
		t.Fatalf("parse: %v", e)
	}
}

func TestOutroModeloSoPorta(t *testing.T) {
	srv := httptest.NewServer(http.NotFoundHandler())
	defer srv.Close()
	if p := Check(&config.Config{}, antenaDe(t, srv.URL, "OutroLeitor")); !p.Online || !strings.Contains(p.Detalhe, "respondendo") {
		t.Fatalf("tcp deveria responder: %+v", p)
	}
}

func TestExecValidaECorreAPI(t *testing.T) {
	var metodo, corpo, path string
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		metodo, path = r.Method, r.URL.RequestURI()
		b := make([]byte, 100)
		n, _ := r.Body.Read(b)
		corpo = string(b[:n])
		w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()
	a := antenaDe(t, srv.URL, "ImpinjR700")
	cfg := &config.Config{ReaderUser: "root", ReaderPass: "impinj"}
	body := `{"x":1}`
	r := Exec(cfg, a, "put", "/api/v1/profiles/inventory/presets/default", &body)
	if !r.OK || metodo != "PUT" || corpo != body || path != "/api/v1/profiles/inventory/presets/default" {
		t.Fatalf("exec: %+v %s %s %s", r, metodo, path, corpo)
	}
	for _, c := range []struct{ m, p string }{{"DELETE", "/api/v1/x"}, {"GET", "/etc/passwd"}, {"GET", "/api/v1/../x"}} {
		if r := Exec(cfg, a, c.m, c.p, nil); r.OK || r.Resposta == "" {
			t.Fatalf("deveria recusar %v: %+v", c, r)
		}
	}
	if r := Exec(cfg, cloud.Antena{Modelo: "AcuraEdgeR30", IP: "1.1.1.1"}, "GET", "/api/v1/x", nil); r.OK {
		t.Fatalf("só R700 aceita comando")
	}
}
