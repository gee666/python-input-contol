from __future__ import annotations

import ctypes
import ctypes.util
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypeVar

from .models import BrowserContext, ModifierKey, ScreenPoint

# Virtual desktop bounds can change at runtime: monitors get plugged/unplugged,
# the user rearranges displays, scaling changes, or the first lookup fails
# transiently at startup.  Cache successful results for a short TTL so repeated
# pointer-command validation stays fast, but expire quickly enough that we
# pick up display changes.  ``None`` results are never cached – they are
# retried on the very next call so an early-startup failure does not pin us
# to "unavailable" for the whole process lifetime.
_VIRTUAL_DESKTOP_BOUNDS_TTL_SECONDS = 5.0

_T = TypeVar("_T")


class _TtlCache:
    """Tiny TTL cache for a single value produced by a zero-arg callable.

    Only successful (non-``None``) results are cached.  ``cache_clear()``
    mirrors the ``functools.lru_cache`` interface so existing tests keep
    working.
    """

    __slots__ = ("_func", "_ttl", "_lock", "_value", "_expires_at", "__wrapped__")

    def __init__(self, func: Callable[[], _T | None], ttl: float) -> None:
        self._func = func
        self._ttl = ttl
        self._lock = threading.Lock()
        self._value: _T | None = None
        self._expires_at: float = 0.0
        self.__wrapped__ = func

    def __call__(self) -> _T | None:
        now = time.monotonic()
        with self._lock:
            if self._value is not None and now < self._expires_at:
                return self._value
        result = self._func()
        with self._lock:
            if result is not None:
                self._value = result
                self._expires_at = time.monotonic() + self._ttl
            else:
                # Do not cache failures – retry on the very next call.
                self._value = None
                self._expires_at = 0.0
        return result

    def cache_clear(self) -> None:
        with self._lock:
            self._value = None
            self._expires_at = 0.0


def _ttl_cached(ttl: float) -> Callable[[Callable[[], _T | None]], _TtlCache]:
    def decorator(func: Callable[[], _T | None]) -> _TtlCache:
        return _TtlCache(func, ttl)

    return decorator

_ACTIVE_MONITOR_PATTERN = re.compile(r"^\s*\d+:\s+\S+\s+(\d+)/\d+x(\d+)/\d+([+-]\d+)([+-]\d+)")
_MACOS_CORE_GRAPHICS_LIBRARY_CANDIDATES = (
    "CoreGraphics",
    "ApplicationServices",
    "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics",
)
_MACOS_ACTIVE_DISPLAY_LIST_MAX = 32


@dataclass(frozen=True)
class VirtualDesktopBounds:
    left: float
    top: float
    right: float
    bottom: float

    def contains(self, point: ScreenPoint) -> bool:
        return self.left <= point.x <= self.right and self.top <= point.y <= self.bottom

    def clamp(self, point: ScreenPoint) -> ScreenPoint:
        return ScreenPoint(
            x=max(self.left, min(self.right, point.x)),
            y=max(self.top, min(self.bottom, point.y)),
        )


@dataclass(frozen=True)
class MacOSDisplayGeometry:
    left: float
    top: float
    width: float
    height: float
    pixel_width: int | None = None
    pixel_height: int | None = None

    def physical_bounds(self) -> VirtualDesktopBounds | None:
        if self.width <= 0 or self.height <= 0:
            return None

        x_scale = 1.0
        if self.pixel_width is not None and self.pixel_width > 0:
            x_scale = self.pixel_width / self.width

        y_scale = 1.0
        if self.pixel_height is not None and self.pixel_height > 0:
            y_scale = self.pixel_height / self.height

        left = self.left * x_scale
        top = self.top * y_scale
        return VirtualDesktopBounds(
            left=left,
            top=top,
            right=left + self.width * x_scale,
            bottom=top + self.height * y_scale,
        )


class PlatformAdapter(Protocol):
    def platform_name(self) -> str: ...
    def select_all_modifier(self) -> ModifierKey: ...
    def virtual_desktop_bounds(self) -> VirtualDesktopBounds | None: ...


