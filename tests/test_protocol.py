from __future__ import annotations

import io
import json
import struct
from dataclasses import dataclass, field
from typing import Any

import pytest

import python_input_control.protocol as protocol_module
from python_input_control.dispatch import CommandDispatcher
from python_input_control.errors import ProtocolDecodeError, RecoverableFramingError
from python_input_control.models import ModifierKey
from python_input_control.protocol import (
    NativeMessagingHost,
    decode_json_message,
    encode_native_message,
    read_native_message,
)
from python_input_control.randomness import SeededRandom


@dataclass
class FakePlatform:
    def platform_name(self) -> str:
        return "linux"

    def select_all_modifier(self) -> ModifierKey:
        return ModifierKey.CONTROL

    def virtual_desktop_bounds(self):
        return None


@dataclass
class RecordingKeyboardBackend:
    tab_ids: list[str] = field(default_factory=list)

    def press_tab(self, command, context) -> None:
        self.tab_ids.append(command.id)

    def type_text(self, command, context) -> None:  # pragma: no cover - not used here
        raise AssertionError("unexpected type_text")

    def select_all_and_delete(self, command, modifier, context) -> None:  # pragma: no cover - not used here
        raise AssertionError("unexpected select_all_and_delete")


def _context() -> dict[str, float]:
    return {
        "screenX": 0.0,
        "screenY": 0.0,
        "outerHeight": 900.0,
        "innerHeight": 820.0,
        "outerWidth": 1280.0,
        "innerWidth": 1280.0,
        "devicePixelRatio": 1.0,
        "scrollX": 0.0,
        "scrollY": 0.0,
    }


def _message(command_id: str, *, command: str = "key_tab", params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": command_id,
        "command": command,
        "params": params or {},
        "context": _context(),
    }


def _extract_framed_responses(stream_bytes: bytes) -> list[dict[str, Any]]:
    stream = io.BytesIO(stream_bytes)
    responses: list[dict[str, Any]] = []
    while True:
        payload = read_native_message(stream)
        if payload is None:
            return responses
        responses.append(dict(decode_json_message(payload)))


def _framed_payload_length(message: bytes) -> int:
    return struct.unpack("=I", message[:4])[0]


