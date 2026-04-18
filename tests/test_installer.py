from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from python_input_control.installer import (
    INSTALLER_PROBE_TOKEN,
    HostProbeResult,
    build_installation_plan,
    build_manifest,
    default_manifest_path,
    extension_id_to_origin,
    install,
    normalize_extension_id,
    resolve_host_executable,
    uninstall,
    verify_installation,
)


def _successful_probe(_path: Path) -> HostProbeResult:
    return HostProbeResult(returncode=0, stdout=f"{INSTALLER_PROBE_TOKEN}\n", stderr="")


def test_normalize_extension_id_accepts_raw_and_prefixed_values() -> None:
    assert normalize_extension_id("abcdefghijklmnop") == "abcdefghijklmnop"
    assert normalize_extension_id("chrome-extension://abcdefghijklmnop/") == "abcdefghijklmnop"
    assert extension_id_to_origin("abcdefghijklmnop") == "chrome-extension://abcdefghijklmnop/"


@pytest.mark.parametrize(
    "value",
    ["", "chrome-extension://", "bad/value", "bad:value", "bad value", "ABCDEF"],
)
def test_normalize_extension_id_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_extension_id(value)


def test_build_manifest_deduplicates_origins_and_resolves_executable(tmp_path: Path) -> None:
    executable = tmp_path / "python-input-control"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")

    manifest = build_manifest(
        executable,
        ["abcdefghijklmnop", "chrome-extension://abcdefghijklmnop/", "qrstuvwxyzabcdef"],
        host_name="com.example.host",
    )

    assert manifest.name == "com.example.host"
    assert manifest.path == str(executable.resolve())
    assert manifest.allowed_origins == (
        "chrome-extension://abcdefghijklmnop/",
        "chrome-extension://qrstuvwxyzabcdef/",
    )


