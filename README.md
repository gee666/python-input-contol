# python-input-control

Architecture-first Python package for a Chrome Native Messaging host that validates browser-issued input commands and dispatches them to injected mouse and keyboard backends.

## Current scope

This layer implements:

- Chrome Native Messaging framing over stdin/stdout
- Sequential host processing with one response per request
- Stable response envelopes: `id`, `status`, `error`
- Validation and typed command models for the supported protocol commands
- Coordinate translation helpers for browser viewport -> physical screen space
- Injectable platform, randomness, timing, mouse, and keyboard contracts
- Concrete `pyautogui` and `pynput` runtime backends for mouse and keyboard automation
- Chrome native host manifest generation and platform registration helpers
- A repo-level installer that can `pip install` the package and register the host

The default CLI wires both concrete backends when their runtime dependencies are present and reports explicit backend availability when they are not.

## Package layout

- `python_input_control.protocol` — framing, JSON decoding, sequential host loop
- `python_input_control.dispatch` — request validation and command routing
- `python_input_control.models` — typed command and response models
- `python_input_control.platform` — OS detection, modifier selection, coordinate helpers, and bounds helpers
- `python_input_control.randomness` — seeded RNG wrapper for deterministic tests
- `python_input_control.timing` — pure timing helpers used by backend humanization logic
- `python_input_control.mouse_motion` — Bézier path generation, scroll planning, and post-action timing helpers
- `python_input_control.backends` — backend execution context and interface contracts
- `python_input_control.backends.pyautogui_mouse_backend` — concrete mouse backend wiring
- `python_input_control.backends.pynput_keyboard` — concrete keyboard backend and testable key-emission helpers
- `python_input_control.installer` — native host manifest planning, installation, uninstall, and verification
- `python_input_control.permissions` — macOS Accessibility permission guidance helpers

## Installation

The native host installs **once per machine**. Extension IDs are managed separately —
any number of extensions can share a single host via the allow-list.

Install the package with runtime backend dependencies and register the Chrome native
host manifest (use a Python 3.11+ interpreter):

```bash
# 1. Install the native host once (no extension ID required)
python3 install.py
# or, after pip installation:
python-input-control-install install

# 2. Allow one or more browser extensions to use it
python-input-control-install allow <EXTENSION_ID> [<EXTENSION_ID> ...]

# Inspect / revoke access at any time
python-input-control-install list-allowed
python-input-control-install disallow <EXTENSION_ID>
```

As a one-liner convenience you can still seed the allow-list at install time:

```bash
python3 install.py --extension-id <your-extension-id>
```

Useful `install.py` options:

- `--editable` — install the package in editable mode
- `--with-standalone` — also install the `pyinstaller` extra
- `--skip-pip-install` — only write or verify the manifest registration
- `--host-path <path>` — override the launcher path stored in the manifest
- `--manifest-path <path>` — override the manifest file location
- `--platform linux|macos|windows` — force a platform target for dry runs or tests
- `--dry-run` — print the planned pip and manifest commands without changing the system

### CLI reference

After installation, the following subcommands are available via
`python-input-control-install` (or `python -m python_input_control.installer`):

- `install` — write the manifest and (on Windows) register the pointer. Accepts
  optional `--extension-id` flags to seed the allow-list; safe to run with none.
- `verify` — validate an installed manifest + registry pointer.
- `uninstall` — remove the manifest + registry pointer.
- `allow <EXTENSION_ID> [<EXTENSION_ID> ...]` — append extension IDs to the
  allow-list of an already-installed manifest. Accepts raw IDs, full
  `chrome-extension://ID/` URLs, and trailing-slash variants. Use `--dry-run`
  to preview changes without writing.
- `disallow <EXTENSION_ID> [...]` — remove extension IDs from the allow-list.
  Leaving the list empty is valid; it just means no extension can use the host
  until `allow` is called again.
- `list-allowed` — print the raw extension IDs currently in the allow-list,
  one per line. Add `--json` for a machine-readable single-line payload.

All three of `allow` / `disallow` / `list-allowed` require that the manifest has
already been written by `install`; they exit with code `2` and point you at the
`install` command otherwise. They never touch the Windows registry — the
registry pointer is written once by `install` and keeps pointing at the same
manifest file regardless of allow-list mutations.

### Chrome registration paths

The installer writes the native host manifest to the platform-specific Chrome location required by the PRD:

- **Linux:** `~/.config/google-chrome/NativeMessagingHosts/<host>.json`
- **macOS:** `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/<host>.json`
- **Windows:** manifest JSON under `%LOCALAPPDATA%\python-input-control\NativeMessagingHosts\<host>.json` plus registry key `HKCU\Software\Google\Chrome\NativeMessagingHosts\<host>`

Default host name:

- `com.workshop.python_input_control`

The generated manifest stores `allowed_origins` in the required Chrome form:

- `chrome-extension://<extension-id>/`

## CLI

Run the native host:

```bash
python3 -m python_input_control.cli
```

or after installation:

```bash
python-input-control
```

Useful options:

- `--seed <value>` — deterministic seed passed to backend execution context
- `--backend-status` — print the current default backend wiring and exit
- `--check-permissions` — print runtime permission status and macOS guidance, then exit

## Native messaging contract

Incoming messages must be UTF-8 JSON objects framed with a 4-byte native-endian unsigned length prefix.

Example request:

