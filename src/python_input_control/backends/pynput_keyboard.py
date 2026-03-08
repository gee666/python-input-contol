from __future__ import annotations

import string
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from ..errors import BackendUnavailableError
from ..models import KeyTabCommand, ModifierKey, SelectAllAndDeleteCommand, TypeCommand
from ..randomness import RandomSource
from ..timing import jittered_delay_ms, wpm_to_inter_key_delay_ms
from .keyboard_backend import KeyboardBackend

if TYPE_CHECKING:
    from . import BackendExecutionContext

_DEFAULT_MIN_WPM = 60.0
_DEFAULT_MAX_WPM = 100.0
_SELECT_ALL_DELAY_MIN_MS = 50.0
_SELECT_ALL_DELAY_MAX_MS = 100.0
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


class KeyboardKey(str, Enum):
    TAB = "tab"
    ENTER = "enter"
    SHIFT = "shift"
    CONTROL = "control"
    COMMAND = "command"
    DELETE = "delete"


class KeyboardEventSink(Protocol):
    def press_key(self, key: KeyboardKey) -> None: ...
    def release_key(self, key: KeyboardKey) -> None: ...
    def press_character(self, character: str) -> None: ...
    def release_character(self, character: str) -> None: ...


@dataclass(frozen=True)
class CharacterKeyPlan:
    character: str | None = None
    special_key: KeyboardKey | None = None
    requires_shift: bool = False


class PynputKeyboardBackend(KeyboardBackend):
    def __init__(self, sink_factory: Callable[[], KeyboardEventSink] | None = None) -> None:
        self._sink_factory = sink_factory or build_pynput_keyboard_sink
        self._sink: KeyboardEventSink | None = None

    def press_tab(self, command: KeyTabCommand, context: BackendExecutionContext) -> None:
        self._tap_key(KeyboardKey.TAB)

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

    def select_all_and_delete(
        self,
        command: SelectAllAndDeleteCommand,
        modifier: ModifierKey,
        context: BackendExecutionContext,
    ) -> None:
        modifier_key = keyboard_key_for_modifier(modifier)
        with self._held_key(modifier_key):
            self._tap_character("a")
        _sleep_ms(context, context.rng.uniform(_SELECT_ALL_DELAY_MIN_MS, _SELECT_ALL_DELAY_MAX_MS))
        self._tap_key(KeyboardKey.DELETE)

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

    def _tap_key(self, key: KeyboardKey) -> None:
        sink = self._get_sink()
        sink.press_key(key)
        sink.release_key(key)

    def _tap_character(self, character: str) -> None:
        sink = self._get_sink()
        sink.press_character(character)
        sink.release_character(character)

    @contextmanager
    def _held_key(self, key: KeyboardKey):
        sink = self._get_sink()
        sink.press_key(key)
        try:
            yield
        finally:
            sink.release_key(key)

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

    def _resolve_key(self, key: KeyboardKey):
        key_namespace = self.key_namespace
        mapping = {
            KeyboardKey.TAB: key_namespace.tab,
            KeyboardKey.ENTER: key_namespace.enter,
            KeyboardKey.SHIFT: key_namespace.shift,
            KeyboardKey.CONTROL: key_namespace.ctrl,
            KeyboardKey.COMMAND: key_namespace.cmd,
            KeyboardKey.DELETE: key_namespace.delete,
        }
        return mapping[key]


class UnavailablePynputKeyboardBackend(KeyboardBackend):
    def __init__(self, message: str) -> None:
        self.message = message

    def press_tab(self, command: KeyTabCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError(self.message, command.id)

    def type_text(self, command: TypeCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError(self.message, command.id)

    def select_all_and_delete(
        self,
        command: SelectAllAndDeleteCommand,
        modifier: ModifierKey,
        context: BackendExecutionContext,
    ) -> None:
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
    if character in string.ascii_uppercase:
        return CharacterKeyPlan(character=character.lower(), requires_shift=True)
    if character in _SHIFTED_SYMBOLS:
        return CharacterKeyPlan(character=_SHIFTED_SYMBOLS[character], requires_shift=True)
    return CharacterKeyPlan(character=character)


def keyboard_key_for_modifier(modifier: ModifierKey) -> KeyboardKey:
    if modifier == ModifierKey.COMMAND:
        return KeyboardKey.COMMAND
    return KeyboardKey.CONTROL


def extra_pause_after_character_ms(character: str, rng: RandomSource) -> float:
    if character not in _EXTRA_PAUSE_CHARACTERS:
        return 0.0

    pause_probability = 0.35 if character == " " else 0.6
    if rng.random() >= pause_probability:
        return 0.0
    return rng.uniform(150.0, 300.0)


def _sleep_ms(context: BackendExecutionContext, delay_ms: float) -> None:
    context.sleep(max(0.0, delay_ms) / 1000.0)
