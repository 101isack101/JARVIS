from memory.context_assembler import ContextResult, build_project_context, estimate_tokens, _wrap
from memory.obsidian_vault import ObsidianVault
from memory import notes as notes_mod
from memory import triage as triage_mod


def _write_card(vault, project, body):
    path = triage_mod.project_card_path(vault, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    notes_mod.write_note(vault, path, body=body, frontmatter={"type": "project-memory-card"})
    return path


class FakeRAG:
    def __init__(self, results=None):
        self.results = results or []
        self.queries = []

    def search(self, query, top_k=3):
        self.queries.append((query, top_k))
        return self.results[:top_k]


def test_estimate_tokens_uses_char_heuristic():
    assert estimate_tokens("a" * 40) == 10


def test_no_project_detected_returns_empty(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()

    result = build_project_context(vault, rag, "¿qué hora es en Tokio?")

    assert isinstance(result, ContextResult)
    assert result.text == ""
    assert result.project is None
    assert result.sources == []
    assert rag.queries == []  # sin proyecto, ni siquiera consulta RAG


def test_includes_project_card_when_present(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- revisar server TS\n")

    result = build_project_context(vault, rag, "ayúdame con Polymath IDE y el server")

    assert result.project == "Polymath IDE"
    assert "card" in result.sources
    assert "revisar server TS" in result.text
    assert "CONTEXTO DE PROYECTO: Polymath IDE" in result.text


def test_missing_card_does_not_crash(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()

    result = build_project_context(vault, rag, "ayúdame con Polymath IDE")

    assert result.project == "Polymath IDE"
    assert "card" not in result.sources


def _write_session(vault, name, text):
    # SESSIONS_SUBDIR real es "sessions" (no "Sesiones" como asumía el plan)
    base = vault.memory_path / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_text(text, encoding="utf-8")


def test_includes_session_recall_when_present(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- algo\n")
    _write_session(
        vault,
        "2026-05-29_2100_sesion.md",
        "# Sesión\n\n## Resumen\nTrabajamos en el editor Monaco.\n\n## Pendientes\n- conectar el agente\n",
    )

    result = build_project_context(vault, rag, "sigamos con Polymath IDE")

    assert "session" in result.sources
    assert "Monaco" in result.text or "conectar el agente" in result.text
    assert "Sesión anterior" in result.text


from types import SimpleNamespace


def _result(score, text, title="Nota", rel_path="Jarvis Memory/Nota.md"):
    return SimpleNamespace(
        score=score,
        chunk=SimpleNamespace(title=title, rel_path=rel_path, text=text),
    )


def test_includes_rag_chunks_above_threshold(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG(results=[
        _result(0.80, "Decidimos usar DynamoDB para estado."),
        _result(0.60, "Optimistic locking con version field."),
        _result(0.10, "Ruido irrelevante por debajo del umbral."),
    ])
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- algo\n")

    result = build_project_context(vault, rag, "Polymath IDE estado y locking")

    assert any(s.startswith("rag:") for s in result.sources)
    assert "DynamoDB" in result.text
    assert "Optimistic locking" in result.text
    assert "Ruido irrelevante" not in result.text  # filtrado por MIN_RAG_SCORE
    assert rag.queries == [("Polymath IDE estado y locking", 3)]


def test_rag_only_no_card_no_session_still_returns_context(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG(results=[_result(0.90, "Algo muy relevante sobre Polymath.")])

    result = build_project_context(vault, rag, "Polymath IDE dudas")

    assert result.project == "Polymath IDE"
    assert any(s.startswith("rag:") for s in result.sources)
    assert "Algo muy relevante" in result.text


def test_budget_drops_rag_before_card(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    # _format_rag recorta cada snippet a ~220 chars, así que el RAG real cuesta
    # ~65 tokens. Card ~18 tokens. budget=70 deja entrar la card pero excede al
    # sumar el RAG (18+65=83 > 70), forzando el descarte del RAG.
    big_chunk = "X" * 4000
    rag = FakeRAG(results=[_result(0.90, big_chunk)])
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- conservar esto\n")

    result = build_project_context(vault, rag, "Polymath IDE", token_budget=70)

    assert "conservar esto" in result.text          # la card sobrevive
    assert "card" in result.sources
    assert not any(s.startswith("rag:") for s in result.sources)  # RAG se descartó
    assert estimate_tokens(result.text) <= 70 + estimate_tokens(_wrap("Polymath IDE", []))


def test_budget_generous_keeps_everything(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG(results=[_result(0.90, "memoria relevante")])
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- algo\n")

    result = build_project_context(vault, rag, "Polymath IDE", token_budget=5000)

    assert "card" in result.sources
    assert any(s.startswith("rag:") for s in result.sources)


class ExplodingRAG:
    def search(self, query, top_k=3):
        raise RuntimeError("boom")


def test_rag_failure_does_not_break_assembly(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- algo\n")

    result = build_project_context(vault, ExplodingRAG(), "Polymath IDE")

    assert "card" in result.sources          # la card sobrevive
    assert not any(s.startswith("rag:") for s in result.sources)
    assert result.text != ""
