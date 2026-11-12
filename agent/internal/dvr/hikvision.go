package dvr

// Hikvision (DVR/NVR ou câmera IP com cartão SD). Playback RTSP: rtsp://user:pass@ip:554/Streaming/tracks/<canal*100+stream>?starttime=..&endtime=..
// com horário em UTC. O formato do horário muda entre firmwares (compacto 20261112T130000Z na documentação; ISO 2026-11-12T13:00:00Z
// em relatos de câmeras novas): o agente tenta os dois. A hora do aparelho vem do ISAPI (GET /ISAPI/System/time, digest).

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"regexp"
	"time"
)

var reLocalTime = regexp.MustCompile(`<localTime>\s*([^<\s]+)\s*</localTime>`)

// HikOffset devolve (relógio do aparelho − relógio do PC) como instantes absolutos: não depende de fuso.
func HikOffset(ctx context.Context, cam Camera, opt Options, agora time.Time) (time.Duration, error) {
	port := cam.HTTPPort
	if port == 0 {
		port = 80
	}
	u := fmt.Sprintf("http://%s/ISAPI/System/time", net.JoinHostPort(cam.Host, fmt.Sprint(port)))
	body, err := Get(ctx, clientOr(opt.Client), u, cam.User, cam.Pass)
	if err != nil {
		return 0, err
	}
	m := reLocalTime.FindStringSubmatch(string(body))
	if m == nil {
		return 0, fmt.Errorf("resposta inesperada do ISAPI ao ler a hora: %.80q", string(body))
	}
	var dev time.Time
	for _, l := range []string{time.RFC3339, "2006-01-02T15:04:05Z0700", "2006-01-02T15:04:05"} {
		if dev, err = time.Parse(l, m[1]); err == nil {
			break
		}
	}
	if err != nil {
		return 0, fmt.Errorf("hora do aparelho em formato desconhecido: %q", m[1])
	}
	off := dev.Sub(agora).Round(time.Second)
	if off > -2*time.Second && off < 2*time.Second {
		return 0, nil
	}
	return off, nil
}

// HikPlaybackURLs monta as URLs de playback do trecho [ini, fim] (instantes do relógio do aparelho) nos dois formatos de horário.
func HikPlaybackURLs(cam Camera, ini, fim time.Time) []string {
	port := cam.RTSPPort
	if port == 0 {
		port = 554
	}
	canal := cam.Canal
	if canal < 1 {
		canal = 1
	}
	base := url.URL{Scheme: "rtsp", User: url.UserPassword(cam.User, cam.Pass), Host: net.JoinHostPort(cam.Host, fmt.Sprint(port)),
		Path: fmt.Sprintf("/Streaming/tracks/%d01", canal)} // 101 = canal 1, stream principal; 201 = canal 2...
	i, f := ini.UTC(), fim.UTC()
	compacto := fmt.Sprintf("?starttime=%s&endtime=%s", i.Format("20060102T150405Z"), f.Format("20060102T150405Z"))
	iso := fmt.Sprintf("?starttime=%s&endtime=%s", i.Format("2006-01-02T15:04:05Z"), f.Format("2006-01-02T15:04:05Z"))
	return []string{base.String() + compacto, base.String() + iso}
}
