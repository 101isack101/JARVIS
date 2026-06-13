"""Windows desktop icon positioning for Jarvis.

This controls the visual positions of icons in the Windows desktop ListView.
It does not delete files, move installations, or modify file contents.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass, field
from typing import Callable


LVM_FIRST = 0x1000
LVM_GETITEMCOUNT = LVM_FIRST + 4
LVM_SETITEMPOSITION = LVM_FIRST + 15


def _make_lparam(x: int, y: int) -> int:
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def compute_grid_positions(
    count: int,
    *,
    layout: str = "left",
    screen_width: int = 1920,
    screen_height: int = 1080,
    start_x: int = 20,
    start_y: int = 20,
    spacing_x: int = 120,
    spacing_y: int = 95,
    columns: int | None = None,
) -> list[tuple[int, int]]:
    """Return icon positions for a simple deterministic desktop layout."""
    count = max(0, int(count or 0))
    spacing_x = max(64, int(spacing_x or 120))
    spacing_y = max(64, int(spacing_y or 95))
    start_x = max(0, int(start_x or 0))
    start_y = max(0, int(start_y or 0))
    rows = max(1, (screen_height - start_y) // spacing_y)
    cols = max(1, int(columns or ((count + rows - 1) // rows or 1)))
    normalized = (layout or "left").strip().lower()

    positions: list[tuple[int, int]] = []
    for i in range(count):
        col = i // rows
        row = i % rows
        if normalized in {"right", "grid_right"}:
            x = max(0, screen_width - start_x - spacing_x * (col + 1))
        elif normalized in {"top", "rows_top"}:
            row = i // cols
            col = i % cols
            x = start_x + col * spacing_x
        else:
            x = start_x + col * spacing_x
        y = start_y + row * spacing_y
        positions.append((x, y))
    return positions


def _find_desktop_listview() -> int:
    if os.name != "nt":
        return 0

    user32 = ctypes.windll.user32
    progman = user32.FindWindowW("Progman", None)
    defview = user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)
    if defview:
        listview = user32.FindWindowExW(defview, 0, "SysListView32", None)
        if listview:
            return int(listview)

    found = ctypes.c_void_p(0)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_windows(hwnd, _):
        nonlocal found
        shell = user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
        if shell:
            listview = user32.FindWindowExW(shell, 0, "SysListView32", None)
            if listview:
                found = ctypes.c_void_p(listview)
                return False
        return True

    user32.EnumWindows(enum_windows, 0)
    return int(found.value or 0)


@dataclass
class DesktopIconController:
    approval_broker: object | None = None
    hwnd_provider: Callable[[], int] = _find_desktop_listview

    def _hwnd(self) -> int:
        return int(self.hwnd_provider() or 0)

    def _item_count(self, hwnd: int) -> int:
        return int(ctypes.windll.user32.SendMessageW(hwnd, LVM_GETITEMCOUNT, 0, 0))

    def status(self) -> dict:
        hwnd = self._hwnd()
        if not hwnd:
            return {
                "ok": False,
                "available": False,
                "error": "no encontre el ListView del escritorio de Windows",
            }
        return {
            "ok": True,
            "available": True,
            "hwnd": hwnd,
            "icon_count": self._item_count(hwnd),
            "actions": ["status", "arrange"],
            "note": "Reposiciona iconos visuales; no mueve archivos ni programas instalados.",
        }

    def _request_approval(self, action: str, details: str) -> bool:
        if self.approval_broker is None:
            return False
        return bool(self.approval_broker.request(
            risk="desktop_icon_position",
            title=f"Jarvis quiere mover iconos del escritorio ({action})",
            details=details,
            timeout_s=30.0,
        ))

    def arrange(
        self,
        *,
        layout: str = "left",
        limit: int | None = None,
        start_x: int = 20,
        start_y: int = 20,
        spacing_x: int = 120,
        spacing_y: int = 95,
        columns: int | None = None,
    ) -> dict:
        hwnd = self._hwnd()
        if not hwnd:
            return {
                "ok": False,
                "available": False,
                "executed": False,
                "error": "no encontre el ListView del escritorio de Windows",
            }
        count = self._item_count(hwnd)
        move_count = min(count, max(0, int(limit))) if limit is not None else count
        if move_count <= 0:
            return {"ok": True, "available": True, "executed": False, "message": "no hay iconos para mover"}
        if not self._request_approval(
            "arrange",
            (
                f"Layout: {layout}\n"
                f"Iconos a reposicionar: {move_count}\n"
                f"Inicio: ({start_x}, {start_y})\n"
                f"Espaciado: ({spacing_x}, {spacing_y})"
            ),
        ):
            return {
                "ok": False,
                "available": True,
                "executed": False,
                "error": "movimiento de iconos rechazado o sin aprobacion HITL",
            }

        user32 = ctypes.windll.user32
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
        positions = compute_grid_positions(
            move_count,
            layout=layout,
            screen_width=width,
            screen_height=height,
            start_x=start_x,
            start_y=start_y,
            spacing_x=spacing_x,
            spacing_y=spacing_y,
            columns=columns,
        )
        for i, (x, y) in enumerate(positions):
            user32.SendMessageW(hwnd, LVM_SETITEMPOSITION, i, _make_lparam(x, y))
        user32.InvalidateRect(hwnd, None, True)
        user32.UpdateWindow(hwnd)
        return {
            "ok": True,
            "available": True,
            "executed": True,
            "moved_count": len(positions),
            "layout": layout,
            "note": (
                "Iconos reposicionados visualmente. Si Windows tiene auto-organizar "
                "activado, puede recolocarlos otra vez."
            ),
        }
