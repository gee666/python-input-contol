from __future__ import annotations

import json
from pathlib import Path

from python_input_control.installer import HostProbeResult, INSTALLER_PROBE_TOKEN, main


def _successful_probe(_path: Path) -> HostProbeResult:
    return HostProbeResult(returncode=0, stdout=f"{INSTALLER_PROBE_TOKEN}\n", stderr="")


def test_installer_main_install_verify_and_uninstall_round_trip(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("python_input_control.installer._run_host_executable_probe", _successful_probe)
    monkeypatch.setenv("HOME", str(tmp_path))

    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    manifest_path = tmp_path / ".config" / "google-chrome" / "NativeMessagingHosts" / "com.example.host.json"

    install_exit_code = main(
        [
            "install",
            "--extension-id",
            "abcdefghijklmnop",
            "--host-name",
            "com.example.host",
            "--host-path",
            str(executable),
            "--manifest-path",
            str(manifest_path),
            "--platform",
            "linux",
        ]
    )
    install_output = capsys.readouterr().out

    assert install_exit_code == 0
    assert manifest_path.exists()
    assert "Verification succeeded." in install_output

    verify_exit_code = main(
        [
            "verify",
            "--extension-id",
            "abcdefghijklmnop",
            "--host-name",
            "com.example.host",
            "--host-path",
            str(executable),
            "--manifest-path",
            str(manifest_path),
            "--platform",
            "linux",
        ]
    )
    verify_output = capsys.readouterr().out

    assert verify_exit_code == 0
    assert "Verification succeeded." in verify_output

    uninstall_exit_code = main(
        [
            "uninstall",
            "--host-name",
            "com.example.host",
            "--manifest-path",
            str(manifest_path),
            "--platform",
            "linux",
        ]
    )
    uninstall_output = capsys.readouterr().out

    assert uninstall_exit_code == 0
    assert not manifest_path.exists()
    assert f"Remove manifest file {manifest_path.resolve()}" in uninstall_output


def test_installer_main_verify_fails_for_non_discoverable_custom_manifest_path(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setattr("python_input_control.installer._run_host_executable_probe", _successful_probe)
    monkeypatch.setenv("HOME", str(tmp_path))

    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    manifest_path = tmp_path / "manifest" / "com.example.host.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "name": "com.example.host",
                "path": str(executable.resolve()),
                "type": "stdio",
                "allowed_origins": ["chrome-extension://abcdefghijklmnop/"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "verify",
            "--extension-id",
            "abcdefghijklmnop",
            "--host-name",
            "com.example.host",
            "--host-path",
            str(executable),
            "--manifest-path",
            str(manifest_path),
            "--platform",
            "linux",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "VERIFY FAILED:" in output
    assert "Manifest is not installed at Chrome's discoverable path" in output


def test_installer_main_uninstall_dry_run(tmp_path: Path, capsys) -> None:
    manifest_path = tmp_path / "manifest" / "com.example.host.json"

    exit_code = main(
        [
            "uninstall",
            "--host-name",
            "com.example.host",
            "--manifest-path",
            str(manifest_path),
            "--platform",
            "linux",
            "--dry-run",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "DRY RUN: Remove manifest file" in output
    assert not manifest_path.exists()
