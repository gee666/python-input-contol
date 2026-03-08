from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterable, Literal, Sequence

DEFAULT_HOST_NAME = "com.workshop.python_input_control"
DEFAULT_SCRIPT_NAME = "python-input-control"
DEFAULT_MANIFEST_DESCRIPTION = "Native messaging host for browser-driven OS input automation."
WINDOWS_REGISTRY_BASE = r"Software\Google\Chrome\NativeMessagingHosts"
INSTALLER_PROBE_ARGUMENT = "--installer-probe"
INSTALLER_PROBE_TOKEN = "python-input-control-installer-probe=ok"
HOST_PROBE_TIMEOUT_SECONDS = 5.0

PlatformName = Literal["windows", "macos", "linux"]
_EXTENSION_ID_PATTERN = re.compile(r"^[a-z0-9]+$")
_ALLOWED_ORIGIN_PREFIX = "chrome-extension://"


@dataclass(frozen=True)
class HostManifest:
    name: str
    description: str
    path: str
    allowed_origins: tuple[str, ...]
    type: str = "stdio"

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "type": self.type,
            "allowed_origins": list(self.allowed_origins),
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class InstallationPlan:
    platform_name: PlatformName
    host_name: str
    executable_path: Path
    manifest_path: Path
    manifest: HostManifest
    registry_path: str | None = None
    discoverable_manifest_path: Path | None = None


@dataclass(frozen=True)
class HostProbeResult:
    returncode: int
    stdout: str
    stderr: str


HostProbeRunner = Callable[[Path], HostProbeResult]


def detect_platform_name() -> PlatformName:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    raise ValueError(f"Unsupported platform: {sys.platform}")


