from __future__ import annotations

import math

from .randomness import RandomSource, bounded_gauss


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def ease_in_out(value: float) -> float:
    bounded = clamp(value, 0.0, 1.0)
    return bounded * bounded * (3.0 - 2.0 * bounded)


def estimate_mouse_duration_ms(distance_px: float, minimum_ms: int = 150, maximum_ms: int = 1200) -> int:
    distance = max(0.0, distance_px)
    estimate = 120.0 + 180.0 * math.log2(1.0 + distance / 100.0)
    return int(round(clamp(estimate, minimum_ms, maximum_ms)))


def estimate_scroll_duration_ms(total_delta_css: float, minimum_ms: int = 150, maximum_ms: int = 1200) -> int:
    magnitude = max(0.0, abs(total_delta_css))
    estimate = 100.0 + 160.0 * math.log2(1.0 + magnitude / 120.0)
    return int(round(clamp(estimate, minimum_ms, maximum_ms)))


def wpm_to_inter_key_delay_ms(wpm: float) -> float:
    if wpm <= 0:
        raise ValueError("wpm must be greater than zero")
    return 12000.0 / wpm


def jittered_delay_ms(base_ms: float, rng: RandomSource, jitter_ratio: float = 0.25, minimum_ms: float = 0.0) -> float:
    sigma = abs(base_ms) * abs(jitter_ratio)
    return bounded_gauss(rng, base_ms, sigma, minimum_ms, float("inf"))
