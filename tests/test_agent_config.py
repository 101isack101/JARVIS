from agent_runtime.runtime import AgentRuntimeConfig


def test_agent_runtime_defaults_off(monkeypatch):
    for key in (
        "JARVIS_AGENT_RUNTIME_ENABLED", "JARVIS_AGENT_MAX_STEPS",
        "JARVIS_AGENT_DEADLINE_S", "JARVIS_AGENT_MAX_ACTIVE",
        "JARVIS_AGENT_ARTIFACT_MAX_CHARS", "JARVIS_AGENT_PLANNER_MAX_TOKENS",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = AgentRuntimeConfig.from_env()
    assert cfg.enabled is False
    assert cfg.max_steps == 8
    assert cfg.max_active == 1


def test_agent_runtime_env_is_clamped(monkeypatch):
    monkeypatch.setenv("JARVIS_AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("JARVIS_AGENT_MAX_STEPS", "99")
    monkeypatch.setenv("JARVIS_AGENT_DEADLINE_S", "1")
    monkeypatch.setenv("JARVIS_AGENT_MAX_ACTIVE", "5")
    cfg = AgentRuntimeConfig.from_env()
    assert cfg.enabled is True
    assert cfg.max_steps == 8
    assert cfg.deadline_seconds == 30
    assert cfg.max_active == 1
