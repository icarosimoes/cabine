//go:build windows

package tray

import (
	"os/exec"
	"strings"
	"time"

	"github.com/getlantern/systray"

	"cabine.solidsd.com.br/agent/internal/config"
)

const maxLinhas = 16

func Run() {
	systray.Run(onReady, func() {})
}

func onReady() {
	systray.SetIcon(iconUnknown)
	systray.SetTitle("Cabine Agent")
	systray.SetTooltip("Cabine RFID Agent")

	head := systray.AddMenuItem("Cabine RFID Agent v"+config.Version, "")
	head.Disable()
	status := systray.AddMenuItem("Aguardando…", "")
	status.Disable()
	systray.AddSeparator()
	var linhas [maxLinhas]*systray.MenuItem
	for i := range linhas {
		linhas[i] = systray.AddMenuItem("", "")
		linhas[i].Disable()
		linhas[i].Hide()
	}
	systray.AddSeparator()
	painel := systray.AddMenuItem("Abrir painel do Cabine", "")
	logs := systray.AddMenuItem("Abrir pasta de logs", "")
	sair := systray.AddMenuItem("Sair do ícone", "O serviço continua rodando")

	go func() {
		for {
			select {
			case <-painel.ClickedCh:
				st, _ := config.ReadState()
				url := config.DefaultServer
				// state.json é gravável por usuários locais: só aceita http(s), nunca file:/exe/etc.
				if st != nil && (strings.HasPrefix(st.Server, "https://") || strings.HasPrefix(st.Server, "http://")) {
					url = st.Server
				}
				_ = exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()
			case <-logs.ClickedCh:
				_ = exec.Command("explorer", config.PublicDir()).Start()
			case <-sair.ClickedCh:
				systray.Quit()
				return
			}
		}
	}()

	go func() {
		for {
			st, _ := config.ReadState()
			kind, msg, ls := Summarize(st, time.Now())
			switch kind {
			case OK:
				systray.SetIcon(iconOK)
			case Fail:
				systray.SetIcon(iconFail)
			default:
				systray.SetIcon(iconUnknown)
			}
			systray.SetTooltip("Cabine Agent — " + msg)
			status.SetTitle(msg)
			for i := range linhas {
				if i < len(ls) {
					linhas[i].SetTitle(ls[i])
					linhas[i].Show()
				} else {
					linhas[i].Hide()
				}
			}
			time.Sleep(3 * time.Second)
		}
	}()
}
