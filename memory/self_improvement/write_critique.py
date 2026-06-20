"""Auto-crítica en escritura (KSI Fase 4).

Detecta de forma determinista un `content` vago al guardarlo y, solo en ese caso,
pide al reasoner que lo reescriba preciso. Fail-safe total: ante cualquier fallo
devuelve el texto original. Stateless: no persiste nada.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Muletillas de imprecisión, bilingüe ES/EN. Matching por palabra, case-insensitive.
_VAGUE_TERMS = (
    # Español
    "algo", "varios", "varias", "creo", "más o menos", "mas o menos", "etc",
    "no estoy seguro", "como que", "tal vez", "quizá", "quizas", "supongo",
    "cosas", "alguna cosa",
    # Inglés
    "some", "a few", "several", "i think", "kind of", "sort of", "maybe",
    "i guess", "stuff", "things", "not sure", "somehow",
)

# Señales de concreción: si aparecen, el texto NO se considera vago aunque tenga
# muletillas. Dígitos, rutas, file.ext, acrónimos en MAYÚSCULAS, identificadores
# camelCase/PascalCase. Evita marcar como "concreta" una mayúscula de inicio de
# frase normal.
_CONCRETE_RE = re.compile(
    r"\d"                       # cualquier dígito
    r"|[/\\]"                   # separadores de ruta
    r"|\w+\.\w+"                # file.ext / modulo.attr
    r"|\b[A-Z]{2,}\b"           # acrónimos: API, RAG, AEC
    r"|\b\w*[a-z]\w*[A-Z]\w*"   # camelCase / PascalCase
)


@dataclass(frozen=True)
class CritiqueResult:
    text: str
    doubt: bool


def detect_vague(text: str) -> bool:
    """True si el texto tiene muletilla de imprecisión Y carece de concreción."""
    t = (text or "").strip()
    if not t:
        return False
    low = t.lower()
    has_vague = any(re.search(rf"\b{re.escape(term)}\b", low) for term in _VAGUE_TERMS)
    if not has_vague:
        return False
    if _CONCRETE_RE.search(t):
        return False
    return True
