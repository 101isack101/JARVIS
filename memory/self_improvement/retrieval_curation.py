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

    @staticmethod
    def _prompt_hash(prompt: str) -> str:
        return hashlib.sha1((prompt or "").encode("utf-8")).hexdigest()[:16]

    def _stat(self, key: str) -> dict:
        return self._chunks.setdefault(
            key, {"retrieved": 0, "used": 0, "last_used": None, "last_touch": None}
        )

    def rerank(self, results: list) -> list:
        try:
            adjusted = []
            for r in results:
                key = self.chunk_key(r.chunk.rel_path, r.chunk.text)
                r.score = r.score * self.quality_factor(key)
                adjusted.append(r)
            adjusted.sort(key=lambda r: r.score, reverse=True)
            return adjusted
        except Exception:
            return results

    def note_retrieval(self, prompt: str, results: list, *, today=None) -> None:
        try:
            today = today or date.today().isoformat()
            pend: list = []
            for r in results:
                key = self.chunk_key(r.chunk.rel_path, r.chunk.text)
                st = self._stat(key)
                st["retrieved"] += 1
                st["last_touch"] = today
                pend.append([key, r.chunk.text])
            if pend:
                self._pending[self._prompt_hash(prompt)] = pend
                self._save()
        except Exception:
            pass

    def attribute_usage(self, prompt: str, response_text: str, *, today=None) -> None:
        try:
            phash = self._prompt_hash(prompt)
            pend = self._pending.get(phash)
            if not pend or not (response_text or "").strip():
                return
            today = today or date.today().isoformat()
            texts = [response_text] + [text for _, text in pend]
            emb = np.asarray(self.embed_fn(texts), dtype="float32")
            resp_vec = emb[0]
            resp_norm = float(np.linalg.norm(resp_vec)) or 1.0
            for i, (key, _text) in enumerate(pend):
                vec = emb[i + 1]
                denom = (float(np.linalg.norm(vec)) or 1.0) * resp_norm
                cos = float(np.dot(resp_vec, vec)) / denom
                if cos >= self.config.use_threshold:
                    st = self._stat(key)
                    st["used"] += 1
                    st["last_used"] = today
                    st["last_touch"] = today
            self._pending.pop(phash, None)
            self._save()
        except Exception:
            pass

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
