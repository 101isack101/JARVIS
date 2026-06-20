from types import SimpleNamespace

from memory.notes import read_note
from memory.obsidian_vault import ObsidianVault
from memory.tools import ToolContext, jarvis_recall, jarvis_remember


class FakeRAG:
    def __init__(self):
        self.indexed = []
        self.saved = 0
        self.search_results = []

    def index_file(self, path):
        self.indexed.append(path)
        return 1

    def save(self):
        self.saved += 1

    def search(self, query, top_k=3):
        return self.search_results[:top_k]


def test_jarvis_remember_writes_metadata_and_indexes_immediately(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    ctx = ToolContext(vault=vault, rag=rag)

    result = jarvis_remember(
        ctx,
        title="Agentics memory card",
        content="# Agentics memory card\n\nDecidimos mantener DynamoDB para estado.",
        tags=["decision", "Agentics"],
    )

    note_path = vault.memory_path / "Agentics memory card.md"
    card_path = vault.memory_path / "Project Memory Cards" / "Agentics_Code_Team.md"
    note = read_note(vault, note_path)

    assert result["saved"] is True
    assert result["operation"] == "created"
    assert result["indexed"] is True
    assert result["memory_kind"] == "decision"
    assert result["project"] == "Agentics_Code_Team"
    assert result["project_card"]["updated"] is True
    assert rag.indexed == [note_path, card_path]
    assert rag.saved == 2
    assert note.frontmatter["type"] == "jarvis-memory"
    assert note.frontmatter["memory_kind"] == "decision"
    assert note.frontmatter["project"] == "Agentics_Code_Team"
    assert "jarvis-memory" in note.tags
    assert card_path.exists()


def test_jarvis_remember_appends_existing_note_instead_of_overwriting(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    ctx = ToolContext(vault=vault, rag=rag)

    jarvis_remember(
        ctx,
        title="General preference",
        content="# General preference\n\nIsaac prefiere notas granulares.",
        tags=["preference"],
    )
    result = jarvis_remember(
        ctx,
        title="General preference",
        content="Tambien prefiere que Study Mode genere flashcards.",
        tags=["study"],
    )

    note_path = vault.memory_path / "General preference.md"
    text = note_path.read_text(encoding="utf-8")
    note = read_note(vault, note_path)

    assert result["operation"] == "appended"
    assert result["indexed"] is True
    assert "Isaac prefiere notas granulares." in text
    assert "Tambien prefiere que Study Mode genere flashcards." in text
    assert "## Memory update - preference" in text
    assert "preference" in note.tags
    assert "study" in note.tags
    assert rag.indexed == [note_path, note_path]
    assert rag.saved == 2


def test_jarvis_recall_returns_memory_metadata(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    ctx = ToolContext(vault=vault, rag=rag)

    jarvis_remember(
        ctx,
        title="Decision Sonnet",
        content="# Decision Sonnet\n\nDecidimos usar Sonnet por costo y latencia.",
        tags=["decision", "jarvis"],
    )
    rag.search_results = [
        SimpleNamespace(
            score=0.72,
            chunk=SimpleNamespace(
                title="Decision Sonnet",
                rel_path="Jarvis Memory/Decision Sonnet.md",
                text="Decidimos usar Sonnet por costo y latencia.",
            ),
        )
    ]

    result = jarvis_recall(ctx, "por que sonnet", top_k=1)

    found = result["results"][0]
    assert found["memory_kind"] == "decision"
    assert found["type"] == "jarvis-memory"
    assert found["confidence"] == "high"
    assert "decision" in found["tags"]


def test_jarvis_remember_blocks_sensitive_content(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    ctx = ToolContext(vault=vault, rag=rag)

    result = jarvis_remember(
        ctx,
        title="API key",
        content="GEMINI_API_KEY=AIza1234567890abcdefghijklmnopqrstuvwxyz",
        tags=["credential"],
    )

    assert result["saved"] is False
    assert result["blocked"] is True
    assert result["memory_kind"] == "sensitive"
    assert not (vault.memory_path / "API key.md").exists()
    assert rag.indexed == []


def test_project_memory_card_collects_project_updates(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    ctx = ToolContext(vault=vault, rag=rag)

    jarvis_remember(
        ctx,
        title="Polymath pending work",
        content="Queda pendiente retomar Polymath IDE y revisar el servidor TypeScript.",
        tags=["todo"],
    )

    card_path = vault.memory_path / "Project Memory Cards" / "Polymath IDE.md"
    card = card_path.read_text(encoding="utf-8")

    assert "# Polymath IDE - Memory Card" in card
    assert "## Pending" in card
    assert "retomar Polymath IDE" in card
    assert "- (pending)" not in card.split("## Pending", 1)[1].split("## Procedures", 1)[0]


class FakeReasoner:
    def __init__(self, text):
        self._text = text
        self.calls = 0

    def ask(self, instructions, *, context_extra="", max_tokens=300):
        self.calls += 1
        return type("Resp", (), {"text": self._text})()


def test_remember_refines_vague_content_when_enabled(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    reasoner = FakeReasoner('{"text": "Isaac prefiere notas granulares por proyecto", "doubt": false}')
    ctx = ToolContext(vault=vault, rag=rag, reasoner=reasoner, write_critique_enabled=True)

    jarvis_remember(
        ctx,
        title="Preferencia notas",
        content="Isaac quiere algo más granular, no estoy seguro de cómo",
        tags=["preference"],
    )

    note = read_note(vault, vault.memory_path / "Preferencia notas.md")
    assert "Isaac prefiere notas granulares por proyecto" in note.body
    assert "algo más granular" not in note.body
    assert reasoner.calls == 1


def test_remember_doubt_appends_marker(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    reasoner = FakeReasoner('{"text": "Isaac mencionó un cambio pendiente sin detallar", "doubt": true}')
    ctx = ToolContext(vault=vault, rag=rag, reasoner=reasoner, write_critique_enabled=True)

    jarvis_remember(
        ctx,
        title="Cambio pendiente",
        content="hay que cambiar algo, no estoy seguro qué",
        tags=["todo"],
    )

    text = (vault.memory_path / "Cambio pendiente.md").read_text(encoding="utf-8")
    assert "<!-- ksi-doubt:vague -->" in text


def test_remember_disabled_leaves_content_untouched(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    reasoner = FakeReasoner('{"text": "NO DEBERIA USARSE", "doubt": false}')
    ctx = ToolContext(vault=vault, rag=rag, reasoner=reasoner)  # flag default False

    jarvis_remember(
        ctx,
        title="Preferencia notas",
        content="Isaac quiere algo más granular, no estoy seguro de cómo",
        tags=["preference"],
    )

    note = read_note(vault, vault.memory_path / "Preferencia notas.md")
    assert "Isaac quiere algo más granular" in note.body
    assert "NO DEBERIA USARSE" not in note.body
    assert reasoner.calls == 0
