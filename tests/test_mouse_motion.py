from __future__ import annotations

import math

from python_input_control.models import ScreenPoint
from python_input_control.mouse_motion import build_mouse_path, build_scroll_steps, generate_bezier_control_points
from python_input_control.platform import VirtualDesktopBounds
from python_input_control.randomness import SeededRandom


def test_generate_bezier_control_points_stay_within_expected_offset_band() -> None:
    start = ScreenPoint(10.0, 20.0)
    end = ScreenPoint(210.0, 20.0)
    rng = SeededRandom(123)

    control_1, control_2 = generate_bezier_control_points(start, end, rng)
    distance = math.hypot(end.x - start.x, end.y - start.y)

    offset_1 = abs(control_1.y - start.y)
    offset_2 = abs(control_2.y - start.y)

    assert 0.15 * distance <= offset_1 <= 0.4 * distance
    assert 0.15 * distance <= offset_2 <= 0.4 * distance
    assert start.x < control_1.x < control_2.x < end.x


def test_build_mouse_path_preserves_endpoints_and_clamps_to_bounds() -> None:
    bounds = VirtualDesktopBounds(left=0.0, top=0.0, right=100.0, bottom=100.0)
    start = ScreenPoint(10.0, 10.0)
    end = ScreenPoint(90.0, 90.0)

    path = build_mouse_path(start, end, SeededRandom(456), bounds=bounds, sample_count=32)

    assert path[0] == start
    assert path[-1] == end
    assert len(path) == 33
    assert all(bounds.contains(point) for point in path)


def test_build_scroll_steps_preserves_tick_totals_and_delay_budget() -> None:
    steps = build_scroll_steps(300.0, 800.0, SeededRandom(789), duration_ms=600)

    assert sum(step.horizontal_ticks for step in steps) == 3
    assert sum(step.vertical_ticks for step in steps) == -8
    assert len(steps) == 8
    assert math.isclose(sum(step.delay_s for step in steps), 0.6, rel_tol=1e-9, abs_tol=1e-9)
    assert steps[-1].delay_s == 0.0
