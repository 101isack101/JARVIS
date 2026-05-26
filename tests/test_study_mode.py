from memory.obsidian_vault import ObsidianVault
from study.mode import StudyModeController, StudyModeConfig


class FakeReasoner:
    def ask(self, prompt, context_extra=None, max_tokens=1024):
        class Response:
            text = "## Resumen ejecutivo\n\n- Nota sintetizada de prueba.\n\n## Fuentes\n\n- Test"

        return Response()


def test_study_mode_observation_flushes_to_obsidian(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    controller = StudyModeController(
        vault=vault,
        reasoner=FakeReasoner(),
        config=StudyModeConfig(capture_interval_s=999, max_chars_per_capture=2000),
    )

    started = controller.start(
        title="AWS Lambda Study",
        note_path="Study Mode/AWS Lambda.md",
        continuous=False,
        capture_now=False,
    )
    assert started["ok"] is True

    captured = controller.add_observation(
        "Lambda event source mappings conectan streams/queues con funciones."
    )
    assert captured["captured"] is True

    flushed = controller.flush_now()
    assert flushed["ok"] is True
    assert flushed["flushed"] is True
    assert flushed["used_reasoner"] is True

    note_path = vault.memory_path / "Study Mode" / "AWS Lambda.md"
    text = note_path.read_text(encoding="utf-8")
    assert "AWS Lambda Study" in text
    assert "Nota sintetizada de prueba" in text

    stopped = controller.stop(flush=False)
    assert stopped["ok"] is True
