from __future__ import annotations

import io
from unittest.mock import patch, sentinel

from python_input_control.cli import main
from python_input_control.permissions import MissingAccessibilityPermissionError


def test_main_exits_with_stderr_guidance_when_accessibility_is_missing() -> None:
    stderr = io.StringIO()

    with patch(
        "python_input_control.cli.ensure_macos_accessibility_or_raise",
        side_effect=MissingAccessibilityPermissionError("Grant accessibility access."),
    ), patch("sys.stderr", stderr):
        exit_code = main([])

    assert exit_code == 1
    assert stderr.getvalue() == "Grant accessibility access.\n"


def test_main_runs_host_when_permissions_are_available() -> None:
    with patch("python_input_control.cli.ensure_macos_accessibility_or_raise") as ensure_permissions, patch(
        "python_input_control.cli.build_default_mouse_backend",
        return_value=sentinel.mouse_backend,
    ), patch(
        "python_input_control.cli.build_default_keyboard_backend",
        return_value=sentinel.keyboard_backend,
    ), patch(
        "python_input_control.cli.CommandDispatcher",
        return_value=sentinel.dispatcher,
    ) as dispatcher_class, patch("python_input_control.cli.run_host", return_value=7) as run_host:
        exit_code = main(["--seed", "seed-123"])

    assert exit_code == 7
    ensure_permissions.assert_called_once()
    dispatcher_class.assert_called_once()
    _, kwargs = dispatcher_class.call_args
    assert kwargs["mouse_backend"] is sentinel.mouse_backend
    assert kwargs["keyboard_backend"] is sentinel.keyboard_backend
    run_host.assert_called_once_with(sentinel.dispatcher)


def test_main_ignores_unknown_native_host_launcher_arguments() -> None:
    with patch("python_input_control.cli.ensure_macos_accessibility_or_raise"), patch(
        "python_input_control.cli.build_default_mouse_backend",
        return_value=sentinel.mouse_backend,
    ), patch(
        "python_input_control.cli.build_default_keyboard_backend",
        return_value=sentinel.keyboard_backend,
    ), patch(
        "python_input_control.cli.CommandDispatcher",
        return_value=sentinel.dispatcher,
    ), patch("python_input_control.cli.run_host", return_value=0) as run_host:
        exit_code = main(["chrome-extension://abcdefghijklmnop/", "--parent-window=0"])

    assert exit_code == 0
    run_host.assert_called_once_with(sentinel.dispatcher)


def test_main_prints_backend_status_and_exits(capsys) -> None:
    with patch("python_input_control.cli.default_mouse_backend_status", return_value="pyautogui"), patch(
        "python_input_control.cli.default_keyboard_backend_status",
        return_value="pynput",
    ):
        exit_code = main(["--backend-status"])

    output = capsys.readouterr().out

    assert exit_code == 0
    assert "mouse_backend=pyautogui" in output
    assert "keyboard_backend=pynput" in output


def test_main_installer_probe_exits_before_runtime_checks(capsys) -> None:
    with patch("python_input_control.cli.ensure_macos_accessibility_or_raise") as ensure_permissions:
        exit_code = main(["--installer-probe"])

    output = capsys.readouterr().out

    assert exit_code == 0
    assert output.strip() == "python-input-control-installer-probe=ok"
    ensure_permissions.assert_not_called()


def test_main_check_permissions_reports_missing_accessibility(capsys) -> None:
    with patch("python_input_control.cli.macos_accessibility_is_trusted", return_value=False), patch(
        "python_input_control.cli.macos_accessibility_guidance",
        return_value="Grant access",
    ):
        exit_code = main(["--check-permissions"])

    output = capsys.readouterr().out

    assert exit_code == 1
    assert "macos_accessibility=missing" in output
    assert "Grant access" in output
