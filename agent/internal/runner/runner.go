// Package runner é o laço do agente: busca as antenas no Cabine, testa cada uma, envia o status,
// executa os comandos pendentes e grava o estado local para o tray.
package runner

import (
	"context"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
	"cabine.solidsd.com.br/agent/internal/dvr"
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
	// Fetch grava o trecho do DVR em outPath (padrão: dvr.Fetch). Troca-se nos testes.
	Fetch func(ctx context.Context, cam dvr.Camera, opt dvr.Options, ini, fim time.Time, outPath string) error
	// Build monta o clipe a partir do buffer de uma câmera em modo rtsp; Record roda o gravador (padrão: (*dvr.Recorder).Run).
	Build  func(ctx context.Context, rec *dvr.Recorder, opt dvr.Options, ini, fim time.Time, outPath string) error
	Record func(ctx context.Context, rec *dvr.Recorder)

	saidaMu sync.Mutex
	saidas  map[int]*saidaEntry

	recMu     sync.Mutex
	recorders map[int]*recEntry
	recWG     sync.WaitGroup

	clipesMu   sync.Mutex
	clipesNoAr map[int]bool  // ids em andamento (o servidor pode reenviar enquanto o download corre)
	clipesSem  chan struct{} // limita downloads simultâneos
	clipesWG   sync.WaitGroup

	antenas   []cloud.Antena
	falhas    map[int]int
	intervalo time.Duration
}

func New(cfg *config.Config) *Runner {
	return &Runner{Cfg: cfg, Client: cloud.New(cfg.Server, cfg.Token), Probe: reader.Check, Exec: reader.Exec,
		Now: time.Now, Fetch: dvr.Fetch, Build: dvr.BuildClip, Record: func(ctx context.Context, r *dvr.Recorder) { r.Run(ctx) },
		recorders: map[int]*recEntry{}, saidas: map[int]*saidaEntry{}, falhas: map[int]int{}, intervalo: 15 * time.Second,
		clipesNoAr: map[int]bool{}, clipesSem: make(chan struct{}, 2)}
}

