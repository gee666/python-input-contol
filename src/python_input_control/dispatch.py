from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

from .backends import BackendExecutionContext
from .backends.keyboard_backend import KeyboardBackend, UnsupportedKeyboardBackend
from .backends.mouse_backend import MouseBackend, UnsupportedMouseBackend
from .errors import (
    CommandExecutionError,
    CoordinateOutOfBoundsError,
    DesktopBoundsUnavailableError,
    InputControlError,
    UnknownCommandError,
    ValidationError,
)
from .models import (
    BrowserContext,
    Command,
    CoordinateCommand,
    KeyTabCommand,
    MouseButton,
    MouseClickCommand,
    MouseDoubleClickCommand,
    MouseMoveCommand,
    ResponseEnvelope,
    ScreenPoint,
    ScrollCommand,
    SelectAllAndDeleteCommand,
    TypeCommand,
)
from .platform import PlatformAdapter, SystemPlatformAdapter, VirtualDesktopBounds, translate_viewport_to_physical_screen
from .randomness import RandomSource, SeededRandom

SUPPORTED_COMMANDS = frozenset(
    {
        "mouse_move",
        "mouse_left_click",
        "mouse_right_click",
        "mouse_double_click",
        "scroll",
        "key_tab",
        "type",
        "select_all_and_delete",
    }
)


class CommandDispatcher:
    def __init__(
        self,
        mouse_backend: MouseBackend | None = None,
        keyboard_backend: KeyboardBackend | None = None,
        platform: PlatformAdapter | None = None,
        rng: RandomSource | None = None,
        sleep=time.sleep,
    ) -> None:
        self.mouse_backend = mouse_backend or UnsupportedMouseBackend()
        self.keyboard_backend = keyboard_backend or UnsupportedKeyboardBackend()
        self.runtime = BackendExecutionContext(
            platform=platform or SystemPlatformAdapter(),
            rng=rng or SeededRandom(),
            sleep=sleep,
        )

    def handle_message(self, raw_message: Mapping[str, Any]) -> ResponseEnvelope:
        command_id = _extract_command_id(raw_message)
        try:
            command = parse_command(raw_message)
            self.dispatch(command)
            return ResponseEnvelope.ok(command.id)
        except InputControlError as exc:
            return ResponseEnvelope.error_response(exc.command_id or command_id, str(exc))
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ResponseEnvelope.error_response(command_id, f"Unhandled host error: {exc}")

    def dispatch(self, command: Command) -> None:
        try:
            if isinstance(command, MouseMoveCommand):
                target = self._translate_and_validate(command)
                self.mouse_backend.move(command, target, self.runtime)
                return
            if isinstance(command, MouseClickCommand):
                target = self._translate_and_validate(command)
                self.mouse_backend.click(command, target, self.runtime)
                return
            if isinstance(command, MouseDoubleClickCommand):
                target = self._translate_and_validate(command)
                self.mouse_backend.double_click(command, target, self.runtime)
                return
            if isinstance(command, ScrollCommand):
                target = self._translate_and_validate(command)
                self.mouse_backend.scroll(command, target, self.runtime)
                return
            if isinstance(command, KeyTabCommand):
                self.keyboard_backend.press_tab(command, self.runtime)
                return
            if isinstance(command, TypeCommand):
                self.keyboard_backend.type_text(command, self.runtime)
                return
            if isinstance(command, SelectAllAndDeleteCommand):
                modifier = self.runtime.platform.select_all_modifier()
                self.keyboard_backend.select_all_and_delete(command, modifier, self.runtime)
                return
        except InputControlError:
            raise
        except Exception as exc:
            raise CommandExecutionError(str(exc), command.id) from exc

        raise UnknownCommandError(f"Unsupported command type: {type(command).__name__}", command.id)

    def _translate_and_validate(self, command: CoordinateCommand) -> ScreenPoint:
        translated = translate_viewport_to_physical_screen(command.context, command.x, command.y)
        if not math.isfinite(translated.x) or not math.isfinite(translated.y):
            raise ValidationError("Translated coordinates must be finite numbers", command.id)
        bounds = self._require_virtual_desktop_bounds(command.id)
        if not bounds.contains(translated):
            raise CoordinateOutOfBoundsError(
                f"Coordinates ({translated.x}, {translated.y}) fall outside the virtual desktop bounds",
                command.id,
            )
        return translated

    def _require_virtual_desktop_bounds(self, command_id: str) -> VirtualDesktopBounds:
        bounds = self.runtime.platform.virtual_desktop_bounds()
        if bounds is None:
            raise DesktopBoundsUnavailableError(
                "Virtual desktop bounds are unavailable; refusing to execute pointer-targeted command",
                command_id,
            )
        return bounds


