from __future__ import annotations

import sys
from types import SimpleNamespace

from python_input_control.models import ScreenPoint
from python_input_control.platform import (
    MacOSDisplayGeometry,
    SystemPlatformAdapter,
    VirtualDesktopBounds,
    _linux_virtual_desktop_bounds,
    _macos_virtual_desktop_bounds,
    _windows_virtual_desktop_bounds,
    clamp_point_to_bounds,
)


class _FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


class _FakeUser32:
    def __init__(self, metrics: dict[int, int]) -> None:
        self._metrics = metrics

    def GetSystemMetrics(self, metric_id: int) -> int:
        return self._metrics[metric_id]


def test_linux_virtual_desktop_bounds_parses_active_monitor_output(monkeypatch) -> None:
    _linux_virtual_desktop_bounds.cache_clear()
    monkeypatch.setattr(
        "python_input_control.platform.subprocess.run",
        lambda *args, **kwargs: _FakeCompletedProcess(
            "Monitors: 2\n"
            " 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1\n"
            " 1: +HDMI-1 2560/597x1440/336-2560+0  HDMI-1\n"
        ),
    )

    bounds = _linux_virtual_desktop_bounds()

    assert bounds is not None
    assert bounds.left == -2560.0
    assert bounds.top == 0.0
    assert bounds.right == 1920.0
    assert bounds.bottom == 1440.0


def test_windows_virtual_desktop_bounds_reads_system_metrics(monkeypatch) -> None:
    _windows_virtual_desktop_bounds.cache_clear()
    fake_ctypes = SimpleNamespace(
        windll=SimpleNamespace(
            user32=_FakeUser32(
                {
                    76: -1920,
                    77: -100,
                    78: 3840,
                    79: 2260,
                }
            )
        )
    )
    monkeypatch.setattr("python_input_control.platform.ctypes", fake_ctypes)

    bounds = _windows_virtual_desktop_bounds()

    assert bounds is not None
    assert bounds.left == -1920.0
    assert bounds.top == -100.0
    assert bounds.right == 1920.0
    assert bounds.bottom == 2160.0


def test_macos_virtual_desktop_bounds_merges_scaled_display_geometries(monkeypatch) -> None:
    _macos_virtual_desktop_bounds.cache_clear()
    monkeypatch.setattr(
        "python_input_control.platform._macos_active_display_geometries_coregraphics",
        lambda: [
            MacOSDisplayGeometry(
                left=-1728.0,
                top=0.0,
                width=1728.0,
                height=1117.0,
                pixel_width=3456,
                pixel_height=2234,
            ),
            MacOSDisplayGeometry(
                left=0.0,
                top=-900.0,
                width=1440.0,
                height=900.0,
                pixel_width=1440,
                pixel_height=900,
            ),
        ],
    )
    monkeypatch.setattr("python_input_control.platform._macos_active_display_geometries_quartz", lambda: None)

    bounds = _macos_virtual_desktop_bounds()

    assert bounds is not None
    assert bounds.left == -3456.0
    assert bounds.top == -900.0
    assert bounds.right == 1440.0
    assert bounds.bottom == 2234.0


def test_macos_virtual_desktop_bounds_falls_back_to_quartz(monkeypatch) -> None:
    _macos_virtual_desktop_bounds.cache_clear()
    monkeypatch.setattr("python_input_control.platform._macos_active_display_geometries_coregraphics", lambda: None)
    monkeypatch.setattr(
        "python_input_control.platform._macos_active_display_geometries_quartz",
        lambda: [
            MacOSDisplayGeometry(
                left=-1280.0,
                top=0.0,
                width=1280.0,
                height=800.0,
                pixel_width=1280,
                pixel_height=800,
            ),
            MacOSDisplayGeometry(
                left=0.0,
                top=0.0,
                width=2560.0,
                height=1440.0,
                pixel_width=2560,
                pixel_height=1440,
            ),
        ],
    )

    bounds = _macos_virtual_desktop_bounds()

    assert bounds is not None
    assert bounds.left == -1280.0
    assert bounds.top == 0.0
    assert bounds.right == 2560.0
    assert bounds.bottom == 1440.0


