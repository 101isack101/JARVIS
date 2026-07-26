import re
from pathlib import Path

from jarvis_version import JARVIS_VERSION, JARVIS_VERSION_LABEL


def test_version_module_stays_in_sync_with_version_file():
    """VERSION es la unica fuente; el modulo no debe divergir ni inventar formato.

    Deliberadamente no fija el numero: eso obligaba a editar este test en cada
    release y solo comprobaba que VERSION se afirme a si mismo.
    """
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    raw = version_file.read_text(encoding="utf-8").strip()

    assert re.fullmatch(r"\d+\.\d{2}", raw), raw
    assert JARVIS_VERSION == raw
    assert JARVIS_VERSION_LABEL == f"v{raw}"


def test_changelog_documents_current_version():
    root = Path(__file__).resolve().parent.parent
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")

    assert f"## {JARVIS_VERSION} - " in changelog
