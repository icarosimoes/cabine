package runner

import (
	"context"
	"fmt"
	"log"
	"strings"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/saida"
)

type saidaEntry struct {
	key    string
	cancel context.CancelFunc
}

// syncSaida mantém um coletor por antena Acura marcada como "evasão" (só com "coletaEvasao": true no config.json).
func (r *Runner) syncSaida(antenas []cloud.Antena) {
	r.saidaMu.Lock()
	defer r.saidaMu.Unlock()
	want := map[int]bool{}
	if r.Cfg.ColetaEvasao {
		for _, a := range antenas {
			if !a.Evasao || a.IP == "" || a.Porta <= 0 || !strings.Contains(strings.ToLower(a.Modelo), "acura") {
				continue
			}
			want[a.ID] = true
			addr := fmt.Sprintf("%s:%d", a.IP, a.Porta)
			if e, ok := r.saidas[a.ID]; ok {
				if e.key == addr {
					continue
				}
				e.cancel()
			}
			ctx, cancel := context.WithCancel(context.Background())
			r.saidas[a.ID] = &saidaEntry{key: addr, cancel: cancel}
			id, nome := a.ID, a.Nome
			l := saida.NewListener(nome, addr, func(epc string) error { return r.Client.PostSaida(id, epc) })
			log.Printf("[saida %s] coletando evasão em %s", nome, addr)
			go l.Run(ctx)
		}
	}
	for id, e := range r.saidas {
		if !want[id] {
			e.cancel()
			delete(r.saidas, id)
		}
	}
}