// Cycle executa um ciclo completo e devolve o estado gravado.
func (r *Runner) Cycle() *config.State {
	st := &config.State{Versao: config.Version, Server: r.Cfg.Server, Ciclo: r.Now().Unix()}
	if c, err := r.Client.GetConfig(); err == nil {
		r.antenas = c.Antenas
		r.syncRecorders(c.Cameras)
		r.syncSaida(c.Antenas)
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
		for _, cl := range resp.Clipes {
			r.startClipe(cl) // em segundo plano: o playback corre em tempo real e não pode travar o status
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

// startClipe busca o trecho no DVR e envia ao servidor, sem bloquear o ciclo de status.
func (r *Runner) startClipe(cl cloud.Clipe) {
	r.clipesMu.Lock()
	if r.clipesNoAr[cl.ID] {
		r.clipesMu.Unlock()
		return
	}
	r.clipesNoAr[cl.ID] = true
	r.clipesMu.Unlock()
	r.clipesWG.Add(1)
	go func() {
		defer r.clipesWG.Done()
		r.clipesSem <- struct{}{}
		defer func() {
			<-r.clipesSem
			r.clipesMu.Lock()
			delete(r.clipesNoAr, cl.ID)
			r.clipesMu.Unlock()
		}()
		if err := r.processaClipe(cl); err != nil {
			log.Printf("[clipe #%d] %v", cl.ID, err)
			if e2 := r.Client.PostClipeErro(cl.ID, err.Error()); e2 != nil {
				log.Printf("[clipe #%d] avisar erro: %v", cl.ID, e2)
			}
		}
	}()
}

// credenciais da câmera: entrada própria em `cameras` (por nome) vence dvrUser/dvrPass.
func (r *Runner) cred(nome string) (string, string) {
	if c, ok := r.Cfg.Cameras[nome]; ok {
		return c.User, c.Pass
	}
	return r.Cfg.DVRUser, r.Cfg.DVRPass
}

func (r *Runner) camera(cl cloud.Clipe) dvr.Camera {
	u, p := r.cred(cl.Camera)
	brand := ""
	if cl.Tipo == "hikvision" {
		brand = "hikvision"
	}
	return dvr.Camera{Host: cl.IP, HTTPPort: cl.Porta, RTSPPort: r.Cfg.RTSPPort, Canal: cl.Canal, User: u, Pass: p, Brand: brand}
}

type recEntry struct {
	rec    *dvr.Recorder
	key    string // muda quando IP/canal/caminho/credencial mudam: o gravador é recriado
	cancel context.CancelFunc
}

func ffmpegDe(cfg *config.Config) string {
	if cfg.FFmpeg != "" {
		return cfg.FFmpeg
	}
	return ""
}

// syncRecorders deixa rodando um gravador por câmera em modo buffer da configuração do servidor.
func (r *Runner) syncRecorders(cams []cloud.CameraCfg) {
	r.recMu.Lock()
	defer r.recMu.Unlock()
	want := map[int]bool{}
	for _, c := range cams {
		want[c.ID] = true
		u, p := r.cred(c.Nome)
		if u == "" || p == "" {
			log.Printf("[gravador %s] sem usuário/senha no config.json (dvrUser/dvrPass ou cameras.%q): não gravo", c.Nome, c.Nome)
			if e, ok := r.recorders[c.ID]; ok {
				e.cancel()
				delete(r.recorders, c.ID)
			}
			continue
		}
		url := dvr.LiveURL(dvr.Camera{Host: c.IP, RTSPPort: r.Cfg.RTSPPort, Canal: c.Canal, User: u, Pass: p}, c.RTSPPath)
		if e, ok := r.recorders[c.ID]; ok && e.key == url {
			continue
		} else if ok {
			e.cancel()
		}
		ff := ffmpegDe(r.Cfg)
		if ff == "" {
			var err error
			if ff, err = dvr.ResolveFFmpeg(""); err != nil {
				log.Printf("[gravador %s] %v", c.Nome, err)
				continue
			}
		}
		ctx, cancel := context.WithCancel(context.Background())
		rec := &dvr.Recorder{Name: c.Nome, URL: url, Pass: p, FFmpeg: ff, Dir: filepath.Join(config.DataDir(), "buffer", fmt.Sprint(c.ID))}
		r.recorders[c.ID] = &recEntry{rec: rec, key: url, cancel: cancel}
		log.Printf("[gravador %s] iniciando (canal %d, buffer em %s)", c.Nome, c.Canal, rec.Dir)
		r.recWG.Add(1)
		go func() { defer r.recWG.Done(); r.Record(ctx, rec) }()
	}
	for id, e := range r.recorders {
		if !want[id] {
			e.cancel()
			delete(r.recorders, id)
		}
	}
}

// StopRecorders encerra todos os gravadores (desligamento do serviço e testes).
func (r *Runner) StopRecorders() {
	r.recMu.Lock()
	for id, e := range r.recorders {
		e.cancel()
		delete(r.recorders, id)
	}
	r.recMu.Unlock()
}

func (r *Runner) processaClipe(cl cloud.Clipe) error {
	dir := filepath.Join(config.DataDir(), "clipes")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	out := filepath.Join(dir, fmt.Sprintf("%d.mp4", cl.ID))
	defer os.Remove(out)
	ini, fim := time.Unix(int64(cl.Inicio), 0), time.Unix(int64(cl.Fim), 0)
	log.Printf("[clipe #%d] %s canal %d, %s → %s", cl.ID, cl.Camera, cl.Canal, ini.Format("15:04:05"), fim.Format("15:04:05"))
	opt := dvr.Options{FFmpeg: r.Cfg.FFmpeg, Codec: r.Cfg.VideoCodec}
	if cl.Tipo == "rtsp" {
		r.recMu.Lock()
		e := r.recorders[cl.CameraID]
		r.recMu.Unlock()
		if e == nil {
			return fmt.Errorf("a câmera %q é de modo buffer, mas este agente não está gravando o ao vivo dela (falta usuário/senha no config.json ou o agente é novo demais)", cl.Camera)
		}
		if err := r.Build(context.Background(), e.rec, opt, ini, fim, out); err != nil {
			return err
		}
	} else if err := r.Fetch(context.Background(), r.camera(cl), opt, ini, fim, out); err != nil {
		return err
	}
	if err := r.Client.PutClipe(cl.ID, out); err != nil {
		return fmt.Errorf("enviar ao servidor: %w", err)
	}
	log.Printf("[clipe #%d] enviado", cl.ID)
	return nil
}

// WaitRecorders espera os gravadores terminarem (depois de StopRecorders).
func (r *Runner) WaitRecorders() { r.recWG.Wait() }

// WaitClipes espera os downloads em andamento (usado nos testes e no desligamento).
func (r *Runner) WaitClipes() { r.clipesWG.Wait() }

// Loop roda até o contexto ser cancelado.
func (r *Runner) Loop(ctx context.Context) {
	for {
		r.Cycle()
		select {
		case <-ctx.Done():
			r.StopRecorders()
			return
		case <-time.After(r.intervalo):
		}
	}
}
