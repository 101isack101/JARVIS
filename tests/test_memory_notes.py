from memory.notes import list_notes
from memory.obsidian_vault import ObsidianVault


def test_list_notes_folder_searches_readable_vault_not_only_memory(tmp_path):
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Projects" / "Polymath.md").write_text(
        "# Polymath IDE\n\nProyecto fuera de Jarvis Memory.",
        encoding="utf-8",
    )

    vault = ObsidianVault(
        vault_path=tmp_path,
        memory_folder="Jarvis Memory",
        read_all=True,
    )

    notes = list_notes(vault, folder="Projects", limit=10)

    assert [(path.name, title) for path, title in notes] == [
        ("Polymath.md", "Polymath IDE")
    ]
