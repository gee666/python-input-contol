from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest.mock import patch

import install as repo_install


def test_build_pip_command_includes_expected_extras() -> None:
    assert repo_install.build_pip_command("python", editable=False, with_standalone=False) == [
        "python",
        "-m",
        "pip",
        "install",
        ".[backends]",
    ]
    assert repo_install.build_pip_command("python", editable=True, with_standalone=True) == [
        "python",
        "-m",
        "pip",
        "install",
        "-e",
        ".[backends,standalone]",
    ]


def test_build_manifest_command_forwards_optional_flags() -> None:
    args = argparse.Namespace(
        extension_id=["abc", "def"],
        host_name="com.example.host",
        host_path="/tmp/python-input-control",
        manifest_path="/tmp/com.example.host.json",
        platform="linux",
        dry_run=True,
    )

    command = repo_install.build_manifest_command(args, "python")

    assert command == [
        "python",
        "-m",
        "python_input_control.installer",
        "install",
        "--extension-id",
        "abc",
        "--extension-id",
        "def",
        "--host-name",
        "com.example.host",
        "--host-path",
        "/tmp/python-input-control",
        "--manifest-path",
        "/tmp/com.example.host.json",
        "--platform",
        "linux",
        "--dry-run",
    ]


def test_build_installer_environment_prepends_src_to_pythonpath() -> None:
    repo_root = Path("/workspace/project")

    with patch.dict(os.environ, {"PYTHONPATH": "/existing/path"}, clear=False):
        environment = repo_install.build_installer_environment(repo_root)

    assert environment["PYTHONPATH"] == os.pathsep.join(
        [str((repo_root / "src").resolve()), "/existing/path"]
    )


def test_main_dry_run_prints_planned_commands(capsys) -> None:
    exit_code = repo_install.main(["--extension-id", "abc123", "--dry-run"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "PIP COMMAND:" in captured.out
    assert "MANIFEST COMMAND:" in captured.out
    assert "--extension-id abc123" in captured.out


def test_main_executes_pip_and_manifest_subprocesses() -> None:
    recorded_calls: list[tuple[list[str], Path, dict[str, str] | None]] = []

    def fake_run(command, *, cwd, check, env=None):
        assert check is True
        recorded_calls.append((command, cwd, env))
        return None

    with patch("install.subprocess.run", side_effect=fake_run):
        exit_code = repo_install.main(["--extension-id", "abc123", "--skip-pip-install"])

    assert exit_code == 0
    assert len(recorded_calls) == 1
    command, cwd, env = recorded_calls[0]
    assert command[:4] == [repo_install.sys.executable, "-m", "python_input_control.installer", "install"]
    assert cwd == Path(repo_install.__file__).resolve().parent
    assert env is not None
    assert str((cwd / "src").resolve()) in env["PYTHONPATH"].split(os.pathsep)