def test_default_manifest_path_matches_prd_locations(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    local_appdata = tmp_path / "local-appdata"

    assert default_manifest_path("com.example.host", platform_name="linux", home_dir=home_dir) == (
        home_dir / ".config" / "google-chrome" / "NativeMessagingHosts" / "com.example.host.json"
    )
    assert default_manifest_path("com.example.host", platform_name="macos", home_dir=home_dir) == (
        home_dir
        / "Library"
        / "Application Support"
        / "Google"
        / "Chrome"
        / "NativeMessagingHosts"
        / "com.example.host.json"
    )
    assert default_manifest_path(
        "com.example.host",
        platform_name="windows",
        home_dir=home_dir,
        local_appdata_dir=local_appdata,
    ) == (
        local_appdata / "python-input-control" / "NativeMessagingHosts" / "com.example.host.json"
    )


def test_linux_install_verify_and_uninstall_round_trip(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    plan = build_installation_plan(
        ["abcdefghijklmnop"],
        host_name="com.example.host",
        host_path=executable,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )

    actions = install(plan)

    assert actions == [f"Write manifest to {plan.manifest_path}"]
    assert plan.manifest_path.exists()
    assert verify_installation(plan, probe_runner=_successful_probe) == []

    removal_actions = uninstall(
        host_name="com.example.host",
        platform_name="linux",
        home_dir=tmp_path / "home",
    )

    assert removal_actions == [f"Remove manifest file {plan.manifest_path}"]
    assert not plan.manifest_path.exists()


def test_windows_installation_plan_uses_local_appdata_and_registry(tmp_path: Path) -> None:
    executable = tmp_path / "python-input-control.exe"
    executable.write_text("binary", encoding="utf-8")
    home_dir = tmp_path / "home"
    local_appdata = tmp_path / "LocalAppData"

    plan = build_installation_plan(
        ["abcdefghijklmnop"],
        host_name="com.example.host",
        host_path=executable,
        platform_name="windows",
        home_dir=home_dir,
        local_appdata_dir=local_appdata,
    )

    assert plan.manifest_path == (
        local_appdata / "python-input-control" / "NativeMessagingHosts" / "com.example.host.json"
    ).resolve()
    assert plan.registry_path == r"Software\Google\Chrome\NativeMessagingHosts\com.example.host"


def test_resolve_host_executable_rejects_directories(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_host_executable(tmp_path)


def test_verify_installation_treats_allowed_origins_as_order_insensitive(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    plan = build_installation_plan(
        ["abcdefghijklmnop", "qrstuvwxyzabcdef"],
        host_name="com.example.host",
        host_path=executable,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )
    install(plan)

    plan.manifest_path.write_text(
        json.dumps(
            {
                "name": "com.example.host",
                "path": str(executable.resolve()),
                "type": "stdio",
                "allowed_origins": [
                    "chrome-extension://qrstuvwxyzabcdef/",
                    "chrome-extension://abcdefghijklmnop/",
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert verify_installation(plan, probe_runner=_successful_probe) == []


def test_verify_installation_reports_manifest_content_mismatch(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    plan = build_installation_plan(
        ["abcdefghijklmnop"],
        host_name="com.example.host",
        host_path=executable,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )
    install(plan)

    plan.manifest_path.write_text(
        '{"name": "wrong.host", "path": "/tmp/wrong", "type": "stdio", "allowed_origins": []}\n',
        encoding="utf-8",
    )

    issues = verify_installation(plan, probe_runner=_successful_probe)

    assert any("Manifest name mismatch" in issue for issue in issues)
    assert any("Manifest path mismatch" in issue for issue in issues)
    # allowed_origins=[] is now valid-but-unused; the mismatch line is an
    # "allowed_origins mismatch" because the plan expected one origin.
    assert any("allowed_origins mismatch" in issue for issue in issues)


def test_verify_installation_rejects_non_discoverable_manifest_path_on_linux(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    manifest_path = tmp_path / "other" / "com.example.host.json"

    plan = build_installation_plan(
        ["abcdefghijklmnop"],
        host_name="com.example.host",
        host_path=executable,
        manifest_path=manifest_path,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )
    install(plan)

    issues = verify_installation(plan, probe_runner=_successful_probe)

    assert any("Manifest is not installed at Chrome's discoverable path" in issue for issue in issues)
    assert any(str(plan.discoverable_manifest_path) in issue for issue in issues)
    assert any(str(plan.manifest_path) in issue for issue in issues)


def test_verify_installation_reports_non_executable_posix_host(tmp_path: Path) -> None:
    if sys.platform.startswith("win"):
        pytest.skip("Windows does not preserve POSIX executable bits for these temp files")

    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o644)

    plan = build_installation_plan(
        ["abcdefghijklmnop"],
        host_name="com.example.host",
        host_path=executable,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )
    install(plan)

    issues = verify_installation(plan, probe_runner=_successful_probe)

    assert any("Host executable is not executable" in issue for issue in issues)


def test_verify_installation_reports_manifest_root_not_object(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    plan = build_installation_plan(
        ["abcdefghijklmnop"],
        host_name="com.example.host",
        host_path=executable,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )
    install(plan)
    plan.manifest_path.write_text("[]\n", encoding="utf-8")

    issues = verify_installation(plan, probe_runner=_successful_probe)

    assert issues == ["Manifest root must be a JSON object, found list"]


@pytest.mark.parametrize(
    ("manifest_payload", "expected_issue"),
    [
        (
            {"name": "com.example.host", "path": "/tmp/host", "type": "stdio", "allowed_origins": "bad"},
            "Manifest allowed_origins must be a list",
        ),
        (
            {
                "name": "com.example.host",
                "path": "/tmp/host",
                "type": "stdio",
                "allowed_origins": [123],
            },
            "Manifest allowed_origins[0] must be a string",
        ),
        (
            {
                "name": "com.example.host",
                "path": "/tmp/host",
                "type": "stdio",
                "allowed_origins": ["https://example.com/"],
            },
            "Manifest allowed_origins[0] is invalid",
        ),
        (
            {
                "name": "com.example.host",
                "path": "/tmp/host",
                "type": "stdio",
                "allowed_origins": [
                    "chrome-extension://abcdefghijklmnop/",
                    "chrome-extension://abcdefghijklmnop/",
                ],
            },
            "Manifest allowed_origins must not contain duplicates",
        ),
    ],
)
def test_verify_installation_reports_invalid_allowed_origins_shape(
    tmp_path: Path,
    manifest_payload: dict[str, object],
    expected_issue: str,
) -> None:
    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    plan = build_installation_plan(
        ["abcdefghijklmnop"],
        host_name="com.example.host",
        host_path=executable,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )
    install(plan)
    plan.manifest_path.write_text(json.dumps(manifest_payload) + "\n", encoding="utf-8")

    issues = verify_installation(plan, probe_runner=_successful_probe)

    assert any(expected_issue in issue for issue in issues)


def test_verify_installation_reports_probe_failure(tmp_path: Path) -> None:
    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    plan = build_installation_plan(
        ["abcdefghijklmnop"],
        host_name="com.example.host",
        host_path=executable,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )
    install(plan)

    def failing_probe(_path: Path) -> HostProbeResult:
        return HostProbeResult(returncode=2, stdout="", stderr="boom")

    issues = verify_installation(plan, probe_runner=failing_probe)

    assert any("Host executable probe failed with exit code 2: boom" in issue for issue in issues)
