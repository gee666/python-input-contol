from __future__ import annotations

import string
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from ..errors import BackendUnavailableError, CommandCancelledError
from ..models import PressKeyCommand, PressShortcutCommand, TypeCommand
from ..randomness import RandomSource
from ..timing import jittered_delay_ms, wpm_to_inter_key_delay_ms
from .keyboard_backend import KeyboardBackend

if TYPE_CHECKING:
    from . import BackendExecutionContext

_DEFAULT_MIN_WPM = 60.0
_DEFAULT_MAX_WPM = 100.0
_SHORTCUT_DELAY_MIN_MS = 50.0
_SHORTCUT_DELAY_MAX_MS = 100.0
_TYPING_JITTER_RATIO = 0.25
_MIN_INTER_KEY_DELAY_MS = 10.0
_EXTRA_PAUSE_CHARACTERS = frozenset({" ", ",", ".", ";", ":", "!", "?"})
_SHIFTED_SYMBOLS = {
    "~": "`",
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
}
_KEY_ALIASES = {
    "esc": "escape",
    "return": "enter",
    "cmd": "command",
    "meta": "command",
    "super": "command",
    "win": "command",
    "windows": "command",
    "ctrl": "control",
    "ctl": "control",
    "option": "alt",
    "opt": "alt",
    "altgr": "alt_gr",
    "del": "delete",
    "ins": "insert",
    "space": "space",
    "spacebar": "space",
    "pgup": "page_up",
    "pageup": "page_up",
    "pgdn": "page_down",
    "pagedown": "page_down",
    "capslock": "caps_lock",
    "numlock": "num_lock",
    "scrolllock": "scroll_lock",
    "printscreen": "print_screen",
    "prtsc": "print_screen",
    "prtscr": "print_screen",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
}
_SPECIAL_KEY_ATTRIBUTE_ALIASES = {
    "escape": "esc",
    "control": "ctrl",
    "command": "cmd",
}


class KeyboardKey(str, Enum):
    TAB = "tab"
    ENTER = "enter"
    ESCAPE = "escape"
    SHIFT = "shift"
    CONTROL = "control"
    COMMAND = "command"
    ALT = "alt"
    DELETE = "delete"
    BACKSPACE = "backspace"
    SPACE = "space"


class KeyboardEventSink(Protocol):
    def press_key(self, key: KeyboardKey) -> None: ...
    def release_key(self, key: KeyboardKey) -> None: ...
    def press_character(self, character: str) -> None: ...
    def release_character(self, character: str) -> None: ...
    def press_special_key(self, key_name: str) -> None: ...
    def release_special_key(self, key_name: str) -> None: ...


@dataclass(frozen=True)
class CharacterKeyPlan:
    character: str | None = None
    special_key: KeyboardKey | None = None
    requires_shift: bool = False


@dataclass(frozen=True)
class KeySpecPlan:
    character: str | None = None
    special_key: KeyboardKey | None = None
    special_key_name: str | None = None
    requires_shift: bool = False