def test_system_platform_adapter_uses_macos_bounds_source(monkeypatch) -> None:
    adapter = SystemPlatformAdapter()
    expected = VirtualDesktopBounds(left=-1440.0, top=0.0, right=2560.0, bottom=1600.0)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("python_input_control.platform._macos_virtual_desktop_bounds", lambda: expected)

    assert adapter.virtual_desktop_bounds() == expected


def test_clamp_point_to_bounds_limits_coordinates() -> None:
    point = ScreenPoint(x=250.0, y=-20.0)

    clamped = clamp_point_to_bounds(point, VirtualDesktopBounds(left=0.0, top=10.0, right=200.0, bottom=150.0))

    assert clamped == ScreenPoint(200.0, 10.0)


def test_linux_virtual_desktop_bounds_refresh_after_ttl(monkeypatch) -> None:
    """Regression for Medium #7: cached bounds must expire after the TTL.

    Simulate two consecutive calls with different underlying geometries and
    assert that the second call (after the TTL elapses) returns the new
    geometry rather than the stale cached one.
    """
    import python_input_control.platform as platform_module

    platform_module._linux_virtual_desktop_bounds.cache_clear()
    # Shrink the TTL so the test runs quickly.
    monkeypatch.setattr(platform_module, "_VIRTUAL_DESKTOP_BOUNDS_TTL_SECONDS", 0.05)
    # Rebuild the cached callable so it picks up the tiny TTL.
    monkeypatch.setattr(
        platform_module,
        "_linux_virtual_desktop_bounds",
        platform_module._TtlCache(
            platform_module._linux_virtual_desktop_bounds.__wrapped__,
            platform_module._VIRTUAL_DESKTOP_BOUNDS_TTL_SECONDS,
        ),
    )

    call_count = {"n": 0}
    outputs = [
        "Monitors: 1\n 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1\n",
        "Monitors: 1\n 0: +*eDP-1 2560/344x1440/194+0+0  eDP-1\n",
    ]

    def _fake_run(*args, **kwargs):
        index = min(call_count["n"], len(outputs) - 1)
        call_count["n"] += 1
        return _FakeCompletedProcess(outputs[index])

    monkeypatch.setattr(platform_module.subprocess, "run", _fake_run)

    first = platform_module._linux_virtual_desktop_bounds()
    assert first is not None
    assert first.right == 1920.0

    # Within the TTL: cached, no extra subprocess call.
    again = platform_module._linux_virtual_desktop_bounds()
    assert again == first
    assert call_count["n"] == 1

    # After the TTL: the cached value expires and we re-query, picking up
    # the new geometry.
    import time as _time
    _time.sleep(0.08)
    refreshed = platform_module._linux_virtual_desktop_bounds()
    assert refreshed is not None
    assert refreshed.right == 2560.0
    assert call_count["n"] >= 2


def test_linux_virtual_desktop_bounds_none_result_is_not_cached(monkeypatch) -> None:
    """``None`` (detection failure) must be retried on the very next call."""
    import python_input_control.platform as platform_module

    platform_module._linux_virtual_desktop_bounds.cache_clear()

    call_count = {"n": 0}

    def _fake_run(*args, **kwargs):
        call_count["n"] += 1
        # Return invalid output the first time, valid the second.
        if call_count["n"] == 1:
            return _FakeCompletedProcess("", returncode=1)
        return _FakeCompletedProcess(
            "Monitors: 1\n 0: +*eDP-1 1920/344x1080/194+0+0  eDP-1\n"
        )

    monkeypatch.setattr(platform_module.subprocess, "run", _fake_run)

    first = platform_module._linux_virtual_desktop_bounds()
    assert first is None

    second = platform_module._linux_virtual_desktop_bounds()
    assert second is not None
    assert second.right == 1920.0
    assert call_count["n"] == 2
