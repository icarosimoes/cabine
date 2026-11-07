package tray

import (
	"bytes"
	"encoding/binary"
	"image"
	"image/color"
	"image/png"
)

// Ícone desenhado em código (sem arquivos): círculo colorido com borda. Empacotado como ICO (PNG dentro),
// formato que o systray do Windows aceita.
func circle(c color.RGBA) []byte {
	const n = 32
	img := image.NewRGBA(image.Rect(0, 0, n, n))
	cx, cy, r := 15.5, 15.5, 14.0
	for y := 0; y < n; y++ {
		for x := 0; x < n; x++ {
			dx, dy := float64(x)-cx, float64(y)-cy
			d := dx*dx + dy*dy
			switch {
			case d <= (r-3)*(r-3):
				img.Set(x, y, c)
			case d <= r*r:
				img.Set(x, y, color.RGBA{255, 255, 255, 230})
			}
		}
	}
	var buf bytes.Buffer
	_ = png.Encode(&buf, img)
	return toICO(buf.Bytes())
}

func toICO(p []byte) []byte {
	const off = 22
	ico := make([]byte, off, off+len(p))
	binary.LittleEndian.PutUint16(ico[2:], 1)
	binary.LittleEndian.PutUint16(ico[4:], 1)
	ico[6], ico[7] = 32, 32
	binary.LittleEndian.PutUint16(ico[10:], 1)
	binary.LittleEndian.PutUint16(ico[12:], 32)
	binary.LittleEndian.PutUint32(ico[14:], uint32(len(p)))
	binary.LittleEndian.PutUint32(ico[18:], off)
	return append(ico, p...)
}

var (
	iconOK      = circle(color.RGBA{34, 197, 94, 255})   // verde: tudo no ar
	iconFail    = circle(color.RGBA{239, 68, 68, 255})   // vermelho: antena offline / sem servidor / serviço parado
	iconUnknown = circle(color.RGBA{148, 163, 184, 255}) // cinza: sem dados ainda
)
