from memory.self_improvement.config import KnowledgeImproverConfig


def test_defaults_are_safe():
    cfg = KnowledgeImproverConfig()
    assert cfg.enabled is True
    assert 0.0 < cfg.sim_threshold <= 1.0
    assert cfg.decay_half_life_days > 0
    assert cfg.min_cluster_size >= 2


def test_from_env_overrides():
    env = {
        "JARVIS_KSI_ENABLED": "false",
        "JARVIS_KSI_SIM_THRESHOLD": "0.9",
        "JARVIS_KSI_TOKEN_BUDGET": "0",
        "JARVIS_KSI_DECAY_HALF_LIFE_DAYS": "30",
        "JARVIS_KSI_MIN_CLUSTER_SIZE": "3",
    }
    cfg = KnowledgeImproverConfig.from_env(env)
    assert cfg.enabled is False
    assert cfg.sim_threshold == 0.9
    assert cfg.token_budget == 0
    assert cfg.decay_half_life_days == 30
    assert cfg.min_cluster_size == 3


def test_from_env_bad_values_fall_back():
    cfg = KnowledgeImproverConfig.from_env({"JARVIS_KSI_SIM_THRESHOLD": "abc"})
    assert cfg.sim_threshold == KnowledgeImproverConfig().sim_threshold
