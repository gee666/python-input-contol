from __future__ import annotations

import math
from dataclasses import dataclass, field

from python_input_control.backends import BackendExecutionContext
from python_input_control.backends.pyautogui_mouse_backend import PyAutoGuiMouseBackend
from python_input_control.models import BrowserContext, MouseButton, MouseClickCommand, MouseMoveCommand, ScreenPoint, ScrollCommand
from python_input_control.platform import ModifierKey, VirtualDesktopBounds
from python_input_control.randomness import SeededRandom


@dataclass
class FakePlatform:
    name: str = "linux"
    bounds: VirtualDesktopBounds | None = None

    def platform_name(self) -> str:
        return self.name

    def select_all_modifier(self) -> ModifierKey:
        return ModifierKey.CONTROL

    def virtual_desktop_bounds(self) -> VirtualDesktopBounds | None:
        return self.bounds


@dataclass
class FakeMouseController:
    current_position: ScreenPoint
    moves: list[ScreenPoint] = field(default_factory=list)
    button_events: list[tuple[str, str]] = field(default_factory=list)
    vertical_scrolls: list[int] = field(default_factory=list)
    horizontal_scrolls: list[int] = field(default_factory=list)

    def position(self) -> ScreenPoint:
        return self.current_position

    def move_to(self, point: ScreenPoint) -> None:
        self.moves.append(point)
        self.current_position = point

    def mouse_down(self, button: MouseButton) -> None:
        self.button_events.append(("down", button.value))

    def mouse_up(self, button: MouseButton) -> None:
        self.button_events.append(("up", button.value))

    def scroll(self, clicks: int) -> None:
        self.vertical_scrolls.append(clicks)

    def hscroll(self, clicks: int) -> None:
        self.horizontal_scrolls.append(clicks)


def _browser_context(device_pixel_ratio: float = 1.0) -> BrowserContext:
    return BrowserContext(
        screen_x=100.0,
        screen_y=50.0,
        outer_height=900.0,
        inner_height=820.0,
        outer_width=1280.0,
        inner_width=1280.0,
        device_pixel_ratio=device_pixel_ratio,
        scroll_x=0.0,
        scroll_y=0.0,
    )


def test_move_converts_retina_targets_back_to_pyautogui_space() -> None:
    controller = FakeMouseController(current_position=ScreenPoint(10.0, 20.0))
    sleeps: list[float] = []
    backend = PyAutoGuiMouseBackend(controller=controller, add_post_action_pause=False)
    runtime = BackendExecutionContext(
        platform=FakePlatform(name="macos", bounds=VirtualDesktopBounds(-500.0, -500.0, 2000.0, 2000.0)),
        rng=SeededRandom(123),
        sleep=sleeps.append,
    )
    command = MouseMoveCommand(id="move-1", context=_browser_context(device_pixel_ratio=2.0), x=0.0, y=0.0, duration_ms=240)

    backend.move(command, ScreenPoint(220.0, 140.0), runtime)

    assert controller.moves[-1] == ScreenPoint(110.0, 70.0)
    assert sleeps
    assert math.isclose(sum(sleeps), 0.24, rel_tol=1e-9, abs_tol=1e-9)


def test_click_uses_requested_hold_duration_and_button() -> None:
    controller = FakeMouseController(current_position=ScreenPoint(50.0, 50.0))
    sleeps: list[float] = []
    backend = PyAutoGuiMouseBackend(controller=controller, add_post_action_pause=True)
    runtime = BackendExecutionContext(
        platform=FakePlatform(),
        rng=SeededRandom(999),
        sleep=sleeps.append,
    )
    command = MouseClickCommand(
        id="click-1",
        context=_browser_context(),
        x=0.0,
        y=0.0,
        button=MouseButton.RIGHT,
        move_duration_ms=100,
        hold_ms=90,
    )

    backend.click(command, ScreenPoint(50.0, 50.0), runtime)

    assert controller.button_events == [("down", "right"), ("up", "right")]
    assert sleeps[0] == 0.09
    assert 0.05 <= sleeps[1] <= 0.15


def test_scroll_sends_vertical_and_horizontal_tick_totals() -> None:
    controller = FakeMouseController(current_position=ScreenPoint(20.0, 20.0))
    sleeps: list[float] = []
    backend = PyAutoGuiMouseBackend(controller=controller, add_post_action_pause=False)
    runtime = BackendExecutionContext(
        platform=FakePlatform(),
        rng=SeededRandom(321),
        sleep=sleeps.append,
    )
    command = ScrollCommand(
        id="scroll-1",
        context=_browser_context(),
        x=0.0,
        y=0.0,
        delta_x=300.0,
        delta_y=800.0,
        duration_ms=600,
    )

    backend.scroll(command, ScreenPoint(20.0, 20.0), runtime)

    assert sum(controller.vertical_scrolls) == -8
    assert sum(controller.horizontal_scrolls) == 3
    assert math.isclose(sum(sleeps), 0.6, rel_tol=1e-9, abs_tol=1e-9)
