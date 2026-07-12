from pathlib import Path

from jarvis_version import JARVIS_VERSION, JARVIS_VERSION_LABEL


def test_version_is_1_06():
    version_file = Path(__file__).resolve().parent.parent / "VERSION"

    assert version_file.read_text(encoding="utf-8").strip() == "1.06"
    assert JARVIS_VERSION == "1.06"
    assert JARVIS_VERSION_LABEL == "v1.06"
