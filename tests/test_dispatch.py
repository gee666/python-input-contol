from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from python_input_control.backends import BackendExecutionContext
from python_input_control.dispatch import CommandDispatcher, parse_command
from python_input_control.errors import ValidationError
from python_input_control.models import (
    KeyEscapeCommand,
    KeyTabCommand,
    ModifierKey,
    MouseButton,
    MouseClickCommand,
    MouseDoubleClickCommand,
    MouseMoveCommand,
    ScreenPoint,
    ScrollCommand,
    SelectAllAndDeleteCommand,
    TypeCommand,
)
from python_input_control.platform import SystemPlatformAdapter, VirtualDesktopBounds
from python_input_control.randomness import SeededRandom


@dataclass
class FakePlatform:
    modifier: ModifierKey = ModifierKey.CONTROL
    bounds: VirtualDesktopBounds | None = None
    name: str = "linux"

    def platform_name(self) -> str:
        return self.name

    def select_all_modifier(self) -> ModifierKey:
        return self.modifier

    def virtual_desktop_bounds(self) -> VirtualDesktopBounds | None:
        return self.bounds


@dataclass
class RecordingMouseBackend:
    moves: list[tuple[str, ScreenPoint]] = field(default_factory=list)

    def move(self, command: MouseMoveCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        self.moves.append((command.id, target))

    def click(self, command, target, context) -> None:  # pragma: no cover - not used in these tests
        raise AssertionError("unexpected click")

    def double_click(self, command, target, context) -> None:  # pragma: no cover - not used in these tests
        raise AssertionError("unexpected double click")

    def scroll(self, command, target, context) -> None:  # pragma: no cover - not used in these tests
        raise AssertionError("unexpected scroll")


@dataclass
class RecordingPointerMouseBackend:
    calls: list[tuple[str, str, ScreenPoint]] = field(default_factory=list)

    def move(self, command: MouseMoveCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        self.calls.append(("mouse_move", command.id, target))

    def click(self, command, target, context) -> None:
        self.calls.append((f"mouse_click:{command.button.value}", command.id, target))

    def double_click(self, command, target, context) -> None:
        self.calls.append(("mouse_double_click", command.id, target))

    def scroll(self, command, target, context) -> None:
        self.calls.append(("scroll", command.id, target))


@dataclass
class RecordingKeyboardBackend:
    pressed_tab_ids: list[str] = field(default_factory=list)
    pressed_escape_ids: list[str] = field(default_factory=list)
    typed: list[str] = field(default_factory=list)
    select_all_calls: list[tuple[str, ModifierKey]] = field(default_factory=list)

    def press_tab(self, command, context) -> None:
        self.pressed_tab_ids.append(command.id)

    def press_escape(self, command, context) -> None:
        self.pressed_escape_ids.append(command.id)

    def type_text(self, command, context) -> None:
        self.typed.append(command.text)

    def select_all_and_delete(self, command, modifier: ModifierKey, context) -> None:
        self.select_all_calls.append((command.id, modifier))


@dataclass
class RaisingMouseBackend(RecordingMouseBackend):
    def move(self, command: MouseMoveCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        raise RuntimeError("backend exploded")


@pytest.fixture
def base_context() -> dict[str, float]:
    return {
        "screenX": 100.0,
        "screenY": 50.0,
        "outerHeight": 900.0,
        "innerHeight": 820.0,
        "outerWidth": 1280.0,
        "innerWidth": 1280.0,
        "devicePixelRatio": 1.0,
        "scrollX": 0.0,
        "scrollY": 0.0,
    }


def build_message(
    command: str,
    *,
    command_id: str = "cmd-1",
    params: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": command_id,
        "command": command,
        "params": params or {},
        "context": context or {
            "screenX": 100.0,
            "screenY": 50.0,
            "outerHeight": 900.0,
            "innerHeight": 820.0,
            "outerWidth": 1280.0,
            "innerWidth": 1280.0,
            "devicePixelRatio": 1.0,
            "scrollX": 0.0,
            "scrollY": 0.0,
        },
    }


@pytest.mark.parametrize(
    ("command", "params_field"),
    [
        ("mouse_move", "duration_ms"),
        ("mouse_left_click", "move_duration_ms"),
        ("mouse_left_click", "hold_ms"),
        ("mouse_double_click", "interval_ms"),
        ("scroll", "duration_ms"),
    ],
)
@pytest.mark.parametrize("value", [0, -1, 1.5, float("nan"), float("inf")])
def test_parse_command_rejects_invalid_positive_millisecond_fields(
    command: str,
    params_field: str,
    value: float,
) -> None:
    params: dict[str, Any] = {"x": 10.0, "y": 20.0}
    if command == "scroll":
        params.update({"delta_x": 0.0, "delta_y": 100.0})
    params[params_field] = value

    with pytest.raises(ValidationError):
        parse_command(build_message(command, params=params))


@pytest.mark.parametrize(
    "context_updates",
    [
        {"outerHeight": 799.0, "innerHeight": 800.0},
        {"outerWidth": 999.0, "innerWidth": 1000.0},
        {"outerHeight": -1.0},
        {"innerWidth": -1.0},
    ],
)
def test_parse_command_rejects_invalid_browser_context_dimensions(
    base_context: dict[str, float],
    context_updates: dict[str, float],
) -> None:
    context = {**base_context, **context_updates}

    with pytest.raises(ValidationError):
        parse_command(build_message("key_tab", context=context))


def test_parse_command_requires_all_context_fields(base_context: dict[str, float]) -> None:
    context = dict(base_context)
    context.pop("scrollY")

    with pytest.raises(ValidationError, match="Missing required field 'scrollY'"):
        parse_command(build_message("key_tab", context=context))


@pytest.mark.parametrize(
    ("raw_message", "expected_type", "expected_fields"),
    [
        (
            build_message("mouse_move", params={"x": 10.0, "y": 20.0, "duration_ms": 150}),
            MouseMoveCommand,
            {"x": 10.0, "y": 20.0, "duration_ms": 150},
        ),
        (
            build_message("mouse_left_click", params={"x": 10.0, "y": 20.0, "move_duration_ms": 200, "hold_ms": 90}),
            MouseClickCommand,
            {"button": MouseButton.LEFT, "move_duration_ms": 200, "hold_ms": 90},
        ),
        (
            build_message("mouse_right_click", params={"x": 10.0, "y": 20.0}),
            MouseClickCommand,
            {"button": MouseButton.RIGHT},
        ),
        (
            build_message("mouse_double_click", params={"x": 10.0, "y": 20.0, "interval_ms": 120}),
            MouseDoubleClickCommand,
            {"interval_ms": 120},
        ),
        (
            build_message("scroll", params={"x": 10.0, "y": 20.0, "delta_x": 50.0, "delta_y": -100.0, "duration_ms": 300}),
            ScrollCommand,
            {"delta_x": 50.0, "delta_y": -100.0, "duration_ms": 300},
        ),
        (
            build_message("key_tab"),
            KeyTabCommand,
            {},
        ),
        (
            build_message("key_escape"),
            KeyEscapeCommand,
            {},
        ),
        (
            build_message("type", params={"text": "hello", "wpm": 80.0}),
            TypeCommand,
            {"text": "hello", "wpm": 80.0},
        ),
        (
            build_message("select_all_and_delete"),
            SelectAllAndDeleteCommand,
            {},
        ),
    ],
)
def test_parse_command_builds_expected_command_models(
    raw_message: dict[str, Any],
    expected_type: type,
    expected_fields: dict[str, Any],
) -> None:
    command = parse_command(raw_message)

    assert isinstance(command, expected_type)
    for field_name, expected_value in expected_fields.items():
        assert getattr(command, field_name) == expected_value


@pytest.mark.parametrize("wpm", [0, -1, float("nan"), float("inf")])
def test_parse_command_rejects_invalid_wpm_values(wpm: float) -> None:
    with pytest.raises(ValidationError):
        parse_command(build_message("type", params={"text": "hello", "wpm": wpm}))


@pytest.mark.parametrize(
    "modifier",
    [ModifierKey.CONTROL, ModifierKey.COMMAND],
)
def test_dispatch_select_all_and_delete_uses_platform_modifier(modifier: ModifierKey) -> None:
    keyboard_backend = RecordingKeyboardBackend()
    dispatcher = CommandDispatcher(
        mouse_backend=RecordingMouseBackend(),
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(modifier=modifier),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    response = dispatcher.handle_message(build_message("select_all_and_delete", command_id=f"clear-{modifier.value}"))

    assert response.status == "ok"
    assert keyboard_backend.select_all_calls == [(f"clear-{modifier.value}", modifier)]


def test_dispatch_key_escape_calls_keyboard_backend() -> None:
    keyboard_backend = RecordingKeyboardBackend()
    dispatcher = CommandDispatcher(
        mouse_backend=RecordingMouseBackend(),
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    response = dispatcher.handle_message(build_message("key_escape", command_id="esc-1"))

    assert response.status == "ok"
    assert keyboard_backend.pressed_escape_ids == ["esc-1"]


def test_dispatch_rejects_unknown_command() -> None:
    dispatcher = CommandDispatcher(
        mouse_backend=RecordingMouseBackend(),
        keyboard_backend=RecordingKeyboardBackend(),
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    response = dispatcher.handle_message(build_message("nope"))

    assert response.status == "error"
    assert response.error == "Unknown command: nope"


@pytest.mark.parametrize(
    ("command", "params"),
    [
        ("mouse_move", {"x": 10.0, "y": 20.0}),
        ("mouse_left_click", {"x": 10.0, "y": 20.0}),
        ("mouse_right_click", {"x": 10.0, "y": 20.0}),
        ("mouse_double_click", {"x": 10.0, "y": 20.0}),
        ("scroll", {"x": 10.0, "y": 20.0, "delta_x": 0.0, "delta_y": -120.0}),
    ],
)
def test_dispatch_rejects_pointer_commands_when_virtual_desktop_bounds_are_unavailable(
    command: str,
    params: dict[str, float],
) -> None:
    mouse_backend = RecordingPointerMouseBackend()
    dispatcher = CommandDispatcher(
        mouse_backend=mouse_backend,
        keyboard_backend=RecordingKeyboardBackend(),
        platform=FakePlatform(bounds=None),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    response = dispatcher.handle_message(build_message(command, params=params, command_id=f"{command}-1"))

    assert response.status == "error"
    assert response.error == "Virtual desktop bounds are unavailable; refusing to execute pointer-targeted command"
    assert mouse_backend.calls == []


def test_dispatch_rejects_coordinates_outside_virtual_desktop_bounds() -> None:
    mouse_backend = RecordingMouseBackend()
    dispatcher = CommandDispatcher(
        mouse_backend=mouse_backend,
        keyboard_backend=RecordingKeyboardBackend(),
        platform=FakePlatform(bounds=VirtualDesktopBounds(left=0.0, top=0.0, right=200.0, bottom=200.0)),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    response = dispatcher.handle_message(build_message("mouse_move", params={"x": 250.0, "y": 250.0}))

    assert response.status == "error"
    assert "fall outside the virtual desktop bounds" in (response.error or "")
    assert mouse_backend.moves == []


def test_dispatch_rejects_coordinates_outside_virtual_desktop_bounds_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    mouse_backend = RecordingMouseBackend()
    dispatcher = CommandDispatcher(
        mouse_backend=mouse_backend,
        keyboard_backend=RecordingKeyboardBackend(),
        platform=SystemPlatformAdapter(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr(
        "python_input_control.platform._macos_virtual_desktop_bounds",
        lambda: VirtualDesktopBounds(left=-1440.0, top=0.0, right=2560.0, bottom=1600.0),
    )

    response = dispatcher.handle_message(build_message("mouse_move", params={"x": 3000.0, "y": 2000.0}))

    assert response.status == "error"
    assert "fall outside the virtual desktop bounds" in (response.error or "")
    assert mouse_backend.moves == []


def test_dispatch_rejects_pointer_commands_when_system_bounds_discovery_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mouse_backend = RecordingPointerMouseBackend()
    dispatcher = CommandDispatcher(
        mouse_backend=mouse_backend,
        keyboard_backend=RecordingKeyboardBackend(),
        platform=SystemPlatformAdapter(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("python_input_control.platform._linux_virtual_desktop_bounds", lambda: None)

    response = dispatcher.handle_message(build_message("scroll", params={"x": 10.0, "y": 20.0, "delta_x": 0.0, "delta_y": -120.0}))

    assert response.status == "error"
    assert response.error == "Virtual desktop bounds are unavailable; refusing to execute pointer-targeted command"
    assert mouse_backend.calls == []


def test_dispatch_wraps_backend_failures_without_crashing() -> None:
    dispatcher = CommandDispatcher(
        mouse_backend=RaisingMouseBackend(),
        keyboard_backend=RecordingKeyboardBackend(),
        platform=FakePlatform(bounds=VirtualDesktopBounds(left=0.0, top=0.0, right=2000.0, bottom=2000.0)),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    response = dispatcher.handle_message(build_message("mouse_move", params={"x": 10.0, "y": 20.0}))

    assert response.status == "error"
    assert response.error == "backend exploded"
