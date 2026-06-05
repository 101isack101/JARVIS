from datetime import date

from memory.obsidian_vault import ObsidianVault
from memory import notes as notes_mod
from memory import triage as triage_mod
from proactivity.config import ProactivityConfig
from proactivity.engine import ProactivityEngine


def _write_card(vault, project, body, frontmatter=None):
    path = triage_mod.project_card_path(vault, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {"type": "project-memory-card", "importance": "high", "confidence": "high"}
    fm.update(frontmatter or {})
    notes_mod.write_note(vault, path, body=body, frontmatter=fm)


def _write_session(vault, name, body):
    base = vault.memory_path / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_text(body, encoding="utf-8")


class FakeRAG:
    def search(self, query, top_k=3):
        return []


def test_build_briefing_mentions_stale_pending(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(
        vault, "Polymath IDE",
        "# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n",
    )
    _write_session(
        vault, "2026-05-10_2100_sesion.md",
        "# S\n\n## Resumen\n- x\n\n## Pendientes\n- (ninguno)\n\n## Proyectos tocados\n- [[03-PROJECTS/polymath]]\n",
    )
    cfg = ProactivityConfig(min_score=0.0, stale_pending_days=7)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")

    block = eng.build_briefing(vault, today=date(2026, 5, 30))
    assert "BRIEFING PROACTIVO" in block
    assert "Polymath IDE" in block


def test_build_briefing_empty_when_disabled(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    cfg = ProactivityConfig(enabled=False)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")
    assert eng.build_briefing(vault, today=date(2026, 5, 30)) == ""


def test_build_briefing_failsafe_on_broken_vault(tmp_path):
    cfg = ProactivityConfig(min_score=0.0)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")

    class Boom:
        memory_path = tmp_path / "nope"

        def __getattr__(self, name):
            raise RuntimeError("boom")

    assert eng.build_briefing(Boom(), today=date(2026, 5, 30)) == ""
