// Package dvr busca no DVR Intelbras (família Dahua) o trecho gravado de um canal e entrega um mp4.
//
// Duas chamadas ao DVR, ambas na LAN:
//   - HTTP CGI (digest ou basic) para ler a hora do aparelho e corrigir o deslocamento do relógio;
//   - RTSP de playback (/cam/playback?channel=N&starttime=..&endtime=..) lido pelo ffmpeg, que grava o mp4.
//
// O playback RTSP corre em tempo real: um trecho de 70 s leva ~70 s para baixar.
package dvr

import (
	"context"
	"crypto/md5"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"
)

// Camera identifica o canal de um DVR e as credenciais para falar com ele.
type Camera struct {
	Host     string
	HTTPPort int // CGI (hora do aparelho); padrão 80
	RTSPPort int // padrão 554
	Canal    int
	User     string
	Pass     string
}

// Options controla a extração.
type Options struct {
	FFmpeg string // caminho do ffmpeg; vazio = procura ao lado do .exe e no PATH
	Codec  string // "copy" (padrão) ou "h264" (reencoda; use se o DVR grava em H.265 e o navegador não toca)
	Client *http.Client
}

const layoutDVR = "2006-01-02 15:04:05"

// ---- hora do DVR ----

var reResult = regexp.MustCompile(`(?m)^result=(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})`)

// Offset devolve (hora do DVR − hora local do PC), com os dois relógios lidos como "hora de parede".
// Somar o offset à hora de parede local dá a hora de parede que o DVR usa para indexar as gravações.
func Offset(ctx context.Context, cam Camera, opt Options, agora time.Time) (time.Duration, error) {
	port := cam.HTTPPort
	if port == 0 {
		port = 80
	}
	u := fmt.Sprintf("http://%s/cgi-bin/global.cgi?action=getCurrentTime", net.JoinHostPort(cam.Host, fmt.Sprint(port)))
	body, err := Get(ctx, clientOr(opt.Client), u, cam.User, cam.Pass)
	if err != nil {
		return 0, err
	}
	m := reResult.FindStringSubmatch(string(body))
	if m == nil {
		return 0, fmt.Errorf("resposta inesperada do DVR ao ler a hora: %.80q", string(body))
	}
	dvr, err := time.Parse(layoutDVR, m[1])
	if err != nil {
		return 0, err
	}
	local, _ := time.Parse(layoutDVR, agora.Format(layoutDVR))
	off := dvr.Sub(local).Round(time.Second)
	if off > -2*time.Second && off < 2*time.Second {
		return 0, nil // ruído de latência
	}
	return off, nil
}

// WallTime converte um instante para a hora de parede que o DVR entende.
func WallTime(t time.Time, off time.Duration) time.Time {
	w, _ := time.Parse(layoutDVR, t.Local().Format(layoutDVR))
	return w.Add(off)
}

// PlaybackURL monta a URL RTSP de reprodução do trecho [ini, fim] (horas de parede do DVR).
func PlaybackURL(cam Camera, ini, fim time.Time) string {
	port := cam.RTSPPort
	if port == 0 {
		port = 554
	}
	const l = "2006_01_02_15_04_05"
	u := url.URL{Scheme: "rtsp", User: url.UserPassword(cam.User, cam.Pass), Host: net.JoinHostPort(cam.Host, fmt.Sprint(port)), Path: "/cam/playback"}
	q := fmt.Sprintf("channel=%d&subtype=0&starttime=%s&endtime=%s", cam.Canal, ini.Format(l), fim.Format(l))
	return u.String() + "?" + q
}