@dataclass
class SystemPlatformAdapter:
    def platform_name(self) -> str:
        if sys.platform.startswith("win"):
            return "windows"
        if sys.platform == "darwin":
            return "macos"
        if sys.platform.startswith("linux"):
            return "linux"
        return "unknown"

    def select_all_modifier(self) -> ModifierKey:
        return ModifierKey.COMMAND if self.platform_name() == "macos" else ModifierKey.CONTROL

    def virtual_desktop_bounds(self) -> VirtualDesktopBounds | None:
        platform_name = self.platform_name()
        if platform_name == "windows":
            return _windows_virtual_desktop_bounds()
        if platform_name == "macos":
            return _macos_virtual_desktop_bounds()
        if platform_name == "linux":
            return _linux_virtual_desktop_bounds()
        return None


def translate_viewport_to_physical_screen(context: BrowserContext, viewport_x: float, viewport_y: float) -> ScreenPoint:
    viewport_origin_x = context.screen_x + context.browser_chrome_width / 2.0
    viewport_origin_y = context.screen_y + context.browser_chrome_height
    # Browser geometry collected from JavaScript is reported in browser/CSS units.
    # Convert the full viewport origin plus target point into physical pixels as one step
    # so HiDPI scaling does not leave a constant offset based on the window position.
    return ScreenPoint(
        x=(viewport_origin_x + viewport_x) * context.device_pixel_ratio,
        y=(viewport_origin_y + viewport_y) * context.device_pixel_ratio,
    )


def adapt_point_for_pyautogui(point: ScreenPoint, context: BrowserContext, platform_name: str) -> ScreenPoint:
    if platform_name == "macos" and context.device_pixel_ratio > 0:
        return ScreenPoint(x=point.x / context.device_pixel_ratio, y=point.y / context.device_pixel_ratio)
    return point


def restore_point_from_pyautogui(point: ScreenPoint, context: BrowserContext, platform_name: str) -> ScreenPoint:
    if platform_name == "macos" and context.device_pixel_ratio > 0:
        return ScreenPoint(x=point.x * context.device_pixel_ratio, y=point.y * context.device_pixel_ratio)
    return point


def clamp_point_to_bounds(point: ScreenPoint, bounds: VirtualDesktopBounds) -> ScreenPoint:
    return bounds.clamp(point)


@_ttl_cached(_VIRTUAL_DESKTOP_BOUNDS_TTL_SECONDS)
def _windows_virtual_desktop_bounds() -> VirtualDesktopBounds | None:
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return None

    try:
        left = float(user32.GetSystemMetrics(76))
        top = float(user32.GetSystemMetrics(77))
        width = float(user32.GetSystemMetrics(78))
        height = float(user32.GetSystemMetrics(79))
    except Exception:
        return None

    if width <= 0 or height <= 0:
        return None
    return VirtualDesktopBounds(left=left, top=top, right=left + width, bottom=top + height)


@_ttl_cached(_VIRTUAL_DESKTOP_BOUNDS_TTL_SECONDS)
def _macos_virtual_desktop_bounds() -> VirtualDesktopBounds | None:
    for display_source in (_macos_active_display_geometries_coregraphics, _macos_active_display_geometries_quartz):
        bounds = _virtual_desktop_bounds_from_display_geometries(display_source())
        if bounds is not None:
            return bounds
    return None


