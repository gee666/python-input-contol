from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from python_input_control.backends.pynput_keyboard import UnavailablePynputKeyboardBackend, build_default_keyboard_backend
from python_input_control.backends.pyautogui_mouse_backend import UnavailablePyAutoGuiMouseBackend, build_default_mouse_backend
from python_input_control.dispatch import CommandDispatcher
from python_input_control.protocol import NativeMessagingHost, decode_json_message, encode_native_message, read_native_message
from python_input_control.randomness import SeededRandom

pytestmark = pytest.mark.integration

_ENABLE_ENV_VAR = "PYTHON_INPUT_CONTROL_RUN_INTEGRATION"
_DEVICE_PIXEL_RATIO_ENV_VAR = "PYTHON_INPUT_CONTROL_INTEGRATION_DEVICE_PIXEL_RATIO"
_DEFAULT_DEVICE_PIXEL_RATIO = 1.0
_READY_TIMEOUT_S = 15.0
_EVENT_TIMEOUT_S = 12.0
_POLL_INTERVAL_S = 0.05
_HARNESS_SCRIPT = Path(__file__).with_name("_desktop_harness.py")


@dataclass
class DesktopHarness:
    process: subprocess.Popen[str]
    state_path: Path
    device_pixel_ratio: float

    def close(self) -> None:
        if self.process.poll() is not None:
            self.process.stdout and self.process.stdout.close()
            self.process.stderr and self.process.stderr.close()
            return

        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        finally:
            if self.process.stdout:
                self.process.stdout.close()
            if self.process.stderr:
                self.process.stderr.close()

    def read_state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def wait_for(self, predicate: Callable[[dict[str, Any]], bool], *, timeout_s: float, description: str) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last_state: dict[str, Any] | None = None
        last_error: str | None = None

        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stdout = ""
                stderr = ""
                if self.process.stdout:
                    stdout = self.process.stdout.read()
                if self.process.stderr:
                    stderr = self.process.stderr.read()
                raise AssertionError(
                    f"Desktop harness exited before {description}. stdout={stdout!r} stderr={stderr!r}"
                )
            if self.state_path.exists():
                try:
                    last_state = self.read_state()
                except (json.JSONDecodeError, OSError) as exc:
                    last_error = str(exc)
                else:
                    if predicate(last_state):
                        return last_state
            time.sleep(_POLL_INTERVAL_S)

        raise AssertionError(
            f"Timed out waiting for {description}. last_state={last_state!r} last_error={last_error!r}"
        )

    def browser_context(self, state: Mapping[str, Any] | None = None) -> dict[str, float]:
        current_state = dict(state or self.read_state())
        viewport = current_state["viewport"]
        viewport_width = float(viewport["width"])
        viewport_height = float(viewport["height"])
        return {
            "screenX": float(viewport["root_x"]) / self.device_pixel_ratio,
            "screenY": float(viewport["root_y"]) / self.device_pixel_ratio,
            "outerHeight": viewport_height / self.device_pixel_ratio,
            "innerHeight": viewport_height / self.device_pixel_ratio,
            "outerWidth": viewport_width / self.device_pixel_ratio,
            "innerWidth": viewport_width / self.device_pixel_ratio,
            "devicePixelRatio": self.device_pixel_ratio,
            "scrollX": 0.0,
            "scrollY": 0.0,
        }

    def css_target(self, name: str, state: Mapping[str, Any] | None = None) -> dict[str, float]:
        current_state = dict(state or self.read_state())
        target = current_state["targets"][name]
        return {
            "x": float(target["center_x"]) / self.device_pixel_ratio,
            "y": float(target["center_y"]) / self.device_pixel_ratio,
        }


@pytest.fixture
def integration_environment() -> float:
    enabled = os.environ.get(_ENABLE_ENV_VAR, "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        pytest.skip(
            f"desktop integration tests are disabled; set {_ENABLE_ENV_VAR}=1 to allow real mouse and keyboard input"
        )

    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("desktop integration tests require an active graphical session")

    pytest.importorskip("tkinter", reason="desktop integration tests require tkinter for the desktop harness")

    raw_device_pixel_ratio = os.environ.get(_DEVICE_PIXEL_RATIO_ENV_VAR, str(_DEFAULT_DEVICE_PIXEL_RATIO))
    try:
        device_pixel_ratio = float(raw_device_pixel_ratio)
    except ValueError as exc:  # pragma: no cover - defensive validation
        raise AssertionError(f"{_DEVICE_PIXEL_RATIO_ENV_VAR} must be a number, got {raw_device_pixel_ratio!r}") from exc

    if device_pixel_ratio <= 0:
        raise AssertionError(f"{_DEVICE_PIXEL_RATIO_ENV_VAR} must be greater than zero")

    return device_pixel_ratio