def normalize_extension_id(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("Extension ID must be a non-empty string")
    prefix = "chrome-extension://"
    if candidate.startswith(prefix):
        candidate = candidate[len(prefix) :]
    candidate = candidate.rstrip("/")
    if "/" in candidate or ":" in candidate:
        raise ValueError(f"Extension ID must be a raw Chrome extension ID, got: {value!r}")
    if any(character.isspace() for character in candidate) or not _EXTENSION_ID_PATTERN.fullmatch(candidate):
        raise ValueError(f"Extension ID contains unsupported characters: {value!r}")
    return candidate


def extension_id_to_origin(extension_id: str) -> str:
    return f"chrome-extension://{normalize_extension_id(extension_id)}/"


def build_manifest(
    executable_path: Path | str,
    extension_ids: Iterable[str],
    *,
    host_name: str = DEFAULT_HOST_NAME,
    description: str = DEFAULT_MANIFEST_DESCRIPTION,
) -> HostManifest:
    executable = Path(executable_path).expanduser().resolve()
    origins = tuple(dict.fromkeys(extension_id_to_origin(value) for value in extension_ids))
    if not origins:
        raise ValueError("At least one extension ID is required to build the manifest")
    return HostManifest(
        name=host_name,
        description=description,
        path=str(executable),
        allowed_origins=origins,
    )


def default_manifest_path(
    host_name: str = DEFAULT_HOST_NAME,
    *,
    platform_name: PlatformName | None = None,
    home_dir: Path | None = None,
    local_appdata_dir: Path | None = None,
) -> Path:
    resolved_platform = platform_name or detect_platform_name()
    home = (home_dir or _default_home_dir()).expanduser()

    if resolved_platform == "linux":
        return home / ".config" / "google-chrome" / "NativeMessagingHosts" / f"{host_name}.json"
    if resolved_platform == "macos":
        return (
            home
            / "Library"
            / "Application Support"
            / "Google"
            / "Chrome"
            / "NativeMessagingHosts"
            / f"{host_name}.json"
        )
    if resolved_platform == "windows":
        local_appdata = local_appdata_dir or _default_local_appdata_dir(home)
        return local_appdata / "python-input-control" / "NativeMessagingHosts" / f"{host_name}.json"
    raise ValueError(f"Unsupported platform: {resolved_platform}")


def default_registry_path(host_name: str = DEFAULT_HOST_NAME) -> str:
    return f"{WINDOWS_REGISTRY_BASE}\\{host_name}"


def resolve_host_executable(
    host_path: str | Path | None = None,
    *,
    script_name: str = DEFAULT_SCRIPT_NAME,
    platform_name: PlatformName | None = None,
) -> Path:
    if host_path is not None:
        explicit_path = Path(host_path).expanduser().resolve()
        if not explicit_path.exists() or not explicit_path.is_file():
            raise FileNotFoundError(f"Host executable does not exist: {explicit_path}")
        return explicit_path

    candidates = _launcher_candidates(script_name, platform_name=platform_name)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

    searched = "\n".join(f"- {candidate}" for candidate in candidates)
    raise FileNotFoundError(
        "Could not locate the installed native host launcher. "
        "Install the package first or pass --host-path explicitly.\n"
        f"Searched:\n{searched}"
    )


def build_installation_plan(
    extension_ids: Iterable[str],
    *,
    host_name: str = DEFAULT_HOST_NAME,
    host_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    platform_name: PlatformName | None = None,
    description: str = DEFAULT_MANIFEST_DESCRIPTION,
    home_dir: Path | None = None,
    local_appdata_dir: Path | None = None,
) -> InstallationPlan:
    resolved_platform = platform_name or detect_platform_name()
    executable_path = resolve_host_executable(host_path, platform_name=resolved_platform)
    default_path = default_manifest_path(
        host_name,
        platform_name=resolved_platform,
        home_dir=home_dir,
        local_appdata_dir=local_appdata_dir,
    ).resolve()
    resolved_manifest_path = Path(manifest_path).expanduser().resolve() if manifest_path is not None else default_path
    manifest = build_manifest(executable_path, extension_ids, host_name=host_name, description=description)
    registry_path = default_registry_path(host_name) if resolved_platform == "windows" else None
    discoverable_manifest_path = default_path if resolved_platform in {"linux", "macos"} else None
    return InstallationPlan(
        platform_name=resolved_platform,
        host_name=host_name,
        executable_path=executable_path,
        manifest_path=resolved_manifest_path,
        manifest=manifest,
        registry_path=registry_path,
        discoverable_manifest_path=discoverable_manifest_path,
    )


def install(plan: InstallationPlan, *, dry_run: bool = False) -> list[str]:
    actions = [f"Write manifest to {plan.manifest_path}"]
    if plan.platform_name == "windows":
        actions.append(f"Register manifest in HKCU\\{plan.registry_path or default_registry_path(plan.host_name)}")
    if dry_run:
        return [f"DRY RUN: {line}" for line in actions]

    plan.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    plan.manifest_path.write_text(plan.manifest.to_json(), encoding="utf-8")

    if plan.platform_name == "windows":
        _write_windows_registry_pointer(plan.registry_path or default_registry_path(plan.host_name), plan.manifest_path)

    return actions


def uninstall(
    host_name: str = DEFAULT_HOST_NAME,
    *,
    manifest_path: str | Path | None = None,
    platform_name: PlatformName | None = None,
    home_dir: Path | None = None,
    local_appdata_dir: Path | None = None,
    dry_run: bool = False,
) -> list[str]:
    resolved_platform = platform_name or detect_platform_name()
    resolved_manifest_path = (
        Path(manifest_path).expanduser().resolve()
        if manifest_path is not None
        else default_manifest_path(
            host_name,
            platform_name=resolved_platform,
            home_dir=home_dir,
            local_appdata_dir=local_appdata_dir,
        ).resolve()
    )
    actions: list[str] = []

    if resolved_platform == "windows":
        registry_path = default_registry_path(host_name)
        actions.append(f"Remove Chrome registry key HKCU\\{registry_path}")
        if not dry_run:
            _delete_windows_registry_pointer(registry_path)

    actions.append(f"Remove manifest file {resolved_manifest_path}")
    if not dry_run and resolved_manifest_path.exists():
        resolved_manifest_path.unlink()
        _prune_empty_parents(resolved_manifest_path.parent)

    if dry_run:
        return [f"DRY RUN: {line}" for line in actions]
    return actions


def verify_installation(
    plan: InstallationPlan,
    *,
    probe_runner: HostProbeRunner | None = None,
) -> list[str]:
    issues: list[str] = []

    issues.extend(_verify_manifest_discoverability(plan))
    issues.extend(_verify_host_executable(plan, probe_runner=probe_runner))

    if not plan.manifest_path.exists():
        issues.append(f"Manifest file does not exist: {plan.manifest_path}")
        return issues

    try:
        manifest_data = json.loads(plan.manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issues.append(f"Manifest file is not valid JSON: {exc}")
        return issues

    if not isinstance(manifest_data, dict):
        issues.append(f"Manifest root must be a JSON object, found {type(manifest_data).__name__}")
        return issues

    if manifest_data.get("name") != plan.host_name:
        issues.append(
            f"Manifest name mismatch: expected {plan.host_name!r}, found {manifest_data.get('name')!r}"
        )

    manifest_path_value = manifest_data.get("path")
    if manifest_path_value != str(plan.executable_path):
        issues.append(
            f"Manifest path mismatch: expected {plan.executable_path!s}, found {manifest_path_value!r}"
        )
    elif not isinstance(manifest_path_value, str) or not _is_absolute_path_string(manifest_path_value):
        issues.append(f"Manifest path must be an absolute path, found {manifest_path_value!r}")

    if manifest_data.get("type") != "stdio":
        issues.append(f"Manifest type must be 'stdio', found {manifest_data.get('type')!r}")

    manifest_origins = manifest_data.get("allowed_origins")
    normalized_manifest_origins = _validate_allowed_origins(manifest_origins)
    if isinstance(normalized_manifest_origins, list):
        if set(normalized_manifest_origins) != set(plan.manifest.allowed_origins):
            issues.append(
                "Manifest allowed_origins mismatch: "
                f"expected {list(plan.manifest.allowed_origins)!r}, found {manifest_origins!r}"
            )
    else:
        issues.append(normalized_manifest_origins)

    if plan.platform_name == "windows":
        registry_issue = _verify_windows_registry_pointer(
            plan.registry_path or default_registry_path(plan.host_name),
            plan.manifest_path,
        )
        if registry_issue:
            issues.append(registry_issue)

    return issues


def plan_summary(plan: InstallationPlan) -> list[str]:
    lines = [
        f"Install host '{plan.host_name}' for platform {plan.platform_name}",
        f"Use executable {plan.executable_path}",
        f"Write manifest to {plan.manifest_path}",
        f"Allow origins: {', '.join(plan.manifest.allowed_origins)}",
    ]
    if plan.discoverable_manifest_path is not None and plan.manifest_path != plan.discoverable_manifest_path:
        lines.append(f"Chrome discoverability expects manifest at {plan.discoverable_manifest_path}")
    if plan.registry_path is not None:
        lines.append(f"Register manifest in HKCU\\{plan.registry_path}")
    return lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install or verify the Chrome native messaging host manifest.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Write the native host manifest and platform registration")
    _add_common_plan_arguments(install_parser)
    install_parser.add_argument("--dry-run", action="store_true", help="Print planned actions without writing anything")

    verify_parser = subparsers.add_parser("verify", help="Verify the native host registration")
    _add_common_plan_arguments(verify_parser)

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove the native host manifest and platform registration")
    uninstall_parser.add_argument("--host-name", default=DEFAULT_HOST_NAME)
    uninstall_parser.add_argument("--manifest-path")
    uninstall_parser.add_argument("--platform", choices=("linux", "macos", "windows"))
    uninstall_parser.add_argument("--dry-run", action="store_true", help="Print planned removals without deleting anything")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "uninstall":
        for line in uninstall(
            host_name=args.host_name,
            manifest_path=args.manifest_path,
            platform_name=args.platform,
            dry_run=args.dry_run,
        ):
            print(line)
        return 0

    if not args.extension_id:
        parser.error("the following arguments are required: --extension-id")

    plan = build_installation_plan(
        extension_ids=args.extension_id,
        host_name=args.host_name,
        host_path=args.host_path,
        manifest_path=args.manifest_path,
        platform_name=args.platform,
    )

    for line in plan_summary(plan):
        print(line)

    if plan.platform_name == "macos":
        from .permissions import macos_accessibility_guidance, macos_accessibility_is_trusted

        trusted = macos_accessibility_is_trusted()
        if trusted is False:
            print("")
            print(macos_accessibility_guidance(plan.executable_path))

    if args.command == "verify":
        issues = verify_installation(plan)
        if issues:
            for issue in issues:
                print(f"VERIFY FAILED: {issue}")
            return 1
        print("Verification succeeded.")
        return 0

    for line in install(plan, dry_run=args.dry_run):
        print(line)

    if not args.dry_run:
        issues = verify_installation(plan)
        if issues:
            for issue in issues:
                print(f"VERIFY FAILED: {issue}")
            return 1
        print("Verification succeeded.")
    return 0


def _add_common_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--extension-id", action="append", default=[], help="Chrome extension ID allowed to connect")
    parser.add_argument("--host-name", default=DEFAULT_HOST_NAME)
    parser.add_argument("--host-path", help="Absolute path to the native host executable or launcher")
    parser.add_argument("--manifest-path", help="Override the manifest JSON output path")
    parser.add_argument("--platform", choices=("linux", "macos", "windows"))


def _launcher_candidates(script_name: str, *, platform_name: PlatformName | None = None) -> list[Path]:
    candidates: list[Path] = []
    scripts_dir = Path(sysconfig.get_path("scripts"))
    resolved_platform = platform_name or detect_platform_name()

    if resolved_platform == "windows":
        names = [
            f"{script_name}.exe",
            f"{script_name}.cmd",
            f"{script_name}-script.py",
            script_name,
        ]
    else:
        names = [script_name]

    candidates.extend(scripts_dir / name for name in names)

    which_path = shutil.which(script_name)
    if which_path:
        candidates.append(Path(which_path))

    deduplicated: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate not in seen:
            deduplicated.append(candidate)
            seen.add(candidate)
    return deduplicated


def _default_home_dir() -> Path:
    home = os.environ.get("HOME")
    if home:
        return Path(home)
    return Path.home()


def _default_local_appdata_dir(home_dir: Path) -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata).expanduser()
    return home_dir / "AppData" / "Local"


def _verify_manifest_discoverability(plan: InstallationPlan) -> list[str]:
    issues: list[str] = []
    if plan.platform_name not in {"linux", "macos"}:
        return issues

    expected_filename = f"{plan.host_name}.json"
    if plan.manifest_path.name != expected_filename:
        issues.append(
            f"Manifest filename is not Chrome-discoverable: expected {expected_filename!r}, found {plan.manifest_path.name!r}"
        )

    if plan.discoverable_manifest_path is not None and plan.manifest_path != plan.discoverable_manifest_path:
        issues.append(
            "Manifest is not installed at Chrome's discoverable path: "
            f"expected {plan.discoverable_manifest_path}, found {plan.manifest_path}"
        )
    return issues


def _verify_host_executable(
    plan: InstallationPlan,
    *,
    probe_runner: HostProbeRunner | None,
) -> list[str]:
    issues: list[str] = []

    if not plan.executable_path.exists():
        issues.append(f"Host executable does not exist: {plan.executable_path}")
        return issues

    if not plan.executable_path.is_file():
        issues.append(f"Host executable is not a file: {plan.executable_path}")
        return issues

    try:
        current_platform = detect_platform_name()
    except ValueError:
        current_platform = None

    if (
        plan.platform_name in {"linux", "macos"}
        and current_platform in {"linux", "macos"}
        and not _is_posix_executable(plan.executable_path)
    ):
        issues.append(f"Host executable is not executable: {plan.executable_path}")

    runner = probe_runner
    if runner is None:
        if current_platform == plan.platform_name:
            runner = _run_host_executable_probe

    if runner is None:
        return issues

    try:
        result = runner(plan.executable_path)
    except subprocess.TimeoutExpired:
        issues.append(
            f"Host executable probe timed out after {HOST_PROBE_TIMEOUT_SECONDS} seconds: {plan.executable_path}"
        )
        return issues
    except OSError as exc:
        issues.append(f"Host executable probe failed to launch {plan.executable_path}: {exc}")
        return issues

    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        if details:
            issues.append(
                f"Host executable probe failed with exit code {result.returncode}: {details}"
            )
        else:
            issues.append(f"Host executable probe failed with exit code {result.returncode}")
        return issues

    probe_output = result.stdout.strip().splitlines()
    if INSTALLER_PROBE_TOKEN not in probe_output:
        issues.append(
            "Host executable probe did not return the expected installer token; "
            f"output was {result.stdout!r}"
        )

    return issues


def _run_host_executable_probe(executable_path: Path) -> HostProbeResult:
    completed = subprocess.run(
        [str(executable_path), INSTALLER_PROBE_ARGUMENT],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=HOST_PROBE_TIMEOUT_SECONDS,
        check=False,
    )
    return HostProbeResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _validate_allowed_origins(value: object) -> list[str] | str:
    if not isinstance(value, list):
        return f"Manifest allowed_origins must be a list, found {type(value).__name__}"
    if not value:
        return "Manifest allowed_origins must be a non-empty list"

    normalized: list[str] = []
    for index, origin in enumerate(value):
        if not isinstance(origin, str):
            return f"Manifest allowed_origins[{index}] must be a string, found {type(origin).__name__}"
        try:
            normalized.append(_normalize_allowed_origin(origin))
        except ValueError as exc:
            return f"Manifest allowed_origins[{index}] is invalid: {exc}"

    if len(set(normalized)) != len(normalized):
        return "Manifest allowed_origins must not contain duplicates"
    return normalized


def _normalize_allowed_origin(origin: str) -> str:
    if not origin.startswith(_ALLOWED_ORIGIN_PREFIX) or not origin.endswith("/"):
        raise ValueError(f"expected chrome-extension://<extension-id>/ origin, found {origin!r}")
    extension_id = origin[len(_ALLOWED_ORIGIN_PREFIX) : -1]
    return extension_id_to_origin(extension_id)


def _write_windows_registry_pointer(registry_path: str, manifest_path: Path) -> None:
    try:
        import winreg
    except ImportError as exc:  # pragma: no cover - non-Windows fallback
        raise RuntimeError("Windows registry APIs are unavailable on this platform") from exc

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, registry_path, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))