@_ttl_cached(_VIRTUAL_DESKTOP_BOUNDS_TTL_SECONDS)
def _linux_virtual_desktop_bounds() -> VirtualDesktopBounds | None:
    try:
        result = subprocess.run(
            ["xrandr", "--listactivemonitors"],
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    monitors: list[VirtualDesktopBounds] = []
    for line in result.stdout.splitlines():
        match = _ACTIVE_MONITOR_PATTERN.match(line)
        if match is None:
            continue
        width, height, left, top = (int(group) for group in match.groups())
        monitors.append(
            VirtualDesktopBounds(
                left=float(left),
                top=float(top),
                right=float(left + width),
                bottom=float(top + height),
            )
        )

    if not monitors:
        return None

    return VirtualDesktopBounds(
        left=min(monitor.left for monitor in monitors),
        top=min(monitor.top for monitor in monitors),
        right=max(monitor.right for monitor in monitors),
        bottom=max(monitor.bottom for monitor in monitors),
    )


def _virtual_desktop_bounds_from_display_geometries(
    displays: list[MacOSDisplayGeometry] | None,
) -> VirtualDesktopBounds | None:
    if not displays:
        return None

    physical_bounds = [display.physical_bounds() for display in displays]
    valid_bounds = [bounds for bounds in physical_bounds if bounds is not None]
    if not valid_bounds:
        return None

    return VirtualDesktopBounds(
        left=min(bounds.left for bounds in valid_bounds),
        top=min(bounds.top for bounds in valid_bounds),
        right=max(bounds.right for bounds in valid_bounds),
        bottom=max(bounds.bottom for bounds in valid_bounds),
    )


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


class _CGDirectDisplayID(ctypes.c_uint32):
    pass


class _CGDisplayCount(ctypes.c_uint32):
    pass


def _macos_active_display_geometries_coregraphics() -> list[MacOSDisplayGeometry] | None:
    core_graphics = _load_macos_core_graphics_library()
    if core_graphics is None:
        return None

    try:
        get_active_display_list = core_graphics.CGGetActiveDisplayList
        get_active_display_list.argtypes = [
            _CGDisplayCount,
            ctypes.POINTER(_CGDirectDisplayID),
            ctypes.POINTER(_CGDisplayCount),
        ]
        get_active_display_list.restype = ctypes.c_int32

        get_display_bounds = core_graphics.CGDisplayBounds
        get_display_bounds.argtypes = [_CGDirectDisplayID]
        get_display_bounds.restype = _CGRect

        get_display_pixels_wide = core_graphics.CGDisplayPixelsWide
        get_display_pixels_wide.argtypes = [_CGDirectDisplayID]
        get_display_pixels_wide.restype = ctypes.c_size_t

        get_display_pixels_high = core_graphics.CGDisplayPixelsHigh
        get_display_pixels_high.argtypes = [_CGDirectDisplayID]
        get_display_pixels_high.restype = ctypes.c_size_t
    except AttributeError:
        return None

    try:
        display_ids = (_CGDirectDisplayID * _MACOS_ACTIVE_DISPLAY_LIST_MAX)()
        display_count = _CGDisplayCount()
        error = get_active_display_list(
            _CGDisplayCount(_MACOS_ACTIVE_DISPLAY_LIST_MAX),
            display_ids,
            ctypes.byref(display_count),
        )
    except Exception:
        return None

    if error != 0:
        return None

    displays: list[MacOSDisplayGeometry] = []
    for display_id in display_ids[: int(display_count.value)]:
        try:
            bounds = get_display_bounds(display_id)
            displays.append(
                MacOSDisplayGeometry(
                    left=float(bounds.origin.x),
                    top=float(bounds.origin.y),
                    width=float(bounds.size.width),
                    height=float(bounds.size.height),
                    pixel_width=int(get_display_pixels_wide(display_id)) or None,
                    pixel_height=int(get_display_pixels_high(display_id)) or None,
                )
            )
        except Exception:
            return None

    return displays or None


def _macos_active_display_geometries_quartz() -> list[MacOSDisplayGeometry] | None:
    try:
        import Quartz  # type: ignore
    except Exception:
        return None

    try:
        error, display_ids, display_count = Quartz.CGGetActiveDisplayList(_MACOS_ACTIVE_DISPLAY_LIST_MAX, None, None)
    except Exception:
        return None

    if error != 0:
        return None

    displays: list[MacOSDisplayGeometry] = []
    for display_id in list(display_ids)[: int(display_count)]:
        try:
            bounds = Quartz.CGDisplayBounds(display_id)
            displays.append(
                MacOSDisplayGeometry(
                    left=float(bounds.origin.x),
                    top=float(bounds.origin.y),
                    width=float(bounds.size.width),
                    height=float(bounds.size.height),
                    pixel_width=int(Quartz.CGDisplayPixelsWide(display_id)) or None,
                    pixel_height=int(Quartz.CGDisplayPixelsHigh(display_id)) or None,
                )
            )
        except Exception:
            return None

    return displays or None


def _load_macos_core_graphics_library() -> Any | None:
    for candidate in _MACOS_CORE_GRAPHICS_LIBRARY_CANDIDATES:
        library_name = candidate
        if "/" not in candidate:
            library_name = ctypes.util.find_library(candidate) or candidate

        try:
            return ctypes.cdll.LoadLibrary(library_name)
        except OSError:
            continue

    return None
