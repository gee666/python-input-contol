from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from .errors import InputControlError


class MissingAccessibilityPermissionError(InputControlError):
    """Raised when macOS Accessibility permission is required but not granted."""


def macos_accessibility_is_trusted() -> bool | None:
    if sys.platform != "darwin":
        return None

    framework_path = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
    try:
        application_services = ctypes.cdll.LoadLibrary(framework_path)
        function = application_services.AXIsProcessTrusted
        function.restype = ctypes.c_bool
        function.argtypes = []
        return bool(function())
    except Exception:
        return None


def macos_accessibility_guidance(executable_path: str | Path) -> str:
    resolved_path = Path(executable_path).expanduser().resolve()
    return (
        "macOS Accessibility permission is required for real mouse and keyboard control.\n"
        "Open System Settings -> Privacy & Security -> Accessibility and allow the host executable.\n"
        f"Host executable: {resolved_path}\n"
        "If the host is packaged as a standalone app or moved after installation, remove any stale entry and add the new path."
    )


def ensure_macos_accessibility_or_raise(executable_path: str | Path) -> None:
    trusted = macos_accessibility_is_trusted()
    if trusted is False:
        raise MissingAccessibilityPermissionError(macos_accessibility_guidance(executable_path))
