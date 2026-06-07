from pathlib import Path

from jarvis_version import JARVIS_VERSION, JARVIS_VERSION_LABEL


def test_version_is_1_02():
    version_file = Path(__file__).resolve().parent.parent / "VERSION"

    assert version_file.read_text(encoding="utf-8").strip() == "1.02"
    assert JARVIS_VERSION == "1.02"
    assert JARVIS_VERSION_LABEL == "v1.02"
