import json
from types import SimpleNamespace

import numpy as np

from memory import notes as notes_mod
from memory.context_assembler import build_project_context
from memory.obsidian_vault import ObsidianVault
from memory.semantic import SemanticMemoryIndex, SourceDocument, SourceRegistry, summarize_jsonl_history
from memory.tools import ToolContext, ToolDispatcher


def _fake_embed(texts):
    rows = []
    for text in texts:
        haystack = text.lower()
        vec = np.zeros(384, dtype=np.float32)
        for idx, token in enumerate(
            ["polymath", "agentics", "jarvis", "faiss", "spotify", "obsidian", "debug"]
        ):
            if token in haystack:
                vec[idx] = 1.0
        if not vec.any():
            vec[20] = 1.0
        vec = vec / np.linalg.norm(vec)
        rows.append(vec)
    return np.vstack(rows).astype(np.float32)


def test_source_registry_collects_safe_local_sources(tmp_path):
    vault_root = tmp_path / "vault"
    workspace = tmp_path / "workspace"
    claude_mem = tmp_path / "claude_memory"
    codex_mem = tmp_path / "codex_memory"
    history = tmp_path / "history"
    for path in (vault_root, workspace, claude_mem, codex_mem, history):
        path.mkdir()

    vault = ObsidianVault(vault_root, read_all=True)
    notes_mod.write_note(
        vault,
        vault.memory_path / "Polymath.md",
        body="Polymath IDE usa FAISS para memoria semantica.",
        frontmatter={"confidence": "high", "project": "Polymath IDE"},
        tags=["project"],
    )
    (claude_mem / "project_agentics.md").write_text(
        "Agentics_Code_Team usa Step Functions.", encoding="utf-8"
    )
    (workspace / "README.md").write_text("JARVIS project docs sobre Obsidian.", encoding="utf-8")
    (workspace / ".env").write_text("TOKEN=abc", encoding="utf-8")
    (history / "session.jsonl").write_text(
        json.dumps({"type": "message", "message": {"content": "Debug Polymath con FAISS"}}) + "\n",
        encoding="utf-8",
    )

    registry = SourceRegistry(
        vault=vault,
        workspace_root=workspace,
        claude_memory_dir=claude_mem,
        codex_memory_dir=codex_mem,
        claude_history_dir=history,
        codex_sessions_dir=tmp_path / "missing",
        sources=("obsidian", "claude_memory", "agent_history_summaries", "project_docs"),
    )

    docs = list(registry.iter_documents())
    source_types = {doc.source_type for doc in docs}

    assert {"obsidian", "claude_memory", "claude_history_summary", "project_doc"} <= source_types
    assert all(".env" not in doc.path for doc in docs)
    assert any(doc.project == "Polymath IDE" for doc in docs)


def test_summarize_jsonl_history_redacts_secrets(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "message",
                "payload": {
                    "text": "Usamos GEMINI_API_KEY=AIza1234567890abcdefghijklmnopqrstuvwxyz para probar."
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = summarize_jsonl_history(path)

    assert "AIza" not in summary
    assert "[REDACTED" in summary
    assert "Resumen extractivo" in summary


def test_semantic_index_search_returns_metadata_and_redacts(tmp_path):
    index = SemanticMemoryIndex(index_dir=tmp_path / "semantic")
    index._embed = _fake_embed
    docs = [
        SourceDocument(
            source_type="claude_memory",
            source_uri="claude_memory:project_polymath.md",
            path="project_polymath.md",
            title="Polymath memory",
            text="Polymath IDE decision: usar FAISS. SECRET_TOKEN=abc12345678901234567890",
            project="Polymath IDE",
            confidence="high",
        ),
        SourceDocument(
            source_type="project_doc",
            source_uri="project_doc:README.md",
            path="README.md",
            title="Readme",
            text="Documentacion general de JARVIS.",
            confidence="low",
        ),
    ]

    stats = index.index_documents(docs)
    results = index.search("Polymath FAISS", top_k=2, min_score=0.0)

    assert stats["documents_total"] == 2
    assert results[0].chunk.source_type == "claude_memory"
    assert results[0].chunk.project == "Polymath IDE"
    assert "abc12345678901234567890" not in results[0].chunk.text
    assert "[REDACTED]" in results[0].chunk.text


def test_semantic_index_reindex_tombstones_old_chunks(tmp_path):
    index = SemanticMemoryIndex(index_dir=tmp_path / "semantic")
    index._embed = _fake_embed
    first = SourceDocument(
        source_type="claude_memory",
        source_uri="claude_memory:project_polymath.md",
        path="project_polymath.md",
        title="Polymath memory",
        text="Polymath IDE usaba una memoria vieja.",
    )
    second = SourceDocument(
        source_type="claude_memory",
        source_uri="claude_memory:project_polymath.md",
        path="project_polymath.md",
        title="Polymath memory",
        text="Polymath IDE ahora usa FAISS semantico.",
    )

    index.index_documents([first])
    index.index_documents([second])
    results = index.search("Polymath FAISS", top_k=5, min_score=0.0)

    assert len(results) == 1
    assert "FAISS semantico" in results[0].chunk.text
    assert "memoria vieja" not in results[0].chunk.text


def test_jarvis_recall_prefers_semantic_memory(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault = ObsidianVault(vault_root, read_all=True)

    class FakeSemantic:
        def search(self, query, top_k=3):
            return [
                SimpleNamespace(
                    score=0.77,
                    chunk=SimpleNamespace(
                        title="Agentics memory",
                        rel_path="project_agentics.md",
                        text="Agentics reutiliza Step Functions.",
                        source_type="claude_memory",
                        source_uri="claude_memory:project_agentics.md",
                        date="2026-06-01",
                        project="Agentics_Code_Team",
                        confidence="high",
                        tags=["project"],
                    ),
                )
            ]

    ctx = ToolContext(vault=vault, rag=SimpleNamespace(search=lambda *a, **k: []), semantic_memory=FakeSemantic())
    dispatcher = ToolDispatcher(ctx)

    result = dispatcher.call("jarvis_recall", {"query": "Agentics Step Functions", "top_k": 5})

    assert result["backend"] == "semantic"
    assert result["found"] == 1
    assert result["results"][0]["source_type"] == "claude_memory"
    assert result["results"][0]["project"] == "Agentics_Code_Team"


def test_context_assembler_uses_semantic_memory_when_available(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault = ObsidianVault(vault_root, read_all=True)

    class FakeSemantic:
        def __init__(self):
            self.queries = []

        def search(self, query, top_k=3):
            self.queries.append((query, top_k))
            return [
                SimpleNamespace(
                    score=0.82,
                    chunk=SimpleNamespace(
                        title="Polymath semantic",
                        rel_path="semantic:poly",
                        text="Polymath IDE ya tenia locking documentado.",
                        source_type="claude_memory",
                    ),
                )
            ]

    semantic = FakeSemantic()
    legacy = SimpleNamespace(search=lambda *a, **k: [])

    result = build_project_context(
        vault,
        legacy,
        "sigamos con Polymath IDE",
        semantic_memory=semantic,
    )

    assert semantic.queries == [("sigamos con Polymath IDE", 3)]
    assert "locking documentado" in result.text
    assert any(source.startswith("rag:") for source in result.sources)
