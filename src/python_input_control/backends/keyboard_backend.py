from __future__ import annotations

from typing import Protocol

from ..errors import BackendUnavailableError
from ..models import PressKeyCommand, PressShortcutCommand, TypeCommand
from . import BackendExecutionContext


class KeyboardBackend(Protocol):
    def press_key(self, command: PressKeyCommand, context: BackendExecutionContext) -> None: ...
    def press_shortcut(self, command: PressShortcutCommand, context: BackendExecutionContext) -> None: ...
    def type_text(self, command: TypeCommand, context: BackendExecutionContext) -> None: ...


class UnsupportedKeyboardBackend:
    def press_key(self, command: PressKeyCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError("Keyboard backend is not configured", command.id)

    def press_shortcut(self, command: PressShortcutCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError("Keyboard backend is not configured", command.id)

    def type_text(self, command: TypeCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError("Keyboard backend is not configured", command.id)
