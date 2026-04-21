from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from python_input_control.installer import (
    HostProbeResult,
    INSTALLER_PROBE_TOKEN,
    allow_command,
    build_installation_plan,
    default_registry_path,
    disallow_command,
    install,
    list_allowed_command,
    main,
    mutate_allowed_origins,
    verify_installation,
)


def _successful_probe(_path: Path) -> HostProbeResult:
    return HostProbeResult(returncode=0, stdout=f"{INSTALLER_PROBE_TOKEN}\n", stderr="")


def _make_installed_plan(tmp_path: Path, extension_ids=()) -> tuple[Path, Path]:
    """Install a manifest on Linux under ``tmp_path`` and return (manifest_path, executable)."""
    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    plan = build_installation_plan(
        list(extension_ids),
        host_name="com.example.host",
        host_path=executable,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )
    install(plan)
    return plan.manifest_path, executable


# ---------------------------------------------------------------------------
# mutate_allowed_origins: pure-function tests
# ---------------------------------------------------------------------------


def test_mutate_allowed_origins_add_only() -> None:
    manifest = {"allowed_origins": []}
    new_manifest, added, skipped = mutate_allowed_origins(
        manifest, add=["abcdefghijklmnop", "qrstuvwxyzabcdef"]
    )
    assert added == ["abcdefghijklmnop", "qrstuvwxyzabcdef"]
    assert skipped == []
    assert new_manifest["allowed_origins"] == [
        "chrome-extension://abcdefghijklmnop/",
        "chrome-extension://qrstuvwxyzabcdef/",
    ]


def test_mutate_allowed_origins_remove_only() -> None:
    manifest = {
        "allowed_origins": [
            "chrome-extension://abcdefghijklmnop/",
            "chrome-extension://qrstuvwxyzabcdef/",
        ]
    }
    new_manifest, affected, skipped = mutate_allowed_origins(
        manifest, remove=["abcdefghijklmnop", "notpresentxxxxxx"]
    )
    assert affected == ["abcdefghijklmnop"]
    assert skipped == ["notpresentxxxxxx"]
    assert new_manifest["allowed_origins"] == ["chrome-extension://qrstuvwxyzabcdef/"]


def test_mutate_allowed_origins_add_and_remove() -> None:
    manifest = {"allowed_origins": ["chrome-extension://abcdefghijklmnop/"]}
    new_manifest, affected, skipped = mutate_allowed_origins(
        manifest, add=["qrstuvwxyzabcdef"], remove=["abcdefghijklmnop"]
    )
    assert sorted(affected) == ["abcdefghijklmnop", "qrstuvwxyzabcdef"]
    assert skipped == []
    assert new_manifest["allowed_origins"] == ["chrome-extension://qrstuvwxyzabcdef/"]


def test_mutate_allowed_origins_deduplicates_and_preserves_order() -> None:
    manifest = {
        "allowed_origins": [
            "chrome-extension://abcdefghijklmnop/",
            "chrome-extension://qrstuvwxyzabcdef/",
        ]
    }
    new_manifest, added, skipped = mutate_allowed_origins(
        manifest, add=["abcdefghijklmnop", "newwwwwwwwwwwwww"]
    )
    assert added == ["newwwwwwwwwwwwww"]
    assert skipped == ["abcdefghijklmnop"]
    assert new_manifest["allowed_origins"] == [
        "chrome-extension://abcdefghijklmnop/",
        "chrome-extension://qrstuvwxyzabcdef/",
        "chrome-extension://newwwwwwwwwwwwww/",
    ]


def test_mutate_allowed_origins_accepts_prefixed_ids() -> None:
    manifest = {"allowed_origins": []}
    new_manifest, added, _ = mutate_allowed_origins(
        manifest,
        add=[
            "abcdefghijklmnop",
            "chrome-extension://qrstuvwxyzabcdef/",
            "chrome-extension://mmmmmmmmmmmmmmmm",
        ],
    )
    assert added == ["abcdefghijklmnop", "qrstuvwxyzabcdef", "mmmmmmmmmmmmmmmm"]
    assert new_manifest["allowed_origins"] == [
        "chrome-extension://abcdefghijklmnop/",
        "chrome-extension://qrstuvwxyzabcdef/",
        "chrome-extension://mmmmmmmmmmmmmmmm/",
    ]


# ---------------------------------------------------------------------------
# install without --extension-id
# ---------------------------------------------------------------------------