// FFmpegArgs são os argumentos para gravar o playback em out.
func FFmpegArgs(playback string, dur time.Duration, codec, out string) []string {
	args := []string{"-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", playback, "-t", fmt.Sprint(int(dur.Seconds())), "-an"}
	if codec == "h264" {
		args = append(args, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p")
	} else {
		args = append(args, "-c:v", "copy")
	}
	return append(args, "-movflags", "+faststart", "-y", out)
}

// Fetch grava em outPath o trecho [ini, fim] do canal. ini/fim são instantes reais (corrigidos pelo relógio do DVR aqui dentro).
func Fetch(ctx context.Context, cam Camera, opt Options, ini, fim time.Time, outPath string) error {
	if cam.User == "" || cam.Pass == "" {
		return errors.New("usuário/senha do DVR não configurados no config.json do agente (dvrUser/dvrPass ou cameras.<nome>)")
	}
	ff, err := resolveFFmpeg(opt.FFmpeg)
	if err != nil {
		return err
	}
	off, err := Offset(ctx, cam, opt, time.Now())
	if err != nil {
		return fmt.Errorf("DVR %s não respondeu à leitura da hora (IP, porta HTTP e credencial estão certos?): %w", cam.Host, err)
	}
	a, b := WallTime(ini, off), WallTime(fim, off)
	dur := fim.Sub(ini)
	// o playback corre em tempo real: prazo = duração + folga para conectar
	cctx, cancel := context.WithTimeout(ctx, dur+2*time.Minute)
	defer cancel()
	_ = os.Remove(outPath)
	cmd := exec.CommandContext(cctx, ff, FFmpegArgs(PlaybackURL(cam, a, b), dur, opt.Codec, outPath)...)
	saida, runErr := cmd.CombinedOutput()
	msg := strings.TrimSpace(redact(string(saida), cam.Pass))
	if cctx.Err() != nil {
		return fmt.Errorf("ffmpeg excedeu o prazo ao ler o DVR: %s", tail(msg, 300))
	}
	fi, statErr := os.Stat(outPath)
	if runErr != nil || statErr != nil || fi.Size() < 4096 {
		_ = os.Remove(outPath)
		if msg == "" {
			msg = "o DVR não devolveu vídeo nesse intervalo (canal sem gravação? gravação só por movimento?)"
		}
		return fmt.Errorf("sem vídeo do canal %d entre %s e %s (hora do DVR): %s", cam.Canal, a.Format("15:04:05"), b.Format("15:04:05"), tail(msg, 300))
	}
	return nil
}

// ResolveFFmpeg acha o ffmpeg (configurado, ao lado do agente ou no PATH).
func ResolveFFmpeg(configured string) (string, error) { return resolveFFmpeg(configured) }

func resolveFFmpeg(configured string) (string, error) {
	if configured != "" {
		return configured, nil
	}
	nome := "ffmpeg"
	if runtime.GOOS == "windows" {
		nome = "ffmpeg.exe"
	}
	if exe, err := os.Executable(); err == nil {
		p := filepath.Join(filepath.Dir(exe), nome)
		if _, err := os.Stat(p); err == nil {
			return p, nil
		}
	}
	if p, err := exec.LookPath(nome); err == nil {
		return p, nil
	}
	return "", errors.New("ffmpeg não encontrado: coloque o " + nome + " na pasta do agente ou informe ffmpegPath no config.json")
}

func redact(s, pass string) string {
	if pass == "" {
		return s
	}
	return strings.ReplaceAll(strings.ReplaceAll(s, pass, "***"), url.QueryEscape(pass), "***")
}

func tail(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return "…" + s[len(s)-n:]
}

func clientOr(c *http.Client) *http.Client {
	if c != nil {
		return c
	}
	return &http.Client{Timeout: 15 * time.Second}
}

// ---- HTTP com digest/basic ----

var reParam = regexp.MustCompile(`(\w+)=(?:"([^"]*)"|([^,\s]*))`)

// Get faz GET autenticando por Digest (padrão dos DVRs Intelbras/Dahua) ou Basic, conforme o desafio do aparelho.
func Get(ctx context.Context, c *http.Client, rawURL, user, pass string) ([]byte, error) {
	req, _ := http.NewRequestWithContext(ctx, "GET", rawURL, nil)
	resp, err := c.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		return readOK(resp)
	}
	io.Copy(io.Discard, resp.Body)
	desafio := resp.Header.Get("WWW-Authenticate")
	req2, _ := http.NewRequestWithContext(ctx, "GET", rawURL, nil)
	switch {
	case strings.HasPrefix(strings.ToLower(desafio), "digest"):
		h, err := digestHeader(desafio, "GET", req2.URL.RequestURI(), user, pass)
		if err != nil {
			return nil, err
		}
		req2.Header.Set("Authorization", h)
	case strings.HasPrefix(strings.ToLower(desafio), "basic"):
		req2.SetBasicAuth(user, pass)
	default:
		return nil, fmt.Errorf("HTTP 401 sem desafio de autenticação conhecido")
	}
	resp2, err := c.Do(req2)
	if err != nil {
		return nil, err
	}
	defer resp2.Body.Close()
	if resp2.StatusCode == http.StatusUnauthorized {
		return nil, errors.New("usuário ou senha do DVR recusados (401)")
	}
	return readOK(resp2)
}

func readOK(r *http.Response) ([]byte, error) {
	b, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if r.StatusCode >= 300 {
		return nil, fmt.Errorf("DVR respondeu HTTP %d", r.StatusCode)
	}
	return b, nil
}

func md5hex(s string) string { h := md5.Sum([]byte(s)); return hex.EncodeToString(h[:]) }

func digestHeader(desafio, method, uri, user, pass string) (string, error) {
	p := map[string]string{}
	for _, m := range reParam.FindAllStringSubmatch(desafio[len("Digest"):], -1) {
		v := m[2]
		if v == "" {
			v = m[3]
		}
		p[strings.ToLower(m[1])] = v
	}
	if p["nonce"] == "" || p["realm"] == "" {
		return "", errors.New("desafio digest incompleto")
	}
	ha1 := md5hex(user + ":" + p["realm"] + ":" + pass)
	ha2 := md5hex(method + ":" + uri)
	var resp, extra string
	if strings.Contains(p["qop"], "auth") {
		cn := make([]byte, 8)
		rand.Read(cn)
		cnonce := hex.EncodeToString(cn)
		resp = md5hex(ha1 + ":" + p["nonce"] + ":00000001:" + cnonce + ":auth:" + ha2)
		extra = fmt.Sprintf(`, qop=auth, nc=00000001, cnonce="%s"`, cnonce)
	} else {
		resp = md5hex(ha1 + ":" + p["nonce"] + ":" + ha2)
	}
	h := fmt.Sprintf(`Digest username="%s", realm="%s", nonce="%s", uri="%s", response="%s"%s`, user, p["realm"], p["nonce"], uri, resp, extra)
	if p["opaque"] != "" {
		h += fmt.Sprintf(`, opaque="%s"`, p["opaque"])
	}
	return h, nil
}
