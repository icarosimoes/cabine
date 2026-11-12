package saida

import (
	"context"
	"net"
	"sync"
	"testing"
	"time"
)

func TestParseEPC(t *testing.T) {
	casos := map[string]struct {
		epc string
		ok  bool
	}{
		"0xe2801160600002054d1f9a3b\r\n": {"E2801160600002054D1F9A3B", true},
		"E2801160600002054D1F9A3B":       {"E2801160600002054D1F9A3B", true},
		"2054d1f9a3b":                    {"", false}, // fragmento sem prefixo
		"0x2054d1f9":                     {"2054D1F9", true},
		"0xZZ":                           {"", false},
		"":                               {"", false},
	}
	for in, w := range casos {
		g, ok := ParseEPC(in)
		if g != w.epc || ok != w.ok {
			t.Errorf("%q: got %q,%v", in, g, ok)
		}
	}
}

func TestDebounce(t *testing.T) {
	now := time.Unix(1000, 0)
	d := NewDebounce(120 * time.Second)
	d.Now = func() time.Time { return now }
	if !d.Novo("A") || d.Novo("A") {
		t.Fatal("primeira passa, repetida não")
	}
	now = now.Add(100 * time.Second)
	if d.Novo("A") { // ainda sendo lida: renova
		t.Fatal("não deve rearmar enquanto lida")
	}
	now = now.Add(121 * time.Second)
	if !d.Novo("A") {
		t.Fatal("deve rearmar após silêncio")
	}
}

func TestListenerEntregaUmaVezEReconecta(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	conexoes := 0
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			conexoes++
			n := conexoes
			go func() {
				c.Write([]byte("2054d1f9a3b\r\n")) // 1ª linha cortada: descartada
				if n == 1 {
					for i := 0; i < 3; i++ {
						c.Write([]byte("0xE2801160600002054D1F9A3B\r\n")) // repetida: 1 entrega
					}
					time.Sleep(100 * time.Millisecond)
					c.Close() // força reconexão
					return
				}
				c.Write([]byte("0xE2801160600002054D1F9AAA\r\n"))
				time.Sleep(time.Second)
				c.Close()
			}()
		}
	}()
	var mu sync.Mutex
	var got []string
	l := NewListener("t", ln.Addr().String(), func(e string) error { mu.Lock(); got = append(got, e); mu.Unlock(); return nil })
	l.Espera = func(ctx context.Context, d time.Duration) { time.Sleep(10 * time.Millisecond) }
	ctx, cancel := context.WithTimeout(context.Background(), 700*time.Millisecond)
	defer cancel()
	l.Run(ctx)
	mu.Lock()
	defer mu.Unlock()
	if len(got) != 2 || got[0] != "E2801160600002054D1F9A3B" || got[1] != "E2801160600002054D1F9AAA" {
		t.Fatalf("entregas: %v", got)
	}
}
