from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..errors import BackendUnavailableError, CommandCancelledError
from ..models import BrowserContext, MouseButton, MouseClickCommand, MouseMoveCommand, ScreenPoint, ScrollCommand
from ..mouse_motion import (
    build_mouse_path,
    build_scroll_steps,
    default_click_hold_ms,
    default_double_click_interval_ms,
    default_post_action_pause_s,
    distance_between_points,
)
from ..platform import adapt_point_for_pyautogui, restore_point_from_pyautogui
from ..timing import estimate_mouse_duration_ms
from . import BackendExecutionContext
from .mouse_backend import MouseBackend


class MouseController(Protocol):
    def position(self) -> ScreenPoint: ...
    def move_to(self, point: ScreenPoint) -> None: ...
    def mouse_down(self, button: MouseButton) -> None: ...
    def mouse_up(self, button: MouseButton) -> None: ...
    def scroll(self, clicks: int) -> None: ...
    def hscroll(self, clicks: int) -> None: ...


class UnavailablePyAutoGuiMouseBackend(MouseBackend):
    def __init__(self, message: str) -> None:
        self.message = message

    def move(self, command: MouseMoveCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError(self.message, command.id)

    def click(self, command: MouseClickCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError(self.message, command.id)

    def scroll(self, command: ScrollCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError(self.message, command.id)


@dataclass
class PyAutoGuiModuleController:
    module: Any

    def __post_init__(self) -> None:
        if hasattr(self.module, "PAUSE"):
            self.module.PAUSE = 0

    def position(self) -> ScreenPoint:
        position = self.module.position()
        return ScreenPoint(x=float(position.x), y=float(position.y))

    def move_to(self, point: ScreenPoint) -> None:
        self.module.moveTo(round(point.x), round(point.y))

    def mouse_down(self, button: MouseButton) -> None:
        self.module.mouseDown(button=button.value)

    def mouse_up(self, button: MouseButton) -> None:
        self.module.mouseUp(button=button.value)

    def scroll(self, clicks: int) -> None:
        self.module.scroll(clicks)

    def hscroll(self, clicks: int) -> None:
        if clicks == 0:
            return
        if not hasattr(self.module, "hscroll"):
            raise NotImplementedError("Horizontal scrolling is not supported by the active mouse backend")
        self.module.hscroll(clicks)


@dataclass
class PyAutoGuiMouseBackend(MouseBackend):
    controller: MouseController
    add_post_action_pause: bool = True

    def move(self, command: MouseMoveCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        self._move_cursor(command.context, target, command.duration_ms, context)
        self._post_action_pause(context)

    def click(self, command: MouseClickCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        self._move_cursor(command.context, target, command.move_duration_ms, context)
        hold_ms = command.hold_ms if command.hold_ms is not None else default_click_hold_ms(context.rng)
        interval_ms = command.interval_ms if command.interval_ms is not None else default_double_click_interval_ms(context.rng)

        for index in range(command.count):
            self._button_press(command.button, hold_ms, context)
            if index < command.count - 1:
                _sleep(context, interval_ms / 1000.0)
        self._post_action_pause(context)

    def scroll(self, command: ScrollCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        self._move_cursor(command.context, target, None, context)
        steps = build_scroll_steps(command.delta_x, command.delta_y, context.rng, duration_ms=command.duration_ms)
        for index, step in enumerate(steps):
            if step.vertical_ticks:
                self.controller.scroll(step.vertical_ticks)
            if step.horizontal_ticks:
                self.controller.hscroll(step.horizontal_ticks)
            if index < len(steps) - 1 and step.delay_s > 0:
                _sleep(context, step.delay_s)
        self._post_action_pause(context)

    def _move_cursor(
        self,
        browser_context: BrowserContext,
        target: ScreenPoint,
        requested_duration_ms: int | None,
        context: BackendExecutionContext,
    ) -> None:
        platform_name = context.platform.platform_name()
        current_backend_position = self.controller.position()
        start = restore_point_from_pyautogui(current_backend_position, browser_context, platform_name)
        path = build_mouse_path(
            start,
            target,
            context.rng,
            bounds=context.platform.virtual_desktop_bounds(),
        )
        backend_path = self._adapt_path_for_backend(path, browser_context, platform_name)
        if len(backend_path) <= 1:
            return

        duration_ms = requested_duration_ms
        if duration_ms is None:
            duration_ms = estimate_mouse_duration_ms(distance_between_points(start, target))
        sleep_interval_s = max(0.0, duration_ms / 1000.0) / max(1, len(backend_path) - 1)

        for point in backend_path[1:]:
            self.controller.move_to(point)
            if sleep_interval_s > 0:
                _sleep(context, sleep_interval_s)

    def _adapt_path_for_backend(self, path: list[ScreenPoint], browser_context: BrowserContext, platform_name: str) -> list[ScreenPoint]:
        backend_path: list[ScreenPoint] = []
        for point in path:
            adapted = adapt_point_for_pyautogui(point, browser_context, platform_name)
            rounded = ScreenPoint(x=round(adapted.x), y=round(adapted.y))
            if backend_path and rounded == backend_path[-1]:
                continue
            backend_path.append(rounded)
        return backend_path

    def _button_press(self, button: MouseButton, hold_ms: int, context: BackendExecutionContext) -> None:
        self.controller.mouse_down(button)
        _sleep(context, hold_ms / 1000.0)
        self.controller.mouse_up(button)

    def _post_action_pause(self, context: BackendExecutionContext) -> None:
        if not self.add_post_action_pause:
            return
        _sleep(context, default_post_action_pause_s(context.rng))


def _sleep(context: BackendExecutionContext, seconds: float) -> None:
    """Sleep for *seconds*, waking early if cancel_event is set."""
    ev = context.cancel_event
    if ev is not None:
        if ev.wait(max(0.0, seconds)):
            raise CommandCancelledError("Command cancelled", None)
    else:
        context.sleep(max(0.0, seconds))


def build_default_mouse_backend() -> MouseBackend:
    try:
        pyautogui = _import_pyautogui()
    except Exception as exc:  # pragma: no cover - environment dependent
        return UnavailablePyAutoGuiMouseBackend(f"Mouse backend is unavailable: {exc}")
    return PyAutoGuiMouseBackend(controller=PyAutoGuiModuleController(pyautogui))


def default_mouse_backend_status() -> str:
    try:
        _import_pyautogui()
    except Exception as exc:  # pragma: no cover - environment dependent
        return f"pyautogui-unavailable ({exc})"
    return "pyautogui"


def _import_pyautogui() -> Any:
    import pyautogui  # type: ignore

    return pyautogui
