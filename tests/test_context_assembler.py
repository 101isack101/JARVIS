from memory.context_assembler import ContextResult, build_project_context, estimate_tokens
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
