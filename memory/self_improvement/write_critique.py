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


_INSTRUCTIONS = (
    "Eres el bibliotecario de JARVIS. Te paso una memoria que un detector marcó "
    "como VAGA o imprecisa. Reescríbela para que sea precisa y concreta, "
    "conservando SOLO la información presente. PROHIBIDO inventar datos, nombres, "
    "números o fechas que no estén en el texto. Si no puedes concretarla por falta "
    "de información objetiva, devuélvela lo más clara posible y marca doubt=true. "
    'Responde SOLO un objeto JSON: {"text": "<memoria reescrita>", "doubt": true|false}.'
)


def _extract_json(text: str) -> dict | None:
    """Primer objeto JSON balanceado dentro del texto (self-heal básico).

    Espeja memory/self_improvement/judge.py para mantener el módulo aislado.
    """
    s = text or ""
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    return None


def refine(reasoner, text: str, *, max_tokens: int = 300) -> CritiqueResult:
    """Pide al reasoner reescribir `text`. SOLO se llama sobre texto vago."""
    if reasoner is None:
        return CritiqueResult(text=text, doubt=False)
    try:
        resp = reasoner.ask(_INSTRUCTIONS, context_extra="MEMORIA:\n" + text, max_tokens=max_tokens)
        data = _extract_json(getattr(resp, "text", "") or "")
    except Exception:
        return CritiqueResult(text=text, doubt=False)
    if not isinstance(data, dict) or "text" not in data:
        return CritiqueResult(text=text, doubt=False)
    refined = str(data.get("text") or "").strip()
    if not refined:
        return CritiqueResult(text=text, doubt=False)
    return CritiqueResult(text=refined, doubt=bool(data.get("doubt")))


def critique(reasoner, text: str, *, enabled: bool, max_tokens: int = 300) -> CritiqueResult:
    """Fachada fail-safe — único punto de entrada para jarvis_remember."""
    try:
        if not enabled:
            return CritiqueResult(text=text, doubt=False)
        if not detect_vague(text):
            return CritiqueResult(text=text, doubt=False)
        return refine(reasoner, text, max_tokens=max_tokens)
    except Exception:
        return CritiqueResult(text=text, doubt=False)
