from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .backends.pyautogui_mouse_backend import build_default_mouse_backend, default_mouse_backend_status
from .backends.pynput_keyboard import build_default_keyboard_backend, default_keyboard_backend_status
from .dispatch import CommandDispatcher
from .permissions import (
    MissingAccessibilityPermissionError,
    ensure_macos_accessibility_or_raise,
    macos_accessibility_guidance,
    macos_accessibility_is_trusted,
)
from .protocol import run_host
from .randomness import SeededRandom

INSTALLER_PROBE_TOKEN = "python-input-control-installer-probe=ok"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Native messaging host for browser-driven input automation.")
    parser.add_argument("--seed", help="Deterministic random seed for backend execution")
    parser.add_argument(
        "--backend-status",
        action="store_true",
        help="Print the current default backend wiring and exit",
    )
    parser.add_argument(
        "--check-permissions",
        action="store_true",
        help="Print runtime permission status and guidance, then exit",
    )
    parser.add_argument("--installer-probe", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, _unknown_args = parser.parse_known_args(argv)

    if args.installer_probe:
        print(INSTALLER_PROBE_TOKEN)
        return 0

    if args.backend_status:
        print(f"mouse_backend={default_mouse_backend_status()}")
        print(f"keyboard_backend={default_keyboard_backend_status()}")
        return 0

    if args.check_permissions:
        trusted = macos_accessibility_is_trusted()
        if trusted is None:
            print("macos_accessibility=not_applicable")
            return 0
        if trusted:
            print("macos_accessibility=trusted")
            return 0
        print("macos_accessibility=missing")
        print(macos_accessibility_guidance(sys.argv[0]))
        return 1

    try:
        ensure_macos_accessibility_or_raise(sys.argv[0])
    except MissingAccessibilityPermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    dispatcher = CommandDispatcher(
        mouse_backend=build_default_mouse_backend(),
        keyboard_backend=build_default_keyboard_backend(),
        rng=SeededRandom(args.seed),
    )
    return run_host(dispatcher)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
