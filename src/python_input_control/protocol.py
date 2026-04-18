from __future__ import annotations

import json
import queue
import struct
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, BinaryIO, TextIO

from .dispatch import CommandDispatcher, _extract_command_id
from .errors import FramingError, ProtocolDecodeError, RecoverableFramingError
from .models import ResponseEnvelope

_NATIVE_MESSAGE_HEADER = struct.Struct("=I")
_MAX_NATIVE_MESSAGE_SIZE = 4 * 1024 * 1024


def _read_exact(stream: BinaryIO, length: int, allow_eof: bool) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = stream.read(length - len(chunks))
        if chunk == b"":
            if allow_eof and not chunks:
                return None
            raise FramingError("Unexpected EOF while reading native message stream")
        chunks.extend(chunk)
    return bytes(chunks)


def _discard_exact(stream: BinaryIO, length: int) -> None:
    remaining = length
    while remaining > 0:
        chunk = stream.read(min(remaining, 64 * 1024))
        if chunk == b"":
            raise FramingError("Unexpected EOF while reading native message stream")
        remaining -= len(chunk)


def encode_native_message(message: Mapping[str, Any] | ResponseEnvelope) -> bytes:
    payload_object = message.as_dict() if isinstance(message, ResponseEnvelope) else dict(message)
    payload = json.dumps(payload_object, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _NATIVE_MESSAGE_HEADER.pack(len(payload)) + payload


def read_native_message(stream: BinaryIO) -> bytes | None:
    header = _read_exact(stream, _NATIVE_MESSAGE_HEADER.size, allow_eof=True)
    if header is None:
        return None
    (payload_length,) = _NATIVE_MESSAGE_HEADER.unpack(header)
    if payload_length > _MAX_NATIVE_MESSAGE_SIZE:
        _discard_exact(stream, payload_length)
        raise RecoverableFramingError(
            f"Native message payload length {payload_length} exceeds the maximum supported size {_MAX_NATIVE_MESSAGE_SIZE}"
        )
    payload = _read_exact(stream, payload_length, allow_eof=False)
    assert payload is not None
    return payload


def decode_json_message(payload: bytes) -> Mapping[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolDecodeError("Payload must be valid UTF-8") from exc
    try:
        decoded = json.loads(text)
    except JSONDecodeError as exc:
        raise ProtocolDecodeError(f"Malformed JSON: {exc.msg}") from exc
    if not isinstance(decoded, Mapping):
        raise ProtocolDecodeError("Incoming JSON payload must be an object")
    return decoded


@dataclass
class NativeMessagingHost:
    dispatcher: CommandDispatcher
    input_stream: BinaryIO
    output_stream: BinaryIO
    error_stream: TextIO

    def serve_forever(self) -> int:  # noqa: C901  (deliberately linear control flow)
        """
        Serve native-messaging commands with support for mid-command cancellation.

        Architecture
        ------------
        * A *stdin reader thread* (Thread A) reads raw messages from stdin and
          puts them on an in-process queue.  It runs concurrently with command
          execution so that a ``cancel`` command (or an EOF caused by the JS side
          calling ``port.disconnect()``) is noticed immediately.

        * The *main thread* (this method) dequeues messages and dispatches
          commands.  Each command runs on a *command thread* (Thread B) so the
          main thread can keep reading the queue (and therefore notice cancel/EOF)
          while a long-running command such as ``type`` is executing.

        Cancellation flow
        -----------------
        When the extension calls ``stop()`` it disconnects the native-messaging
        port (via ``InputControlBridge.abort()``).  Chrome closes Python's stdin,
        which causes Thread A to read EOF and enqueue ``None``.  The main thread
        then sets ``cancel_event``, which the running command's sleep loop checks
        between every keystroke / motion step and raises ``CommandCancelledError``
        to abort as soon as possible (within one inter-key delay, typically <100 ms).

        A ``{"command": "cancel"}`` message sent explicitly before disconnecting
        also triggers cancellation in the same way.
        """
        cancel_event = self.dispatcher.cancel_event
        msg_queue: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        write_lock = threading.Lock()

        # ── Thread A: read raw bytes from stdin ──────────────────────────────
        def _stdin_reader() -> None:
            while True:
                try:
                    payload = read_native_message(self.input_stream)
                except RecoverableFramingError as exc:
                    msg_queue.put(("recoverable_error", exc))
                    continue
                except FramingError as exc:
                    msg_queue.put(("fatal_error", exc))
                    return
                msg_queue.put(("payload", payload))
                if payload is None:  # EOF sentinel
                    return

        stdin_thread = threading.Thread(target=_stdin_reader, daemon=True)
        stdin_thread.start()

        # ── Helper: write a response, swallowing broken-pipe errors ─────────
        def _write_safe(response: ResponseEnvelope) -> None:
            try:
                with write_lock:
                    self.write_response(response)
            except (BrokenPipeError, OSError):
                pass  # port was closed by the time we tried to write

        current_cmd_thread: threading.Thread | None = None

        # ── Main dispatch loop ───────────────────────────────────────────────
        while True:
            kind, item = msg_queue.get()

            # ── Framing errors from Thread A ─────────────────────────────────
            if kind == "recoverable_error":
                self._log(str(item))
                _write_safe(ResponseEnvelope.error_response(None, str(item)))
                continue

            if kind == "fatal_error":
                self._log(str(item))
                _write_safe(ResponseEnvelope.error_response(None, str(item)))
                cancel_event.set()
                if current_cmd_thread and current_cmd_thread.is_alive():
                    current_cmd_thread.join(timeout=1.0)
                return 1

            # kind == "payload" from here on
            payload: bytes | None = item

            # ── EOF: JS disconnected the port ─────────────────────────────────
            if payload is None:
                cancel_event.set()
                if current_cmd_thread and current_cmd_thread.is_alive():
                    current_cmd_thread.join(timeout=1.0)
                return 0

            # ── Decode JSON ──────────────────────────────────────────────────
            try:
                message = decode_json_message(payload)
            except ProtocolDecodeError as exc:
                self._log(str(exc))
                _write_safe(ResponseEnvelope.error_response(None, str(exc)))
                continue

            command_id = _extract_command_id(message)
            command_name = message.get("command", "")

            # ── 'cancel' command: stop whatever is running ────────────────────
            if command_name == "cancel":
                cancel_event.set()
                if current_cmd_thread and current_cmd_thread.is_alive():
                    current_cmd_thread.join(timeout=1.0)
                cancel_event.clear()
                current_cmd_thread = None
                _write_safe(ResponseEnvelope.ok(command_id))
                continue

            # ── Regular command ───────────────────────────────────────────────
            # If a previous command thread is still alive (e.g. user sent two
            # commands quickly without cancelling), wait for it to finish first
            # so commands stay serialised.
            if current_cmd_thread and current_cmd_thread.is_alive():
                current_cmd_thread.join()

            # Clear any leftover cancel signal before starting the new command.
            cancel_event.clear()

            def _run_command(msg: Mapping[str, Any] = message) -> None:
                response = self.dispatcher.handle_message(msg)
                _write_safe(response)

            current_cmd_thread = threading.Thread(target=_run_command, daemon=True)
            current_cmd_thread.start()
            # ← do NOT join here; loop back to read the next queued message
            #   so a cancel that arrives during execution is processed promptly.

    def _handle_payload(self, payload: bytes) -> ResponseEnvelope:
        try:
            message = decode_json_message(payload)
        except ProtocolDecodeError as exc:
            self._log(str(exc))
            return ResponseEnvelope.error_response(None, str(exc))
        response = self.dispatcher.handle_message(message)
        if response.status == "error" and response.error:
            self._log(response.error)
        return response

    def write_response(self, response: ResponseEnvelope) -> None:
        self.output_stream.write(encode_native_message(response))
        self.output_stream.flush()

    def _log(self, message: str) -> None:
        self.error_stream.write(f"python-input-control: {message}\n")
        self.error_stream.flush()


def run_host(dispatcher: CommandDispatcher | None = None) -> int:
    host = NativeMessagingHost(
        dispatcher=dispatcher or CommandDispatcher(),
        input_stream=sys.stdin.buffer,
        output_stream=sys.stdout.buffer,
        error_stream=sys.stderr,
    )
    return host.serve_forever()
