from memory.context_assembler import ContextResult, build_project_context, estimate_tokens
from memory.obsidian_vault import ObsidianVault


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
