// Package runner é o laço do agente: busca as antenas no Cabine, testa cada uma, envia o status,
// executa os comandos pendentes e grava o estado local para o tray.
package runner

import (
	"context"
	"log"
	"os"
	"sync"
	"time"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
	"cabine.solidsd.com.br/agent/internal/reader"
)

// Falhas consecutivas antes de reportar uma antena como offline (evita alarme por 1 pacote perdido).
const falhasParaOffline = 2

type Runner struct {
	Cfg    *config.Config
	Client *cloud.Client
	Probe  func(*config.Config, cloud.Antena) reader.Probe
	Exec   func(*config.Config, cloud.Antena, string, string, *string) cloud.Resultado
	Now    func() time.Time

	antenas   []cloud.Antena
	falhas    map[int]int
	intervalo time.Duration
}

func New(cfg *config.Config) *Runner {
	return &Runner{Cfg: cfg, Client: cloud.New(cfg.Server, cfg.Token), Probe: reader.Check, Exec: reader.Exec,
		Now: time.Now, falhas: map[int]int{}, intervalo: 15 * time.Second}
}

// Cycle executa um ciclo completo e devolve o estado gravado.
func (r *Runner) Cycle() *config.State {
	st := &config.State{Versao: config.Version, Server: r.Cfg.Server, Ciclo: r.Now().Unix()}
	if c, err := r.Client.GetConfig(); err == nil {
		r.antenas = c.Antenas
		if c.IntervaloSeg >= 5 {
			r.intervalo = time.Duration(c.IntervaloSeg) * time.Second
		}
	} else {
		st.Erro = "config: " + err.Error()
		log.Printf("[runner] %v", err)
	}

	// sonda todas as antenas em paralelo
	res := make([]reader.Probe, len(r.antenas))
	var wg sync.WaitGroup
	for i, a := range r.antenas {
		wg.Add(1)
		go func(i int, a cloud.Antena) {
			defer wg.Done()
			res[i] = r.Probe(r.Cfg, a)
		}(i, a)
	}
	wg.Wait()

	req := &cloud.StatusReq{Versao: config.Version}
	req.Host, _ = os.Hostname()
	for i, a := range r.antenas {
		p := res[i]
		if p.Online {
			r.falhas[a.ID] = 0
		} else {
			r.falhas[a.ID]++
		}
		limite := falhasParaOffline
		if a.Evasao {
			limite = 1 // antena de evasão: não esperar uma segunda falha
		}
		online := p.Online || r.falhas[a.ID] < limite // 1ª falha: ainda não derruba (exceto evasão)
		detalhe := p.Detalhe
		if a.Evasao {
			detalhe = "[evasão] " + detalhe
		}
		if !p.Online && online {
			detalhe = "falha isolada, reconfirmando: " + p.Detalhe
		}
		req.Antenas = append(req.Antenas, cloud.StatusAntena{ID: a.ID, Online: online, LatenciaMs: p.LatenciaMs, Detalhe: detalhe, Extra: p.Extra})
		st.Antenas = append(st.Antenas, config.AntenaState{Nome: a.Nome, IP: a.IP, Online: online, Latencia: p.LatenciaMs, Detalhe: detalhe, Verificado: r.Now().Unix()})
	}

	resp, err := r.Client.PostStatus(req)
	if err != nil {
		st.Erro = "status: " + err.Error()
		log.Printf("[runner] %v", err)
	} else {
		st.ServidorOK = st.Erro == ""
		for _, cmd := range resp.Comandos {
			r.run(cmd)
		}
	}
	if err := config.WriteState(st); err != nil {
		log.Printf("[runner] gravar estado: %v", err)
	}
	return st
}

func (r *Runner) run(cmd cloud.Comando) {
	var ant *cloud.Antena
	for i := range r.antenas {
		if r.antenas[i].ID == cmd.AntenaID {
			ant = &r.antenas[i]
		}
	}
	var out cloud.Resultado
	if ant == nil {
		out = cloud.Resultado{Resposta: "antena não está na lista deste agente (sem IP cadastrado?)"}
	} else {
		log.Printf("[runner] comando #%d %s %s em %s", cmd.ID, cmd.Metodo, cmd.Caminho, ant.Nome)
		out = r.Exec(r.Cfg, *ant, cmd.Metodo, cmd.Caminho, cmd.Corpo)
	}
	if err := r.Client.PostResultado(cmd.ID, &out); err != nil {
		log.Printf("[runner] resultado do comando #%d: %v", cmd.ID, err)
	}
}

// Loop roda até o contexto ser cancelado.
func (r *Runner) Loop(ctx context.Context) {
	for {
		r.Cycle()
		select {
		case <-ctx.Done():
			return
		case <-time.After(r.intervalo):
		}
	}
}
