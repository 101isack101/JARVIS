import json

from skills.registry import BUILTIN_SKILLS, SkillRegistry, active_skill_prompt_block


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
    assert "ask_grok_deep" in result["skill"]["tools"]
    assert "validators paralelos" in result["skill"]["instructions"]


# --- Reporte de skills JSON saltadas en el error journal ---------------------

SOURCE = "skills.registry.load"


def _journal(tmp_path, monkeypatch):
    """Fija el journal en tmp_path y devuelve una funcion que lo lee como JSONL."""
    path = tmp_path / "errors.jsonl"
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL_PATH", str(path))
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL", "true")

    def read():
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row.get("source") == SOURCE]

    return read


def _registry(tmp_path, files):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    for name, content in files.items():
        (skill_dir / name).write_text(content, encoding="utf-8")
    return SkillRegistry(skill_dir=skill_dir, state_path=tmp_path / "state.json", import_dirs=[])


def _valid(name):
    return json.dumps({"name": name, "title": name, "description": "ok"})


def test_broken_json_is_journaled_and_valid_skill_loads(tmp_path, monkeypatch):
    read = _journal(tmp_path, monkeypatch)

    registry = _registry(tmp_path, {"broken.json": "{not json", "good.json": _valid("good_skill")})

    assert "good_skill" in {s["name"] for s in registry.list()}
    assert registry.get("good_skill")["ok"] is True
    rows = read()
    assert len(rows) == 1
    assert rows[0]["severity"] == "warning"
    assert rows[0]["context"]["file"] == "broken.json"
    assert rows[0]["error_type"] == "JSONDecodeError"


def test_json_without_name_is_journaled_and_skipped(tmp_path, monkeypatch):
    read = _journal(tmp_path, monkeypatch)

    registry = _registry(tmp_path, {"noname.json": json.dumps({"title": "x", "description": "y"})})

    assert {s["name"] for s in registry.list()} == {s.name for s in BUILTIN_SKILLS}
    rows = read()
    assert len(rows) == 1
    assert rows[0]["severity"] == "warning"
    assert rows[0]["context"]["file"] == "noname.json"
    assert "sin nombre" in rows[0]["message"]


def test_json_root_not_object_is_journaled_without_raising(tmp_path, monkeypatch):
    read = _journal(tmp_path, monkeypatch)

    registry = _registry(tmp_path, {"list.json": "[1, 2]"})

    assert registry.get("desktop_operator")["ok"] is True
    rows = read()
    assert len(rows) == 1
    assert rows[0]["context"]["file"] == "list.json"


def test_broken_file_sorted_first_does_not_stop_loading(tmp_path, monkeypatch):
    read = _journal(tmp_path, monkeypatch)

    registry = _registry(tmp_path, {"a_broken.json": "{", "z_valid.json": _valid("z_skill")})

    names = {s["name"] for s in registry.list()}
    assert "z_skill" in names
    assert {s.name for s in BUILTIN_SKILLS} <= names
    rows = read()
    assert len(rows) == 1
    assert rows[0]["context"]["file"] == "a_broken.json"


def test_valid_skills_directory_writes_no_journal_entries(tmp_path, monkeypatch):
    read = _journal(tmp_path, monkeypatch)

    _registry(tmp_path, {"one.json": _valid("one_skill"), "two.json": _valid("two_skill")})

    assert read() == []
