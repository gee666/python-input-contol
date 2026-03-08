from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias

ResponseStatus: TypeAlias = Literal["ok", "error"]
CommandName: TypeAlias = Literal[
    "mouse_move",
    "mouse_left_click",
    "mouse_right_click",
    "mouse_double_click",
    "scroll",
    "key_tab",
    "type",
    "select_all_and_delete",
]


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"


class ModifierKey(str, Enum):
    CONTROL = "control"
    COMMAND = "command"


@dataclass(frozen=True)
class BrowserContext:
    screen_x: float
    screen_y: float
    outer_height: float
    inner_height: float
    outer_width: float
    inner_width: float
    device_pixel_ratio: float
    scroll_x: float
    scroll_y: float

    @property
    def browser_chrome_height(self) -> float:
        return self.outer_height - self.inner_height

    @property
    def browser_chrome_width(self) -> float:
        return self.outer_width - self.inner_width


@dataclass(frozen=True)
class ScreenPoint:
    x: float
    y: float


@dataclass(frozen=True)
class BaseCommand:
    id: str
    context: BrowserContext


@dataclass(frozen=True)
class CoordinateCommand(BaseCommand):
    x: float
    y: float


@dataclass(frozen=True)
class MouseMoveCommand(CoordinateCommand):
    duration_ms: int | None = None


@dataclass(frozen=True)
class MouseClickCommand(CoordinateCommand):
    button: MouseButton = MouseButton.LEFT
    move_duration_ms: int | None = None
    hold_ms: int | None = None


@dataclass(frozen=True)
class MouseDoubleClickCommand(CoordinateCommand):
    move_duration_ms: int | None = None
    interval_ms: int | None = None


@dataclass(frozen=True)
class ScrollCommand(CoordinateCommand):
    delta_x: float = 0.0
    delta_y: float = 0.0
    duration_ms: int | None = None


@dataclass(frozen=True)
class KeyTabCommand(BaseCommand):
    pass


@dataclass(frozen=True)
class TypeCommand(BaseCommand):
    text: str
    wpm: float | None = None


@dataclass(frozen=True)
class SelectAllAndDeleteCommand(BaseCommand):
    pass


Command: TypeAlias = (
    MouseMoveCommand
    | MouseClickCommand
    | MouseDoubleClickCommand
    | ScrollCommand
    | KeyTabCommand
    | TypeCommand
    | SelectAllAndDeleteCommand
)


@dataclass(frozen=True)
class ResponseEnvelope:
    id: str | None
    status: ResponseStatus
    error: str | None = None

    @classmethod
    def ok(cls, command_id: str | None) -> "ResponseEnvelope":
        return cls(id=command_id, status="ok", error=None)

    @classmethod
    def error_response(cls, command_id: str | None, message: str) -> "ResponseEnvelope":
        return cls(id=command_id, status="error", error=message)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
        }
