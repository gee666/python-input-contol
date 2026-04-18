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
    if plan.manifest.allowed_origins:
        origins_line = f"Allow origins: {', '.join(plan.manifest.allowed_origins)}"
    else:
        origins_line = (
            "Allow origins: (none \u2014 use 'python-input-control-install allow <id>' "
            "to add extensions)"
        )
    lines = [
        f"Install host '{plan.host_name}' for platform {plan.platform_name}",
        f"Use executable {plan.executable_path}",
        f"Write manifest to {plan.manifest_path}",
        origins_line,
    ]
    if plan.discoverable_manifest_path is not None and plan.manifest_path != plan.discoverable_manifest_path:
        lines.append(f"Chrome discoverability expects manifest at {plan.discoverable_manifest_path}")
    if plan.registry_path is not None:
        lines.append(f"Register manifest in HKCU\\{plan.registry_path}")
    return lines


def mutate_allowed_origins(
    manifest_data: dict,
    *,
    add: Iterable[str] = (),
    remove: Iterable[str] = (),
) -> tuple[dict, list[str], list[str]]:
    """Return a new manifest dict plus lists of affected and skipped IDs.

    For ``add``: ``affected`` = newly added IDs; ``skipped`` = already-present IDs.
    For ``remove``: ``affected`` = removed IDs; ``skipped`` = IDs not in the list.
    When both are supplied, the returned tuple merges them: adds win over removes
    only in the sense that each operation is applied in order (remove, then add).
    """
    existing_ids = _load_manifest_allowed_origins(manifest_data)
    current: list[str] = list(existing_ids)
    affected: list[str] = []
    skipped: list[str] = []

    for raw in remove:
        normalized = normalize_extension_id(raw)
        if normalized in current:
            current.remove(normalized)
            affected.append(normalized)
        else:
            skipped.append(normalized)

    for raw in add:
        normalized = normalize_extension_id(raw)
        if normalized in current:
            skipped.append(normalized)
        else:
            current.append(normalized)
            affected.append(normalized)

    new_manifest = dict(manifest_data)
    new_manifest["allowed_origins"] = [extension_id_to_origin(eid) for eid in current]
    return new_manifest, affected, skipped


def _read_manifest_from_disk(manifest_path: Path) -> dict:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"Manifest file not found: {manifest_path}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Manifest file is not valid JSON ({manifest_path}): {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"Manifest root must be a JSON object, found {type(data).__name__} ({manifest_path})"
        )
    return data


def _write_manifest_to_disk(manifest_path: Path, manifest: HostManifest) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.to_json(), encoding="utf-8")


def _load_manifest_allowed_origins(manifest_data: dict) -> list[str]:
    origins = manifest_data.get("allowed_origins", [])
    if not isinstance(origins, list):
        raise ValueError(
            f"Manifest allowed_origins must be a list, found {type(origins).__name__}"
        )
    ids: list[str] = []
    for index, origin in enumerate(origins):
        if not isinstance(origin, str):
            raise ValueError(
                f"Manifest allowed_origins[{index}] must be a string, found {type(origin).__name__}"
            )
        if not origin.startswith(_ALLOWED_ORIGIN_PREFIX):
            raise ValueError(
                f"Manifest allowed_origins[{index}] must start with {_ALLOWED_ORIGIN_PREFIX!r}, found {origin!r}"
            )
        ids.append(normalize_extension_id(origin))
    # Preserve insertion order while deduplicating.
    seen: set[str] = set()
    deduped: list[str] = []
    for eid in ids:
        if eid not in seen:
            seen.add(eid)
            deduped.append(eid)
    return deduped


def _extract_extension_ids(origins: Iterable[str]) -> list[str]:
    return [normalize_extension_id(origin) for origin in origins]


def _resolve_existing_manifest_path(
    host_name: str,
    manifest_path: str | Path | None,
    platform_name: PlatformName | None,
) -> Path:
    resolved_platform = platform_name or detect_platform_name()
    if manifest_path is not None:
        return Path(manifest_path).expanduser().resolve()
    return default_manifest_path(host_name, platform_name=resolved_platform).resolve()


def _manifest_missing_message(manifest_path: Path) -> str:
    return (
        f"Manifest not found at {manifest_path}. "
        "Run 'python-input-control-install install' first."
    )


def _rebuild_manifest(manifest_data: dict, *, host_name: str) -> HostManifest:
    path_value = manifest_data.get("path")
    description = manifest_data.get("description", DEFAULT_MANIFEST_DESCRIPTION)
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("Manifest is missing a valid 'path' field")
    if not isinstance(description, str):
        description = DEFAULT_MANIFEST_DESCRIPTION
    manifest_name = manifest_data.get("name", host_name)
    if not isinstance(manifest_name, str) or not manifest_name:
        manifest_name = host_name
    origins = manifest_data.get("allowed_origins", [])
    if not isinstance(origins, list):
        raise ValueError("Manifest allowed_origins must be a list")
    origin_tuple = tuple(origins)
    manifest_type = manifest_data.get("type", "stdio")
    if not isinstance(manifest_type, str) or not manifest_type:
        manifest_type = "stdio"
    return HostManifest(
        name=manifest_name,
        description=description,
        path=path_value,
        allowed_origins=origin_tuple,
        type=manifest_type,
    )


