//go:build !windows

package tray

import "fmt"

func Run() { fmt.Println("o tray só existe no Windows") }
