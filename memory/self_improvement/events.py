"""Eventos de memoria derivados de los bullets de las Project Memory Cards.

Un evento es un hecho atómico con procedencia. El id es content-addressed para
que la reconfirmación del mismo hecho colapse al mismo id (idempotencia).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import confidence as conf_mod

_DATE_RE = r"(?P<date>\d{4}-\d{2}-\d{2})"
_CONF_RE = r"\[(?P<conf>[^\]]+)\]"
_KSI_RE = re.compile(r"<!--\s*ksi:(?P<json>\{.*?\})\s*-->\s*$")
_SOURCE_RE = re.compile(r"\s*\(source:\s*\[\[.*?\]\]\)\s*$")


@dataclass(frozen=True)
class MemoryEvent:
    id: str
    text: str
    section: str
    project: str
    source: str = ""
    learned_at: str = ""
    confidence: float = conf_mod._DEFAULT
    reinforced: int = 1
    superseded_by: str | None = None


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def event_id(text: str) -> str:
    return hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()[:16]


def parse_bullet(line: str, *, section: str, project: str) -> MemoryEvent | None:
    raw = (line or "").rstrip()
    if not raw.strip().startswith("- "):
        return None
    body = raw.strip()[2:].strip()
    if not body or body.lower() == "(pending)":
        return None

    ksi: dict = {}
    m_ksi = _KSI_RE.search(body)
    if m_ksi:
        try:
            ksi = json.loads(m_ksi.group("json"))
        except json.JSONDecodeError:
            ksi = {}
        body = body[: m_ksi.start()].rstrip()

    learned_at = ""
    m_date = re.match(_DATE_RE, body)
    if m_date:
        learned_at = m_date.group("date")
        body = body[m_date.end():].strip()

    confidence = None
    m_conf = re.match(_CONF_RE, body)
    if m_conf:
        token = m_conf.group("conf").split("/")[-1].strip()
        try:
            confidence = float(token)
        except ValueError:
            confidence = conf_mod.legacy_to_float(token)
        body = body[m_conf.end():].strip()

    body = _SOURCE_RE.sub("", body).strip()
    if not body:
        return None

    text = body
    return MemoryEvent(
        id=str(ksi.get("id") or event_id(text)),
        text=text,
        section=section,
        project=project,
        source=ksi.get("source", f"card:{project}"),
        learned_at=learned_at or (ksi.get("learned_at") or date.today().isoformat()),
        confidence=confidence if confidence is not None else conf_mod._DEFAULT,
        reinforced=int(ksi.get("reinforced", 1)),
        superseded_by=ksi.get("superseded_by"),
    )


def serialize_bullet(ev: MemoryEvent) -> str:
    meta = {"id": ev.id, "reinforced": ev.reinforced, "learned_at": ev.learned_at}
    if ev.superseded_by:
        meta["superseded_by"] = ev.superseded_by
    tag = "<!-- ksi:" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + " -->"
    return f"- {ev.learned_at} [{ev.confidence:.2f}] {ev.text} {tag}"
