"""Detección determinista de candidatos para el reasoner.

- duplicados: clusters por similitud coseno (embeddings inyectados)
- contradicciones: pares del mismo proyecto con polaridad de negación opuesta
  y alto solapamiento de tokens. Heurística barata; el reasoner decide después.
"""

from __future__ import annotations

import re
from typing import Callable

import numpy as np

from .events import MemoryEvent

EmbedFn = Callable[[list[str]], "np.ndarray"]

_NEG = {"no", "nunca", "jamas", "jamás", "sin"}
_TOKEN_RE = re.compile(r"[a-z0-9áéíóúñ]+")


def detect_duplicate_clusters(
    events: list[MemoryEvent], embed_fn: EmbedFn, *, threshold: float, min_size: int = 2
) -> list[list[MemoryEvent]]:
    if len(events) < min_size:
        return []
    vecs = embed_fn([e.text for e in events])
    n = len(events)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if events[i].project != events[j].project:
                continue
            sim = float(np.dot(vecs[i], vecs[j]))
            if sim >= threshold:
                union(i, j)

    groups: dict[int, list[MemoryEvent]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(events[idx])
    return [g for g in groups.values() if len(g) >= min_size]


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _polarity(text: str) -> bool:
    return bool(_tokens(text) & _NEG)


def detect_contradictions(events: list[MemoryEvent]) -> list[tuple[MemoryEvent, MemoryEvent]]:
    out: list[tuple[MemoryEvent, MemoryEvent]] = []
    n = len(events)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = events[i], events[j]
            if a.project != b.project:
                continue
            if _polarity(a.text) == _polarity(b.text):
                continue
            ta, tb = _tokens(a.text) - _NEG, _tokens(b.text) - _NEG
            if not ta or not tb:
                continue
            overlap = len(ta & tb) / len(ta | tb)
            if overlap >= 0.6:
                out.append((a, b))
    return out
