"""
vision/detect.py - Deteccion one-shot de bounding box para el crosshair.

Usa client.models.generate_content (NO la sesion Live) con salida estructurada
para obtener {label, box_2d} en coords normalizadas 0..1000 (convencion de
spatial understanding de Gemini). Funciones de parseo separadas para testear
sin red.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_DETECT_MODEL = os.environ.get("JARVIS_CAMERA_DETECT_MODEL", "gemini-3.1-flash")

_PROMPT = (
    "Detecta el objeto principal que se muestra a la camara. Devuelve SOLO un JSON "
    '{"label": "<nombre corto>", "box_2d": [ymin, xmin, ymax, xmax]} con coordenadas '
    "normalizadas de 0 a 1000. Sin texto extra."
)


def parse_box_2d(text: str) -> dict | None:
    """Extrae {label, box_2d} de la respuesta del modelo. None si no hay box valido."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    box = data.get("box_2d")
    if not (isinstance(box, list) and len(box) == 4):
        return None
    try:
        box = [int(v) for v in box]
    except Exception:
        return None
    return {"label": str(data.get("label", "")), "box_2d": box}


def box_to_pixels(box_2d, width: int, height: int, ox: int = 0, oy: int = 0):
    """box_2d=[ymin,xmin,ymax,xmax] en 0..1000 -> (x1,y1,x2,y2) px del preview."""
    ymin, xmin, ymax, xmax = box_2d
    x1 = int(xmin / 1000 * width) + ox
    y1 = int(ymin / 1000 * height) + oy
    x2 = int(xmax / 1000 * width) + ox
    y2 = int(ymax / 1000 * height) + oy
    return (x1, y1, x2, y2)


def detect_object(client: Any, jpeg_bytes: bytes) -> dict | None:
    """Llamada real a Gemini (one-shot). Devuelve {label, box_2d} o None.

    `client` es un genai.Client ya construido (lo provee jarvis.py). Se aisla aqui
    para poder mockearlo en tests sin tocar red.
    """
    from google.genai import types
    try:
        resp = client.models.generate_content(
            model=_DETECT_MODEL,
            contents=[
                types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                types.Part(text=_PROMPT),
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return parse_box_2d(getattr(resp, "text", "") or "")
    except Exception:
        return None
