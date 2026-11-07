// cabine-agent — binário único (Windows): serviço, tray e instalador.
//
//	cabine-agent install --server URL --token TOKEN   instala o serviço e o tray (Administrador)
//	cabine-agent uninstall                            remove tudo (Administrador)
//	cabine-agent status                               estado do serviço e das antenas
//	cabine-agent tray                                 ícone da bandeja (iniciado no logon pelo instalador)
//	cabine-agent run                                  roda em primeiro plano (diagnóstico)
//	(sem argumentos, iniciado pelo Windows)           modo serviço
//
// Compilado com -H windowsgui: logs vão para ProgramData\CabineAgent\public\agent.log.
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"cabine.solidsd.com.br/agent/internal/cloud"
	"cabine.solidsd.com.br/agent/internal/config"
	"cabine.solidsd.com.br/agent/internal/install"
	"cabine.solidsd.com.br/agent/internal/runner"
	"cabine.solidsd.com.br/agent/internal/service"
	"cabine.solidsd.com.br/agent/internal/tray"
	"cabine.solidsd.com.br/agent/internal/winutil"
)

var version = "" // injetado por -ldflags

func main() {
	if version != "" {
		config.Version = version
	}
	if service.IsWindowsService() {
		setupLog()
		cfg, err := config.Load("")
		if err != nil {
			log.Fatalf("[agent] config: %v", err)
		}
		log.Printf("[agent] versao=%s modo=service servidor=%s", config.Version, cfg.Server)
		if err := service.Run(cfg); err != nil {
			log.Fatalf("[agent] service: %v", err)
		}
		return
	}
	cmd := ""
	if len(os.Args) > 1 {
		cmd = os.Args[1]
	}
	switch cmd {
	case "tray":
		setupLog()
		tray.Run()
	case "install":
		winutil.AttachConsole()
		doInstall(os.Args[2:])
	case "uninstall":
		winutil.AttachConsole()
		if !winutil.IsAdmin() {
			fatal("execute como Administrador")
		}
		if err := install.Uninstall(); err != nil {
			fatal(err.Error())
		}
		out("Cabine RFID Agent removido.")
	case "status":
		winutil.AttachConsole()
		doStatus()
	case "run":
		winutil.AttachConsole()
		cfg, err := config.Load("")
		if err != nil {
			fatal("config: " + err.Error())
		}
		log.SetFlags(log.LstdFlags)
		out("rodando em primeiro plano (Ctrl+C para sair)…")
		_ = service.Run(cfg)
	case "version", "--version", "-v":
		winutil.AttachConsole()
		out("cabine-agent " + config.Version)
	default:
		winutil.AttachConsole()
		out(usage)
	}
}

const usage = `cabine-agent — monitora e configura os leitores RFID do hotel

  install --server URL --token TOKEN   instala serviço + ícone na bandeja (Administrador)
  uninstall                            remove (Administrador)
  status                               estado do serviço e das antenas
  tray                                 ícone da bandeja
  run                                  roda em primeiro plano (diagnóstico)
  version`

func doInstall(args []string) {
	fs := flag.NewFlagSet("install", flag.ExitOnError)
	server := fs.String("server", config.DefaultServer, "URL do Cabine")
	token := fs.String("token", "", "token do agente (Antenas > Agentes no painel)")
	_ = fs.Parse(args)
	if !winutil.IsAdmin() {
		fatal("execute como Administrador")
	}
	if strings.TrimSpace(*token) == "" {
		if old, err := config.Load(""); err == nil && old.Token != "" { // reinstalação/atualização: reaproveita o token já instalado
			*token = old.Token
			out("usando o token já instalado")
		} else {
			fatal("informe --token (gere em Antenas > Agentes no painel do Cabine)")
		}
	}
	srv := strings.TrimRight(strings.TrimSpace(*server), "/")
	out("validando o token no servidor " + srv + " …")
	c, err := cloud.New(srv, strings.TrimSpace(*token)).GetConfig()
	if err != nil {
		fatal("não consegui falar com o Cabine: " + err.Error())
	}
	out(fmt.Sprintf("ok: agente %q, %d antena(s) com IP para monitorar", c.Agente, len(c.Antenas)))
	if err := install.Install(srv, strings.TrimSpace(*token)); err != nil {
		fatal(err.Error())
	}
	out("Instalado. Serviço 'CabineAgent' em execução; o ícone aparece na bandeja (verde = tudo no ar).")
	winutil.MessageBox("Cabine RFID Agent", "Instalado com sucesso.\nO ícone aparece na bandeja do Windows.")
}

func doStatus() {
	out("serviço: " + install.Status())
	st, err := config.ReadState()
	if err != nil {
		out("estado: ainda sem dados (" + err.Error() + ")")
		return
	}
	k, msg, linhas := tray.Summarize(st, time.Now())
	_ = k
	out("resumo: " + msg)
	for _, l := range linhas {
		out("  " + l)
	}
}

func out(s string) { fmt.Fprintln(os.Stdout, s) }
func fatal(s string) {
	fmt.Fprintln(os.Stderr, "erro: "+s)
	winutil.MessageBox("Cabine RFID Agent", s)
	os.Exit(1)
}

func setupLog() {
	log.SetFlags(log.LstdFlags | log.Lmsgprefix)
	_ = os.MkdirAll(config.PublicDir(), 0o755)
	if f, err := os.OpenFile(config.LogPath(), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644); err == nil {
		log.SetOutput(f)
	}
}

var _ = runner.New // mantém o pacote no build (usado via service)
