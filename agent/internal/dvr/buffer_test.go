package dvr

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLiveURLMiboPadraoECustom(t *testing.T) {
	cam := Camera{Host: "192.168.30.70", Canal: 1, User: "admin", Pass: "s@nha"}
	if u := LiveURL(cam, ""); u != "rtsp://admin:s%40nha@192.168.30.70:554/cam/realmonitor?channel=1&subtype=0" {
		t.Fatal(u)
	}
	cam.RTSPPort, cam.Canal = 8554, 2
	if u := LiveURL(cam, "stream{canal}/main"); u != "rtsp://admin:s%40nha@192.168.30.70:8554/stream2/main" {
		t.Fatal(u)
	}
}

func TestRecorderArgsGravaSegmentosComHoraNoNome(t *testing.T) {
	a := strings.Join(RecorderArgs("rtsp://x", "/buf"), " ")
	for _, q := range []string{"-rtsp_transport tcp", "-c:v copy", "-f segment", "-segment_time 5", "-strftime 1", "%Y%m%d_%H%M%S.ts", "-an"} {
		if !strings.Contains(a, q) {
			t.Errorf("faltou %q em %s", q, a)
		}
	}
}

func segs(t *testing.T, dir string, base time.Time, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		os.WriteFile(filepath.Join(dir, base.Add(time.Duration(i*SegSeconds)*time.Second).Format(layoutSeg)+".ts"), []byte("ts"), 0o600)
	}
	os.WriteFile(filepath.Join(dir, "lixo.txt"), nil, 0o600) // ignorado
}

func TestSelectSegments(t *testing.T) {
	dir := t.TempDir()
	base := time.Date(2026, 11, 12, 10, 0, 0, 0, time.Local)
	segs(t, dir, base, 24) // 10:00:00 .. 10:01:55
	ini, fim := base.Add(30*time.Second), base.Add(100*time.Second)
	got := SelectSegments(ListSegments(dir), ini, fim)
	if got[0].Start != base.Add(25*time.Second) { // o segmento que contém `ini` entra
		t.Fatalf("primeiro: %v", got[0].Start.Format("15:04:05"))
	}
	if last := got[len(got)-1].Start; last != base.Add(100*time.Second) {
		t.Fatalf("último: %v", last.Format("15:04:05"))
	}
}

func ffmpegConcat(t *testing.T, corpo string) *Recorder {
	t.Helper()
	if os.PathSeparator == '\\' {
		t.Skip("script de teste é sh")
	}
	p := filepath.Join(t.TempDir(), "ffmpeg")
	os.WriteFile(p, []byte("#!/bin/sh\n"+corpo+"\n"), 0o755)
	return &Recorder{Name: "mibo", FFmpeg: p, Dir: t.TempDir(), Pass: "segredo"}
}

func TestBuildClipJuntaSegmentosECortaNoInicio(t *testing.T) {
	// ffmpeg falso: guarda os argumentos e a lista e escreve um "mp4"
	log := filepath.Join(t.TempDir(), "args.txt")
	rec := ffmpegConcat(t, `echo "$@" > `+log+`; for a; do last="$a"; done; head -c 8000 /dev/zero > "$last"`)
	base := time.Date(2026, 11, 12, 10, 0, 0, 0, time.Local)
	segs(t, rec.Dir, base, 24)
	out := filepath.Join(t.TempDir(), "c.mp4")
	if err := BuildClip(context.Background(), rec, Options{}, base.Add(32*time.Second), base.Add(100*time.Second), out); err != nil {
		t.Fatal(err)
	}
	args, _ := os.ReadFile(log)
	for _, q := range []string{"-f concat", "-ss 7.0", "-t 68", "-c:v copy", "-an"} { // 32 s − início do 1º segmento escolhido (25 s: folga de SegSeconds+2)
		if !strings.Contains(string(args), q) {
			t.Errorf("faltou %q em %s", q, args)
		}
	}
	if _, err := os.Stat(out + ".txt"); err == nil {
		t.Error("lista temporária não foi apagada")
	}
}

