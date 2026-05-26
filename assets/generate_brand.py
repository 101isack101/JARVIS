"""
assets/generate_brand.py - Branding HD de Project Grace.

Disenio:
  - Hexagono pointy-top en cyan filamento sobre fondo dark.
  - Una "G" estilizada formada por 5 trazos rectos dentro del hexagono.
  - Glow neon = halo externo controlado (no neblina interna que come las
    lineas). Render en 2 capas: halo wide+blurred con alpha media + core
    fino y brillante encima.

Salidas:
  - assets/icon.ico       (multi-size 16/32/48/64/128/256; render PER TAMANIO
                           con stroke proporcional ajustado para que el 16px
                           y el 32px sigan siendo legibles).
  - assets/logo_64.png    (transparente; header del overlay tkinter).
  - assets/logo_256.png   (fondo dark; splash / docs).
  - assets/logo_512.png   (fondo dark; alta resolucion para README).

Reproducible: `python assets/generate_brand.py` desde la raiz de Grace.
"""

from __future__ import annotations

import io
import math
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---- Paleta (cyan filamento + halo) ----
BG_DARK = (8, 14, 22, 255)            # fondo dark casi negro azulado
CORE_BRIGHT = (224, 252, 255, 255)    # filamento casi blanco con tinte cyan
HALO_CYAN = (127, 244, 248, 255)      # halo medio (puro cyan saturado)

# Factor de supersampling. Render interno = target * SUPERSAMPLE,
# luego Lanczos downscale al target final. Mayor = mas nitido, mas RAM.
SUPERSAMPLE = 8


