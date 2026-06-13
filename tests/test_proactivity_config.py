from proactivity.config import ProactivityConfig


def test_defaults_when_env_missing():
    cfg = ProactivityConfig.from_env({})
    assert cfg.enabled is True
    assert cfg.stale_pending_days == 7
    assert cfg.stale_project_days == 14
    assert cfg.max_per_session == 3
    assert cfg.cooldown_days == 7
    assert cfg.briefing_top_k == 3
    assert cfg.min_score == 0.35


def test_reads_overrides_from_env():
    env = {
        "JARVIS_PROACTIVITY_ENABLED": "false",
        "JARVIS_PROACTIVITY_STALE_PENDING_DAYS": "3",
        "JARVIS_PROACTIVITY_STALE_PROJECT_DAYS": "21",
        "JARVIS_PROACTIVITY_MAX_PER_SESSION": "1",
        "JARVIS_PROACTIVITY_COOLDOWN_DAYS": "10",
        "JARVIS_PROACTIVITY_BRIEFING_TOP_K": "5",
        "JARVIS_PROACTIVITY_MIN_SCORE": "0.6",
    }
    cfg = ProactivityConfig.from_env(env)
    assert cfg.enabled is False
    assert cfg.stale_pending_days == 3
    assert cfg.stale_project_days == 21
    assert cfg.max_per_session == 1
    assert cfg.cooldown_days == 10
    assert cfg.briefing_top_k == 5
    assert cfg.min_score == 0.6


def test_malformed_values_fall_back_to_default():
    cfg = ProactivityConfig.from_env({"JARVIS_PROACTIVITY_STALE_PENDING_DAYS": "abc"})
    assert cfg.stale_pending_days == 7
