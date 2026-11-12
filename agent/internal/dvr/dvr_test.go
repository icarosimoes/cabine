package dvr

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// DVR falso: exige digest (qop=auth) e devolve a hora configurada.
func dvrFalso(t *testing.T, hora string) (Camera, func()) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		const nonce, realm = "abc123", "Login to DVR"
		auth := r.Header.Get("Authorization")
		if auth == "" {
			w.Header().Set("WWW-Authenticate", `Digest realm="`+realm+`", qop="auth", nonce="`+nonce+`", opaque="op"`)
			w.WriteHeader(401)
			return
		}
		p := map[string]string{}
		for _, m := range reParam.FindAllStringSubmatch(auth[len("Digest"):], -1) {
			v := m[2]
			if v == "" {
				v = m[3]
			}
			p[strings.ToLower(m[1])] = v
		}
		ha1 := md5hex("admin:" + realm + ":segredo")
		esperado := md5hex(ha1 + ":" + nonce + ":" + p["nc"] + ":" + p["cnonce"] + ":auth:" + md5hex("GET:"+r.URL.RequestURI()))
		if p["response"] != esperado {
			w.WriteHeader(401)
			return
		}
		w.Write([]byte("result=" + hora + "\r\n"))
	}))
	host, port, _ := net.SplitHostPort(strings.TrimPrefix(srv.URL, "http://"))
	var pn int
	for _, c := range port {
		pn = pn*10 + int(c-'0')
	}
	return Camera{Host: host, HTTPPort: pn, Canal: 3, User: "admin", Pass: "segredo"}, srv.Close
}

func TestOffsetComDigest(t *testing.T) {
	agora := time.Date(2026, 11, 12, 10, 0, 0, 0, time.Local)
	cam, fim := dvrFalso(t, "2026-11-12 10:03:30")
	defer fim()
	off, err := Offset(context.Background(), cam, Options{}, agora)
	if err != nil || off != 3*time.Minute+30*time.Second {
		t.Fatalf("offset=%v err=%v", off, err)
	}
	// relógio quase certo: ruído de latência vira 0
	cam2, fim2 := dvrFalso(t, "2026-11-12 10:00:01")
	defer fim2()
	if off, _ := Offset(context.Background(), cam2, Options{}, agora); off != 0 {
		t.Fatalf("esperava 0, veio %v", off)
	}
}

func TestOffsetSenhaErrada(t *testing.T) {
	cam, fim := dvrFalso(t, "2026-11-12 10:00:00")
	defer fim()
	cam.Pass = "errada"
	_, err := Offset(context.Background(), cam, Options{}, time.Now())
	if err == nil || !strings.Contains(err.Error(), "recusados") {
		t.Fatalf("esperava erro de credencial, veio %v", err)
	}
}

func TestWallTimeAplicaOffset(t *testing.T) {
	t0 := time.Date(2026, 11, 12, 10, 0, 0, 0, time.Local)
	if got := WallTime(t0, 90*time.Second).Format(layoutDVR); got != "2026-11-12 10:01:30" {
		t.Fatal(got)
	}
}

func TestPlaybackURLEscapaSenhaEInformaJanela(t *testing.T) {
	cam := Camera{Host: "10.0.0.5", Canal: 3, User: "admin", Pass: "p@ss:w/rd"}
	ini := time.Date(2026, 11, 12, 10, 0, 0, 0, time.UTC)
	u := PlaybackURL(cam, ini, ini.Add(70*time.Second))
	for _, quer := range []string{"rtsp://admin:p%40ss%3Aw%2Frd@10.0.0.5:554/cam/playback?", "channel=3", "starttime=2026_11_12_10_00_00", "endtime=2026_11_12_10_01_10"} {
		if !strings.Contains(u, quer) {
			t.Errorf("faltou %q em %s", quer, u)
		}
	}
}

func TestFFmpegArgs(t *testing.T) {
	a := strings.Join(FFmpegArgs("rtsp://x", 70*time.Second, "copy", "o.mp4"), " ")
	if !strings.Contains(a, "-c:v copy") || !strings.Contains(a, "-t 70") || !strings.Contains(a, "-an") {
		t.Fatal(a)
	}
	if b := strings.Join(FFmpegArgs("rtsp://x", 70*time.Second, "h264", "o.mp4"), " "); !strings.Contains(b, "libx264") {
		t.Fatal(b)
	}
}

// ffmpeg falso (script): grava um "mp4" mínimo no último argumento, ou falha conforme o ambiente.
func ffmpegFalso(t *testing.T, corpo string) string {
	t.Helper()
	if os.PathSeparator == '\\' {
		t.Skip("script de teste é sh")
	}
	p := filepath.Join(t.TempDir(), "ffmpeg")
	os.WriteFile(p, []byte("#!/bin/sh\n"+corpo+"\n"), 0o755)
	return p
}

func TestFetchGravaMp4(t *testing.T) {
	cam, fim := dvrFalso(t, time.Now().Format(layoutDVR))
	defer fim()
	ff := ffmpegFalso(t, `for a; do last="$a"; done; head -c 8000 /dev/zero > "$last"`)
	out := filepath.Join(t.TempDir(), "c.mp4")
	now := time.Now()
	if err := Fetch(context.Background(), cam, Options{FFmpeg: ff}, now.Add(-70*time.Second), now, out); err != nil {
		t.Fatal(err)
	}
	if fi, err := os.Stat(out); err != nil || fi.Size() != 8000 {
		t.Fatal("arquivo não gerado", err)
	}
}

func TestFetchSemGravacaoNaoVazaSenha(t *testing.T) {
	cam, fim := dvrFalso(t, time.Now().Format(layoutDVR))
	defer fim()
	ff := ffmpegFalso(t, `echo "rtsp://admin:segredo@x: Server returned 404 Not Found" >&2; exit 1`)
	now := time.Now()
	err := Fetch(context.Background(), cam, Options{FFmpeg: ff}, now.Add(-70*time.Second), now, filepath.Join(t.TempDir(), "c.mp4"))
	if err == nil || !strings.Contains(err.Error(), "404") || strings.Contains(err.Error(), "segredo") {
		t.Fatalf("erro inadequado: %v", err)
	}
}

func TestFetchExigeCredencial(t *testing.T) {
	err := Fetch(context.Background(), Camera{Host: "x", Canal: 1}, Options{}, time.Now(), time.Now(), "o.mp4")
	if err == nil || !strings.Contains(err.Error(), "dvrUser") {
		t.Fatal(err)
	}
}
