//go:build !windows

package install

import "errors"

func Install(server, token string) error { return errors.New("instalação só no Windows") }
func Uninstall() error                   { return errors.New("só no Windows") }
func Status() string                     { return "n/d (fora do Windows)" }
func InstallDir() string                 { return "" }
