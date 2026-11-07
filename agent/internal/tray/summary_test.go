package tray

import (
	"testing"
	"time"

	"cabine.solidsd.com.br/agent/internal/config"
)

func TestSummarize(t *testing.T) {
	now := time.Unix(10_000, 0)
	ok := &config.State{ServidorOK: true, Ciclo: now.Unix() - 5, Antenas: []config.AntenaState{{Nome: "a", Online: true, Latencia: 9}}}
	if k, _, _ := Summarize(ok, now); k != OK {
		t.Fatal("deveria ser OK")
	}
	off := &config.State{ServidorOK: true, Ciclo: now.Unix(), Antenas: []config.AntenaState{{Nome: "a", Online: true}, {Nome: "b", Detalhe: "timeout"}}}
	if k, msg, l := Summarize(off, now); k != Fail || msg != "1 antena(s) offline" || len(l) != 2 {
		t.Fatalf("offline: %v %q", k, msg)
	}
	if k, _, _ := Summarize(&config.State{ServidorOK: false, Ciclo: now.Unix(), Erro: "x"}, now); k != Fail {
		t.Fatal("sem servidor = Fail")
	}
	if k, msg, _ := Summarize(&config.State{ServidorOK: true, Ciclo: now.Unix() - 200}, now); k != Fail || msg == "" {
		t.Fatal("ciclo antigo = serviço parado")
	}
	if k, _, _ := Summarize(nil, now); k != Unknown {
		t.Fatal("sem estado = Unknown")
	}
}