def _hex_vertices(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """Vertices de hexagono pointy-top (un vertice apunta arriba)."""
    verts = []
    for i in range(6):
        a = math.pi / 2 + i * (math.pi / 3)
        x = cx + r * math.cos(a)
        y = cy - r * math.sin(a)
        verts.append((x, y))
    return verts


def _draw_pg_strokes(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    stroke: int,
    color: tuple,
) -> None:
    """Dibuja el simbolo geometrico de Project Grace dentro del hexagono.

    5 trazos: top + left + right + bottom (forman una caja interna) + middle
    horizontal (divider). El simbolo se lee como "PG" en contexto del nombre
    "Project Grace" — es el patron de los monogramas geometricos modernos
    (Lacoste, Audi, etc.) donde la marca y el nombre se refuerzan mutuamente.

    Restriccion geometrica: el hexagono pointy-top tiene ancho maximo solo
    en la flat-zone (y entre cy-r/2 y cy+r/2). Mantener box_h <= r para que
    los horizontales top/bottom no crucen el borde del hex.
    """
    box_h = r * 0.95
    box_w = r * 1.00
    left = cx - box_w / 2
    right = cx + box_w / 2
    top = cy - box_h / 2
    bottom = cy + box_h / 2
    mid_y = cy

    # Half-stroke compensation: las esquinas se ven cuadradas, no con bigotes.
    half = stroke // 2

    # (1) Top horizontal
    draw.line([(left - half, top), (right + half, top)], fill=color, width=stroke)
    # (2) Left vertical completo
    draw.line([(left, top - half), (left, bottom + half)], fill=color, width=stroke)
    # (3) Right vertical completo
    draw.line([(right, top - half), (right, bottom + half)], fill=color, width=stroke)
    # (4) Bottom horizontal
    draw.line([(left - half, bottom), (right + half, bottom)], fill=color, width=stroke)
    # (5) Middle horizontal (divider): separa la "P" arriba de la "G" abajo
    draw.line([(left - half, mid_y), (right + half, mid_y)], fill=color, width=stroke)


def _render_layer(
    canvas_size: int,
    stroke_ratio: float,
    color: tuple,
) -> Image.Image:
    """Renderiza el hex+G a un canvas cuadrado con stroke dado.

    Devuelve RGBA con fondo TRANSPARENTE (para composite posterior).
    """
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = canvas_size / 2
    # r = 0.38 deja margen de 12% en el borde del canvas; critico a 48px
    # donde 5% de margen no se distingue del filo del cuadrado.
    r = canvas_size * 0.38
    stroke = max(2, int(canvas_size * stroke_ratio))

    # Hexagono outline
    verts = _hex_vertices(cx, cy, r)
    draw.polygon(verts, outline=color, width=stroke)

    # Monograma PG interior
    _draw_pg_strokes(draw, cx, cy, r, stroke, color)

    return img


def _set_alpha(img: Image.Image, factor: float) -> Image.Image:
    """Multiplica el canal alpha por `factor` (0..1+)."""
    r, g, b, a = img.split()
    a = a.point(lambda px: max(0, min(255, int(px * factor))))
    return Image.merge("RGBA", (r, g, b, a))


def render_logo(target_size: int, transparent_bg: bool = False) -> Image.Image:
    """Renderiza el logo a un tamano final dado con calidad HD.

    Tecnica del filamento neon:
      1. Render core fino crisp (lineas brillantes nitidas).
      2. Glow = el MISMO core blurreado a 2 radios distintos. Como el blur
         se aplica sobre las lineas finas, el halo crece HACIA AFUERA
         naturalmente. Si en cambio renderizamos un "core con stroke wider"
         para hacer halo, el grosor extra LLENA el interior del hexagono y
         queda turbio. Esa fue la version anterior.
    """
    w = target_size * SUPERSAMPLE

    # Stroke proporcional al tamano. En sizes chicos las lineas necesitan
    # ser mas anchas relativamente para sobrevivir al downscale.
    if target_size <= 24:
        stroke_ratio = 1 / 12      # 16px: 1.3px stroke; minima legibilidad
        glow_intensity = 0.0
    elif target_size <= 48:
        stroke_ratio = 1 / 16      # 32/48px: ~3px stroke; el tamano del shortcut
        glow_intensity = 0.0       # sigue sin glow para no embarrar
    elif target_size <= 96:
        stroke_ratio = 1 / 24
        glow_intensity = 0.45
    elif target_size <= 160:
        stroke_ratio = 1 / 34
        glow_intensity = 0.65
    else:
        stroke_ratio = 1 / 48      # 256+: elegante, muy fino
        glow_intensity = 0.85

    bg = (0, 0, 0, 0) if transparent_bg else BG_DARK
    out = Image.new("RGBA", (w, w), bg)

    # ---- Core crisp ----
    core = _render_layer(w, stroke_ratio, CORE_BRIGHT)

    # ---- Glow = blur del core (NO un stroke wider). Solo si justifica. ----
    if glow_intensity > 0:
        # Halo cercano: blur pequeno + alpha alta. Da el "fulgor del filamento".
        near = core.filter(ImageFilter.GaussianBlur(radius=w * 0.005))
        # Mezclamos al cyan saturado para que el halo tenga TINTE cyan
        # (el core es casi blanco; sin esto el halo seria tambien blanquecino).
        near = _tint(near, HALO_CYAN, mix=0.7)
        near = _set_alpha(near, glow_intensity * 0.9)
        out.alpha_composite(near)

        # Halo lejano: blur grande + alpha baja. Da la difusion al fondo.
        far = core.filter(ImageFilter.GaussianBlur(radius=w * 0.025))
        far = _tint(far, HALO_CYAN, mix=0.85)
        far = _set_alpha(far, glow_intensity * 0.45)
        out.alpha_composite(far)

    # ---- Core crisp al frente: ULTIMO para que las lineas se vean nitidas ----
    out.alpha_composite(core)

    # Downscale Lanczos
    return out.resize((target_size, target_size), Image.LANCZOS)


def _write_ico_png_embedded(path: Path, images: list[Image.Image]) -> None:
    """Escribe un .ico multi-size con cada frame en formato PNG embebido.

    Formato ICO (Vista+) acepta PNG dentro de cada entry — mas compacto y
    soporta alpha real sin truco. La estructura es:
        ICONDIR (6 bytes)
        ICONDIRENTRY[n] (16 bytes cada uno)
        <png-data-frame-1>
        <png-data-frame-2>
        ...

    Lo escribimos a mano porque Pillow 12.x ignora `append_images` al
    guardar ICO y termina con un .ico de un solo frame, que Windows luego
    escala torpemente cuando pide otro size.
    """
    # Encoda cada image como PNG en memoria
    png_blobs: list[bytes] = []
    for im in images:
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        png_blobs.append(buf.getvalue())

    n = len(images)
    # ICONDIR: reserved(0) + type(1=ico) + count
    header = struct.pack("<HHH", 0, 1, n)

    # Acumular offsets. Las entries van inmediatamente despues del header
    # y antes de los blobs PNG.
    offset = 6 + 16 * n
    entries = bytearray()
    for im, blob in zip(images, png_blobs):
        w, h = im.size
        # ICO codifica 256 como 0 en el byte de width/height (max 256)
        bw = 0 if w >= 256 else w
        bh = 0 if h >= 256 else h
        # B B B B   H H I I
        # w h ncol res pln bpp size offset
        entry = struct.pack(
            "<BBBBHHII",
            bw, bh, 0, 0, 1, 32, len(blob), offset,
        )
        entries.extend(entry)
        offset += len(blob)

    with open(path, "wb") as f:
        f.write(header)
        f.write(entries)
        for blob in png_blobs:
            f.write(blob)


def _tint(img: Image.Image, color: tuple, mix: float) -> Image.Image:
    """Tinte una imagen RGBA hacia un color objetivo, preservando alpha.

    mix=0 -> imagen original; mix=1 -> color objetivo puro (con su alpha).
    Util para que el halo blurreado tenga tinte cyan aunque el core sea blanco.
    """
    r, g, b, a = img.split()
    target_r, target_g, target_b = color[0], color[1], color[2]
    new_r = r.point(lambda v: int(v * (1 - mix) + target_r * mix))
    new_g = g.point(lambda v: int(v * (1 - mix) + target_g * mix))
    new_b = b.point(lambda v: int(v * (1 - mix) + target_b * mix))
    return Image.merge("RGBA", (new_r, new_g, new_b, a))


def _load_brand_font(size: int) -> ImageFont.FreeTypeFont:
    """Carga Bahnschrift Bold para el texto del lockup.

    Bahnschrift es la geometric extended sans que viene con Windows 10/11
    (la respuesta de Microsoft a Eurostile/DIN). Es variable font: el peso
    se setea por axis "wght" (700=Bold). Si Bahnschrift no esta disponible
    (Windows < 10 / Wine / Linux), cae a Segoe UI Bold y luego Arial Bold.
    """
    candidates = [
        ("C:/Windows/Fonts/bahnschrift.ttf", 700),  # variable wght axis
        ("C:/Windows/Fonts/segoeuib.ttf", None),    # Segoe UI Bold
        ("C:/Windows/Fonts/arialbd.ttf", None),     # Arial Bold last resort
    ]
    for path, weight_axis in candidates:
        try:
            font = ImageFont.truetype(path, size)
            if weight_axis is not None:
                # Bahnschrift es variable: ajustar peso
                try:
                    font.set_variation_by_axes([weight_axis])
                except Exception:
                    pass  # algunas builds de pillow no exponen variations
            return font
        except (OSError, IOError):
            continue
    # Fallback final: font default de Pillow (bitmap, fea pero funciona)
    return ImageFont.load_default(size=size)


def render_lockup(width: int = 1600, height: int = 900) -> Image.Image:
    """Renderiza el lockup "PROJECT GRACE" completo: monograma PG + texto.

    Layout vertical:
      - 35% superior: monograma hexagonal centrado
      - 65% inferior: texto "PROJECT" sobre "GRACE" en dos lineas, centrado

    Esta es la imagen "splash" — fondo dark, cyan glow, todo a alta calidad.
    No se usa como icono (texto ilegible <128px), se usa en README, docs,
    presentaciones, o splash inicial del overlay si se decide agregarlo.
    """
    ss = 2  # supersampling interno (mayor RAM pero mas nitido)
    W, H = width * ss, height * ss

    bg = Image.new("RGBA", (W, H), BG_DARK)

    # ---- Monograma hexagonal en la zona superior ----
    # El hex ocupa ~30% de la altura total, centrado horizontalmente.
    hex_size = int(H * 0.36)
    hex_y_center = int(H * 0.27)
    hex_layer = render_logo(hex_size, transparent_bg=True)
    # render_logo ya hace downscale; reescalamos al canvas del lockup
    hex_layer_upscaled = hex_layer.resize((hex_size, hex_size), Image.LANCZOS)
    bg.alpha_composite(
        hex_layer_upscaled,
        dest=(int(W / 2 - hex_size / 2), int(hex_y_center - hex_size / 2)),
    )

    # ---- Texto "PROJECT" sobre "GRACE" con jerarquia tipografica ----
    # En el reference "GRACE" es claramente mas grande que "PROJECT" — eso
    # da peso visual al nombre del producto y subordina la palabra "PROJECT".
    font_small = int(H * 0.13)   # PROJECT
    font_big = int(H * 0.20)     # GRACE (~1.5x mas grande)

    text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)

    # Tracking (letter-spacing) manual: dibujamos letra por letra para
    # darle el "extended" feel similar al reference.
    def _draw_tracked(
        text: str, y: int, font: ImageFont.FreeTypeFont, tracking_px: int
    ) -> None:
        glyph_widths = [font.getbbox(ch)[2] - font.getbbox(ch)[0] for ch in text]
        total_w = sum(glyph_widths) + tracking_px * (len(text) - 1)
        x = (W - total_w) // 2
        for ch, gw in zip(text, glyph_widths):
            text_draw.text((x, y), ch, font=font, fill=CORE_BRIGHT)
            x += gw + tracking_px

    font_p = _load_brand_font(font_small)
    font_g = _load_brand_font(font_big)
    # Tracking proporcional a cada size. Mas grande -> mas espaciado.
    track_p = int(font_small * 0.10)
    track_g = int(font_big * 0.06)
    # Posicion vertical: PROJECT primero, GRACE debajo con gap proporcional.
    y_project = int(H * 0.48)
    y_grace = int(y_project + font_small * 1.20)
    _draw_tracked("PROJECT", y_project, font_p, track_p)
    _draw_tracked("GRACE", y_grace, font_g, track_g)

    # ---- Glow sobre el texto ----
    # Mismo patron que el monograma: blur + tinte cyan + alpha. Como el texto
    # ocupa mas pixeles que el monograma, los radios son proporcionalmente
    # mayores para que el halo sea visible.
    near = text_layer.filter(ImageFilter.GaussianBlur(radius=W * 0.003))
    near = _tint(near, HALO_CYAN, mix=0.7)
    near = _set_alpha(near, 0.9)

    far = text_layer.filter(ImageFilter.GaussianBlur(radius=W * 0.012))
    far = _tint(far, HALO_CYAN, mix=0.85)
    far = _set_alpha(far, 0.45)

    bg.alpha_composite(far)
    bg.alpha_composite(near)
    bg.alpha_composite(text_layer)

    # Downscale Lanczos a tamano final
    return bg.resize((width, height), Image.LANCZOS)


