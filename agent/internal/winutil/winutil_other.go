//go:build !windows

package winutil

import "fmt"

func MessageBox(title, text string) { fmt.Println(title + ": " + text) }
func AttachConsole()                {}
func IsAdmin() bool                 { return true }
