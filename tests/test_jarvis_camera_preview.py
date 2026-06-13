from __future__ import annotations


def test_camera_look_tool_result_updates_preview():
    from jarvis import Jarvis

    app = object.__new__(Jarvis)
    seen: list[bytes] = []
    app._tk = lambda fn, force=False: fn()
    app._show_camera_frame = lambda data: seen.append(data)

    app._preview_camera_tool_result(
        "camera_look",
        {"__attach_image": {"png_bytes": b"\xff\xd8\xff_jpeg"}},
    )

    assert seen == [b"\xff\xd8\xff_jpeg"]


def test_non_camera_tool_result_does_not_update_preview():
    from jarvis import Jarvis

    app = object.__new__(Jarvis)
    seen: list[bytes] = []
    app._tk = lambda fn, force=False: fn()
    app._show_camera_frame = lambda data: seen.append(data)

    app._preview_camera_tool_result(
        "screen_look",
        {"__attach_image": {"png_bytes": b"png"}},
    )

    assert seen == []
