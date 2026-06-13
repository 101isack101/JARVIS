from actions.desktop_icons import DesktopIconController, compute_grid_positions
from security.approvals import AutoApprovalBroker


def test_compute_grid_positions_left_layout():
    positions = compute_grid_positions(
        3,
        layout="left",
        screen_width=800,
        screen_height=240,
        start_x=10,
        start_y=10,
        spacing_x=100,
        spacing_y=80,
    )

    assert positions == [(10, 10), (10, 90), (110, 10)]


def test_compute_grid_positions_right_layout():
    positions = compute_grid_positions(
        2,
        layout="right",
        screen_width=800,
        screen_height=240,
        start_x=10,
        start_y=10,
        spacing_x=100,
        spacing_y=80,
    )

    assert positions == [(690, 10), (690, 90)]


def test_desktop_icon_status_reports_unavailable_without_hwnd():
    controller = DesktopIconController(hwnd_provider=lambda: 0)

    result = controller.status()

    assert result["ok"] is False
    assert result["available"] is False


def test_desktop_icon_arrange_requires_desktop_listview():
    controller = DesktopIconController(
        approval_broker=AutoApprovalBroker(approve=True),
        hwnd_provider=lambda: 0,
    )

    result = controller.arrange()

    assert result["ok"] is False
    assert result["executed"] is False
    assert "ListView" in result["error"]
