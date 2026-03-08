from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from python_input_control.installer import (
    HostProbeResult,
    INSTALLER_PROBE_TOKEN,
    _default_local_appdata_dir,
    _launcher_candidates,
    _prune_empty_parents,
    _verify_windows_registry_pointer,
    build_installation_plan,
    default_registry_path,
    install,
    uninstall,
    verify_installation,
)


class _FakeRegistryKey:
    def __init__(self, store: dict[str, tuple[str, int] | str], path: str) -> None:
        self._store = store
        self.path = path

    def __enter__(self) -> str:
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeWinReg(SimpleNamespace):
    HKEY_CURRENT_USER = object()
    KEY_WRITE = object()
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[str, tuple[str, int] | str] = {}

    def CreateKeyEx(self, root, path: str, reserved: int, access) -> _FakeRegistryKey:
        return _FakeRegistryKey(self.store, path)

    def SetValueEx(self, key: str, name: str, reserved: int, value_type: int, value: str) -> None:
        self.store[key] = (value, value_type)

    def DeleteKey(self, root, path: str) -> None:
        if path not in self.store:
            raise FileNotFoundError(path)
        del self.store[path]

    def OpenKey(self, root, path: str) -> _FakeRegistryKey:
        if path not in self.store:
            raise FileNotFoundError(path)
        return _FakeRegistryKey(self.store, path)

    def QueryValueEx(self, key: str, name: str) -> tuple[str, int]:
        value = self.store[key]
        if isinstance(value, tuple):
            return value
        return value, self.REG_SZ


def _successful_probe(_path: Path) -> HostProbeResult:
    return HostProbeResult(returncode=0, stdout=f"{INSTALLER_PROBE_TOKEN}\n", stderr="")


def test_launcher_candidates_include_windows_wrappers(monkeypatch) -> None:
    monkeypatch.setattr("python_input_control.installer.sysconfig.get_path", lambda _name: "/scripts")
    monkeypatch.setattr("python_input_control.installer.shutil.which", lambda _name: "/usr/bin/python-input-control")

    candidates = _launcher_candidates("python-input-control", platform_name="windows")

    assert candidates == [
        Path("/scripts/python-input-control.exe"),
        Path("/scripts/python-input-control.cmd"),
        Path("/scripts/python-input-control-script.py"),
        Path("/scripts/python-input-control"),
        Path("/usr/bin/python-input-control"),
    ]


def test_default_local_appdata_prefers_environment_variable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    assert _default_local_appdata_dir(tmp_path / "home") == tmp_path / "LocalAppData"


def test_windows_install_and_verify_round_trip_with_fake_registry(monkeypatch, tmp_path: Path) -> None:
    fake_winreg = _FakeWinReg()
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    executable = tmp_path / "python-input-control.exe"
    executable.write_text("binary", encoding="utf-8")
    plan = build_installation_plan(
        ["abcdefghijklmnop"],
        host_name="com.example.host",
        host_path=executable,
        platform_name="windows",
        local_appdata_dir=tmp_path / "LocalAppData",
        home_dir=tmp_path / "home",
    )

    actions = install(plan)

    assert actions == [
        f"Write manifest to {plan.manifest_path}",
        f"Register manifest in HKCU\\{plan.registry_path}",
    ]
    stored_value, stored_type = fake_winreg.QueryValueEx(plan.registry_path or default_registry_path(plan.host_name), "")
    assert stored_value == str(plan.manifest_path)
    assert stored_type == fake_winreg.REG_SZ
    assert verify_installation(plan, probe_runner=_successful_probe) == []
    assert _verify_windows_registry_pointer(plan.registry_path or default_registry_path(plan.host_name), plan.manifest_path) is None


def test_windows_uninstall_removes_registry_pointer_with_fake_registry(monkeypatch, tmp_path: Path) -> None:
    fake_winreg = _FakeWinReg()
    registry_path = default_registry_path("com.example.host")
    fake_winreg.store[registry_path] = str(tmp_path / "manifest.json")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    manifest_path = tmp_path / "NativeMessagingHosts" / "com.example.host.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}\n", encoding="utf-8")

    actions = uninstall(
        host_name="com.example.host",
        manifest_path=manifest_path,
        platform_name="windows",
    )

    assert actions == [
        f"Remove Chrome registry key HKCU\\{registry_path}",
        f"Remove manifest file {manifest_path.resolve()}",
    ]
    assert registry_path not in fake_winreg.store
    assert not manifest_path.exists()


def test_verify_windows_registry_pointer_rejects_relative_path(monkeypatch, tmp_path: Path) -> None:
    fake_winreg = _FakeWinReg()
    registry_path = default_registry_path("com.example.host")
    fake_winreg.store[registry_path] = "manifest.json"
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    issue = _verify_windows_registry_pointer(registry_path, tmp_path / "manifest.json")

    assert issue == "Chrome registry key must store an absolute manifest path, found 'manifest.json'"


def test_verify_windows_registry_pointer_rejects_quoted_path(monkeypatch, tmp_path: Path) -> None:
    fake_winreg = _FakeWinReg()
    registry_path = default_registry_path("com.example.host")
    manifest_path = tmp_path / "manifest.json"
    fake_winreg.store[registry_path] = f'"{manifest_path}"'
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    issue = _verify_windows_registry_pointer(registry_path, manifest_path)

    assert issue == f"Chrome registry key must store an unquoted absolute manifest path, found '\"{manifest_path}\"'"


def test_verify_windows_registry_pointer_rejects_non_reg_sz_value(monkeypatch, tmp_path: Path) -> None:
    fake_winreg = _FakeWinReg()
    registry_path = default_registry_path("com.example.host")
    manifest_path = tmp_path / "manifest.json"
    fake_winreg.store[registry_path] = (str(manifest_path), fake_winreg.REG_EXPAND_SZ)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    issue = _verify_windows_registry_pointer(registry_path, manifest_path)

    assert issue == f"Chrome registry key must use REG_SZ, found value type {fake_winreg.REG_EXPAND_SZ!r}"


def test_prune_empty_parents_removes_only_empty_directories(tmp_path: Path) -> None:
    nested_dir = tmp_path / "a" / "b" / "c"
    nested_dir.mkdir(parents=True)
    keep_file = tmp_path / "a" / "keep.txt"
    keep_file.write_text("keep", encoding="utf-8")

    _prune_empty_parents(nested_dir)

    assert not nested_dir.exists()
    assert not (tmp_path / "a" / "b").exists()
    assert (tmp_path / "a").exists()
