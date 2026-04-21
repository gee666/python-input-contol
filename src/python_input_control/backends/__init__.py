from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Callable

from ..errors import CommandCancelledError
from ..platform import PlatformAdapter
from ..randomness import RandomSource

SleepFunction = Callable[[float], None]


@dataclass(frozen=True)
class BackendExecutionContext:
    platform: PlatformAdapter
    rng: RandomSource
    sleep: SleepFunction
    # Optional cancellation event.  When set, interruptible sleeps raise
    # CommandCancelledError so the current command stops between keystrokes /
    # motion steps without waiting for the full duration to elapse.
    cancel_event: Event | None = None

    def interruptible_sleep(self, seconds: float) -> None:
        """Sleep for *seconds*, raising :class:`CommandCancelledError` early
        if ``cancel_event`` fires.

        Timing is driven by :attr:`sleep` (so tests can swap in a recorder /
        fake clock) and cancel-awareness is achieved by polling
        :attr:`cancel_event` between short chunks.  This keeps cancellation
        semantics consistent across ``pause`` and the keyboard / mouse
        backends while remaining deterministic under test-only ``sleep``
        injections.
        """
        duration = max(0.0, seconds)
        ev = self.cancel_event
        if ev is None:
            self.sleep(duration)
            return
        if ev.is_set():
            raise CommandCancelledError("Command cancelled", None)
        # 100 ms poll interval is ``promptly enough`` for user-facing cancel
        # while keeping short pauses (< poll) identical to a single sleep
        # call so the existing sequence/recorder tests stay deterministic.
        poll_seconds = 0.1
        remaining = duration
        while remaining > 0.0:
            chunk = min(poll_seconds, remaining)
            self.sleep(chunk)
            if ev.is_set():
                raise CommandCancelledError("Command cancelled", None)
            remaining -= chunk


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
