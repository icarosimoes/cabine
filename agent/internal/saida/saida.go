// Package saida coleta as leituras da antena de saída (Acura EdgeR30, porta 8080: uma linha "0x<EPC>" por leitura)
// e as entrega ao Cabine, que decide se a peça tinha transferência documentada ou se é evasão.
// Só LÊ da porta: nunca envia nada ao leitor.
package saida

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"strings"
	"sync"
	"time"
)

// ParseEPC extrai o EPC (hex maiúsculo, 24 dígitos) de uma linha do leitor. Aceita "0x…" e, sem prefixo, só se
// tiver 24 dígitos (evita fragmentos de linha cortada).
func ParseEPC(linha string) (string, bool) {
	s := strings.TrimSpace(linha)
	pre := false
	if strings.HasPrefix(s, "0x") || strings.HasPrefix(s, "0X") {
		s, pre = s[2:], true
	}
	if len(s) < 8 || len(s)%2 != 0 || len(s) > 64 {
		return "", false
	}
	if !pre && len(s) != 24 {
		return "", false
	}
	for _, c := range s {
		if !strings.ContainsRune("0123456789abcdefABCDEF", c) {
			return "", false
		}
	}
	return strings.ToUpper(s), true
}

// Debounce deixa passar cada EPC uma vez e só volta a deixar depois de `rearme` sem vê-lo (a tag lida em rajada
// enquanto passa pela porta vira uma única detecção).
type Debounce struct {
	Rearme time.Duration
	Now    func() time.Time
	visto  map[string]time.Time
}

func NewDebounce(rearme time.Duration) *Debounce {
	return &Debounce{Rearme: rearme, Now: time.Now, visto: map[string]time.Time{}}
}

func (d *Debounce) Novo(epc string) bool {
	now := d.Now()
	ult, ok := d.visto[epc]
	d.visto[epc] = now // cada leitura renova: só "rearma" após silêncio
	if len(d.visto) > 5000 {
		for k, t := range d.visto {
			if now.Sub(t) > d.Rearme {
				delete(d.visto, k)
			}
		}
	}
	return !ok || now.Sub(ult) > d.Rearme
}

type Listener struct {
	Nome    string
	Addr    string
	Rearme  time.Duration
	Entrega func(epc string) error // chamada para cada detecção nova
	Dial    func(ctx context.Context, addr string) (net.Conn, error)
	Espera  func(ctx context.Context, d time.Duration) // troca-se nos testes
	MinBack time.Duration
	MaxBack time.Duration
}

func NewListener(nome, addr string, entrega func(string) error) *Listener {
	return &Listener{Nome: nome, Addr: addr, Rearme: 120 * time.Second, Entrega: entrega,
		Dial: func(ctx context.Context, a string) (net.Conn, error) {
			d := net.Dialer{Timeout: 5 * time.Second}
			return d.DialContext(ctx, "tcp", a)
		},
		Espera: func(ctx context.Context, d time.Duration) {
			select {
			case <-ctx.Done():
			case <-time.After(d):
			}
		},
		MinBack: 5 * time.Second, MaxBack: 60 * time.Second}
}

// Run conecta, lê e reconecta até o contexto acabar.
func (l *Listener) Run(ctx context.Context) {
	deb := NewDebounce(l.Rearme)
	back := l.MinBack
	for ctx.Err() == nil {
		inicio := time.Now()
		err := l.sessao(ctx, deb)
		if ctx.Err() != nil {
			return
		}
		if time.Since(inicio) > 30*time.Second {
			back = l.MinBack // a conexão durou: recomeça o backoff
		}
		log.Printf("[saida %s] desconectado (%v); nova tentativa em %s", l.Nome, err, back)
		l.Espera(ctx, back)
		if back *= 2; back > l.MaxBack {
			back = l.MaxBack
		}
	}
}

func (l *Listener) sessao(ctx context.Context, deb *Debounce) error {
	c, err := l.Dial(ctx, l.Addr)
	if err != nil {
		return err
	}
	defer c.Close()
	go func() { <-ctx.Done(); c.Close() }()
	log.Printf("[saida %s] conectado em %s", l.Nome, l.Addr)
	rd := bufio.NewReaderSize(c, 4096)
	if _, err := rd.ReadString('\n'); err != nil { // descarta a 1ª linha: pode vir cortada no meio
		return err
	}
	for {
		linha, err := rd.ReadString('\n')
		if err != nil {
			if errors.Is(err, io.EOF) {
				return fmt.Errorf("conexão encerrada pelo leitor")
			}
			return err
		}
		epc, ok := ParseEPC(linha)
		if !ok || !deb.Novo(epc) {
			continue
		}
		var wg sync.WaitGroup
		wg.Add(1)
		go func() { // não trava a leitura enquanto o servidor responde
			defer wg.Done()
			for i := 0; i < 4; i++ {
				if e := l.Entrega(epc); e == nil {
					return
				} else {
					log.Printf("[saida %s] entregar %s: %v", l.Nome, epc, e)
				}
				l.Espera(ctx, time.Duration(i+1)*3*time.Second)
				if ctx.Err() != nil {
					return
				}
			}
		}()
	}
}