class PynputKeyboardBackend(KeyboardBackend):
    def __init__(self, sink_factory: Callable[[], KeyboardEventSink] | None = None) -> None:
        self._sink_factory = sink_factory or build_pynput_keyboard_sink
        self._sink: KeyboardEventSink | None = None

    def press_key(self, command: PressKeyCommand, context: BackendExecutionContext) -> None:
        for _ in range(command.repeat):
            self._tap_key_spec(command.key)

    def press_shortcut(self, command: PressShortcutCommand, context: BackendExecutionContext) -> None:
        held_keys = list(command.keys[:-1])
        last_key = command.keys[-1]

        with self._held_key_specs(held_keys):
            self._tap_key_spec(last_key)
        _sleep_ms(context, context.rng.uniform(_SHORTCUT_DELAY_MIN_MS, _SHORTCUT_DELAY_MAX_MS))

    def type_text(self, command: TypeCommand, context: BackendExecutionContext) -> None:
        if not command.text:
            return

        wpm = command.wpm if command.wpm is not None else context.rng.uniform(_DEFAULT_MIN_WPM, _DEFAULT_MAX_WPM)
        base_delay_ms = wpm_to_inter_key_delay_ms(wpm)
        last_index = len(command.text) - 1

        for index, character in enumerate(command.text):
            self._emit_character(plan_character_key(character))
            if index == last_index:
                continue
            delay_ms = jittered_delay_ms(
                base_delay_ms,
                context.rng,
                jitter_ratio=_TYPING_JITTER_RATIO,
                minimum_ms=_MIN_INTER_KEY_DELAY_MS,
            )
            delay_ms += extra_pause_after_character_ms(character, context.rng)
            _sleep_ms(context, delay_ms)

    def _emit_character(self, plan: CharacterKeyPlan) -> None:
        sink = self._get_sink()
        if plan.requires_shift:
            sink.press_key(KeyboardKey.SHIFT)
        try:
            if plan.special_key is not None:
                sink.press_key(plan.special_key)
                sink.release_key(plan.special_key)
            elif plan.character is not None:
                sink.press_character(plan.character)
                sink.release_character(plan.character)
            else:  # pragma: no cover - defensive invariant
                raise ValueError("Character plan must define a character or special key")
        finally:
            if plan.requires_shift:
                sink.release_key(KeyboardKey.SHIFT)

    def _tap_key_spec(self, key_name: str) -> None:
        plan = _plan_key_spec(key_name)
        sink = self._get_sink()
        if plan.requires_shift:
            sink.press_key(KeyboardKey.SHIFT)
        try:
            if plan.special_key is not None:
                sink.press_key(plan.special_key)
                sink.release_key(plan.special_key)
                return
            if plan.character is not None:
                sink.press_character(plan.character)
                sink.release_character(plan.character)
                return
            if plan.special_key_name is not None:
                sink.press_special_key(plan.special_key_name)
                sink.release_special_key(plan.special_key_name)
                return
            raise ValueError("Key spec plan must define a character or special key")
        finally:
            if plan.requires_shift:
                sink.release_key(KeyboardKey.SHIFT)

    def _press_key_spec(self, key_name: str) -> None:
        plan = _plan_key_spec(key_name)
        sink = self._get_sink()
        if plan.requires_shift:
            sink.press_key(KeyboardKey.SHIFT)
        if plan.special_key is not None:
            sink.press_key(plan.special_key)
            return
        if plan.character is not None:
            sink.press_character(plan.character)
            return
        if plan.special_key_name is not None:
            sink.press_special_key(plan.special_key_name)
            return
        raise ValueError("Key spec plan must define a character or special key")

    def _release_key_spec(self, key_name: str) -> None:
        plan = _plan_key_spec(key_name)
        sink = self._get_sink()
        if plan.special_key is not None:
            sink.release_key(plan.special_key)
        elif plan.character is not None:
            sink.release_character(plan.character)
        elif plan.special_key_name is not None:
            sink.release_special_key(plan.special_key_name)
        else:  # pragma: no cover - defensive invariant
            raise ValueError("Key spec plan must define a character or special key")
        if plan.requires_shift:
            sink.release_key(KeyboardKey.SHIFT)

    @contextmanager
    def _held_key_specs(self, keys: list[str]):
        pressed_keys: list[str] = []
        try:
            for key in keys:
                self._press_key_spec(key)
                pressed_keys.append(key)
            yield
        finally:
            for key in reversed(pressed_keys):
                self._release_key_spec(key)

    def _get_sink(self) -> KeyboardEventSink:
        if self._sink is None:
            self._sink = self._sink_factory()
        return self._sink


@dataclass
class PynputKeyboardSink:
    controller: object
    key_namespace: object

    def press_key(self, key: KeyboardKey) -> None:
        self.controller.press(self._resolve_key(key))

    def release_key(self, key: KeyboardKey) -> None:
        self.controller.release(self._resolve_key(key))

    def press_character(self, character: str) -> None:
        self.controller.press(character)

    def release_character(self, character: str) -> None:
        self.controller.release(character)

    def press_special_key(self, key_name: str) -> None:
        self.controller.press(self._resolve_special_key(key_name))

    def release_special_key(self, key_name: str) -> None:
        self.controller.release(self._resolve_special_key(key_name))

    def _resolve_key(self, key: KeyboardKey):
        key_namespace = self.key_namespace
        mapping = {
            KeyboardKey.TAB: key_namespace.tab,
            KeyboardKey.ENTER: key_namespace.enter,
            KeyboardKey.ESCAPE: key_namespace.esc,
            KeyboardKey.SHIFT: key_namespace.shift,
            KeyboardKey.CONTROL: key_namespace.ctrl,
            KeyboardKey.COMMAND: getattr(key_namespace, "cmd", getattr(key_namespace, "cmd_l", None)),
            KeyboardKey.ALT: getattr(key_namespace, "alt", getattr(key_namespace, "alt_l", None)),
            KeyboardKey.DELETE: getattr(key_namespace, "delete", getattr(key_namespace, "backspace", None)),
            KeyboardKey.BACKSPACE: getattr(key_namespace, "backspace", None),
            KeyboardKey.SPACE: key_namespace.space,
        }
        resolved = mapping[key]
        if resolved is None:
            raise ValueError(f"Unsupported special key in active pynput backend: {key.value}")
        return resolved

    def _resolve_special_key(self, key_name: str):
        normalized = normalize_key_name(key_name)
        key_namespace = self.key_namespace
        attribute_name = _SPECIAL_KEY_ATTRIBUTE_ALIASES.get(normalized, normalized)
        if hasattr(key_namespace, attribute_name):
            return getattr(key_namespace, attribute_name)
        raise ValueError(f"Unsupported key name: {key_name}")


