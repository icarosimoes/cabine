package dvr

// Modo "buffer": para câmeras sem playback local (Intelbras Mibo iM/iMX, Wi-Fi). O agente mantém um gravador
// ffmpeg lendo o RTSP ao vivo e guardando segmentos curtos (.ts) numa pasta; quando uma evasão pede um clipe,
// os segmentos que cobrem [ini, fim] são juntados num mp4. Só serve para o que foi visto DEPOIS que o gravador subiu.

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// SegSeconds é a duração alvo de cada segmento (com -c copy o corte cai no próximo quadro-chave).
const SegSeconds = 5

const layoutSeg = "20060102_150405"

// DefaultLivePath é o caminho RTSP ao vivo das Mibo iM (e VIP) da Intelbras; {canal} é trocado pelo número do canal.
const DefaultLivePath = "/cam/realmonitor?channel={canal}&subtype=0"

// LiveURL monta a URL RTSP ao vivo. path vazio usa DefaultLivePath.
func LiveURL(cam Camera, path string) string {
	if path == "" {
		path = DefaultLivePath
	}
	path = strings.ReplaceAll(path, "{canal}", fmt.Sprint(cam.Canal))
	port := cam.RTSPPort
	if port == 0 {
		port = 554
	}
	u := url.URL{Scheme: "rtsp", User: url.UserPassword(cam.User, cam.Pass), Host: net.JoinHostPort(cam.Host, fmt.Sprint(port))}
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	return u.String() + path
}

// RecorderArgs são os argumentos do ffmpeg que gravam o ao vivo em segmentos <dir>/AAAAMMDD_hhmmss.ts (hora local).
func RecorderArgs(liveURL, dir string) []string {
	return []string{"-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-i", liveURL, "-an", "-c:v", "copy",
		"-f", "segment", "-segment_time", fmt.Sprint(SegSeconds), "-segment_format", "mpegts", "-reset_timestamps", "1",
		"-strftime", "1", filepath.Join(dir, "%Y%m%d_%H%M%S.ts")}
}

// Segment é um arquivo do buffer.
type Segment struct {
	Path  string
	Start time.Time
}

// ListSegments devolve os segmentos da pasta ordenados por início.
func ListSegments(dir string) []Segment {
	ents, _ := os.ReadDir(dir)
	var out []Segment
	for _, e := range ents {
		n := e.Name()
		if e.IsDir() || !strings.HasSuffix(n, ".ts") {
			continue
		}
		t, err := time.ParseInLocation(layoutSeg, strings.TrimSuffix(n, ".ts"), time.Local)
		if err != nil {
			continue
		}
		out = append(out, Segment{Path: filepath.Join(dir, n), Start: t})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Start.Before(out[j].Start) })
	return out
}

// SelectSegments escolhe os segmentos que cobrem [ini, fim]. O segmento que contém `ini` começou até SegSeconds antes.
func SelectSegments(all []Segment, ini, fim time.Time) []Segment {
	from := ini.Add(-time.Duration(SegSeconds+2) * time.Second)
	var out []Segment
	for _, s := range all {
		if !s.Start.Before(from) && !s.Start.After(fim) {
			out = append(out, s)
		}
	}
	return out
}

// Recorder mantém o ffmpeg do ao vivo rodando (reinicia se cair) e apaga segmentos velhos.
type Recorder struct {
	Name   string
	URL    string // contém senha: nunca logar sem redact
	Pass   string
	Dir    string
	FFmpeg string
	Keep   time.Duration // quanto do passado guardar (padrão 6 min)

	mu      sync.Mutex
	started time.Time
	lastErr string
}

// Uptime diz há quanto tempo o gravador está de pé (0 se não subiu).
func (r *Recorder) Uptime() time.Duration {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.started.IsZero() {
		return 0
	}
	return time.Since(r.started)
}

// LastErr é a última falha do ffmpeg (já sem senha).
func (r *Recorder) LastErr() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.lastErr
}

