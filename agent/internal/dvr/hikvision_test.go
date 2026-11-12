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

// ISAPI falso (digest) devolvendo <localTime> com fuso.
func isapiFalso(t *testing.T, localTime string) (Camera, func()) {
	t.Helper()
	cam, fim := dvrFalso(t, "ignorado")
	fim()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		const nonce, realm = "n0nce", "DS-2CD"
		auth := r.Header.Get("Authorization")
		if auth == "" {
			w.Header().Set("WWW-Authenticate", `Digest realm="`+realm+`", qop="auth", nonce="`+nonce+`"`)
			w.WriteHeader(401)
			return
		}
		if r.URL.Path != "/ISAPI/System/time" {
			w.WriteHeader(404)
			return
		}
		w.Write([]byte(`<?xml version="1.0" encoding="UTF-8"?><Time version="2.0"><timeMode>NTP</timeMode><localTime>` + localTime + `</localTime><timeZone>CST+3:00:00</timeZone></Time>`))
	}))
	host, port, _ := net.SplitHostPort(strings.TrimPrefix(srv.URL, "http://"))
	var pn int
	for _, c := range port {
		pn = pn*10 + int(c-'0')
	}
	cam.Host, cam.HTTPPort, cam.Brand = host, pn, "hikvision"
	return cam, srv.Close
}

func TestHikOffsetIgnoraFusoEUsaInstante(t *testing.T) {
	agora := time.Date(2026, 11, 12, 13, 0, 0, 0, time.UTC) // 10:00 em -03:00
	cam, fim := isapiFalso(t, "2026-11-12T10:03:00-03:00")  // aparelho 3 min adiantado, em horário de Brasília
	defer fim()
	off, err := HikOffset(context.Background(), cam, Options{}, agora)
	if err != nil || off != 3*time.Minute {
		t.Fatalf("offset=%v err=%v", off, err)
	}
	cam2, fim2 := isapiFalso(t, "2026-11-12T10:00:01-03:00")
	defer fim2()
	if off, _ := HikOffset(context.Background(), cam2, Options{}, agora); off != 0 {
		t.Fatalf("esperava 0, veio %v", off)
	}
}

func TestHikPlaybackURLsDoisFormatosEmUTC(t *testing.T) {
	cam := Camera{Host: "10.0.0.9", Canal: 2, User: "admin", Pass: "p@ss"}
	ini := time.Date(2026, 11, 12, 10, 0, 0, 0, time.FixedZone("BRT", -3*3600)) // 13:00 UTC
	urls := HikPlaybackURLs(cam, ini, ini.Add(70*time.Second))
	if len(urls) != 2 {
		t.Fatal(urls)
	}
	if !strings.HasPrefix(urls[0], "rtsp://admin:p%40ss@10.0.0.9:554/Streaming/tracks/201?") {
		t.Fatal(urls[0])
	}
	if !strings.HasSuffix(urls[0], "starttime=20261112T130000Z&endtime=20261112T130110Z") ||
		!strings.HasSuffix(urls[1], "starttime=2026-11-12T13:00:00Z&endtime=2026-11-12T13:01:10Z") {
		t.Fatalf("%v", urls)
	}
}

// ffmpeg falso que só "aceita" o formato ISO: o agente tem que cair para a 2ª URL.
func TestFetchHikvisionTentaOutroFormatoDeHorario(t *testing.T) {
	cam, fim := isapiFalso(t, time.Now().Format("2006-01-02T15:04:05Z07:00"))
	defer fim()
	log := filepath.Join(t.TempDir(), "tentativas.txt")
	ff := ffmpegFalso(t, `echo "$@" >> `+log+`
case "$*" in
  *starttime=????-??-??T*) for a; do last="$a"; done; head -c 8000 /dev/zero > "$last";;
  *) echo "rtsp://admin:segredo@x: 404 Not Found" >&2; exit 1;;
esac`)
	out := filepath.Join(t.TempDir(), "c.mp4")
	now := time.Now()
	if err := Fetch(context.Background(), cam, Options{FFmpeg: ff}, now.Add(-70*time.Second), now, out); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(log)
	if n := strings.Count(string(b), "\n"); n != 2 || !strings.Contains(string(b), "/Streaming/tracks/301?")  // canal 3 do DVR falso {
		t.Fatalf("tentativas (%d): %s", n, b)
	}
	if fi, err := os.Stat(out); err != nil || fi.Size() != 8000 {
		t.Fatal("clipe não gerado", err)
	}
}

func TestFetchHikvisionFalhaNaLeituraDaHoraExplicaISAPI(t *testing.T) {
	cam := Camera{Host: "127.0.0.1", HTTPPort: 1, Canal: 1, User: "a", Pass: "b", Brand: "hikvision"}
	ff := ffmpegFalso(t, "exit 0")
	err := Fetch(context.Background(), cam, Options{FFmpeg: ff}, time.Now(), time.Now(), filepath.Join(t.TempDir(), "c.mp4"))
	if err == nil || !strings.Contains(err.Error(), "ISAPI") {
		t.Fatalf("%v", err)
	}
}
