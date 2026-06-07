from pathlib import Path

from jarvis_version import JARVIS_VERSION, JARVIS_VERSION_LABEL


def test_version_starts_at_1_00():
    version_file = Path(__file__).resolve().parent.parent / "VERSION"

    assert version_file.read_text(encoding="utf-8").strip() == "1.00"
    assert JARVIS_VERSION == "1.00"
    assert JARVIS_VERSION_LABEL == "v1.00"
