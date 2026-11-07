"""Gera os ícones do PWA em app/static/icons/ (PNG). Uso: python3 scripts/gerar_icones.py  (requer Pillow)."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
NAVY, TEAL, WHITE = (15, 22, 35), (20, 184, 166), (226, 232, 240)
SS = 4  # supersampling para bordas suaves


def desenho(tam: int, maskable: bool) -> Image.Image:
    n = tam * SS
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable:  # ocupa tudo; o conteúdo fica na zona segura (~60% central)
        d.rectangle([0, 0, n, n], fill=NAVY)
        esc = 0.60
    else:
        d.rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.22), fill=NAVY)
        esc = 0.74
    cx, cy = n / 2, n * (0.5 + 0.1975 * esc)  # centraliza verticalmente o conjunto ondas + ponto
    base = n * esc / 2
    # ondas de rádio (arcos) saindo do ponto, como um leitor RFID
    for i, (r, esp) in enumerate(((0.40, 0.085), (0.68, 0.085), (0.96, 0.085))):
        rr = base * r * 1.0
        box = [cx - rr, cy - rr, cx + rr, cy + rr]
        d.arc(box, start=225, end=315, fill=TEAL if i < 2 else WHITE, width=int(base * esp * 1.6))
    pt = base * 0.17
    d.ellipse([cx - pt, cy - pt, cx + pt, cy + pt], fill=TEAL)
    return img.resize((tam, tam), Image.LANCZOS)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for nome, tam, mask in (("icon-192.png", 192, False), ("icon-512.png", 512, False), ("maskable-512.png", 512, True),
                            ("apple-touch-icon.png", 180, True), ("favicon-32.png", 32, False)):
        desenho(tam, mask).save(OUT / nome)
        print("ok", nome)
