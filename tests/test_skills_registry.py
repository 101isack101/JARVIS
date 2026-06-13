import json

from skills.registry import SkillRegistry, active_skill_prompt_block


def test_skill_registry_lists_builtin_skills(tmp_path):
    registry = SkillRegistry(
        skill_dir=tmp_path / "skills",
        state_path=tmp_path / "state.json",
        import_dirs=[],
    )

    names = {skill["name"] for skill in registry.list()}

    assert "desktop_operator" in names
    assert "study_capture" in names


def test_skill_registry_activate_persists_state(tmp_path):
    state = tmp_path / "state.json"
    registry = SkillRegistry(
        skill_dir=tmp_path / "skills",
        state_path=state,
        import_dirs=[],
    )

    result = registry.activate("desktop_operator")
    reloaded = SkillRegistry(
        skill_dir=tmp_path / "skills",
        state_path=state,
        import_dirs=[],
    )

    assert result["ok"] is True
    assert result["active"] == "desktop_operator"
    assert reloaded.status()["active"]["name"] == "desktop_operator"


def test_skill_registry_loads_local_json_skill(tmp_path):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "custom.json").write_text(
        json.dumps(
            {
                "name": "custom_skill",
                "title": "Custom Skill",
                "description": "Local test skill.",
                "triggers": ["custom trigger"],
                "tools": ["jarvis_recall"],
                "risk": "low",
                "instructions": "Follow local custom instructions.",
            }
        ),
        encoding="utf-8",
    )
    registry = SkillRegistry(
        skill_dir=skill_dir,
        state_path=tmp_path / "state.json",
        import_dirs=[],
    )

    result = registry.get("custom_skill")

    assert result["ok"] is True
    assert result["skill"]["instructions"] == "Follow local custom instructions."


def test_active_skill_prompt_block_includes_persisted_skill(tmp_path):
    skill_dir = tmp_path / "skills"
    state = tmp_path / "state.json"
    registry = SkillRegistry(skill_dir=skill_dir, state_path=state, import_dirs=[])
    registry.activate("desktop_operator")

    block = active_skill_prompt_block(skill_dir=skill_dir, state_path=state, import_dirs=[])

    assert "SKILL ACTIVA AL ARRANQUE" in block
    assert "desktop_operator" in block
    assert "file_organizer" in block


def test_skill_registry_imports_codex_style_skill_md(tmp_path):
    import_root = tmp_path / "codex-skills"
    skill_path = import_root / "agentics-aws"
    skill_path.mkdir(parents=True)
    (skill_path / "SKILL.md").write_text(
        """---
name: agentics-aws
description: Patron AWS agentico para Step Functions y Lambda.
---

# Agentics AWS

Usar validators paralelos, DynamoDB e idempotencia.
""",
        encoding="utf-8",
    )
    registry = SkillRegistry(
        skill_dir=tmp_path / "local",
        state_path=tmp_path / "state.json",
        import_dirs=[import_root],
    )

    result = registry.get("agentics-aws")

    assert result["ok"] is True
    assert result["skill"]["source_path"].endswith("SKILL.md")
    assert "ask_claude_deep" in result["skill"]["tools"]
    assert "validators paralelos" in result["skill"]["instructions"]
