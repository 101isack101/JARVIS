from memory.self_improvement.config import KnowledgeImproverConfig


def test_curation_defaults():
    c = KnowledgeImproverConfig()
    assert c.rag_curation_enabled is False
    assert c.use_threshold == 0.55
    assert c.cold_start_min == 5
    assert c.factor_floor == 0.6
    assert c.factor_ceil == 1.4
    assert c.usage_decay_days == 45


def test_curation_from_env_reads_gate():
    c = KnowledgeImproverConfig.from_env({
        "JARVIS_RAG_CURATION": "true",
        "JARVIS_KSI_USE_THRESHOLD": "0.7",
        "JARVIS_KSI_COLD_START_MIN": "3",
    })
    assert c.rag_curation_enabled is True
    assert c.use_threshold == 0.7
    assert c.cold_start_min == 3


from memory.self_improvement.retrieval_curation import RetrievalCurator


def _curator(tmp_path, **over):
    cfg = KnowledgeImproverConfig(**over)
    return RetrievalCurator(
        config=cfg,
        embed_fn=lambda texts: __import__("numpy").zeros((len(texts), 2), dtype="float32"),
        state_path=tmp_path / "rag_usage.json",
    )


def test_chunk_key_stable_across_text_and_path():
    k1 = RetrievalCurator.chunk_key("a/b.md", "Hola   mundo")
    k2 = RetrievalCurator.chunk_key("a/b.md", "Hola mundo")   # whitespace collapsa
    k3 = RetrievalCurator.chunk_key("a/b.md", "Otro texto")
    k4 = RetrievalCurator.chunk_key("a/c.md", "Hola mundo")   # otra ruta
    assert k1 == k2
    assert k1 != k3
    assert k1 != k4
    assert len(k1) == 16


def test_quality_factor_cold_start_is_neutral(tmp_path):
    cur = _curator(tmp_path, cold_start_min=5)
    cur._chunks["x"] = {"retrieved": 4, "used": 0, "last_used": None, "last_touch": None}
    assert cur.quality_factor("x") == 1.0          # < cold_start_min
    assert cur.quality_factor("missing") == 1.0     # desconocido


def test_quality_factor_linear(tmp_path):
    cur = _curator(tmp_path, cold_start_min=5, factor_floor=0.6, factor_ceil=1.4)
    cases = {0.0: 0.6, 0.25: 0.8, 0.5: 1.0, 0.75: 1.2, 1.0: 1.4}
    for i, (rate, expected) in enumerate(cases.items()):
        retrieved = 8
        cur._chunks[f"k{i}"] = {
            "retrieved": retrieved, "used": rate * retrieved,
            "last_used": None, "last_touch": None,
        }
        assert abs(cur.quality_factor(f"k{i}") - expected) < 1e-9
