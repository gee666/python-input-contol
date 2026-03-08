from __future__ import annotations

import sys

import pytest

from python_input_control.models import BrowserContext, ModifierKey, ScreenPoint
from python_input_control.platform import (
    SystemPlatformAdapter,
    adapt_point_for_pyautogui,
    restore_point_from_pyautogui,
    translate_viewport_to_physical_screen,
)


@pytest.mark.parametrize(
    ("context", "viewport", "expected"),
    [
        (
            BrowserContext(
                screen_x=100.0,
                screen_y=50.0,
                outer_height=900.0,
                inner_height=820.0,
                outer_width=1280.0,
                inner_width=1280.0,
                device_pixel_ratio=1.0,
                scroll_x=0.0,
                scroll_y=0.0,
            ),
            (640.0, 480.0),
            ScreenPoint(740.0, 610.0),
        ),
        (
            BrowserContext(
                screen_x=100.0,
                screen_y=50.0,
                outer_height=900.0,
                inner_height=815.0,
                outer_width=1280.0,
                inner_width=1280.0,
                device_pixel_ratio=2.0,
                scroll_x=0.0,
                scroll_y=0.0,
            ),
            (10.0, 20.0),
            ScreenPoint(220.0, 310.0),
        ),
        (
            BrowserContext(
                screen_x=-1400.0,
                screen_y=20.0,
                outer_height=900.0,
                inner_height=820.0,
                outer_width=1280.0,
                inner_width=1200.0,
                device_pixel_ratio=1.0,
                scroll_x=0.0,
                scroll_y=0.0,
            ),
            (10.0, 15.0),
            ScreenPoint(-1350.0, 115.0),
        ),
        (
            BrowserContext(
                screen_x=30.0,
                screen_y=40.0,
                outer_height=800.0,
                inner_height=800.0,
                outer_width=1200.0,
                inner_width=1200.0,
                device_pixel_ratio=1.5,
                scroll_x=120.0,
                scroll_y=450.0,
            ),
            (100.0, 200.0),
            ScreenPoint(195.0, 360.0),
        ),
    ],
)
def test_translate_viewport_to_physical_screen_scales_browser_geometry_into_physical_pixels(
    context: BrowserContext,
    viewport: tuple[float, float],
    expected: ScreenPoint,
) -> None:
    assert translate_viewport_to_physical_screen(context, *viewport) == expected


def test_scroll_offsets_do_not_affect_viewport_relative_translation() -> None:
    base_context = BrowserContext(
        screen_x=10.0,
        screen_y=20.0,
        outer_height=900.0,
        inner_height=820.0,
        outer_width=1280.0,
        inner_width=1240.0,
        device_pixel_ratio=1.0,
        scroll_x=0.0,
        scroll_y=0.0,
    )
    scrolled_context = BrowserContext(
        screen_x=10.0,
        screen_y=20.0,
        outer_height=900.0,
        inner_height=820.0,
        outer_width=1280.0,
        inner_width=1240.0,
        device_pixel_ratio=1.0,
        scroll_x=500.0,
        scroll_y=800.0,
    )

    assert translate_viewport_to_physical_screen(base_context, 100.0, 200.0) == translate_viewport_to_physical_screen(
        scrolled_context,
        100.0,
        200.0,
    )


def test_pyautogui_coordinate_adaptation_round_trips_for_macos_retina() -> None:
    context = BrowserContext(
        screen_x=0.0,
        screen_y=0.0,
        outer_height=900.0,
        inner_height=820.0,
        outer_width=1280.0,
        inner_width=1280.0,
        device_pixel_ratio=2.0,
        scroll_x=0.0,
        scroll_y=0.0,
    )
    point = ScreenPoint(220.0, 140.0)

    adapted = adapt_point_for_pyautogui(point, context, "macos")

    assert adapted == ScreenPoint(110.0, 70.0)
    assert restore_point_from_pyautogui(adapted, context, "macos") == point
    assert adapt_point_for_pyautogui(point, context, "linux") == point


@pytest.mark.parametrize(
    ("platform_name", "expected_modifier"),
    [
        ("darwin", ModifierKey.COMMAND),
        ("linux", ModifierKey.CONTROL),
        ("win32", ModifierKey.CONTROL),
    ],
)
def test_system_platform_adapter_selects_expected_modifier(
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    expected_modifier: ModifierKey,
) -> None:
    adapter = SystemPlatformAdapter()

    monkeypatch.setattr(sys, "platform", platform_name)
    assert adapter.select_all_modifier() == expected_modifier