func TestBuildClipSemSegmentosExplicaPorque(t *testing.T) {
	rec := ffmpegConcat(t, "exit 0")
	err := BuildClip(context.Background(), rec, Options{}, time.Now().Add(-time.Minute), time.Now(), filepath.Join(t.TempDir(), "c.mp4"))
	if err == nil || !strings.Contains(err.Error(), "gravador não está rodando") {
		t.Fatalf("esperava explicação do gravador parado, veio %v", err)
	}
	rec.mu.Lock()
	rec.started = time.Now().Add(-20 * time.Second)
	rec.mu.Unlock()
	err = BuildClip(context.Background(), rec, Options{}, time.Now().Add(-time.Minute), time.Now(), filepath.Join(t.TempDir(), "c.mp4"))
	if err == nil || !strings.Contains(err.Error(), "subiu há 20s") {
		t.Fatalf("esperava aviso de buffer curto, veio %v", err)
	}
}

func TestCleanupApagaSoOVelho(t *testing.T) {
	rec := &Recorder{Dir: t.TempDir(), Keep: 6 * time.Minute}
	now := time.Date(2026, 11, 12, 10, 10, 0, 0, time.Local)
	segs(t, rec.Dir, now.Add(-10*time.Minute), 1) // 10 min: apaga
	segs(t, rec.Dir, now.Add(-2*time.Minute), 1)  // 2 min: fica
	rec.Cleanup(now)
	if l := ListSegments(rec.Dir); len(l) != 1 || l[0].Start != now.Add(-2*time.Minute) {
		t.Fatalf("restou: %+v", l)
	}
}

func TestRecorderReiniciaEMascaraSenha(t *testing.T) {
	rec := ffmpegConcat(t, `echo "rtsp://admin:segredo@x: Connection refused" >&2; exit 1`)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { rec.Run(ctx); close(done) }()
	time.Sleep(400 * time.Millisecond)
	cancel()
	<-done
	if e := rec.LastErr(); !strings.Contains(e, "Connection refused") || strings.Contains(e, "segredo") {
		t.Fatalf("lastErr=%q", e)
	}
}

// Com o ffmpeg de verdade (pula se não houver): grava segmentos de uma fonte de teste com os mesmos parâmetros do gravador
// (segment/strftime/mpegts) e monta o clipe pelo caminho de produção.
func TestBuildClipComFFmpegReal(t *testing.T) {
	ff, err := exec.LookPath("ffmpeg")
	if err != nil {
		t.Skip("ffmpeg não instalado")
	}
	rec := &Recorder{Name: "teste", FFmpeg: ff, Dir: t.TempDir()}
	// parte de saída do gravador real (tudo depois de "-i <url>"), trocando só o "copy" por um encode da fonte de teste
	full := RecorderArgs("x", rec.Dir)
	var saida []string
	for i := 0; i < len(full); i++ {
		if full[i] == "-i" {
			i++ // pula a URL
			continue
		}
		if full[i] == "copy" {
			saida = append(saida, "libx264", "-g", "10", "-pix_fmt", "yuv420p")
			continue
		}
		if i > 5 {
			saida = append(saida, full[i])
		}
	}
	args := append([]string{"-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "13"}, saida...)
	inicio := time.Now()
	if out, err := exec.Command(ff, args...).CombinedOutput(); err != nil {
		t.Fatalf("ffmpeg: %v\n%s", err, out)
	}
	if n := len(ListSegments(rec.Dir)); n < 2 {
		t.Fatalf("esperava >=2 segmentos, veio %d", n)
	}
	out := filepath.Join(t.TempDir(), "clipe.mp4")
	if err := BuildClip(context.Background(), rec, Options{}, inicio.Add(2*time.Second), inicio.Add(11*time.Second), out); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(out)
	if len(b) < 4096 || string(b[4:8]) != "ftyp" {
		t.Fatalf("saída não é mp4 (%d bytes)", len(b))
	}
}
