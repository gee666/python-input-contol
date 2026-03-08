from __future__ import annotations

from typing import Protocol

from ..errors import BackendUnavailableError
from ..models import MouseClickCommand, MouseMoveCommand, ScreenPoint, ScrollCommand
from . import BackendExecutionContext


class MouseBackend(Protocol):
    def move(self, command: MouseMoveCommand, target: ScreenPoint, context: BackendExecutionContext) -> None: ...
    def click(self, command: MouseClickCommand, target: ScreenPoint, context: BackendExecutionContext) -> None: ...
    def scroll(self, command: ScrollCommand, target: ScreenPoint, context: BackendExecutionContext) -> None: ...


class UnsupportedMouseBackend:
    def move(self, command: MouseMoveCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError("Mouse backend is not configured", command.id)

    def click(self, command: MouseClickCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError("Mouse backend is not configured", command.id)

    def scroll(self, command: ScrollCommand, target: ScreenPoint, context: BackendExecutionContext) -> None:
        raise BackendUnavailableError("Mouse backend is not configured", command.id)
