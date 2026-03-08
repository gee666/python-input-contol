from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..platform import PlatformAdapter
from ..randomness import RandomSource

SleepFunction = Callable[[float], None]


@dataclass(frozen=True)
class BackendExecutionContext:
    platform: PlatformAdapter
    rng: RandomSource
    sleep: SleepFunction


from .keyboard_backend import KeyboardBackend, UnsupportedKeyboardBackend
from .mouse_backend import MouseBackend, UnsupportedMouseBackend
from .pyautogui_mouse_backend import (
    PyAutoGuiMouseBackend,
    UnavailablePyAutoGuiMouseBackend,
    build_default_mouse_backend,
    default_mouse_backend_status,
)
from .pynput_keyboard import (
    PynputKeyboardBackend,
    UnavailablePynputKeyboardBackend,
    build_default_keyboard_backend,
    default_keyboard_backend_status,
)

__all__ = [
    "BackendExecutionContext",
    "KeyboardBackend",
    "MouseBackend",
    "PyAutoGuiMouseBackend",
    "PynputKeyboardBackend",
    "SleepFunction",
    "UnavailablePyAutoGuiMouseBackend",
    "UnavailablePynputKeyboardBackend",
    "UnsupportedKeyboardBackend",
    "UnsupportedMouseBackend",
    "build_default_keyboard_backend",
    "build_default_mouse_backend",
    "default_keyboard_backend_status",
    "default_mouse_backend_status",
]
