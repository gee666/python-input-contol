from __future__ import annotations

from pathlib import Path

import pytest

from python_input_control.permissions import (
    MissingAccessibilityPermissionError,
    ensure_macos_accessibility_or_raise,
    macos_accessibility_guidance,
    macos_accessibility_is_trusted,
)


def test_macos_accessibility_is_trusted_returns_none_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("python_input_control.permissions.sys.platform", "linux")

    assert macos_accessibility_is_trusted() is None


def test_macos_accessibility_guidance_includes_resolved_executable_path(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    guidance = macos_accessibility_guidance(executable)

    assert str(executable.resolve()) in guidance
    assert "Accessibility" in guidance


def test_ensure_macos_accessibility_or_raise_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("python_input_control.permissions.macos_accessibility_is_trusted", lambda: False)

    with pytest.raises(MissingAccessibilityPermissionError, match="Accessibility permission is required"):
        ensure_macos_accessibility_or_raise("python-input-control")


def test_ensure_macos_accessibility_or_raise_is_noop_when_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("python_input_control.permissions.macos_accessibility_is_trusted", lambda: True)

    ensure_macos_accessibility_or_raise("python-input-control")
