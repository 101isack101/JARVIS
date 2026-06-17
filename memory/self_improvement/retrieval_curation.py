"""Curacion de recuperaciones del RAG por uso real (KSI Fase 3).

El curator mide que chunks usa de verdad el reasoner y re-rankea las
recuperaciones futuras. Es un *decorador* de recuperacion: la fuente de verdad
sigue siendo el indice FAISS + el vault; rag_usage.json es cache desechable.
Fail-safe en cada metodo: nunca puede degradar la respuesta de voz.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import numpy as np

_WS_RE = re.compile(r"\s+")


class RetrievalCurator:
    def __init__(self, *, config, embed_fn, state_path: Path | str = "data/rag_usage.json") -> None:
        self.config = config
        self.embed_fn = embed_fn
        self.state_path = Path(state_path)
        self._chunks: dict[str, dict] = {}
        self._pending: dict[str, list] = {}
        self._load()

    # ---- clave estable ----
    @staticmethod
    def chunk_key(rel_path: str, text: str) -> str:
        norm = _WS_RE.sub(" ", (text or "")).strip()
        raw = f"{rel_path}|{norm}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    # ---- senal de calidad ----
    def quality_factor(self, key: str) -> float:
        st = self._chunks.get(key)
        if st is None or st.get("retrieved", 0) < self.config.cold_start_min:
            return 1.0
        retrieved = st["retrieved"]
        rate = st.get("used", 0) / retrieved if retrieved else 0.0
        floor, ceil = self.config.factor_floor, self.config.factor_ceil
        factor = floor + rate * (ceil - floor)
        return max(floor, min(ceil, factor))

    # ---- persistencia atomica ----
    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._chunks = dict(data.get("chunks", {}))
            self._pending = dict(data.get("pending", {}))
        except Exception:
            self._chunks, self._pending = {}, {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"chunks": self._chunks, "pending": self._pending}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self.state_path)
        except Exception:
            pass
