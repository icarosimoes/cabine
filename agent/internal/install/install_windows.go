//go:build windows

// Package install instala o agente: copia o .exe para Program Files, grava o config, registra o serviço
// (início automático, reinício em caso de falha) e liga o tray no logon de qualquer usuário.
package install

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"golang.org/x/sys/windows/registry"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"

	"cabine.solidsd.com.br/agent/internal/config"
)

const (
	runKey  = `Software\Microsoft\Windows\CurrentVersion\Run`
	runName = "CabineAgentTray"
)

func InstallDir() string {
	pf := os.Getenv("ProgramFiles")
	if pf == "" {
		pf = `C:\Program Files`
	}
	return filepath.Join(pf, "Cabine Agent")
}

func Install(server, token string) error {
	dest := filepath.Join(InstallDir(), "cabine-agent.exe")
	self, err := os.Executable()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(InstallDir(), 0o755); err != nil {
		return fmt.Errorf("criar pasta de instalação (execute como Administrador): %w", err)
	}

	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("conectar ao gerenciador de serviços (execute como Administrador): %w", err)
	}
	defer m.Disconnect()
	// reinstalação: para o serviço antigo e fecha o ícone da bandeja (que também mantém o .exe aberto) antes de sobrescrever
	if s, err := m.OpenService(config.ServiceName); err == nil {
		stopAndWait(s)
		s.Close()
	}
	killOutros()

	// config: guarda o token numa pasta restrita (SYSTEM e Administradores); o estado público fica em \public
	if err := os.MkdirAll(config.PublicDir(), 0o755); err != nil {
		return err
	}
	cfg := &config.Config{Server: server, Token: token}
	if old, err := config.Load(""); err == nil { // preserva credenciais de leitores já configuradas
		cfg.ReaderUser, cfg.ReaderPass, cfg.Readers = old.ReaderUser, old.ReaderPass, old.Readers
		if cfg.Token == "" { // reinstalação sem informar o token: mantém o atual
			cfg.Token = old.Token
		}
	}
	if err := cfg.Save(""); err != nil {
		return err
	}
	_ = exec.Command("icacls", config.DataDir(), "/inheritance:r", "/grant:r", "SYSTEM:(OI)(CI)F", "Administrators:(OI)(CI)F", "Users:(OI)(CI)RX").Run()
	_ = exec.Command("icacls", config.ConfigPath(), "/inheritance:r", "/grant:r", "SYSTEM:F", "Administrators:F").Run()
	_ = exec.Command("icacls", config.PublicDir(), "/grant:r", "Users:(OI)(CI)M").Run() // o tray lê; o log é escrito por usuário

	if self != dest {
		var cerr error
		for i := 0; i < 10; i++ { // o antivírus/Windows pode segurar o arquivo por instantes
			if cerr = copyFile(self, dest); cerr == nil {
				break
			}
			killOutros()
		}
		if cerr != nil {
			return fmt.Errorf("copiar executável (feche o ícone do agente e tente de novo): %w", cerr)
		}
	}

	s, err := m.OpenService(config.ServiceName)
	if err != nil {
		s, err = m.CreateService(config.ServiceName, dest, mgr.Config{
			DisplayName: config.DisplayName, StartType: mgr.StartAutomatic,
			Description: "Monitora os leitores RFID do hotel e conversa com o Cabine RFID.",
		})
		if err != nil {
			return fmt.Errorf("criar serviço: %w", err)
		}
	} else {
		c, _ := s.Config()
		c.BinaryPathName = `"` + dest + `"`
		c.StartType = mgr.StartAutomatic
		_ = s.UpdateConfig(c)
	}
	defer s.Close()
	_ = s.SetRecoveryActions([]mgr.RecoveryAction{
		{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 30 * time.Second},
		{Type: mgr.ServiceRestart, Delay: 60 * time.Second},
	}, 86400)
	if err := s.Start(); err != nil {
		return fmt.Errorf("iniciar serviço: %w", err)
	}

	k, _, err := registry.CreateKey(registry.LOCAL_MACHINE, runKey, registry.SET_VALUE)
	if err == nil {
		_ = k.SetStringValue(runName, `"`+dest+`" tray`)
		k.Close()
	}
	_ = exec.Command(dest, "tray").Start() // já mostra o ícone para quem instalou
	return nil
}

func Uninstall() error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("execute como Administrador: %w", err)
	}
	defer m.Disconnect()
	if s, err := m.OpenService(config.ServiceName); err == nil {
		stopAndWait(s)
		_ = s.Delete()
		s.Close()
	}
	if k, err := registry.OpenKey(registry.LOCAL_MACHINE, runKey, registry.SET_VALUE); err == nil {
		_ = k.DeleteValue(runName)
		k.Close()
	}
	_ = exec.Command("taskkill", "/IM", "cabine-agent.exe", "/F").Run()
	// remove arquivos depois que este processo terminar
	_ = exec.Command("cmd", "/c", fmt.Sprintf(`ping -n 3 127.0.0.1 >nul & rmdir /s /q "%s" & rmdir /s /q "%s"`, InstallDir(), config.DataDir())).Start()
	return nil
}

// killOutros encerra os outros processos cabine-agent.exe (tray, instância avulsa), menos este.
func killOutros() {
	_ = exec.Command("taskkill", "/F", "/FI", "IMAGENAME eq cabine-agent.exe", "/FI", fmt.Sprintf("PID ne %d", os.Getpid())).Run()
	time.Sleep(1500 * time.Millisecond)
}

func stopAndWait(s *mgr.Service) {
	if _, err := s.Control(svc.Stop); err != nil {
		return
	}
	for i := 0; i < 20; i++ {
		if st, err := s.Query(); err != nil || st.State == svc.Stopped {
			return
		}
		time.Sleep(500 * time.Millisecond)
	}
}

func Status() string {
	m, err := mgr.Connect()
	if err != nil {
		return "indisponível (" + err.Error() + ")"
	}
	defer m.Disconnect()
	s, err := m.OpenService(config.ServiceName)
	if err != nil {
		return "não instalado"
	}
	defer s.Close()
	st, err := s.Query()
	if err != nil {
		return "erro: " + err.Error()
	}
	switch st.State {
	case svc.Running:
		return "em execução"
	case svc.Stopped:
		return "parado"
	}
	return fmt.Sprintf("estado %d", st.State)
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	tmp := dst + ".new"
	out, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	out.Close()
	_ = os.Remove(dst)
	return os.Rename(tmp, dst)
}
