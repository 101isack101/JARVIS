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


def test_gap_config_defaults_and_env():
    cfg = KnowledgeImproverConfig()
    assert cfg.min_card_bullets >= 1
    assert 0.0 < cfg.stale_confidence < 1.0
    cfg2 = KnowledgeImproverConfig.from_env({
        "JARVIS_KSI_MIN_CARD_BULLETS": "6",
        "JARVIS_KSI_STALE_CONFIDENCE": "0.25",
    })
    assert cfg2.min_card_bullets == 6
    assert cfg2.stale_confidence == 0.25


def test_write_critique_config_defaults_off_and_env():
    cfg = KnowledgeImproverConfig()
    assert cfg.write_critique_enabled is False
    cfg2 = KnowledgeImproverConfig.from_env({"JARVIS_KSI_WRITE_CRITIQUE": "true"})
    assert cfg2.write_critique_enabled is True
