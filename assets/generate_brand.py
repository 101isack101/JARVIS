"""
assets/generate_brand.py - Branding HD de JARVIS.

Direccion visual:
  - Icono premium y sobrio: fondo graphite, monograma "J" protagonista.
  - Paleta contenida: blanco hielo + cyan, sin acentos multicolor.
  - Detalles muy sutiles: borde interno y una linea de precision.

Salidas:
  - assets/icon.ico
  - assets/icon_elegant.ico
  - assets/logo_64.png
  - assets/logo_256.png
  - assets/logo_512.png
  - assets/lockup_*.png

Reproducible: `python assets/generate_brand.py` desde la raiz de JARVIS.
"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BG_TOP = (13, 16, 22, 255)
BG_BOTTOM = (2, 4, 8, 255)
GRAPHITE = (8, 11, 16, 255)
ICE = (238, 252, 255, 255)
CYAN = (88, 230, 255, 255)
CYAN_DEEP = (18, 144, 176, 255)
STEEL = (144, 166, 178, 255)

SUPERSAMPLE = 8


def _mix(a: tuple[int, int, int, int], b: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(4))


def _set_alpha(img: Image.Image, factor: float) -> Image.Image:
    r, g, b, a = img.split()
    a = a.point(lambda px: max(0, min(255, int(px * factor))))
    return Image.merge("RGBA", (r, g, b, a))


def _tint_from_mask(mask: Image.Image, color: tuple[int, int, int, int]) -> Image.Image:
    layer = Image.new("RGBA", mask.size, color)
    layer.putalpha(mask)
    return layer


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _load_brand_font(size: int, weight: int = 620) -> ImageFont.FreeTypeFont:
    candidates = [
        ("C:/Windows/Fonts/bahnschrift.ttf", weight),
        ("C:/Windows/Fonts/segoeuisb.ttf", None),
        ("C:/Windows/Fonts/segoeuib.ttf", None),
        ("C:/Windows/Fonts/arialbd.ttf", None),
    ]
    for path, weight_axis in candidates:
        try:
            font = ImageFont.truetype(path, size)
            if weight_axis is not None:
                try:
                    font.set_variation_by_axes([weight_axis])
                except Exception:
                    pass
            return font
        except (OSError, IOError):
            continue
    return ImageFont.load_default(size=size)


def _make_background(size: int) -> Image.Image:
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bg)
    for y in range(size):
        t = y / max(1, size - 1)
        draw.line((0, y, size, y), fill=_mix(BG_TOP, BG_BOTTOM, t))

    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.polygon(
        [
            (0, 0),
            (int(size * 0.62), 0),
            (int(size * 0.18), size),
            (0, size),
        ],
        fill=(255, 255, 255, 12),
    )
    sheen = sheen.filter(ImageFilter.GaussianBlur(radius=max(1, int(size * 0.025))))
    bg.alpha_composite(sheen)

    radius = int(size * 0.185)
    edge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edge)
    inset = int(size * 0.045)
    ed.rounded_rectangle(
        (inset, inset, size - inset - 1, size - inset - 1),
        radius=int(radius * 0.82),
        outline=(255, 255, 255, 26),
        width=max(1, int(size * 0.008)),
    )
    if size >= 512:
        ed.rounded_rectangle(
            (inset + int(size * 0.018), inset + int(size * 0.018), size - inset - int(size * 0.018), size - inset - int(size * 0.018)),
            radius=int(radius * 0.70),
            outline=(88, 230, 255, 20),
            width=max(1, int(size * 0.006)),
        )
    bg.alpha_composite(edge)
    bg.putalpha(_rounded_mask(size, radius))
    return bg


def _text_mask(
    text: str,
    size: int,
    font_size: int,
    y_offset: float = -0.02,
    x_offset: float = 0.0,
    weight: int = 620,
) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    font = _load_brand_font(font_size, weight)
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = (size - width) / 2 - bbox[0] + size * x_offset
    y = (size - height) / 2 - bbox[1] + size * y_offset
    draw.text((x, y), text, font=font, fill=255)
    return mask


def _gradient_fill(mask: Image.Image, top: tuple[int, int, int, int], bottom: tuple[int, int, int, int]) -> Image.Image:
    size = mask.size[0]
    fill = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(fill)
    for y in range(size):
        t = y / max(1, size - 1)
        draw.line((0, y, size, y), fill=_mix(top, bottom, t))
    fill.putalpha(mask)
    return fill


def _render_monogram(size: int, target_size: int) -> Image.Image:
    if target_size <= 32:
        font_scale = 0.82
        weight = 650
    elif target_size <= 64:
        font_scale = 0.78
        weight = 560
    else:
        font_scale = 0.75
        weight = 430
    mask = _text_mask(
        "J",
        size,
        int(size * font_scale),
        y_offset=-0.020,
        x_offset=0.018,
        weight=weight,
    )

    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    far = _tint_from_mask(mask, CYAN).filter(ImageFilter.GaussianBlur(radius=max(1, int(size * 0.040))))
    layer.alpha_composite(_set_alpha(far, 0.30 if target_size >= 64 else 0.18))

    near = _tint_from_mask(mask, CYAN_DEEP).filter(ImageFilter.GaussianBlur(radius=max(1, int(size * 0.012))))
    layer.alpha_composite(_set_alpha(near, 0.55))

    body = _gradient_fill(mask, ICE, CYAN)
    layer.alpha_composite(body)

    highlight_mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, int(size * 0.002))))
    highlight = _gradient_fill(highlight_mask, (255, 255, 255, 210), (255, 255, 255, 0))
    layer.alpha_composite(_set_alpha(highlight, 0.35))

    return layer


def render_logo(target_size: int, transparent_bg: bool = False) -> Image.Image:
    size = target_size * SUPERSAMPLE
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if not transparent_bg:
        out.alpha_composite(_make_background(size))

    mono = _render_monogram(size, target_size)
    out.alpha_composite(mono)

    if not transparent_bg and target_size >= 128:
        vignette = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        vd = ImageDraw.Draw(vignette)
        vd.rounded_rectangle(
            (int(size * 0.025), int(size * 0.025), int(size * 0.975), int(size * 0.975)),
            radius=int(size * 0.18),
            outline=(0, 0, 0, 110),
            width=max(2, int(size * 0.030)),
        )
        out.alpha_composite(vignette)

    return out.resize((target_size, target_size), Image.LANCZOS)


def _write_ico_png_embedded(path: Path, images: list[Image.Image]) -> None:
    png_blobs: list[bytes] = []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        png_blobs.append(buf.getvalue())

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = bytearray()
    for im, blob in zip(images, png_blobs):
        width, height = im.size
        entries.extend(
            struct.pack(
                "<BBBBHHII",
                0 if width >= 256 else width,
                0 if height >= 256 else height,
                0,
                0,
                1,
                32,
                len(blob),
                offset,
            )
        )
        offset += len(blob)

    with open(path, "wb") as f:
        f.write(header)
        f.write(entries)
        for blob in png_blobs:
            f.write(blob)


def _draw_tracked(
    draw: ImageDraw.ImageDraw,
    text: str,
    center_x: int,
    y: int,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    tracking_px: int,
) -> None:
    widths = [font.getbbox(ch)[2] - font.getbbox(ch)[0] for ch in text]
    total = sum(widths) + tracking_px * (len(text) - 1)
    x = center_x - total // 2
    for ch, gw in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += gw + tracking_px


def render_lockup(width: int = 1600, height: int = 900) -> Image.Image:
    ss = 2
    W, H = width * ss, height * ss
    bg = Image.new("RGBA", (W, H), GRAPHITE)
    draw = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / max(1, H - 1)
        draw.line((0, y, W, y), fill=_mix((12, 15, 21, 255), (2, 4, 8, 255), t))

    icon_size = int(H * 0.31)
    icon = render_logo(icon_size, transparent_bg=False)
    bg.alpha_composite(icon, (W // 2 - icon_size // 2, int(H * 0.15)))

    text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)
    font_big = _load_brand_font(int(H * 0.155), 650)
    font_small = _load_brand_font(int(H * 0.038), 520)
    _draw_tracked(td, "JARVIS", W // 2, int(H * 0.53), font_big, ICE, int(H * 0.020))
    _draw_tracked(td, "LOCAL AI COMMAND", W // 2, int(H * 0.705), font_small, (*STEEL[:3], 225), int(H * 0.012))

    glow = _tint_from_mask(text_layer.split()[3], CYAN).filter(ImageFilter.GaussianBlur(radius=max(3, int(W * 0.006))))
    bg.alpha_composite(_set_alpha(glow, 0.25))
    bg.alpha_composite(text_layer)

    rule = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rule)
    y = int(H * 0.795)
    rd.rounded_rectangle((int(W * 0.43), y, int(W * 0.57), y + max(2, int(H * 0.006))), radius=int(H * 0.003), fill=(88, 230, 255, 125))
    bg.alpha_composite(rule)

    return bg.resize((width, height), Image.LANCZOS)


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    assets = Path(__file__).resolve().parent
    print(f"Generando branding HD en {assets}")
    print(f"  Supersample factor: {SUPERSAMPLE}x")

    for name, size, transparent in [
        ("logo_64.png", 64, True),
        ("logo_256.png", 256, False),
        ("logo_512.png", 512, False),
    ]:
        img = render_logo(size, transparent_bg=transparent)
        img.save(assets / name, "PNG")
        print(f"  [OK] {name} ({img.size}, bg={'transparent' if transparent else 'dark'})")

    for name, width, height in [
        ("lockup_1600x900.png", 1600, 900),
        ("lockup_800x450.png", 800, 450),
    ]:
        img = render_lockup(width, height)
        img.save(assets / name, "PNG")
        print(f"  [OK] {name} ({img.size})")

    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_layers = [render_logo(size, transparent_bg=False) for size in ico_sizes]
    for ico_name in ["icon.ico", "icon_elegant.ico"]:
        _write_ico_png_embedded(assets / ico_name, ico_layers)
        print(f"  [OK] {ico_name} (sizes={ico_sizes}, render independiente por size)")

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
