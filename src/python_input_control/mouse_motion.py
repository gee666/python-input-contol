from __future__ import annotations

import math
from dataclasses import dataclass

from .models import ScreenPoint
from .platform import VirtualDesktopBounds, clamp_point_to_bounds
from .randomness import RandomSource, bounded_gauss
from .timing import clamp, ease_in_out, estimate_scroll_duration_ms


@dataclass(frozen=True)
class ScrollStep:
    horizontal_ticks: int
    vertical_ticks: int
    delay_s: float = 0.0


def distance_between_points(start: ScreenPoint, end: ScreenPoint) -> float:
    return math.hypot(end.x - start.x, end.y - start.y)


def cubic_bezier_point(
    start: ScreenPoint,
    control_1: ScreenPoint,
    control_2: ScreenPoint,
    end: ScreenPoint,
    t: float,
) -> ScreenPoint:
    bounded_t = clamp(t, 0.0, 1.0)
    inverse_t = 1.0 - bounded_t
    x = (
        inverse_t**3 * start.x
        + 3.0 * inverse_t * inverse_t * bounded_t * control_1.x
        + 3.0 * inverse_t * bounded_t * bounded_t * control_2.x
        + bounded_t**3 * end.x
    )
    y = (
        inverse_t**3 * start.y
        + 3.0 * inverse_t * inverse_t * bounded_t * control_1.y
        + 3.0 * inverse_t * bounded_t * bounded_t * control_2.y
        + bounded_t**3 * end.y
    )
    return ScreenPoint(x=x, y=y)


def generate_bezier_control_points(
    start: ScreenPoint,
    end: ScreenPoint,
    rng: RandomSource,
    minimum_offset_ratio: float = 0.15,
    maximum_offset_ratio: float = 0.4,
) -> tuple[ScreenPoint, ScreenPoint]:
    dx = end.x - start.x
    dy = end.y - start.y
    distance = max(distance_between_points(start, end), 1.0)

    unit_x = dx / distance
    unit_y = dy / distance
    perpendicular_x = -unit_y
    perpendicular_y = unit_x

    first_fraction = rng.uniform(0.2, 0.35)
    second_fraction = rng.uniform(0.65, 0.8)

    first_offset = rng.uniform(minimum_offset_ratio, maximum_offset_ratio) * distance * _random_sign(rng)
    second_offset = rng.uniform(minimum_offset_ratio, maximum_offset_ratio) * distance * _random_sign(rng)

    control_1 = ScreenPoint(
        x=start.x + dx * first_fraction + perpendicular_x * first_offset,
        y=start.y + dy * first_fraction + perpendicular_y * first_offset,
    )
    control_2 = ScreenPoint(
        x=start.x + dx * second_fraction + perpendicular_x * second_offset,
        y=start.y + dy * second_fraction + perpendicular_y * second_offset,
    )
    return control_1, control_2


def build_mouse_path(
    start: ScreenPoint,
    end: ScreenPoint,
    rng: RandomSource,
    *,
    bounds: VirtualDesktopBounds | None = None,
    sample_count: int | None = None,
    jitter_sigma_px: float = 0.85,
    jitter_limit_px: float = 2.0,
) -> list[ScreenPoint]:
    distance = distance_between_points(start, end)
    if distance < 1e-6:
        return [start]

    control_1, control_2 = generate_bezier_control_points(start, end, rng)
    total_samples = sample_count if sample_count is not None else _estimate_sample_count(distance)

    path: list[ScreenPoint] = []
    for index in range(total_samples + 1):
        curve_position = ease_in_out(index / total_samples)
        point = cubic_bezier_point(start, control_1, control_2, end, curve_position)
        if 0 < index < total_samples:
            point = ScreenPoint(
                x=point.x + bounded_gauss(rng, 0.0, jitter_sigma_px, -jitter_limit_px, jitter_limit_px),
                y=point.y + bounded_gauss(rng, 0.0, jitter_sigma_px, -jitter_limit_px, jitter_limit_px),
            )
        if bounds is not None:
            point = clamp_point_to_bounds(point, bounds)
        path.append(point)

    path[0] = start
    path[-1] = end
    return path


