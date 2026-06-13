from actions.file_organizer import FileOrganizer
from security.approvals import AutoApprovalBroker


def test_file_organizer_plan_skips_secret_paths(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "photo.png").write_text("image", encoding="utf-8")
    (source / ".env").write_text("TOKEN=abc", encoding="utf-8")
    organizer = FileOrganizer(
        allowed_roots=[source],
        state_dir=tmp_path / "state",
        mode="dev",
        strict_root_validation=False,
    )

    result = organizer.plan(source_root=str(source))

    assert result["ok"] is True
    assert result["move_count"] == 1
    assert result["preview"][0]["category"] == "Images"
    assert ".env" not in result["preview"][0]["source"]


def test_file_organizer_blocks_roots_outside_allowlist(tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    organizer = FileOrganizer(
        allowed_roots=[allowed],
        state_dir=tmp_path / "state",
        mode="dev",
        strict_root_validation=False,
    )

    result = organizer.scan(root=str(outside))

    assert result["ok"] is False
    assert result["allowed"] is False
    assert "fuera de roots permitidos" in result["error"]


def test_file_organizer_apply_requires_hitl(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "doc.pdf").write_text("pdf", encoding="utf-8")
    organizer = FileOrganizer(
        allowed_roots=[source],
        state_dir=tmp_path / "state",
        mode="prod",
        strict_root_validation=False,
    )
    plan = organizer.plan(source_root=str(source))

    result = organizer.apply(plan["plan_id"])

    assert result["allowed"] is False
    assert result["executed"] is False


def test_file_organizer_apply_dev_is_dry_run_when_approved(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    item = source / "doc.pdf"
    item.write_text("pdf", encoding="utf-8")
    broker = AutoApprovalBroker(approve=True)
    organizer = FileOrganizer(
        allowed_roots=[source],
        state_dir=tmp_path / "state",
        mode="dev",
        approval_broker=broker,
        strict_root_validation=False,
    )
    plan = organizer.plan(source_root=str(source))

    result = organizer.apply(plan["plan_id"])

    assert result["ok"] is False
    assert result["executed"] is False
    assert result["would_move"] == 1
    assert result["requires"] == "JARVIS_ORGANIZER_MODE=prod"
    assert item.exists()
    assert broker.requests[0][0] == "file_move"


def test_file_organizer_preview_creates_visible_plan_without_moving(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    item = source / "photo.png"
    item.write_text("new", encoding="utf-8")
    organizer = FileOrganizer(
        allowed_roots=[source],
        state_dir=tmp_path / "state",
        mode="dev",
        approval_broker=AutoApprovalBroker(approve=True),
        strict_root_validation=False,
    )
    plan = organizer.plan(source_root=str(source))

    result = organizer.preview(plan["plan_id"])

    assert result["ok"] is True
    assert result["executed"] is True
    assert item.exists()
    assert (source / "_Jarvis_Organized_PREVIEW" / "Images").is_dir()
    assert (source / "_Jarvis_Organized_PREVIEW" / "MOVE_PLAN.md").is_file()


def test_file_organizer_shortcuts_are_program_icons(tmp_path):
    source = tmp_path / "Desktop"
    source.mkdir()
    shortcut = source / "Chrome.lnk"
    shortcut.write_text("shortcut", encoding="utf-8")
    organizer = FileOrganizer(
        allowed_roots=[source],
        state_dir=tmp_path / "state",
        mode="dev",
        strict_root_validation=False,
    )

    result = organizer.plan(source_root=str(source))

    assert result["ok"] is True
    assert result["preview"][0]["category"] == "Shortcuts"
    assert result["preview"][0]["type"] == "file"


def test_file_organizer_include_folders_moves_desktop_folders(tmp_path):
    source = tmp_path / "Desktop"
    source.mkdir()
    folder = source / "Project"
    folder.mkdir()
    (folder / "README.md").write_text("project", encoding="utf-8")
    organizer = FileOrganizer(
        allowed_roots=[source],
        state_dir=tmp_path / "state",
        mode="prod",
        approval_broker=AutoApprovalBroker(approve=True),
        strict_root_validation=False,
    )
    plan = organizer.plan(source_root=str(source), include_folders=True)

    result = organizer.apply(plan["plan_id"])

    assert result["ok"] is True
    assert result["applied_count"] == 1
    assert not folder.exists()
    assert (source / "_Jarvis_Organized" / "Folders" / "Project" / "README.md").is_file()


def test_file_organizer_include_folders_skips_folders_with_secrets(tmp_path):
    source = tmp_path / "Desktop"
    source.mkdir()
    folder = source / "UnsafeProject"
    folder.mkdir()
    (folder / ".env").write_text("TOKEN=abc", encoding="utf-8")
    organizer = FileOrganizer(
        allowed_roots=[source],
        state_dir=tmp_path / "state",
        mode="dev",
        strict_root_validation=False,
    )

    result = organizer.plan(source_root=str(source), include_folders=True)

    assert result["ok"] is True
    assert result["move_count"] == 0
    assert "sensible" in result["warnings"][0] or "internos" in result["warnings"][0]
    assert folder.exists()


def test_file_organizer_apply_prod_moves_without_overwrite(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    target = source / "_Jarvis_Organized" / "Images"
    target.mkdir(parents=True)
    existing = target / "photo.png"
    existing.write_text("existing", encoding="utf-8")
    item = source / "photo.png"
    item.write_text("new", encoding="utf-8")
    organizer = FileOrganizer(
        allowed_roots=[source],
        state_dir=tmp_path / "state",
        mode="prod",
        approval_broker=AutoApprovalBroker(approve=True),
        strict_root_validation=False,
    )
    plan = organizer.plan(source_root=str(source))

    result = organizer.apply(plan["plan_id"])

    assert result["ok"] is True
    assert result["executed"] is True
    assert result["applied_count"] == 1
    assert existing.read_text(encoding="utf-8") == "existing"
    assert (target / "photo (2).png").read_text(encoding="utf-8") == "new"
    assert not item.exists()
