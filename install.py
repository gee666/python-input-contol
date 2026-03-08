from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


DEFAULT_EXTRAS = ("backends",)
STANDALONE_EXTRA = "standalone"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install python-input-control and register its Chrome native host manifest.")
    parser.add_argument("--extension-id", action="append", default=[], help="Chrome extension ID allowed to connect")
    parser.add_argument("--editable", action="store_true", help="Install the package in editable mode")
    parser.add_argument(
        "--with-standalone",
        action="store_true",
        help="Also install the PyInstaller build dependency extra",
    )
    parser.add_argument(
        "--skip-pip-install",
        action="store_true",
        help="Skip the pip install step and only write the manifest registration",
    )
    parser.add_argument("--host-name", help="Override the native messaging host name")
    parser.add_argument("--host-path", help="Override the host executable or launcher path")
    parser.add_argument("--manifest-path", help="Override the manifest JSON output path")
    parser.add_argument("--platform", choices=("linux", "macos", "windows"))
    parser.add_argument("--dry-run", action="store_true", help="Print the pip and registration commands without executing")
    return parser


def build_pip_command(
    python_executable: str,
    *,
    editable: bool,
    with_standalone: bool,
) -> list[str]:
    extras = list(DEFAULT_EXTRAS)
    if with_standalone:
        extras.append(STANDALONE_EXTRA)

    install_target = f".[{','.join(extras)}]"
    command = [python_executable, "-m", "pip", "install"]
    if editable:
        command.append("-e")
    command.append(install_target)
    return command


def build_manifest_command(args: argparse.Namespace, python_executable: str) -> list[str]:
    command = [python_executable, "-m", "python_input_control.installer", "install"]
    for extension_id in args.extension_id:
        command.extend(["--extension-id", extension_id])
    if args.host_name:
        command.extend(["--host-name", args.host_name])
    if args.host_path:
        command.extend(["--host-path", args.host_path])
    if args.manifest_path:
        command.extend(["--manifest-path", args.manifest_path])
    if args.platform:
        command.extend(["--platform", args.platform])
    if args.dry_run:
        command.append("--dry-run")
    return command


def build_installer_environment(repo_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    source_root = str((repo_root / "src").resolve())
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing_pythonpath else os.pathsep.join([source_root, existing_pythonpath])
    )
    return environment


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.extension_id:
        parser.error("the following arguments are required: --extension-id")

    repo_root = Path(__file__).resolve().parent
    pip_command = build_pip_command(
        sys.executable,
        editable=args.editable,
        with_standalone=args.with_standalone,
    )
    manifest_command = build_manifest_command(args, sys.executable)
    manifest_environment = build_installer_environment(repo_root)

    if args.dry_run:
        if not args.skip_pip_install:
            print("PIP COMMAND:")
            print(" ".join(pip_command))
        print("MANIFEST COMMAND:")
        print(" ".join(manifest_command))
        return 0

    if not args.skip_pip_install:
        subprocess.run(pip_command, cwd=repo_root, check=True)

    subprocess.run(manifest_command, cwd=repo_root, check=True, env=manifest_environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