def build_scroll_steps(
    delta_x_css: float,
    delta_y_css: float,
    rng: RandomSource,
    *,
    duration_ms: int | None = None,
    css_pixels_per_tick: float = 100.0,
) -> list[ScrollStep]:
    horizontal_ticks = _css_delta_to_horizontal_ticks(delta_x_css, css_pixels_per_tick)
    vertical_ticks = _css_delta_to_vertical_ticks(delta_y_css, css_pixels_per_tick)
    total_tick_magnitude = max(abs(horizontal_ticks), abs(vertical_ticks))
    if total_tick_magnitude == 0:
        return []

    step_count = _estimate_scroll_step_count(total_tick_magnitude)
    resolved_duration_ms = duration_ms or estimate_scroll_duration_ms(max(abs(delta_x_css), abs(delta_y_css)))

    horizontal_parts = _allocate_integer_total(horizontal_ticks, step_count, rng)
    vertical_parts = _allocate_integer_total(vertical_ticks, step_count, rng)
    delay_parts = _allocate_delay_budget(max(0.0, resolved_duration_ms / 1000.0), step_count, rng)

    return [
        ScrollStep(
            horizontal_ticks=horizontal_parts[index],
            vertical_ticks=vertical_parts[index],
            delay_s=delay_parts[index],
        )
        for index in range(step_count)
    ]


def default_click_hold_ms(rng: RandomSource, minimum_ms: int = 60, maximum_ms: int = 120) -> int:
    return rng.randint(minimum_ms, maximum_ms)


def default_double_click_interval_ms(rng: RandomSource, minimum_ms: int = 80, maximum_ms: int = 180) -> int:
    return rng.randint(minimum_ms, maximum_ms)


def default_post_action_pause_s(rng: RandomSource, minimum_ms: int = 50, maximum_ms: int = 150) -> float:
    return rng.uniform(minimum_ms / 1000.0, maximum_ms / 1000.0)


def _estimate_sample_count(distance_px: float) -> int:
    return int(clamp(round(distance_px / 9.0), 24.0, 60.0))


def _estimate_scroll_step_count(total_tick_magnitude: int) -> int:
    if total_tick_magnitude < 8:
        return max(1, total_tick_magnitude)
    return int(clamp(float(total_tick_magnitude), 8.0, 15.0))


def _css_delta_to_horizontal_ticks(delta_x_css: float, css_pixels_per_tick: float) -> int:
    return _delta_css_to_ticks(delta_x_css, css_pixels_per_tick)


def _css_delta_to_vertical_ticks(delta_y_css: float, css_pixels_per_tick: float) -> int:
    return -_delta_css_to_ticks(delta_y_css, css_pixels_per_tick)


def _delta_css_to_ticks(delta_css: float, css_pixels_per_tick: float) -> int:
    if css_pixels_per_tick <= 0:
        raise ValueError("css_pixels_per_tick must be greater than zero")
    if abs(delta_css) < 1e-6:
        return 0
    return int(round(delta_css / css_pixels_per_tick))


def _allocate_integer_total(total: int, step_count: int, rng: RandomSource) -> list[int]:
    if step_count <= 0:
        return []
    magnitude = abs(total)
    if magnitude == 0:
        return [0] * step_count

    sign = 1 if total >= 0 else -1
    weights = _bell_curve_weights(step_count, rng)
    raw_values = [magnitude * weight / sum(weights) for weight in weights]
    floors = [int(math.floor(value)) for value in raw_values]
    remainders = [raw_values[index] - floors[index] for index in range(step_count)]

    remaining = magnitude - sum(floors)
    result = floors[:]
    ranked_indexes = sorted(
        range(step_count),
        key=lambda index: (remainders[index], rng.random()),
        reverse=True,
    )
    for index in ranked_indexes[:remaining]:
        result[index] += 1

    return [sign * value for value in result]


def _allocate_delay_budget(total_delay_s: float, step_count: int, rng: RandomSource) -> list[float]:
    if step_count <= 0:
        return []
    if step_count == 1 or total_delay_s <= 0:
        return [0.0] * step_count

    edge_weights = _edge_heavy_weights(step_count - 1, rng)
    weight_sum = sum(edge_weights)
    delays = [0.0] * step_count
    for index, weight in enumerate(edge_weights):
        delays[index] = total_delay_s * weight / weight_sum
    return delays


def _bell_curve_weights(count: int, rng: RandomSource) -> list[float]:
    weights: list[float] = []
    for index in range(count):
        position = (index + 0.5) / count
        baseline = 0.55 + math.sin(math.pi * position)
        weights.append(max(0.05, baseline * rng.uniform(0.8, 1.2)))
    return weights


def _edge_heavy_weights(count: int, rng: RandomSource) -> list[float]:
    weights: list[float] = []
    for index in range(count):
        position = (index + 1) / (count + 1)
        edge_bias = 0.75 + abs(2.0 * position - 1.0)
        weights.append(max(0.05, edge_bias * rng.uniform(0.9, 1.1)))
    return weights


def _random_sign(rng: RandomSource) -> float:
    return -1.0 if rng.random() < 0.5 else 1.0
