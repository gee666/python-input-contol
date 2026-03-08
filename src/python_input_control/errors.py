from __future__ import annotations

from dataclasses import dataclass


@dataclass(eq=False)
class InputControlError(Exception):
    message: str
    command_id: str | None = None

    def __str__(self) -> str:
        return self.message


class FramingError(InputControlError):
    """Raised when the native messaging byte stream cannot be decoded safely."""


class RecoverableFramingError(FramingError):
    """Raised when a bad frame can be discarded and the host may continue serving."""


class ProtocolDecodeError(InputControlError):
    """Raised when a framed payload is not valid UTF-8 JSON."""


class ValidationError(InputControlError):
    """Raised when an incoming message violates the command schema."""


class UnknownCommandError(ValidationError):
    """Raised when the command field is not one of the supported PRD commands."""


class CoordinateOutOfBoundsError(ValidationError):
    """Raised when translated screen coordinates are outside the known desktop bounds."""


class DesktopBoundsUnavailableError(ValidationError):
    """Raised when pointer commands cannot be validated because desktop bounds discovery failed."""


class BackendUnavailableError(InputControlError):
    """Raised when no concrete backend implementation has been wired in."""


class CommandExecutionError(InputControlError):
    """Raised when a backend fails while executing a validated command."""
