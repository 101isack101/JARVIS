"""Detección de lagunas de conocimiento (KSI Fase 2).

Tres detectores deterministas sobre el estado del vault + el reasoner para
formular preguntas naturales. Reusa confianza y contradicciones de Fase 1.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date

from memory import notes as notes_mod
from memory import triage as triage_mod

from . import confidence as conf_mod
from .detectors import detect_contradictions
from .judge import _extract_json


@dataclass(frozen=True)
class KnowledgeGap:
    gap_id: str
    kind: str
    project: str
    key: str
    context: str = ""
    question: str = ""


def gap_id(kind: str, project: str, key: str) -> str:
    raw = f"{kind}|{project}|{key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _make(kind: str, project: str, key: str, context: str) -> KnowledgeGap:
    return KnowledgeGap(
        gap_id=gap_id(kind, project, key),
        kind=kind, project=project, key=key, context=context,
    )


def detect_poor_cards(states, cfg) -> list[KnowledgeGap]:
    out: list[KnowledgeGap] = []
    for st in states:
        if getattr(st, "staleness_days", None) is None:
            continue  # proyecto sin actividad reciente: no molestar
        total = len(st.open_pendings) + len(st.open_decisions) + len(st.current_state)
        if not st.current_state or total < getattr(cfg, "min_card_bullets", 4):
            out.append(_make(
                "poor_card", st.project, st.project,
                f"El proyecto {st.project} tiene la Memory Card casi vacía.",
            ))
    return out


def detect_stale_facts(events, cfg, *, today=None) -> list[KnowledgeGap]:
    out: list[KnowledgeGap] = []
    for ev in events:
        if ev.superseded_by:
            continue
        decayed = conf_mod.decayed(
            ev.confidence, ev.learned_at,
            half_life_days=cfg.decay_half_life_days, today=today,
        )
        if decayed < getattr(cfg, "stale_confidence", 0.3):
            out.append(_make(
                "stale_fact", ev.project, ev.id,
                f"Hecho sin reconfirmar hace tiempo: {ev.text}",
            ))
    return out


def detect_open_contradictions(events) -> list[KnowledgeGap]:
    out: list[KnowledgeGap] = []
    for a, b in detect_contradictions(events):
        key = "|".join(sorted([a.id, b.id]))
        out.append(_make(
            "open_contradiction", a.project, key,
            f"Contradicción sin resolver: '{a.text}' vs '{b.text}'",
        ))
    return out


def collect_gaps(states, events, cfg, *, today=None) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    gaps.extend(detect_poor_cards(states, cfg))
    gaps.extend(detect_stale_facts(events, cfg, today=today))
    gaps.extend(detect_open_contradictions(events))
    return gaps


_FORMULATE_INSTRUCTIONS = (
    "Eres JARVIS. Te paso lagunas en tu conocimiento sobre los proyectos de Isaac. "
    "Por cada una, formula UNA pregunta breve, natural y en español (primera persona, "
    "directa, sin jerga técnica ni la palabra 'laguna'). Responde SOLO un objeto JSON "
    "que mapea gap_id -> pregunta. Ejemplo: {\"abc123\": \"¿Sigue vigente X?\"}."
)


def formulate_questions(reasoner, gaps: list[KnowledgeGap], *, token_budget: int, max_tokens: int = 600) -> list[KnowledgeGap]:
    if not gaps:
        return []
    if reasoner is None or token_budget <= 0:
        return list(gaps)
    payload = "\n".join(f"- {g.gap_id}: {g.context}" for g in gaps)
    try:
        resp = reasoner.ask(_FORMULATE_INSTRUCTIONS, context_extra="LAGUNAS:\n" + payload, max_tokens=max_tokens)
        data = _extract_json(getattr(resp, "text", "") or "")
    except Exception:
        return list(gaps)
    if not isinstance(data, dict):
        return list(gaps)
    out: list[KnowledgeGap] = []
    for g in gaps:
        q = data.get(g.gap_id)
        out.append(replace(g, question=str(q).strip()) if isinstance(q, str) and q.strip() else g)
    return out


QUESTIONS_HEADING = "Preguntas abiertas"
_GAP_TAG_RE = re.compile(r"<!--\s*ksi-gap:(?P<json>\{.*?\})\s*-->\s*$")


def _gap_tag(gap_id: str, kind: str, status: str) -> str:
    meta = {"gap_id": gap_id, "kind": kind, "status": status}
    return "<!-- ksi-gap:" + json.dumps(meta, ensure_ascii=False, separators=(",", ":")) + " -->"


def serialize_gap_bullet(text: str, *, gap_id: str, kind: str, status: str, today: str) -> str:
    return f"- {today} {text} {_gap_tag(gap_id, kind, status)}"


def _serialize_existing(display: str, *, gap_id: str, kind: str, status: str) -> str:
    return f"- {display} {_gap_tag(gap_id, kind, status)}"


def parse_questions_section(body: str) -> dict:
    """Devuelve {gap_id: {"display", "kind", "status"}} de la sección de preguntas."""
    out: dict = {}
    in_section = False
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            in_section = line[3:].strip() == QUESTIONS_HEADING
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        m = _GAP_TAG_RE.search(stripped)
        if not m:
            continue
        try:
            meta = json.loads(m.group("json"))
        except json.JSONDecodeError:
            continue
        display = stripped[2:m.start()].strip()
        gid = str(meta.get("gap_id") or "")
        if gid:
            out[gid] = {"display": display, "kind": meta.get("kind", ""), "status": meta.get("status", "open")}
    return out


def _strip_section(body: str, heading: str) -> str:
    lines = (body or "").splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i].strip() == f"## {heading}":
            i += 1
            while i < n and not lines[i].startswith("## "):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).rstrip()


def apply_questions(vault, project: str, gaps_with_questions: list[KnowledgeGap], active_gap_ids: set, *, today=None) -> None:
    today = today or date.today().isoformat()
    try:
        path = triage_mod.project_card_path(vault, project)
    except Exception:
        return
    if path.exists():
        note = notes_mod.read_note(vault, path)
        body, frontmatter = note.body, note.frontmatter
    else:
        body = triage_mod.initial_project_card_body(project)
        frontmatter = {"type": "project-memory-card", "project": project}

    existing = parse_questions_section(body)
    for g in gaps_with_questions:
        if g.question and g.gap_id not in existing:
            existing[g.gap_id] = {
                "display": f"{today} {g.question}", "kind": g.kind, "status": "open",
            }
    for gid, rec in existing.items():
        if rec["status"] == "open" and gid not in active_gap_ids:
            rec["status"] = "resolved"

    if not existing:
        return

    base = _strip_section(body, QUESTIONS_HEADING)
    ordered = sorted(existing.items(), key=lambda kv: (kv[1]["status"] != "open", kv[1]["display"]))
    section_lines = [f"## {QUESTIONS_HEADING}", ""]
    section_lines += [
        _serialize_existing(rec["display"], gap_id=gid, kind=rec["kind"], status=rec["status"])
        for gid, rec in ordered
    ]
    new_body = base.rstrip() + "\n\n" + "\n".join(section_lines).rstrip() + "\n"
    notes_mod.write_note(vault, path, new_body, frontmatter)
