#!/usr/bin/env python3
"""Generate Tauri app icons from the Axiom logo."""
import os
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "src-tauri" / "icons"
TMP_DIR = ROOT / "Axiom.iconset"

# Axiom accent green (v2 主题色系: emerald → teal)
COLOR_TOP = "#34d399"      # emerald-400
COLOR_MID = "#14b8a6"      # teal-500
COLOR_BOT = "#0f766e"      # teal-700
BG = "#f8fafc"             # slate-50


def hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def draw_axiom(size: int, bg=BG, padding_ratio=0.18) -> Image.Image:
    """Draw the Axiom stacked-rhombus logo at the requested size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Optional rounded-rect background
    if bg:
        pad = max(1, int(size * 0.06))
        corner = int(size * 0.18)
        draw.rounded_rectangle(
            [pad, pad, size - pad, size - pad],
            radius=corner,
            fill=hex_to_rgb(bg) + (255,),
        )

    # Drawing area inside background padding
    inner = size * (1 - 2 * padding_ratio)
    offset = size * padding_ratio
    # Scale from 24x24 viewBox to inner area
    scale = inner / 24.0
    ox = offset + inner / 2  # center x

    def pt(x, y):
        return ox + (x - 12) * scale, offset + y * scale

    layers = [
        # (path points in 24x24 coords), fill color
        ([(12, 2), (2, 7), (12, 12), (22, 7)], COLOR_TOP),
        ([(12, 7), (2, 12), (12, 17), (22, 12)], COLOR_MID),
        ([(12, 12), (2, 17), (12, 22), (22, 17)], COLOR_BOT),
    ]

    for points, color in layers:
        poly = [pt(x, y) for x, y in points]
        draw.polygon(poly, fill=hex_to_rgb(color))

    return img


def save_pngs():
    sizes = [32, 128, 256]
    for s in sizes:
        img = draw_axiom(s)
        img.save(OUT_DIR / f"{s}x{s}.png", "PNG")
    # 128@2x = 256x256 (same as 256x256 but named differently)
    (OUT_DIR / "128x128@2x.png").write_bytes((OUT_DIR / "256x256.png").read_bytes())


def save_ico():
    """Windows .ico with common sizes."""
    img1024 = draw_axiom(1024)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [img1024.resize((s, s), Image.LANCZOS) for s in sizes]
    img1024.save(OUT_DIR / "icon.ico", format="ICO", sizes=[(i.width, i.height) for i in imgs])


def save_icns():
    """macOS .icns via iconutil."""
    if TMP_DIR.exists():
        for f in TMP_DIR.iterdir():
            f.unlink()
        TMP_DIR.rmdir()
    TMP_DIR.mkdir(parents=True)

    # macOS iconset required sizes
    iconset_sizes = [16, 32, 64, 128, 256, 512, 1024]
    for s in iconset_sizes:
        img = draw_axiom(s)
        img.save(TMP_DIR / f"icon_{s}x{s}.png", "PNG")
        if s <= 512:
            img2x = draw_axiom(s * 2)
            img2x.save(TMP_DIR / f"icon_{s}x{s}@2x.png", "PNG")

    subprocess.run(["iconutil", "-c", "icns", str(TMP_DIR), "-o", str(OUT_DIR / "icon.icns")], check=True)

    # cleanup
    for f in TMP_DIR.iterdir():
        f.unlink()
    TMP_DIR.rmdir()


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    save_pngs()
    save_ico()
    save_icns()
    print(f"Icons generated in {OUT_DIR}")
