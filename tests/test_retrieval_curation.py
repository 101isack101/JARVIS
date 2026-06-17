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


from dataclasses import dataclass


@dataclass
class _Chunk:
    rel_path: str
    text: str
    title: str = ""


@dataclass
class _Result:
    chunk: _Chunk
    score: float


def test_rerank_reorders_by_factor(tmp_path):
    cur = _curator(tmp_path, cold_start_min=2, factor_floor=0.6, factor_ceil=1.4)
    a = _Result(_Chunk("p.md", "alpha"), 0.50)   # mala: factor 0.6 -> 0.30
    b = _Result(_Chunk("p.md", "beta"), 0.45)    # buena: factor 1.4 -> 0.63
    cur._chunks[RetrievalCurator.chunk_key("p.md", "alpha")] = {"retrieved": 10, "used": 0, "last_used": None, "last_touch": None}
    cur._chunks[RetrievalCurator.chunk_key("p.md", "beta")] = {"retrieved": 10, "used": 10, "last_used": None, "last_touch": None}
    out = cur.rerank([a, b])
    assert [r.chunk.text for r in out] == ["beta", "alpha"]
    assert abs(out[0].score - 0.63) < 1e-9


def test_rerank_neutral_when_no_stats(tmp_path):
    cur = _curator(tmp_path)
    a = _Result(_Chunk("p.md", "alpha"), 0.50)
    b = _Result(_Chunk("p.md", "beta"), 0.40)
    out = cur.rerank([a, b])
    assert [r.chunk.text for r in out] == ["alpha", "beta"]   # orden intacto, scores neutrales


def test_rerank_is_fail_safe(tmp_path):
    cur = _curator(tmp_path)
    bad = object()  # sin .chunk / .score
    out = cur.rerank([bad])
    assert out == [bad]   # devuelve la lista intacta, no propaga


def test_note_retrieval_increments_and_persists(tmp_path):
    cur = _curator(tmp_path)
    r = _Result(_Chunk("p.md", "alpha"), 0.5)
    cur.note_retrieval("que es alpha?", [r])
    key = RetrievalCurator.chunk_key("p.md", "alpha")
    assert cur._chunks[key]["retrieved"] == 1
    # pending guarda [key, text] bajo el hash del prompt
    reloaded = RetrievalCurator(config=cur.config, embed_fn=cur.embed_fn, state_path=cur.state_path)
    assert reloaded._chunks[key]["retrieved"] == 1
    assert any(key == k for pend in reloaded._pending.values() for k, _ in pend)


import numpy as np


def _lookup_embed(mapping):
    """embed_fn fake: devuelve el vector mapeado por texto (ya normalizado)."""
    def _fn(texts):
        return np.array([mapping[t] for t in texts], dtype="float32")
    return _fn


def test_attribute_usage_counts_only_used(tmp_path):
    cfg = KnowledgeImproverConfig(use_threshold=0.55)
    mapping = {
        "respuesta sobre alpha": [1.0, 0.0],
        "alpha":                 [1.0, 0.0],   # coseno 1.0 -> usado
        "beta":                  [0.0, 1.0],   # coseno 0.0 -> no usado
    }
    cur = RetrievalCurator(config=cfg, embed_fn=_lookup_embed(mapping), state_path=tmp_path / "u.json")
    ra = _Result(_Chunk("p.md", "alpha"), 0.5)
    rb = _Result(_Chunk("p.md", "beta"), 0.5)
    cur.note_retrieval("q", [ra, rb])
    cur.attribute_usage("q", "respuesta sobre alpha")
    ka = RetrievalCurator.chunk_key("p.md", "alpha")
    kb = RetrievalCurator.chunk_key("p.md", "beta")
    assert cur._chunks[ka]["used"] == 1
    assert cur._chunks[kb]["used"] == 0
    assert cur._pending == {}   # se limpio el pending del prompt


def test_attribute_usage_is_fail_safe(tmp_path):
    def _boom(texts):
        raise RuntimeError("embed caido")
    cfg = KnowledgeImproverConfig()
    cur = RetrievalCurator(config=cfg, embed_fn=_boom, state_path=tmp_path / "u.json")
    cur._chunks["k"] = {"retrieved": 1, "used": 0, "last_used": None, "last_touch": None}
    cur._pending = {RetrievalCurator._prompt_hash("q"): [["k", "alpha"]]}
    cur.attribute_usage("q", "cualquier respuesta")   # no debe propagar
    assert cur._chunks["k"]["used"] == 0


def test_housekeeping_decays_and_purges(tmp_path):
    cfg = KnowledgeImproverConfig(usage_decay_days=45)
    cur = RetrievalCurator(config=cfg, embed_fn=lambda t: np.zeros((len(t), 2), "float32"),
                           state_path=tmp_path / "u.json")
    # chunk stale: tocado hace 45 dias -> factor 0.5
    cur._chunks["stale"] = {"retrieved": 12, "used": 8, "last_used": "2026-05-01", "last_touch": "2026-05-01"}
    # chunk huerfano: no esta en valid_keys -> se elimina
    cur._chunks["orphan"] = {"retrieved": 5, "used": 2, "last_used": None, "last_touch": "2026-06-15"}
    cur.housekeeping(valid_keys={"stale"}, today="2026-06-15")
    assert "orphan" not in cur._chunks
    assert abs(cur._chunks["stale"]["retrieved"] - 6.0) < 1e-6
    assert abs(cur._chunks["stale"]["used"] - 4.0) < 1e-6
    assert cur._chunks["stale"]["last_touch"] == "2026-06-15"


def test_housekeeping_drops_negligible(tmp_path):
    cfg = KnowledgeImproverConfig(usage_decay_days=10)
    cur = RetrievalCurator(config=cfg, embed_fn=lambda t: np.zeros((len(t), 2), "float32"),
                           state_path=tmp_path / "u.json")
    cur._chunks["tiny"] = {"retrieved": 1, "used": 0, "last_used": None, "last_touch": "2026-01-01"}
    cur.housekeeping(valid_keys=None, today="2026-06-15")   # >> 10 dias -> retrieved ~ 0
    assert "tiny" not in cur._chunks