@pytest.fixture
def integration_dispatcher(integration_environment: float) -> CommandDispatcher:
    mouse_backend = build_default_mouse_backend()
    if isinstance(mouse_backend, UnavailablePyAutoGuiMouseBackend):
        pytest.skip(mouse_backend.message)

    keyboard_backend = build_default_keyboard_backend()
    if isinstance(keyboard_backend, UnavailablePynputKeyboardBackend):
        pytest.skip(keyboard_backend.message)

    return CommandDispatcher(
        mouse_backend=mouse_backend,
        keyboard_backend=keyboard_backend,
        rng=SeededRandom("integration-desktop"),
        sleep=time.sleep,
    )


@pytest.fixture
def pyautogui_module(integration_dispatcher: CommandDispatcher):
    try:
        import pyautogui
    except Exception as exc:  # pragma: no cover - depends on local environment
        pytest.skip(f"pyautogui is unavailable in this desktop session: {exc}")
    return pyautogui


@pytest.fixture
def restore_cursor(pyautogui_module) -> Iterator[None]:
    start = pyautogui_module.position()
    yield
    pyautogui_module.moveTo(start.x, start.y)


@pytest.fixture
def desktop_harness_factory(tmp_path: Path, integration_environment: float) -> Iterator[Callable[..., DesktopHarness]]:
    harnesses: list[DesktopHarness] = []

    def _launch(*, focus: str = "none", entry_text: str = "") -> DesktopHarness:
        state_path = tmp_path / f"desktop-harness-{len(harnesses)}.json"
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            [
                sys.executable,
                str(_HARNESS_SCRIPT),
                "--state-file",
                str(state_path),
                "--focus",
                focus,
                "--entry-text",
                entry_text,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        harness = DesktopHarness(process=process, state_path=state_path, device_pixel_ratio=integration_environment)
        harness.wait_for(lambda state: bool(state.get("ready")), timeout_s=_READY_TIMEOUT_S, description="desktop harness ready state")
        harnesses.append(harness)
        return harness

    yield _launch

    for harness in reversed(harnesses):
        harness.close()


@pytest.fixture
def invoke_host_command(integration_dispatcher: CommandDispatcher) -> Callable[..., tuple[dict[str, Any], str]]:
    def _invoke(*, command: str, params: Mapping[str, Any] | None = None, context: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
        message = {
            "id": str(uuid.uuid4()),
            "command": command,
            "params": dict(params or {}),
            "context": dict(context),
        }
        input_stream = io.BytesIO(encode_native_message(message))
        output_stream = io.BytesIO()
        error_stream = io.StringIO()
        host = NativeMessagingHost(
            dispatcher=integration_dispatcher,
            input_stream=input_stream,
            output_stream=output_stream,
            error_stream=error_stream,
        )

        exit_code = host.serve_forever()
        assert exit_code == 0

        payload = read_native_message(io.BytesIO(output_stream.getvalue()))
        assert payload is not None
        response = dict(decode_json_message(payload))
        return response, error_stream.getvalue()

    return _invoke


@pytest.mark.usefixtures("restore_cursor")
def test_mouse_move_positions_cursor_on_real_desktop(
    desktop_harness_factory: Callable[..., DesktopHarness],
    invoke_host_command: Callable[..., tuple[dict[str, Any], str]],
    pyautogui_module,
) -> None:
    harness = desktop_harness_factory()
    state = harness.read_state()
    target = harness.css_target("click", state)

    response, stderr = invoke_host_command(
        command="mouse_move",
        params={"x": target["x"], "y": target["y"], "duration_ms": 220},
        context=harness.browser_context(state),
    )

    assert response == {"id": response["id"], "status": "ok", "error": None}
    assert stderr == ""

    actual_position = pyautogui_module.position()
    expected_state = harness.read_state()
    expected_target = expected_state["targets"]["click"]
    assert abs(actual_position.x - expected_target["center_x"]) <= 2
    assert abs(actual_position.y - expected_target["center_y"]) <= 2


@pytest.mark.usefixtures("restore_cursor")
def test_mouse_click_is_received_by_test_target(
    desktop_harness_factory: Callable[..., DesktopHarness],
    invoke_host_command: Callable[..., tuple[dict[str, Any], str]],
) -> None:
    harness = desktop_harness_factory(focus="click")
    state = harness.read_state()
    target = harness.css_target("click", state)

    response, stderr = invoke_host_command(
        command="mouse_click",
        params={"x": target["x"], "y": target["y"], "button": "left", "count": 1, "move_duration_ms": 240, "hold_ms": 80},
        context=harness.browser_context(state),
    )

    assert response == {"id": response["id"], "status": "ok", "error": None}
    assert stderr == ""

    clicked_state = harness.wait_for(
        lambda snapshot: int(snapshot.get("click_count", 0)) >= 1,
        timeout_s=_EVENT_TIMEOUT_S,
        description="left click to reach the harness target",
    )
    assert clicked_state["click_count"] >= 1


@pytest.mark.usefixtures("restore_cursor")
def test_type_writes_text_into_focused_input_field(
    desktop_harness_factory: Callable[..., DesktopHarness],
    invoke_host_command: Callable[..., tuple[dict[str, Any], str]],
) -> None:
    harness = desktop_harness_factory(focus="entry")
    state = harness.wait_for(lambda snapshot: snapshot.get("focused_widget") == "entry", timeout_s=5.0, description="entry focus")
    expected_text = "Hello world"

    response, stderr = invoke_host_command(
        command="type",
        params={"text": expected_text, "wpm": 150},
        context=harness.browser_context(state),
    )

    assert response == {"id": response["id"], "status": "ok", "error": None}
    assert stderr == ""

    typed_state = harness.wait_for(
        lambda snapshot: snapshot.get("entry_text") == expected_text,
        timeout_s=_EVENT_TIMEOUT_S,
        description="typed text to appear in the focused entry",
    )
    assert typed_state["entry_text"] == expected_text


@pytest.mark.usefixtures("restore_cursor")
def test_scroll_changes_scroll_position_by_expected_tick_total(
    desktop_harness_factory: Callable[..., DesktopHarness],
    invoke_host_command: Callable[..., tuple[dict[str, Any], str]],
) -> None:
    harness = desktop_harness_factory(focus="scroll")
    state = harness.wait_for(lambda snapshot: snapshot.get("focused_widget") == "scroll", timeout_s=5.0, description="scroll canvas focus")
    target = harness.css_target("scroll", state)
    expected_vertical_ticks = -6

    response, stderr = invoke_host_command(
        command="scroll",
        params={
            "x": target["x"],
            "y": target["y"],
            "delta_x": 0.0,
            "delta_y": 600.0,
            "duration_ms": 450,
        },
        context=harness.browser_context(state),
    )

    assert response == {"id": response["id"], "status": "ok", "error": None}
    assert stderr == ""

    scrolled_state = harness.wait_for(
        lambda snapshot: int(snapshot.get("scroll_units_y", 0)) == expected_vertical_ticks,
        timeout_s=_EVENT_TIMEOUT_S,
        description="expected mouse-wheel tick total to reach the scroll target",
    )
    assert scrolled_state["scroll_units_y"] == expected_vertical_ticks
    assert scrolled_state["scroll_yview"][0] > 0.0


@pytest.mark.usefixtures("restore_cursor")
def test_sequence_ctrl_a_then_delete_clears_populated_input_field(
    desktop_harness_factory: Callable[..., DesktopHarness],
    invoke_host_command: Callable[..., tuple[dict[str, Any], str]],
) -> None:
    harness = desktop_harness_factory(focus="entry", entry_text="Prefilled integration text")
    state = harness.wait_for(
        lambda snapshot: snapshot.get("focused_widget") == "entry" and snapshot.get("entry_text") == "Prefilled integration text",
        timeout_s=5.0,
        description="prefilled entry focus",
    )

    response, stderr = invoke_host_command(
        command="sequence",
        params={
            "steps": [
                {"command": "press_shortcut", "params": {"keys": ["control", "a"]}},
                {"command": "press_key", "params": {"key": "delete"}},
            ]
        },
        context=harness.browser_context(state),
    )

    assert response == {"id": response["id"], "status": "ok", "error": None}
    assert stderr == ""

    cleared_state = harness.wait_for(
        lambda snapshot: snapshot.get("entry_text") == "",
        timeout_s=_EVENT_TIMEOUT_S,
        description="prefilled entry to be cleared",
    )
    assert cleared_state["entry_text"] == ""
