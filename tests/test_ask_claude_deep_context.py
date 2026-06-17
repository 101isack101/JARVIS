from memory.obsidian_vault import ObsidianVault
from memory.tools import ToolContext, ask_claude_deep
from memory import notes as notes_mod
from memory import triage as triage_mod


class FakeRAG:
    def search(self, query, top_k=3):
        return []

    def index_file(self, path):
        return 1

    def save(self):
        pass


class CapturingReasoner:
    model = "claude-sonnet-4-6"

    def __init__(self):
        self.captured = None

    def ask(self, prompt, context_extra=None, max_tokens=450):
        self.captured = context_extra
        from claude.reasoner import ReasonerResponse

        return ReasonerResponse(
            text="ok",
            input_tokens=1,
            output_tokens=1,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost_usd=0.0,
            latency_ms=1.0,
        )


def test_ask_claude_deep_appends_auto_context_after_model_context(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    card_path = triage_mod.project_card_path(vault, "Polymath IDE")
    card_path.parent.mkdir(parents=True, exist_ok=True)
    notes_mod.write_note(
        vault,
        card_path,
        body="# Polymath IDE - Memory Card\n\n## Pending\n- conectar agente\n",
        frontmatter={"type": "project-memory-card"},
    )
    reasoner = CapturingReasoner()
    ctx = ToolContext(vault=vault, rag=FakeRAG(), reasoner=reasoner)

    ask_claude_deep(ctx, prompt="sigamos con Polymath IDE", context_extra="nota del modelo")

    captured = reasoner.captured
    assert captured is not None
    assert "nota del modelo" in captured
    assert "conectar agente" in captured
    # auto-contexto va DESPUÉS del context_extra del modelo
    assert captured.index("nota del modelo") < captured.index("conectar agente")


def test_ask_claude_deep_attributes_usage():
    from memory.tools import ToolContext, ask_claude_deep

    class _R:
        text = "respuesta usando alpha"
        latency_ms = 1.0
        cost_usd = 0.0
        input_tokens = output_tokens = cache_creation_tokens = cache_read_tokens = 0

    class _Reasoner:
        model = "claude-x"
        def ask(self, prompt, context_extra=None, max_tokens=0):
            return _R()

    calls = {}

    class _Curator:
        def attribute_usage(self, prompt, text):
            calls["args"] = (prompt, text)

    ctx = ToolContext(vault=None, rag=None, reasoner=_Reasoner(), retrieval_curator=_Curator())
    # _augmented_context es fail-safe con vault=None: devuelve context_extra tal cual
    out = ask_claude_deep(ctx, "pregunta alpha", context_extra=None, max_tokens=200)
    assert out["ok"] is True
    assert calls["args"] == ("pregunta alpha", "respuesta usando alpha")


def test_ask_claude_deep_attribution_is_fail_safe():
    from memory.tools import ToolContext, ask_claude_deep

    class _R:
        text = "x"
        latency_ms = 1.0
        cost_usd = 0.0
        input_tokens = output_tokens = cache_creation_tokens = cache_read_tokens = 0

    class _Reasoner:
        model = "claude-x"
        def ask(self, prompt, context_extra=None, max_tokens=0):
            return _R()

    class _BoomCurator:
        def attribute_usage(self, prompt, text):
            raise RuntimeError("boom")

    ctx = ToolContext(vault=None, rag=None, reasoner=_Reasoner(), retrieval_curator=_BoomCurator())
    out = ask_claude_deep(ctx, "q", context_extra=None)   # no propaga
    assert out["ok"] is True
