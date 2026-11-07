//go:build !windows

package service

import (
	"context"
	"os"
	"os/signal"

	"cabine.solidsd.com.br/agent/internal/config"
	"cabine.solidsd.com.br/agent/internal/runner"
)

func IsWindowsService() bool { return false }

// Run (fora do Windows) roda em primeiro plano até Ctrl+C — útil para desenvolvimento.
func Run(cfg *config.Config) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	runner.New(cfg).Loop(ctx)
	return nil
}
