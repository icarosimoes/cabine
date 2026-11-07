package tray

import (
	"fmt"
	"time"

	"cabine.solidsd.com.br/agent/internal/config"
)

type Kind int

const (
	Unknown Kind = iota
	OK
	Fail
)

// Summarize transforma o estado gravado pelo serviço no que o tray exibe (testável fora do Windows).
func Summarize(s *config.State, now time.Time) (Kind, string, []string) {
	if s == nil {
		return Unknown, "Aguardando o serviço do agente…", nil
	}
	age := now.Sub(time.Unix(s.Ciclo, 0))
	var linhas []string
	off := 0
	for _, a := range s.Antenas {
		mark := "● "
		if !a.Online {
			mark, off = "○ ", off+1
		}
		l := mark + a.Nome
		if a.Online && a.Latencia > 0 {
			l += fmt.Sprintf(" — %d ms", a.Latencia)
		} else if !a.Online && a.Detalhe != "" {
			l += " — " + a.Detalhe
		}
		linhas = append(linhas, l)
	}
	switch {
	case age > 90*time.Second:
		return Fail, "Serviço do agente parado ou travado", linhas
	case !s.ServidorOK:
		return Fail, "Sem contato com o Cabine: " + s.Erro, linhas
	case off > 0:
		return Fail, fmt.Sprintf("%d antena(s) offline", off), linhas
	case len(s.Antenas) == 0:
		return Unknown, "Nenhuma antena com IP cadastrada no Cabine", linhas
	}
	return OK, fmt.Sprintf("Todas as %d antenas no ar", len(s.Antenas)), linhas
}