// Cleanup apaga segmentos mais velhos que Keep.
func (r *Recorder) Cleanup(now time.Time) {
	keep := r.Keep
	if keep == 0 {
		keep = 6 * time.Minute
	}
	for _, s := range ListSegments(r.Dir) {
		if now.Sub(s.Start) > keep {
			os.Remove(s.Path)
		}
	}
}

// Run bloqueia até ctx terminar: sobe o ffmpeg, e se ele cair espera 5 s e sobe de novo.
func (r *Recorder) Run(ctx context.Context) {
	if err := os.MkdirAll(r.Dir, 0o700); err != nil {
		log.Printf("[gravador %s] %v", r.Name, err)
		return
	}
	go func() {
		t := time.NewTicker(30 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case now := <-t.C:
				r.Cleanup(now)
			}
		}
	}()
	for ctx.Err() == nil {
		r.mu.Lock()
		r.started = time.Now()
		r.mu.Unlock()
		out, err := exec.CommandContext(ctx, r.FFmpeg, RecorderArgs(r.URL, r.Dir)...).CombinedOutput()
		r.mu.Lock()
		r.started = time.Time{}
		if ctx.Err() == nil {
			r.lastErr = tail(strings.TrimSpace(redact(string(out), r.Pass)), 300)
			if r.lastErr == "" && err != nil {
				r.lastErr = redact(err.Error(), r.Pass)
			}
			log.Printf("[gravador %s] ffmpeg parou: %s", r.Name, r.lastErr)
		}
		r.mu.Unlock()
		select {
		case <-ctx.Done():
		case <-time.After(5 * time.Second):
		}
	}
}

// BuildClip junta os segmentos que cobrem [ini, fim] num mp4 em outPath.
func BuildClip(ctx context.Context, rec *Recorder, opt Options, ini, fim time.Time, outPath string) error {
	ff := rec.FFmpeg
	if ff == "" {
		var err error
		if ff, err = resolveFFmpeg(opt.FFmpeg); err != nil {
			return err
		}
	}
	segs := SelectSegments(ListSegments(rec.Dir), ini, fim)
	if len(segs) == 0 {
		msg := fmt.Sprintf("sem trecho no buffer da câmera %s para %s–%s", rec.Name, ini.Format("15:04:05"), fim.Format("15:04:05"))
		if up := rec.Uptime(); up == 0 {
			msg += ": o gravador não está rodando (câmera fora do ar, IP/senha/caminho RTSP errados?)"
			if e := rec.LastErr(); e != "" {
				msg += " — " + e
			}
		} else {
			msg += fmt.Sprintf(": o gravador subiu há %ds (o buffer só guarda o que passou depois disso)", int(up.Seconds()))
		}
		return errors.New(msg)
	}
	list := outPath + ".txt"
	var b strings.Builder
	for _, s := range segs {
		fmt.Fprintf(&b, "file '%s'\n", strings.ReplaceAll(filepath.ToSlash(s.Path), "'", `'\''`))
	}
	if err := os.WriteFile(list, []byte(b.String()), 0o600); err != nil {
		return err
	}
	defer os.Remove(list)
	off := ini.Sub(segs[0].Start)
	if off < 0 {
		off = 0
	}
	args := []string{"-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-ss", fmt.Sprintf("%.1f", off.Seconds()), "-i", list,
		"-t", fmt.Sprint(int(fim.Sub(ini).Seconds())), "-an"}
	if opt.Codec == "h264" {
		args = append(args, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p")
	} else {
		args = append(args, "-c:v", "copy")
	}
	args = append(args, "-movflags", "+faststart", "-y", outPath)
	_ = os.Remove(outPath)
	cctx, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()
	out, err := exec.CommandContext(cctx, ff, args...).CombinedOutput()
	fi, statErr := os.Stat(outPath)
	if err != nil || statErr != nil || fi.Size() < 4096 {
		_ = os.Remove(outPath)
		m := strings.TrimSpace(redact(string(out), rec.Pass))
		if m == "" {
			m = "o ffmpeg não gerou o mp4"
		}
		return fmt.Errorf("falha ao montar o clipe da câmera %s: %s", rec.Name, tail(m, 300))
	}
	return nil
}
