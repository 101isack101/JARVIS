from __future__ import annotations

from overlay import ui_widgets


class _FakeUser32:
    def __init__(self) -> None:
        self.regions: list[tuple[int, int, bool]] = []

    def GetParent(self, _winfo_id: int) -> int:
        return 0

    def SetWindowRgn(self, hwnd: int, region: int, redraw: bool) -> int:
        self.regions.append((hwnd, region, redraw))
        return 1


class _FakeGdi32:
    def __init__(self) -> None:
        self.deleted: list[int] = []

    def CreateRoundRectRgn(self, *_args) -> int:
        return 101

    def DeleteObject(self, region: int) -> None:
        self.deleted.append(region)


class _FakeDwmapi:
    def __init__(self) -> None:
        self.calls = 0

    def DwmSetWindowAttribute(self, *_args) -> int:
        self.calls += 1
        return 0


class _FakeWindll:
    def __init__(self) -> None:
        self.user32 = _FakeUser32()
        self.gdi32 = _FakeGdi32()
        self.dwmapi = _FakeDwmapi()


class _FakeWindow:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []
        self.cancelled: list[str] = []
        self.bindings: dict[str, object] = {}

    def after(self, delay_ms: int, callback) -> str:
        after_id = f"after-{len(self.after_calls) + 1}"
        self.after_calls.append((delay_ms, callback))
        return after_id

    def after_cancel(self, after_id: str) -> None:
        self.cancelled.append(after_id)

    def bind(self, event: str, callback, add: str | None = None) -> None:
        self.bindings[event] = callback

    def update_idletasks(self) -> None:
        raise AssertionError("rounding callback must not force Tk re-entry")

    def winfo_exists(self) -> int:
        return 1

    def winfo_width(self) -> int:
        return 780

    def winfo_height(self) -> int:
        return 700

    def winfo_id(self) -> int:
        return 202


def test_apply_window_rounding_avoids_update_idletasks(monkeypatch):
    windll = _FakeWindll()
    monkeypatch.setattr(ui_widgets.sys, "platform", "win32")
    monkeypatch.setattr(ui_widgets.ctypes, "windll", windll, raising=False)

    window = _FakeWindow()

    ui_widgets.apply_window_rounding(window)
    assert len(window.after_calls) == 1

    _delay_ms, callback = window.after_calls.pop(0)
    callback()

    assert windll.user32.regions == [(202, 101, True)]
    assert windll.dwmapi.calls == 1


def test_apply_window_rounding_debounces_configure(monkeypatch):
    windll = _FakeWindll()
    monkeypatch.setattr(ui_widgets.sys, "platform", "win32")
    monkeypatch.setattr(ui_widgets.ctypes, "windll", windll, raising=False)

    window = _FakeWindow()
    ui_widgets.apply_window_rounding(window)

    configure = window.bindings["<Configure>"]
    configure()
    configure()

    assert len(window.after_calls) == 1
