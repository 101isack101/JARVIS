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