def allow_command(
    *,
    extension_ids: Sequence[str],
    host_name: str = DEFAULT_HOST_NAME,
    manifest_path: str | Path | None = None,
    platform_name: PlatformName | None = None,
    dry_run: bool = False,
) -> int:
    resolved_path = _resolve_existing_manifest_path(host_name, manifest_path, platform_name)
    if not resolved_path.exists():
        print(_manifest_missing_message(resolved_path), file=sys.stderr)
        return 2
    try:
        manifest_data = _read_manifest_from_disk(resolved_path)
        new_manifest_data, added, skipped = mutate_allowed_origins(manifest_data, add=extension_ids)
        manifest = _rebuild_manifest(new_manifest_data, host_name=host_name)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for eid in added:
        prefix = "DRY RUN: Would add" if dry_run else "Added:     "
        print(f"{prefix} {eid}")
    for eid in skipped:
        print(f"Already present: {eid}")

    if not dry_run:
        _write_manifest_to_disk(resolved_path, manifest)

    allowed = _extract_extension_ids(manifest.allowed_origins)
    summary_verb = "would allow" if dry_run else "now allows"
    if allowed:
        print(f"Manifest {summary_verb} {len(allowed)} extension(s): {', '.join(allowed)}")
    else:
        print(f"Manifest {summary_verb} 0 extensions.")
    return 0


def disallow_command(
    *,
    extension_ids: Sequence[str],
    host_name: str = DEFAULT_HOST_NAME,
    manifest_path: str | Path | None = None,
    platform_name: PlatformName | None = None,
    dry_run: bool = False,
) -> int:
    resolved_path = _resolve_existing_manifest_path(host_name, manifest_path, platform_name)
    if not resolved_path.exists():
        print(_manifest_missing_message(resolved_path), file=sys.stderr)
        return 2
    try:
        manifest_data = _read_manifest_from_disk(resolved_path)
        new_manifest_data, removed, skipped = mutate_allowed_origins(manifest_data, remove=extension_ids)
        manifest = _rebuild_manifest(new_manifest_data, host_name=host_name)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for eid in removed:
        prefix = "DRY RUN: Would remove" if dry_run else "Removed:   "
        print(f"{prefix} {eid}")
    for eid in skipped:
        print(f"Not present: {eid}")

    if not dry_run:
        _write_manifest_to_disk(resolved_path, manifest)

    allowed = _extract_extension_ids(manifest.allowed_origins)
    summary_verb = "would allow" if dry_run else "now allows"
    if allowed:
        print(f"Manifest {summary_verb} {len(allowed)} extension(s): {', '.join(allowed)}")
    else:
        print(f"Manifest {summary_verb} 0 extensions.")
    return 0


def list_allowed_command(
    *,
    host_name: str = DEFAULT_HOST_NAME,
    manifest_path: str | Path | None = None,
    platform_name: PlatformName | None = None,
    as_json: bool = False,
) -> int:
    resolved_path = _resolve_existing_manifest_path(host_name, manifest_path, platform_name)
    if not resolved_path.exists():
        print(_manifest_missing_message(resolved_path), file=sys.stderr)
        return 2
    try:
        manifest_data = _read_manifest_from_disk(resolved_path)
        ids = _load_manifest_allowed_origins(manifest_data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if as_json:
        payload = {
            "manifest_path": str(resolved_path),
            "host_name": host_name,
            "extension_ids": ids,
        }
        print(json.dumps(payload))
        return 0

    if not ids:
        print(
            "No extension IDs are currently allowed. "
            "Use 'python-input-control-install allow <id>' to add one.",
            file=sys.stderr,
        )
        return 0

    for eid in ids:
        print(eid)
    return 0


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

    allow_parser = subparsers.add_parser(
        "allow",
        help="Add one or more Chrome extension IDs to an already-installed manifest",
    )
    allow_parser.add_argument("extension_ids", metavar="EXTENSION_ID", nargs="+", help="Chrome extension ID to allow")
    _add_manifest_location_arguments(allow_parser)
    allow_parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing")

    disallow_parser = subparsers.add_parser(
        "disallow",
        help="Remove one or more Chrome extension IDs from an already-installed manifest",
    )
    disallow_parser.add_argument("extension_ids", metavar="EXTENSION_ID", nargs="+", help="Chrome extension ID to remove")
    _add_manifest_location_arguments(disallow_parser)
    disallow_parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing")

    list_parser = subparsers.add_parser(
        "list-allowed",
        help="Print the extension IDs currently allowed by the installed manifest",
    )
    _add_manifest_location_arguments(list_parser)
    list_parser.add_argument("--json", action="store_true", help="Emit a JSON object instead of one ID per line")

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

    if args.command == "allow":
        return allow_command(
            extension_ids=args.extension_ids,
            host_name=args.host_name,
            manifest_path=args.manifest_path,
            platform_name=args.platform,
            dry_run=args.dry_run,
        )

    if args.command == "disallow":
        return disallow_command(
            extension_ids=args.extension_ids,
            host_name=args.host_name,
            manifest_path=args.manifest_path,
            platform_name=args.platform,
            dry_run=args.dry_run,
        )

    if args.command == "list-allowed":
        return list_allowed_command(
            host_name=args.host_name,
            manifest_path=args.manifest_path,
            platform_name=args.platform,
            as_json=args.json,
        )

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


def _add_manifest_location_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host-name", default=DEFAULT_HOST_NAME)
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