def _delete_windows_registry_pointer(registry_path: str) -> None:
    try:
        import winreg
    except ImportError as exc:  # pragma: no cover - non-Windows fallback
        raise RuntimeError("Windows registry APIs are unavailable on this platform") from exc

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry_path)
    except FileNotFoundError:
        return


def _verify_windows_registry_pointer(registry_path: str, manifest_path: Path) -> str | None:
    try:
        import winreg
    except ImportError:
        return "Windows registry APIs are unavailable on this platform"

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
            value, value_type = winreg.QueryValueEx(key, "")
    except FileNotFoundError:
        return f"Chrome registry key is missing: HKCU\\{registry_path}"

    if value_type != winreg.REG_SZ:
        return f"Chrome registry key must use REG_SZ, found value type {value_type!r}"
    if not isinstance(value, str) or not value:
        return f"Chrome registry key must contain a manifest path string, found {value!r}"
    if value.startswith('"') or value.endswith('"'):
        return f"Chrome registry key must store an unquoted absolute manifest path, found '{value}'"
    if not _is_absolute_path_string(value):
        return f"Chrome registry key must store an absolute manifest path, found {value!r}"
    if _normalized_path_string(value) != _normalized_path_string(manifest_path):
        return f"Chrome registry key points to {value}, expected {manifest_path}"
    return None


def _is_absolute_path_string(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _is_posix_executable(path: Path) -> bool:
    mode = path.stat().st_mode
    return bool(mode & 0o111)


def _normalized_path_string(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(value)))


def _prune_empty_parents(path: Path) -> None:
    current = path
    for _ in range(3):
        if current == current.parent:
            return
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