def parse_command(raw_message: Mapping[str, Any]) -> Command:
    command_id = _validate_command_id(raw_message.get("id"))
    command_name = _validate_command_name(raw_message.get("command"), command_id)
    context = _parse_browser_context(raw_message.get("context"), command_id)
    params = _parse_params(raw_message.get("params"), command_id)

    if command_name == "mouse_move":
        return MouseMoveCommand(
            id=command_id,
            context=context,
            x=_require_number(params, "x", command_id),
            y=_require_number(params, "y", command_id),
            duration_ms=_optional_positive_int(params, "duration_ms", command_id),
        )

    if command_name == "mouse_left_click":
        return MouseClickCommand(
            id=command_id,
            context=context,
            x=_require_number(params, "x", command_id),
            y=_require_number(params, "y", command_id),
            button=MouseButton.LEFT,
            move_duration_ms=_optional_positive_int(params, "move_duration_ms", command_id),
            hold_ms=_optional_positive_int(params, "hold_ms", command_id),
        )

    if command_name == "mouse_right_click":
        return MouseClickCommand(
            id=command_id,
            context=context,
            x=_require_number(params, "x", command_id),
            y=_require_number(params, "y", command_id),
            button=MouseButton.RIGHT,
            move_duration_ms=_optional_positive_int(params, "move_duration_ms", command_id),
            hold_ms=_optional_positive_int(params, "hold_ms", command_id),
        )

    if command_name == "mouse_double_click":
        return MouseDoubleClickCommand(
            id=command_id,
            context=context,
            x=_require_number(params, "x", command_id),
            y=_require_number(params, "y", command_id),
            move_duration_ms=_optional_positive_int(params, "move_duration_ms", command_id),
            interval_ms=_optional_positive_int(params, "interval_ms", command_id),
        )

    if command_name == "scroll":
        return ScrollCommand(
            id=command_id,
            context=context,
            x=_require_number(params, "x", command_id),
            y=_require_number(params, "y", command_id),
            delta_x=_require_number(params, "delta_x", command_id),
            delta_y=_require_number(params, "delta_y", command_id),
            duration_ms=_optional_positive_int(params, "duration_ms", command_id),
        )

    if command_name == "key_tab":
        return KeyTabCommand(id=command_id, context=context)

    if command_name == "type":
        return TypeCommand(
            id=command_id,
            context=context,
            text=_require_string(params, "text", command_id),
            wpm=_optional_positive_number(params, "wpm", command_id),
        )

    if command_name == "select_all_and_delete":
        return SelectAllAndDeleteCommand(id=command_id, context=context)

    raise UnknownCommandError(f"Unknown command: {command_name}", command_id)


def _extract_command_id(raw_message: Mapping[str, Any]) -> str | None:
    value = raw_message.get("id")
    return value if isinstance(value, str) and value else None


def _validate_command_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError("Field 'id' must be a non-empty string")
    return value


def _validate_command_name(value: Any, command_id: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError("Field 'command' must be a non-empty string", command_id)
    if value not in SUPPORTED_COMMANDS:
        raise UnknownCommandError(f"Unknown command: {value}", command_id)
    return value


def _parse_browser_context(value: Any, command_id: str) -> BrowserContext:
    context = _require_mapping_value(value, "context", command_id)
    browser_context = BrowserContext(
        screen_x=_require_number(context, "screenX", command_id),
        screen_y=_require_number(context, "screenY", command_id),
        outer_height=_require_number(context, "outerHeight", command_id),
        inner_height=_require_number(context, "innerHeight", command_id),
        outer_width=_require_number(context, "outerWidth", command_id),
        inner_width=_require_number(context, "innerWidth", command_id),
        device_pixel_ratio=_require_number(context, "devicePixelRatio", command_id),
        scroll_x=_require_number(context, "scrollX", command_id),
        scroll_y=_require_number(context, "scrollY", command_id),
    )
    if browser_context.device_pixel_ratio <= 0:
        raise ValidationError("Field 'context.devicePixelRatio' must be greater than zero", command_id)
    if browser_context.outer_height < 0 or browser_context.inner_height < 0:
        raise ValidationError("Browser heights must be greater than or equal to zero", command_id)
    if browser_context.outer_width < 0 or browser_context.inner_width < 0:
        raise ValidationError("Browser widths must be greater than or equal to zero", command_id)
    if browser_context.outer_height < browser_context.inner_height:
        raise ValidationError("Field 'context.outerHeight' must be greater than or equal to 'context.innerHeight'", command_id)
    if browser_context.outer_width < browser_context.inner_width:
        raise ValidationError("Field 'context.outerWidth' must be greater than or equal to 'context.innerWidth'", command_id)
    return browser_context


def _parse_params(value: Any, command_id: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    return _require_mapping_value(value, "params", command_id)


def _require_mapping_value(value: Any, field_name: str, command_id: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"Field '{field_name}' must be an object", command_id)
    return value


def _require_number(mapping: Mapping[str, Any], field_name: str, command_id: str) -> float:
    if field_name not in mapping:
        raise ValidationError(f"Missing required field '{field_name}'", command_id)
    value = mapping[field_name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"Field '{field_name}' must be a number", command_id)
    value = float(value)
    if not math.isfinite(value):
        raise ValidationError(f"Field '{field_name}' must be finite", command_id)
    return value


def _optional_positive_int(mapping: Mapping[str, Any], field_name: str, command_id: str) -> int | None:
    if field_name not in mapping or mapping[field_name] is None:
        return None
    value = mapping[field_name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"Field '{field_name}' must be an integer number of milliseconds", command_id)
    numeric_value = float(value)
    if not math.isfinite(numeric_value):
        raise ValidationError(f"Field '{field_name}' must be finite", command_id)
    integer_value = int(numeric_value)
    if integer_value <= 0 or integer_value != numeric_value:
        raise ValidationError(f"Field '{field_name}' must be a positive integer", command_id)
    return integer_value


def _optional_positive_number(mapping: Mapping[str, Any], field_name: str, command_id: str) -> float | None:
    if field_name not in mapping or mapping[field_name] is None:
        return None
    value = mapping[field_name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"Field '{field_name}' must be a number", command_id)
    numeric_value = float(value)
    if numeric_value <= 0 or not math.isfinite(numeric_value):
        raise ValidationError(f"Field '{field_name}' must be a positive finite number", command_id)
    return numeric_value


def _require_string(mapping: Mapping[str, Any], field_name: str, command_id: str) -> str:
    if field_name not in mapping:
        raise ValidationError(f"Missing required field '{field_name}'", command_id)
    value = mapping[field_name]
    if not isinstance(value, str):
        raise ValidationError(f"Field '{field_name}' must be a string", command_id)
    return value