def test_install_without_extension_id_produces_empty_allowed_origins(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("python_input_control.installer._run_host_executable_probe", _successful_probe)
    monkeypatch.setenv("HOME", str(tmp_path))

    executable = tmp_path / "bin" / "python-input-control"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    manifest_path = (
        tmp_path / ".config" / "google-chrome" / "NativeMessagingHosts" / "com.example.host.json"
    )

    exit_code = main(
        [
            "install",
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

    assert exit_code == 0
    assert manifest_path.exists()
    assert "Verification succeeded." in output
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == []


# ---------------------------------------------------------------------------
# allow / disallow / list-allowed: CLI flow
# ---------------------------------------------------------------------------


def test_allow_adds_multiple_ids_in_insertion_order(tmp_path: Path, capsys) -> None:
    manifest_path, _ = _make_installed_plan(tmp_path, extension_ids=["abcdefghijklmnop"])

    exit_code = allow_command(
        extension_ids=["qrstuvwxyzabcdef", "mmmmmmmmmmmmmmmm"],
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
    )
    assert exit_code == 0
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == [
        "chrome-extension://abcdefghijklmnop/",
        "chrome-extension://qrstuvwxyzabcdef/",
        "chrome-extension://mmmmmmmmmmmmmmmm/",
    ]
    out = capsys.readouterr().out
    assert "Added:" in out
    assert "qrstuvwxyzabcdef" in out


def test_allow_reports_already_present_without_duplicating(tmp_path: Path, capsys) -> None:
    manifest_path, _ = _make_installed_plan(tmp_path, extension_ids=["abcdefghijklmnop"])

    exit_code = allow_command(
        extension_ids=["abcdefghijklmnop"],
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
    )
    assert exit_code == 0
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == ["chrome-extension://abcdefghijklmnop/"]
    out = capsys.readouterr().out
    assert "Already present: abcdefghijklmnop" in out


def test_allow_normalises_raw_prefixed_and_trailing_slash(tmp_path: Path) -> None:
    manifest_path, _ = _make_installed_plan(tmp_path, extension_ids=[])

    allow_command(
        extension_ids=["abcdefghijklmnop"],
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
    )
    # same ID in three different shapes — none should add a new entry
    exit_code = allow_command(
        extension_ids=[
            "abcdefghijklmnop",
            "chrome-extension://abcdefghijklmnop/",
            "chrome-extension://abcdefghijklmnop",
        ],
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
    )
    assert exit_code == 0
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == ["chrome-extension://abcdefghijklmnop/"]


def test_disallow_removes_id_and_reports_not_found(tmp_path: Path, capsys) -> None:
    manifest_path, _ = _make_installed_plan(
        tmp_path, extension_ids=["abcdefghijklmnop", "qrstuvwxyzabcdef"]
    )

    exit_code = disallow_command(
        extension_ids=["abcdefghijklmnop", "notpresentxxxxxx"],
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
    )
    assert exit_code == 0
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == ["chrome-extension://qrstuvwxyzabcdef/"]
    out = capsys.readouterr().out
    assert "Removed:" in out
    assert "Not present: notpresentxxxxxx" in out


def test_disallow_down_to_empty_keeps_manifest_valid(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("python_input_control.installer._run_host_executable_probe", _successful_probe)

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

    exit_code = disallow_command(
        extension_ids=["abcdefghijklmnop"],
        host_name="com.example.host",
        manifest_path=plan.manifest_path,
        platform_name="linux",
    )
    assert exit_code == 0
    data = json.loads(plan.manifest_path.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == []
    # Rebuild a plan that expects zero origins so verify passes.
    empty_plan = build_installation_plan(
        [],
        host_name="com.example.host",
        host_path=executable,
        platform_name="linux",
        home_dir=tmp_path / "home",
    )
    assert verify_installation(empty_plan, probe_runner=_successful_probe) == []


def test_list_allowed_prints_raw_ids_one_per_line(tmp_path: Path, capsys) -> None:
    manifest_path, _ = _make_installed_plan(
        tmp_path, extension_ids=["abcdefghijklmnop", "qrstuvwxyzabcdef"]
    )
    exit_code = list_allowed_command(
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.splitlines() == ["abcdefghijklmnop", "qrstuvwxyzabcdef"]


def test_list_allowed_json_output(tmp_path: Path, capsys) -> None:
    manifest_path, _ = _make_installed_plan(tmp_path, extension_ids=["abcdefghijklmnop"])
    exit_code = list_allowed_command(
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
        as_json=True,
    )
    out = capsys.readouterr().out.strip()
    assert exit_code == 0
    payload = json.loads(out)
    assert payload["host_name"] == "com.example.host"
    assert payload["extension_ids"] == ["abcdefghijklmnop"]
    assert Path(payload["manifest_path"]) == manifest_path.resolve()


def test_list_allowed_empty_is_zero_exit_and_message_on_stderr(tmp_path: Path, capsys) -> None:
    manifest_path, _ = _make_installed_plan(tmp_path, extension_ids=[])
    exit_code = list_allowed_command(
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "No extension IDs" in captured.err


@pytest.mark.parametrize(
    "command_fn, kwargs",
    [
        (allow_command, {"extension_ids": ["abcdefghijklmnop"]}),
        (disallow_command, {"extension_ids": ["abcdefghijklmnop"]}),
        (list_allowed_command, {}),
    ],
)
def test_commands_error_cleanly_when_manifest_missing(
    tmp_path: Path, capsys, command_fn, kwargs
) -> None:
    manifest_path = tmp_path / "no" / "such" / "manifest.json"
    exit_code = command_fn(
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
        **kwargs,
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Manifest not found" in captured.err
    assert "install" in captured.err


def test_allow_dry_run_does_not_write(tmp_path: Path, capsys) -> None:
    manifest_path, _ = _make_installed_plan(tmp_path, extension_ids=["abcdefghijklmnop"])
    before = manifest_path.read_text(encoding="utf-8")

    exit_code = allow_command(
        extension_ids=["qrstuvwxyzabcdef"],
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
        dry_run=True,
    )
    assert exit_code == 0
    assert manifest_path.read_text(encoding="utf-8") == before
    assert "DRY RUN: Would add qrstuvwxyzabcdef" in capsys.readouterr().out


def test_disallow_dry_run_does_not_write(tmp_path: Path, capsys) -> None:
    manifest_path, _ = _make_installed_plan(
        tmp_path, extension_ids=["abcdefghijklmnop"]
    )
    before = manifest_path.read_text(encoding="utf-8")

    exit_code = disallow_command(
        extension_ids=["abcdefghijklmnop"],
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="linux",
        dry_run=True,
    )
    assert exit_code == 0
    assert manifest_path.read_text(encoding="utf-8") == before
    assert "DRY RUN: Would remove abcdefghijklmnop" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main() integration paths
# ---------------------------------------------------------------------------


def test_main_allow_disallow_list_roundtrip(tmp_path: Path, capsys) -> None:
    manifest_path, _ = _make_installed_plan(tmp_path, extension_ids=[])

    assert (
        main(
            [
                "allow",
                "abcdefghijklmnop",
                "qrstuvwxyzabcdef",
                "--host-name",
                "com.example.host",
                "--manifest-path",
                str(manifest_path),
                "--platform",
                "linux",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "list-allowed",
                "--host-name",
                "com.example.host",
                "--manifest-path",
                str(manifest_path),
                "--platform",
                "linux",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out.splitlines()
    assert out == ["abcdefghijklmnop", "qrstuvwxyzabcdef"]

    assert (
        main(
            [
                "disallow",
                "abcdefghijklmnop",
                "--host-name",
                "com.example.host",
                "--manifest-path",
                str(manifest_path),
                "--platform",
                "linux",
            ]
        )
        == 0
    )
    capsys.readouterr()

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == ["chrome-extension://qrstuvwxyzabcdef/"]


# ---------------------------------------------------------------------------
# Windows-specific: allow/disallow must not touch the registry
# ---------------------------------------------------------------------------


class _ExplodingWinReg(SimpleNamespace):
    HKEY_CURRENT_USER = object()
    KEY_WRITE = object()
    REG_SZ = 1

    def CreateKeyEx(self, *a, **kw):  # pragma: no cover - fails test if called
        raise AssertionError("winreg.CreateKeyEx must not be called by allow/disallow")

    def SetValueEx(self, *a, **kw):  # pragma: no cover
        raise AssertionError("winreg.SetValueEx must not be called by allow/disallow")

    def DeleteKey(self, *a, **kw):  # pragma: no cover
        raise AssertionError("winreg.DeleteKey must not be called by allow/disallow")

    def OpenKey(self, *a, **kw):  # pragma: no cover
        raise AssertionError("winreg.OpenKey must not be called by allow/disallow")

    def QueryValueEx(self, *a, **kw):  # pragma: no cover
        raise AssertionError("winreg.QueryValueEx must not be called by allow/disallow")


def _seed_windows_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "LocalAppData" / "python-input-control" / "NativeMessagingHosts" / "com.example.host.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "name": "com.example.host",
                "description": "x",
                "path": str(tmp_path / "python-input-control.exe"),
                "type": "stdio",
                "allowed_origins": ["chrome-extension://abcdefghijklmnop/"],
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def test_windows_allow_does_not_touch_registry(monkeypatch, tmp_path: Path) -> None:
    manifest_path = _seed_windows_manifest(tmp_path)
    monkeypatch.setitem(sys.modules, "winreg", _ExplodingWinReg())

    exit_code = allow_command(
        extension_ids=["qrstuvwxyzabcdef"],
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="windows",
    )
    assert exit_code == 0
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "chrome-extension://qrstuvwxyzabcdef/" in data["allowed_origins"]


def test_windows_disallow_does_not_touch_registry(monkeypatch, tmp_path: Path) -> None:
    manifest_path = _seed_windows_manifest(tmp_path)
    monkeypatch.setitem(sys.modules, "winreg", _ExplodingWinReg())

    exit_code = disallow_command(
        extension_ids=["abcdefghijklmnop"],
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="windows",
    )
    assert exit_code == 0
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == []


def test_windows_list_allowed_does_not_touch_registry(monkeypatch, tmp_path: Path, capsys) -> None:
    manifest_path = _seed_windows_manifest(tmp_path)
    monkeypatch.setitem(sys.modules, "winreg", _ExplodingWinReg())

    exit_code = list_allowed_command(
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="windows",
    )
    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == ["abcdefghijklmnop"]


def test_cli_allow_requires_extension_id(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["allow", "--platform", "linux"])
    err = capsys.readouterr().err
    assert "EXTENSION_ID" in err


def test_allow_disallow_concurrent_updates_do_not_lose_entries(tmp_path: Path) -> None:
    """Regression for Medium #5: manifest updates must be atomic.

    Fire many threads that concurrently allow/disallow distinct extension
    ids and assert that the final manifest is the union of the allow ops
    (minus the intentionally-removed ids) – i.e. no concurrent allow was
    lost to a racing read/modify/write cycle.
    """
    import threading as _threading

    manifest_path, _exe = _make_installed_plan(tmp_path, extension_ids=[])

    # 16-char [a-z0-9] ids so they pass validation.
    alphabet = "abcdefghijklmnop"

    def _id(prefix: str, index: int) -> str:
        suffix = format(index, "04d")
        # Build a valid id by tiling the prefix and replacing the last 4 chars
        # with the index (keeping it 16 chars total, all lowercase alnum).
        base = (prefix * 4)[:12]
        return (base + suffix)[:16]

    allow_ids = [_id("allow", i) for i in range(20)]
    remove_ids = allow_ids[:5]  # these ones get removed by a second wave

    errors: list[BaseException] = []

    def _allow_one(eid: str) -> None:
        try:
            allow_command(
                extension_ids=[eid],
                host_name="com.example.host",
                manifest_path=manifest_path,
                platform_name="linux",
            )
        except BaseException as exc:  # pragma: no cover - diagnostics only
            errors.append(exc)

    threads = [_threading.Thread(target=_allow_one, args=(eid,)) for eid in allow_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive()
    assert errors == []

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    present = set(data["allowed_origins"])
    for eid in allow_ids:
        assert f"chrome-extension://{eid}/" in present, f"missing {eid} – concurrent allow was lost"

    def _disallow_one(eid: str) -> None:
        try:
            disallow_command(
                extension_ids=[eid],
                host_name="com.example.host",
                manifest_path=manifest_path,
                platform_name="linux",
            )
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [_threading.Thread(target=_disallow_one, args=(eid,)) for eid in remove_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive()
    assert errors == []

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    present = set(data["allowed_origins"])
    for eid in remove_ids:
        assert f"chrome-extension://{eid}/" not in present
    for eid in allow_ids[5:]:
        assert f"chrome-extension://{eid}/" in present, f"stray removal lost {eid}"
