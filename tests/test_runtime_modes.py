from runtime_modes import ModeManager


def test_mode_manager_accepts_known_modes():
    modes = ModeManager()

    result = modes.set_mode("coding")

    assert result["changed"] is True
    assert modes.get_mode()["mode"] == "coding"


def test_mode_manager_rejects_unknown_modes():
    modes = ModeManager()

    result = modes.set_mode("chaos")

    assert result["changed"] is False
    assert modes.get_mode()["mode"] == "general"
