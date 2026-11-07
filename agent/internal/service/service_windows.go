//go:build windows

package service

import (
	"context"
	"log"

	"golang.org/x/sys/windows/svc"

	"cabine.solidsd.com.br/agent/internal/config"
	"cabine.solidsd.com.br/agent/internal/runner"
)

type handler struct{ cfg *config.Config }

func (h *handler) Execute(_ []string, req <-chan svc.ChangeRequest, st chan<- svc.Status) (bool, uint32) {
	st <- svc.Status{State: svc.StartPending}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() { runner.New(h.cfg).Loop(ctx); close(done) }()
	st <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	for c := range req {
		switch c.Cmd {
		case svc.Interrogate:
			st <- c.CurrentStatus
		case svc.Stop, svc.Shutdown:
			st <- svc.Status{State: svc.StopPending}
			cancel()
			<-done
			return false, 0
		default:
			log.Printf("[service] comando inesperado: %d", c.Cmd)
		}
	}
	return false, 0
}

func IsWindowsService() bool {
	ok, err := svc.IsWindowsService()
	return err == nil && ok
}

func Run(cfg *config.Config) error { return svc.Run(config.ServiceName, &handler{cfg: cfg}) }
