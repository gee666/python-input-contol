from __future__ import annotations

from typing import Protocol

from ..errors import BackendUnavailableError
from ..models import KeyEscapeCommand, KeyTabCommand, ModifierKey, SelectAllAndDeleteCommand, TypeCommand
from . import BackendExecutionContext


class KeyboardBackend(Protocol):
    def press_tab(self, command: KeyTabCommand, context: BackendExecutionContext) -> None: ...
    def press_escape(self, command: KeyEscapeCommand, context: BackendExecutionContext) -> None: ...
    def type_text(self, command: TypeCommand, context: BackendExecutionContext) -> None: ...
    def select_all_and_delete(
        self,
        command: SelectAllAndDeleteCommand,
        modifier: ModifierKey,
        context: BackendExecutionContext,
    ) -> None: ...


class UnsupportedKeyboardBackend:
    def press_tab(self, command: KeyTabCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError("Keyboard backend is not configured", command.id)

    def press_escape(self, command: KeyEscapeCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError("Keyboard backend is not configured", command.id)

    def type_text(self, command: TypeCommand, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError("Keyboard backend is not configured", command.id)

    def select_all_and_delete(
        self,
        command: SelectAllAndDeleteCommand,
        modifier: ModifierKey,
        context: BackendExecutionContext,
    ) -> None:
        raise BackendUnavailableError("Keyboard backend is not configured", command.id)