class UnavailablePynputKeyboardBackend(KeyboardBackend):
    def __init__(self, message: str) -> None:
        self.message = message

    def press_key(self, command: PressKeyCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError(self.message, command.id)

    def press_shortcut(self, command: PressShortcutCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError(self.message, command.id)

    def type_text(self, command: TypeCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError(self.message, command.id)


class _UnavailablePynputDependencyError(BackendUnavailableError):
    pass


def build_default_keyboard_backend() -> KeyboardBackend:
    try:
        _import_pynput_keyboard()
    except Exception as exc:  # pragma: no cover - environment dependent
        return UnavailablePynputKeyboardBackend(f"Keyboard backend is unavailable: {exc}")
    return PynputKeyboardBackend()


def default_keyboard_backend_status() -> str:
    try:
        _import_pynput_keyboard()
    except Exception as exc:  # pragma: no cover - environment dependent
        return f"pynput-unavailable ({exc})"
    return "pynput"


def build_pynput_keyboard_sink() -> KeyboardEventSink:
    try:
        controller_class, key_namespace = _import_pynput_keyboard()
    except Exception as exc:  # pragma: no cover - depends on local OS/input stack
        raise _UnavailablePynputDependencyError(f"Keyboard backend is unavailable: {exc}") from exc
    return PynputKeyboardSink(controller=controller_class(), key_namespace=key_namespace)


def _import_pynput_keyboard() -> tuple[object, object]:
    from pynput.keyboard import Controller, Key

    return Controller, Key


def plan_character_key(character: str) -> CharacterKeyPlan:
    if len(character) != 1:
        raise ValueError("Typing plans require single characters")

    if character in {"\n", "\r"}:
        return CharacterKeyPlan(special_key=KeyboardKey.ENTER)
    if character == "\t":
        return CharacterKeyPlan(special_key=KeyboardKey.TAB)
    if character == " ":
        return CharacterKeyPlan(special_key=KeyboardKey.SPACE)
    if character in string.ascii_uppercase:
        return CharacterKeyPlan(character=character.lower(), requires_shift=True)
    if character in _SHIFTED_SYMBOLS:
        return CharacterKeyPlan(character=_SHIFTED_SYMBOLS[character], requires_shift=True)
    return CharacterKeyPlan(character=character)


def normalize_key_name(key_name: str) -> str:
    normalized = key_name.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        raise ValueError("Key names must be non-empty strings")
    return _KEY_ALIASES.get(normalized, normalized)


def _enum_key_for_name(key_name: str) -> KeyboardKey | None:
    mapping = {
        "tab": KeyboardKey.TAB,
        "enter": KeyboardKey.ENTER,
        "escape": KeyboardKey.ESCAPE,
        "shift": KeyboardKey.SHIFT,
        "control": KeyboardKey.CONTROL,
        "command": KeyboardKey.COMMAND,
        "alt": KeyboardKey.ALT,
        "delete": KeyboardKey.DELETE,
        "backspace": KeyboardKey.BACKSPACE,
        "space": KeyboardKey.SPACE,
    }
    return mapping.get(key_name)


def _plan_key_spec(key_name: str) -> KeySpecPlan:
    if len(key_name) == 1:
        character_plan = plan_character_key(key_name)
        return KeySpecPlan(
            character=character_plan.character,
            special_key=character_plan.special_key,
            requires_shift=character_plan.requires_shift,
        )

    normalized = normalize_key_name(key_name)
    enum_key = _enum_key_for_name(normalized)
    if enum_key is not None:
        return KeySpecPlan(special_key=enum_key)
    return KeySpecPlan(special_key_name=normalized)


def extra_pause_after_character_ms(character: str, rng: RandomSource) -> float:
    if character not in _EXTRA_PAUSE_CHARACTERS:
        return 0.0

    pause_probability = 0.35 if character == " " else 0.6
    if rng.random() >= pause_probability:
        return 0.0
    return rng.uniform(150.0, 300.0)


def _sleep_ms(context: BackendExecutionContext, delay_ms: float) -> None:
    """Sleep for *delay_ms* milliseconds, waking early if cancel_event is set."""
    seconds = max(0.0, delay_ms) / 1000.0
    ev = context.cancel_event
    if ev is not None:
        if ev.wait(seconds):          # returns True when the event fires = cancel
            raise CommandCancelledError("Command cancelled", None)
    else:
        context.sleep(seconds)