def _json_message_with_minimum_payload_size(size: int) -> dict[str, str]:
    template = {"id": "payload", "blob": ""}
    overhead = len(json.dumps(template, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    target_size = max(size, overhead)
    return {"id": "payload", "blob": "x" * (target_size - overhead)}


@pytest.mark.parametrize("payload_size", [1, 255, 65535])
def test_read_native_message_round_trip_at_boundary_payload_sizes(payload_size: int) -> None:
    payload = b"x" * payload_size

    framed = struct.pack("=I", len(payload)) + payload
    decoded_payload = read_native_message(io.BytesIO(framed))

    assert decoded_payload == payload


def test_encode_native_message_round_trip_preserves_json_object() -> None:
    message = _json_message_with_minimum_payload_size(255)

    encoded = encode_native_message(message)
    decoded_payload = read_native_message(io.BytesIO(encoded))

    assert decoded_payload is not None
    assert decode_json_message(decoded_payload) == message


@pytest.mark.parametrize(
    "payload",
    [b"\xff", b"[]"],
)
def test_decode_json_message_rejects_invalid_utf8_and_non_object_payloads(payload: bytes) -> None:
    with pytest.raises(ProtocolDecodeError):
        decode_json_message(payload)


def test_read_native_message_rejects_oversized_payloads() -> None:
    oversized_payload = b"x" * (4 * 1024 * 1024 + 1)
    oversized_message = struct.pack("=I", len(oversized_payload)) + oversized_payload

    with pytest.raises(RecoverableFramingError, match="exceeds the maximum supported size"):
        read_native_message(io.BytesIO(oversized_message))


def test_host_recovers_from_malformed_json_and_keeps_serving() -> None:
    keyboard_backend = RecordingKeyboardBackend()
    dispatcher = CommandDispatcher(
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )
    malformed_payload = b"{"
    input_stream = io.BytesIO(struct.pack("=I", len(malformed_payload)) + malformed_payload + encode_native_message(_message("ok-1")))
    output_stream = io.BytesIO()
    error_stream = io.StringIO()
    host = NativeMessagingHost(
        dispatcher=dispatcher,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    exit_code = host.serve_forever()
    responses = _extract_framed_responses(output_stream.getvalue())

    assert exit_code == 0
    assert responses == [
        {"id": None, "status": "error", "error": "Malformed JSON: Expecting property name enclosed in double quotes"},
        {"id": "ok-1", "status": "ok", "error": None},
    ]
    assert keyboard_backend.tab_ids == ["ok-1"]
    assert "Malformed JSON" in error_stream.getvalue()


def test_host_recovers_from_oversized_message_frame_and_keeps_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    original_max_size = protocol_module._MAX_NATIVE_MESSAGE_SIZE
    valid_frame = encode_native_message(_message("ok-1"))
    max_size = _framed_payload_length(valid_frame)
    monkeypatch.setattr(protocol_module, "_MAX_NATIVE_MESSAGE_SIZE", max_size)

    keyboard_backend = RecordingKeyboardBackend()
    dispatcher = CommandDispatcher(
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )
    oversized_payload = b"x" * (max_size + 1)
    input_stream = io.BytesIO(struct.pack("=I", len(oversized_payload)) + oversized_payload + valid_frame)
    output_stream = io.BytesIO()
    error_stream = io.StringIO()
    host = NativeMessagingHost(
        dispatcher=dispatcher,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    exit_code = host.serve_forever()
    monkeypatch.setattr(protocol_module, "_MAX_NATIVE_MESSAGE_SIZE", original_max_size)
    responses = _extract_framed_responses(output_stream.getvalue())

    assert exit_code == 0
    expected_error = (
        f"Native message payload length {len(oversized_payload)} exceeds the maximum supported size {max_size}"
    )

    assert responses == [
        {"id": None, "status": "error", "error": expected_error},
        {"id": "ok-1", "status": "ok", "error": None},
    ]
    assert keyboard_backend.tab_ids == ["ok-1"]
    assert error_stream.getvalue() == f"python-input-control: {expected_error}\n"


def test_host_discards_oversized_payload_before_reading_the_next_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    original_max_size = protocol_module._MAX_NATIVE_MESSAGE_SIZE
    ghost_frame = encode_native_message(_message("ghost"))
    real_frame = encode_native_message(_message("real"))
    max_size = max(_framed_payload_length(real_frame), len(ghost_frame))
    oversized_payload = ghost_frame + b"x" * (max_size + 1 - len(ghost_frame))
    monkeypatch.setattr(protocol_module, "_MAX_NATIVE_MESSAGE_SIZE", max_size)

    keyboard_backend = RecordingKeyboardBackend()
    dispatcher = CommandDispatcher(
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )
    input_stream = io.BytesIO(struct.pack("=I", len(oversized_payload)) + oversized_payload + real_frame)
    output_stream = io.BytesIO()
    error_stream = io.StringIO()
    host = NativeMessagingHost(
        dispatcher=dispatcher,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    exit_code = host.serve_forever()
    monkeypatch.setattr(protocol_module, "_MAX_NATIVE_MESSAGE_SIZE", original_max_size)
    responses = _extract_framed_responses(output_stream.getvalue())

    assert exit_code == 0
    expected_error = (
        f"Native message payload length {len(oversized_payload)} exceeds the maximum supported size {max_size}"
    )

    assert responses == [
        {"id": None, "status": "error", "error": expected_error},
        {"id": "real", "status": "ok", "error": None},
    ]
    assert keyboard_backend.tab_ids == ["real"]
    assert "ghost" not in output_stream.getvalue().decode("utf-8", errors="ignore")
    assert error_stream.getvalue() == f"python-input-control: {expected_error}\n"


def test_host_returns_error_and_exits_when_an_oversized_frame_cannot_be_fully_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_max_size = protocol_module._MAX_NATIVE_MESSAGE_SIZE
    ok_frame = encode_native_message(_message("ok-1"))
    max_size = _framed_payload_length(ok_frame)
    monkeypatch.setattr(protocol_module, "_MAX_NATIVE_MESSAGE_SIZE", max_size)

    keyboard_backend = RecordingKeyboardBackend()
    dispatcher = CommandDispatcher(
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )
    input_stream = io.BytesIO(ok_frame + struct.pack("=I", max_size + 1) + b"1234")
    output_stream = io.BytesIO()
    error_stream = io.StringIO()
    host = NativeMessagingHost(
        dispatcher=dispatcher,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    exit_code = host.serve_forever()
    monkeypatch.setattr(protocol_module, "_MAX_NATIVE_MESSAGE_SIZE", original_max_size)
    responses = _extract_framed_responses(output_stream.getvalue())

    assert exit_code == 1
    assert responses == [
        {"id": "ok-1", "status": "ok", "error": None},
        {"id": None, "status": "error", "error": "Unexpected EOF while reading native message stream"},
    ]
    assert keyboard_backend.tab_ids == ["ok-1"]
    assert error_stream.getvalue() == "python-input-control: Unexpected EOF while reading native message stream\n"


def test_host_returns_error_and_exits_on_unrecoverable_truncated_message_frame() -> None:
    keyboard_backend = RecordingKeyboardBackend()
    dispatcher = CommandDispatcher(
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )
    input_stream = io.BytesIO(encode_native_message(_message("ok-1")) + struct.pack("=I", 10) + b"{}")
    output_stream = io.BytesIO()
    error_stream = io.StringIO()
    host = NativeMessagingHost(
        dispatcher=dispatcher,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    exit_code = host.serve_forever()
    responses = _extract_framed_responses(output_stream.getvalue())

    assert exit_code == 1
    assert responses == [
        {"id": "ok-1", "status": "ok", "error": None},
        {"id": None, "status": "error", "error": "Unexpected EOF while reading native message stream"},
    ]
    assert keyboard_backend.tab_ids == ["ok-1"]
    assert error_stream.getvalue() == "python-input-control: Unexpected EOF while reading native message stream\n"


def test_host_returns_error_for_pointer_command_when_bounds_are_unavailable() -> None:
    dispatcher = CommandDispatcher(platform=FakePlatform(), rng=SeededRandom(123), sleep=lambda _seconds: None)
    input_stream = io.BytesIO(
        encode_native_message(
            _message("mouse-1", command="mouse_move", params={"x": 10.0, "y": 20.0})
        )
    )
    output_stream = io.BytesIO()
    error_stream = io.StringIO()
    host = NativeMessagingHost(
        dispatcher=dispatcher,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    exit_code = host.serve_forever()
    responses = _extract_framed_responses(output_stream.getvalue())

    assert exit_code == 0
    assert responses == [
        {
            "id": "mouse-1",
            "status": "error",
            "error": "Virtual desktop bounds are unavailable; refusing to execute pointer-targeted command",
        }
    ]
    assert error_stream.getvalue() == (
        "python-input-control: Virtual desktop bounds are unavailable; refusing to execute pointer-targeted command\n"
    )


def test_host_processes_100_back_to_back_commands_in_order() -> None:
    keyboard_backend = RecordingKeyboardBackend()
    dispatcher = CommandDispatcher(
        keyboard_backend=keyboard_backend,
        platform=FakePlatform(),
        rng=SeededRandom(123),
        sleep=lambda _seconds: None,
    )
    input_stream = io.BytesIO(b"".join(encode_native_message(_message(f"cmd-{index:03d}")) for index in range(100)))
    output_stream = io.BytesIO()
    error_stream = io.StringIO()
    host = NativeMessagingHost(
        dispatcher=dispatcher,
        input_stream=input_stream,
        output_stream=output_stream,
        error_stream=error_stream,
    )

    exit_code = host.serve_forever()
    responses = _extract_framed_responses(output_stream.getvalue())

    assert exit_code == 0
    assert keyboard_backend.tab_ids == [f"cmd-{index:03d}" for index in range(100)]
    assert [response["id"] for response in responses] == keyboard_backend.tab_ids
    assert all(response == {"id": response["id"], "status": "ok", "error": None} for response in responses)
    assert error_stream.getvalue() == ""