def main() -> int:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    assets = Path(__file__).resolve().parent
    print(f"Generando branding HD en {assets}")
    print(f"  Supersample factor: {SUPERSAMPLE}x")

    # PNGs del monograma solo (sin texto)
    targets_png = [
        ("logo_64.png", 64, True),
        ("logo_256.png", 256, False),
        ("logo_512.png", 512, False),
    ]
    for name, size, transparent in targets_png:
        img = render_logo(size, transparent_bg=transparent)
        img.save(assets / name, "PNG")
        print(f"  [OK] {name} ({img.size}, bg={'transparent' if transparent else 'dark'})")

    # Lockup completo: monograma + "PROJECT GRACE". Para splash/docs/README.
    for name, w, h in [
        ("lockup_1600x900.png", 1600, 900),
        ("lockup_800x450.png", 800, 450),
    ]:
        img = render_lockup(w, h)
        img.save(assets / name, "PNG")
        print(f"  [OK] {name} ({img.size})")

    # ICO multi-size, render INDEPENDIENTE por tamano. Escribimos el .ico
    # binario a mano porque Pillow 12.x con `append_images` solo conserva
    # el primer frame (bug/limitacion silenciosa que arruina los thumbs).
    ico_sizes = [16, 32, 48, 64, 128, 256]
    ico_layers = [render_logo(s, transparent_bg=False) for s in ico_sizes]
    _write_ico_png_embedded(assets / "icon.ico", ico_layers)
    print(f"  [OK] icon.ico (sizes={ico_sizes}, render independiente por size)")

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
