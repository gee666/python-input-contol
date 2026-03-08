from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, get_args

import pytest

from python_input_control.backends import BackendExecutionContext
from python_input_control.dispatch import SUPPORTED_COMMANDS, CommandDispatcher, parse_command
from python_input_control.errors import ValidationError
from python_input_control.models import (
    CommandName,
    MouseButton,
    MouseClickCommand,
    MouseMoveCommand,
    PauseCommand,
    PressKeyCommand,
    PressShortcutCommand,
    ScreenPoint,
    ScrollCommand,
    SequenceCommand,
    TypeCommand,
)
from python_input_control.platform import SystemPlatformAdapter, VirtualDesktopBounds
from python_input_control.randomness import SeededRandom


@dataclass
class FakePlatform:
    bounds: VirtualDesktopBounds | None = None
    name: str = "linux"

    def platform_name(self) -> str:
        return self.name

    def select_all_modifier(self):  # pragma: no cover - legacy platform API compatibility
        raise AssertionError("select_all_modifier should not be used by the redesigned protocol")

    def virtual_desktop_bounds(self) -> VirtualDesktopBounds | None:
        return self.bounds


@dataclass
class RecordingMouseBackend:
    moves: list[tuple[str, ScreenPoint]] = field(default_factory=list)
    clicks: list[tuple[str, MouseButton, int, ScreenPoint]] = field(default_factory=list)
    scrolls: list[tuple[str, ScreenPoint]] = field(default_factory=list)

    def move(self, command: MouseMoveCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        self.moves.append((command.id, target))

    def click(self, command: MouseClickCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        self.clicks.append((command.id, command.button, command.count, target))

    def scroll(self, command: ScrollCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        self.scrolls.append((command.id, target))


@dataclass
class RecordingKeyboardBackend:
    pressed_keys: list[tuple[str, str, int]] = field(default_factory=list)
    shortcuts: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    typed: list[str] = field(default_factory=list)

    def press_key(self, command: PressKeyCommand, context: BackendExecutionContext) -> None:
        self.pressed_keys.append((command.id, command.key, command.repeat))

    def press_shortcut(self, command: PressShortcutCommand, context: BackendExecutionContext) -> None:
        self.shortcuts.append((command.id, command.keys))

    def type_text(self, command: TypeCommand, context: BackendExecutionContext) -> None:
        self.typed.append(command.text)


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


def test_supported_command_lists_match_redesigned_protocol_exactly() -> None:
    expected_commands = {
        "mouse_move",
        "mouse_click",
        "scroll",
        "type",
        "press_key",
        "press_shortcut",
        "pause",
        "sequence",
    }

    assert SUPPORTED_COMMANDS == expected_commands
    assert set(get_args(CommandName)) == expected_commands


@pytest.mark.parametrize(
    "legacy_command",
    [
        "mouse_left_click",
        "mouse_right_click",
        "mouse_double_click",
        "key_tab",
        "key_escape",
        "select_all_and_delete",
    ],
)
def test_parse_command_rejects_removed_legacy_command_names(legacy_command: str) -> None:
    with pytest.raises(ValidationError, match=f"Unknown command: {legacy_command}"):
        parse_command(build_message(legacy_command))


@pytest.mark.parametrize(
    ("command", "params_field"),
    [
        ("mouse_move", "duration_ms"),
        ("mouse_click", "move_duration_ms"),
        ("mouse_click", "hold_ms"),
        ("mouse_click", "interval_ms"),
        ("scroll", "duration_ms"),
    ],
)
@pytest.mark.parametrize("value", [-1, 1.5, float("nan"), float("inf")])
def test_parse_command_rejects_invalid_optional_mouse_timing_fields(command: str, params_field: str, value: float) -> None:
    params: dict[str, Any]
    if command == "mouse_move":
        params = {"x": 10.0, "y": 20.0}
    elif command == "mouse_click":
        params = {"x": 10.0, "y": 20.0}
    else:
        params = {"x": 10.0, "y": 20.0, "delta_x": 0.0, "delta_y": 100.0}
    params[params_field] = value

    with pytest.raises(ValidationError):
        parse_command(build_message(command, params=params))


@pytest.mark.parametrize("value", [0, -1, 1.5, float("nan"), float("inf")])
def test_parse_command_rejects_invalid_positive_integer_repeat(value: float) -> None:
    with pytest.raises(ValidationError):
        parse_command(build_message("press_key", params={"key": "tab", "repeat": value}))


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
        parse_command(build_message("press_key", params={"key": "tab"}, context=context))


def test_parse_command_requires_all_context_fields(base_context: dict[str, float]) -> None:
    context = dict(base_context)
    context.pop("scrollY")

    with pytest.raises(ValidationError, match="Missing required field 'scrollY'"):
        parse_command(build_message("press_key", params={"key": "tab"}, context=context))


@pytest.mark.parametrize(
    ("raw_message", "expected_type", "expected_fields"),
    [
        (
            build_message("mouse_move", params={"x": 10.0, "y": 20.0, "duration_ms": 150}),
            MouseMoveCommand,
            {"x": 10.0, "y": 20.0, "duration_ms": 150},
        ),
        (
            build_message(
                "mouse_click",
                params={"x": 10.0, "y": 20.0, "button": "middle", "count": 2, "move_duration_ms": 200, "hold_ms": 90, "interval_ms": 120},
            ),
            MouseClickCommand,
            {"button": MouseButton.MIDDLE, "count": 2, "move_duration_ms": 200, "hold_ms": 90, "interval_ms": 120},
        ),
        (
            build_message("scroll", params={"x": 10.0, "y": 20.0, "delta_x": 50.0, "delta_y": -100.0, "duration_ms": 300}),
            ScrollCommand,
            {"delta_x": 50.0, "delta_y": -100.0, "duration_ms": 300},
        ),
        (
            build_message("type", params={"text": "hello", "wpm": 80.0}),
            TypeCommand,
            {"text": "hello", "wpm": 80.0},
        ),
        (
            build_message("press_key", params={"key": "escape", "repeat": 3}),
            PressKeyCommand,
            {"key": "escape", "repeat": 3},
        ),
        (
            build_message("press_shortcut", params={"keys": ["control", "a"]}),
            PressShortcutCommand,
            {"keys": ("control", "a")},
        ),
        (
            build_message("pause", params={"duration_ms": 0}),
            PauseCommand,
            {"duration_ms": 0},
        ),
        (
            build_message(
                "sequence",
                params={
                    "steps": [
                        {"command": "press_shortcut", "params": {"keys": ["control", "a"]}},
                        {"command": "press_key", "params": {"key": "delete"}},
                    ]
                },
            ),
            SequenceCommand,
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


def test_parse_command_accepts_shortcut_string_alias() -> None:
    command = parse_command(build_message("press_shortcut", params={"shortcut": "ctrl+a"}))

    assert isinstance(command, PressShortcutCommand)
    assert command.keys == ("ctrl", "a")


def test_parse_command_accepts_zero_duration_mouse_timing_fields() -> None:
    move = parse_command(build_message("mouse_move", params={"x": 10.0, "y": 20.0, "duration_ms": 0}))
    click = parse_command(
        build_message(
            "mouse_click",
            params={
                "x": 10.0,
                "y": 20.0,
                "button": "right",
                "count": 3,
                "move_duration_ms": 0,
                "hold_ms": 0,
                "interval_ms": 0,
            },
        )
    )
    scroll = parse_command(
        build_message("scroll", params={"x": 10.0, "y": 20.0, "delta_x": 25.0, "delta_y": -125.0, "duration_ms": 0})
    )

    assert isinstance(move, MouseMoveCommand)
    assert move.duration_ms == 0
    assert isinstance(click, MouseClickCommand)
    assert click.button is MouseButton.RIGHT
    assert click.count == 3
    assert click.move_duration_ms == 0
    assert click.hold_ms == 0
    assert click.interval_ms == 0
    assert isinstance(scroll, ScrollCommand)
    assert scroll.duration_ms == 0


@pytest.mark.parametrize("wpm", [0, -1, float("nan"), float("inf")])
def test_parse_command_rejects_invalid_wpm_values(wpm: float) -> None:
    with pytest.raises(ValidationError):
        parse_command(build_message("type", params={"text": "hello", "wpm": wpm}))


def test_parse_command_rejects_nested_sequence() -> None:
    with pytest.raises(ValidationError, match="Nested sequence commands are not supported"):
        parse_command(
            build_message(
                "sequence",
                params={"steps": [{"command": "sequence", "params": {"steps": []}}]},
            )
        )


def test_parse_command_rejects_removed_legacy_sequence_step_command() -> None:
    with pytest.raises(ValidationError, match="Unknown command: select_all_and_delete"):
        parse_command(
            build_message(
                "sequence",
                params={"steps": [{"command": "select_all_and_delete", "params": {}}]},
            )
        )


def test_dispatch_press_key_calls_keyboard_backend() -> None:
    keyboard_backend = RecordingKeyboardBackend()
    dispatcher = CommandDispatcher(
        mouse_backend=RecordingMouseBackend(),
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    response = dispatcher.handle_message(build_message("press_key", command_id="esc-1", params={"key": "escape", "repeat": 2}))

    assert response.status == "ok"
    assert keyboard_backend.pressed_keys == [("esc-1", "escape", 2)]


def test_dispatch_sequence_runs_keyboard_steps_in_order() -> None:
    keyboard_backend = RecordingKeyboardBackend()
    sleeps: list[float] = []
    dispatcher = CommandDispatcher(
        mouse_backend=RecordingMouseBackend(),
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=sleeps.append,
    )

    response = dispatcher.handle_message(
        build_message(
            "sequence",
            command_id="seq-1",
            params={
                "steps": [
                    {"command": "type", "params": {"text": "hello"}},
                    {"command": "press_shortcut", "params": {"keys": ["control", "a"]}},
                    {"command": "pause", "params": {"duration_ms": 75}},
                    {"command": "press_key", "params": {"key": "delete"}},
                ]
            },
        )
    )

    assert response.status == "ok"
    assert keyboard_backend.typed == ["hello"]
    assert keyboard_backend.shortcuts == [("seq-1", ("control", "a"))]
    assert keyboard_backend.pressed_keys == [("seq-1", "delete", 1)]
    assert sleeps == [0.075]


def test_dispatch_sequence_runs_pointer_steps_in_order() -> None:
    mouse_backend = RecordingMouseBackend()
    dispatcher = CommandDispatcher(
        mouse_backend=mouse_backend,
        keyboard_backend=RecordingKeyboardBackend(),
        platform=FakePlatform(bounds=VirtualDesktopBounds(left=0.0, top=0.0, right=4000.0, bottom=4000.0)),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )

    response = dispatcher.handle_message(
        build_message(
            "sequence",
            command_id="pointer-seq-1",
            params={
                "steps": [
                    {"command": "mouse_move", "params": {"x": 10.0, "y": 20.0, "duration_ms": 0}},
                    {
                        "command": "mouse_click",
                        "params": {"x": 12.0, "y": 22.0, "button": "right", "count": 2, "interval_ms": 0},
                    },
                    {"command": "scroll", "params": {"x": 15.0, "y": 25.0, "delta_x": 50.0, "delta_y": -150.0, "duration_ms": 0}},
                ]
            },
        )
    )

    assert response.status == "ok"
    assert mouse_backend.moves == [("pointer-seq-1", ScreenPoint(110.0, 150.0))]
    assert mouse_backend.clicks == [("pointer-seq-1", MouseButton.RIGHT, 2, ScreenPoint(112.0, 152.0))]
    assert mouse_backend.scrolls == [("pointer-seq-1", ScreenPoint(115.0, 155.0))]


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
        ("mouse_click", {"x": 10.0, "y": 20.0}),
        ("scroll", {"x": 10.0, "y": 20.0, "delta_x": 0.0, "delta_y": -120.0}),
    ],
)
def test_dispatch_rejects_pointer_commands_when_virtual_desktop_bounds_are_unavailable(
    command: str,
    params: dict[str, float],
) -> None:
    mouse_backend = RecordingMouseBackend()
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
    assert mouse_backend.moves == []
    assert mouse_backend.clicks == []
    assert mouse_backend.scrolls == []


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
    mouse_backend = RecordingMouseBackend()
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
    assert mouse_backend.moves == []
    assert mouse_backend.clicks == []
    assert mouse_backend.scrolls == []


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