```json
{
  "id": "1234",
  "command": "mouse_move",
  "params": {"x": 640, "y": 480, "duration_ms": 400},
  "context": {
    "screenX": 100,
    "screenY": 50,
    "outerHeight": 900,
    "innerHeight": 815,
    "outerWidth": 1280,
    "innerWidth": 1280,
    "devicePixelRatio": 2,
    "scrollX": 0,
    "scrollY": 0
  }
}
```

Example response:

```json
{
  "id": "1234",
  "status": "ok",
  "error": null
}
```

### Supported commands

The host accepts exactly these command names:

- `mouse_move`
- `mouse_click`
- `scroll`
- `type`
- `press_key`
- `press_shortcut`
- `pause`
- `sequence`

Removed legacy command names are no longer supported:

- `mouse_left_click`
- `mouse_right_click`
- `mouse_double_click`
- `key_tab`
- `key_escape`
- `select_all_and_delete`

### Command shapes

#### `mouse_move`

```json
{
  "command": "mouse_move",
  "params": {"x": 640, "y": 480, "duration_ms": 400}
}
```

#### `mouse_click`

`mouse_click` generalizes button and click count.

- `button`: `left` | `right` | `middle` (default `left`)
- `count`: positive integer click count (default `1`)
- `move_duration_ms`, `hold_ms`, `interval_ms`: optional timing controls

```json
{
  "command": "mouse_click",
  "params": {
    "x": 640,
    "y": 480,
    "button": "right",
    "count": 2,
    "move_duration_ms": 200,
    "hold_ms": 80,
    "interval_ms": 120
  }
}
```

#### `scroll`

```json
{
  "command": "scroll",
  "params": {
    "x": 640,
    "y": 480,
    "delta_x": 0,
    "delta_y": 600,
    "duration_ms": 450
  }
}
```

#### `type`

```json
{
  "command": "type",
  "params": {"text": "Hello world", "wpm": 120}
}
```

#### `press_key`

- `key`: arbitrary key name
- `repeat`: optional positive integer, default `1`

```json
{
  "command": "press_key",
  "params": {"key": "escape", "repeat": 2}
}
```

#### `press_shortcut`

Prefer `keys` as the canonical parameter shape.

```json
{
  "command": "press_shortcut",
  "params": {"keys": ["control", "shift", "p"]}
}
```

A convenience string alias may also be accepted by the host:

```json
{
  "command": "press_shortcut",
  "params": {"shortcut": "ctrl+shift+p"}
}
```

#### `pause`

```json
{
  "command": "pause",
  "params": {"duration_ms": 250}
}
```

#### `sequence`

`sequence` executes ordered steps. Nested `sequence` steps are intentionally rejected for now.

Supported step commands include at least:

- `press_key`
- `press_shortcut`
- `type`
- `pause`

The current implementation also accepts pointer-oriented steps such as `mouse_move`, `mouse_click`, and `scroll`.

Example: select all, then delete.

```json
{
  "command": "sequence",
  "params": {
    "steps": [
      {"command": "press_shortcut", "params": {"keys": ["control", "a"]}},
      {"command": "press_key", "params": {"key": "delete"}}
    ]
  }
}
```

## macOS Accessibility guidance

Real mouse and keyboard automation on macOS requires Accessibility permission.

Check guidance without starting the host loop:

```bash
python-input-control --check-permissions
```

The installer also prints a reminder when it detects that Accessibility trust is missing during registration on macOS. The runtime CLI now refuses to start the native host loop on macOS when Accessibility permission is missing, prints the same actionable guidance to stderr, and exits with code `1`.

## Standalone packaging

Install the runtime backend dependencies plus the PyInstaller build tooling:

```bash
pip install .[standalone]
```

A PyInstaller spec file is included at:

- `packaging/pyinstaller/python-input-control.spec`

Example build command:

```bash
pyinstaller packaging/pyinstaller/python-input-control.spec
```

Register the built binary as the Chrome native host without reinstalling the Python package (`dist/python-input-control.exe` on Windows):

```bash
python3 install.py \
  --skip-pip-install \
  --host-path dist/python-input-control \
  --extension-id <your-extension-id>
```

Then verify the generated registration:

```bash
PYTHONPATH=src python3 -m python_input_control.installer verify \
  --host-path dist/python-input-control \
  --extension-id <your-extension-id>
```

## Backend contracts for follow-up engineers

Concrete backends must implement the protocols in:

- `src/python_input_control/backends/mouse_backend.py`
- `src/python_input_control/backends/keyboard_backend.py`

Dispatch passes a `BackendExecutionContext` containing:

- `platform` — platform adapter and screen-bounds source
- `rng` — deterministic random source
- `sleep` — injectable sleep function for tests

Mouse backends receive translated **physical screen coordinates**. Browser geometry values collected from JavaScript (`screenX`, `screenY`, `outerWidth`, `innerWidth`, and viewport-relative target coordinates) are treated as browser/CSS units first; the host converts the entire browser offset plus target point into physical pixels before issuing OS input. If a backend needs a platform-specific coordinate adaptation step (for example pyautogui on macOS Retina), it should call helpers in `python_input_control.platform` before issuing OS calls.

## Development notes

- All command validation failures return `status: "error"` without crashing the host.
- Malformed JSON requests also return a framed error response when the payload can still be read.
- Stream framing failures are treated as fatal because the byte stream can no longer be trusted.
