from __future__ import annotations

from python_input_control.timing import ease_in_out


def test_ease_in_out_is_bounded_and_monotonic() -> None:
    samples = [ease_in_out(index / 100.0) for index in range(101)]

    assert samples[0] == 0.0
    assert samples[-1] == 1.0
    assert all(0.0 <= sample <= 1.0 for sample in samples)
    assert all(left <= right for left, right in zip(samples, samples[1:], strict=False))
