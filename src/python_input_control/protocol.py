from __future__ import annotations

import collections
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

# Bounded inbox for the stdin reader thread.  The reader thread blocks briefly
# on ``put`` and drops frames when shutting down, so a buggy/malicious client
# cannot make the host accumulate unbounded framed messages in memory.
_INBOX_MAX_FRAMES = 256
_INBOX_PUT_TIMEOUT = 1.0
# How often the main loop wakes up while a command thread is running so it can
# notice the command thread exiting even if no new messages arrive.
_MAIN_LOOP_POLL_INTERVAL = 0.05


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

    def serve_forever(self) -> int:  # noqa: C901  (state machine kept in one place on purpose)
        """
        Serve native-messaging commands with support for mid-command cancellation.

        Scheduler / cancellation state machine
        --------------------------------------
        * A *stdin reader thread* reads framed messages from stdin and pushes
          them onto a bounded inbox (``queue.Queue(maxsize=_INBOX_MAX_FRAMES)``).
          It blocks briefly on ``put`` so the host applies back-pressure to a
          fast writer; during shutdown the reader drops queued frames rather
          than hang forever.

        * The *main loop* (this method) drains the inbox continuously.
          - ``cancel`` and EOF are honored *immediately* even while a command
            thread is running: the main loop never blocks on the command
            thread's ``join()``.  Non-cancel commands queued behind a running
            command are appended to ``pending_commands`` so they do not
            prevent a later ``cancel``/EOF from being observed.
          - A new regular command is only started when
            ``current_cmd_thread is None`` *and* ``cancel_event`` is clear.
            ``cancel_event`` is only cleared after the previous command thread
            has actually exited, so if a backend ignores cancellation the host
            refuses to start the next command until the in-flight one is truly
            done (commands stay serialised in wall-clock time).

        * Logging policy is centralised: every response that carries an
          ``error`` is logged to stderr via ``_write_response_and_log`` exactly
          once, and only at the point it is emitted on the wire.
        """
        cancel_event = self.dispatcher.cancel_event
        inbox: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=_INBOX_MAX_FRAMES)
        write_lock = threading.Lock()
        shutdown_flag = threading.Event()

        # ── Stdin reader thread ─────────────────────────────────────────────
        def _stdin_reader() -> None:
            while not shutdown_flag.is_set():
                try:
                    payload = read_native_message(self.input_stream)
                except RecoverableFramingError as exc:
                    if not _bounded_put(inbox, ("recoverable_error", exc), shutdown_flag):
                        return
                    continue
                except FramingError as exc:
                    _bounded_put(inbox, ("fatal_error", exc), shutdown_flag)
                    return
                if not _bounded_put(inbox, ("payload", payload), shutdown_flag):
                    return
                if payload is None:  # EOF sentinel – reader is done
                    return

        stdin_thread = threading.Thread(target=_stdin_reader, daemon=True)
        stdin_thread.start()

        current_cmd_thread: threading.Thread | None = None
        pending_commands: collections.deque[Mapping[str, Any]] = collections.deque()
        eof_seen = False
        fatal_exit_code: int | None = None

        def _write_response_and_log(response: ResponseEnvelope) -> None:
            """Emit ``response`` on stdout and, if it is an error, log it once."""
            try:
                with write_lock:
                    self.write_response(response)
            except (BrokenPipeError, OSError):
                pass  # port was closed by the time we tried to write
            if response.status == "error" and response.error:
                self._log(response.error)

        def _handle_item(kind: str, item: Any) -> None:
            """Route one inbox item to the scheduler state."""
            nonlocal eof_seen, fatal_exit_code
            if kind == "recoverable_error":
                _write_response_and_log(ResponseEnvelope.error_response(None, str(item)))
                return
            if kind == "fatal_error":
                _write_response_and_log(ResponseEnvelope.error_response(None, str(item)))
                cancel_event.set()
                eof_seen = True
                fatal_exit_code = 1
                return
            # kind == "payload"
            payload: bytes | None = item
            if payload is None:  # EOF
                # EOF marks the end of input.  We do NOT implicitly set
                # ``cancel_event`` here: doing so would abort an in-flight
                # command and drop already-queued commands.  Callers that
                # want abort-on-disconnect semantics should send an explicit
                # ``cancel`` message before closing stdin.  Pending commands
                # keep draining; once they and any running thread are done
                # the main loop exits cleanly.
                eof_seen = True
                return
            try:
                message = decode_json_message(payload)
            except ProtocolDecodeError as exc:
                _write_response_and_log(ResponseEnvelope.error_response(None, str(exc)))
                return
            command_id = _extract_command_id(message)
            command_name = message.get("command", "")
            if command_name == "cancel":
                # Cancel is honored immediately – even if queued regular
                # commands are ahead of it, and even if the running worker
                # ignores the event.  Pending commands are dropped so the
                # cancel takes effect promptly, and ``cancel_event`` stays
                # set until the running command thread (if any) actually
                # exits so two commands can never overlap in wall-clock time.
                cancel_event.set()
                pending_commands.clear()
                _write_response_and_log(ResponseEnvelope.ok(command_id))
                return
            pending_commands.append(message)

        try:
            while True:
                # 1) Reap any finished command thread.  Only clear cancel_event
                #    AFTER we have observed is_alive() == False, so an
                #    uninterruptible command cannot race ahead of its cancel.
                if current_cmd_thread is not None and not current_cmd_thread.is_alive():
                    current_cmd_thread.join()
                    current_cmd_thread = None
                    cancel_event.clear()

                # 2) Termination: EOF observed AND nothing still running AND
                #    no regular commands still queued.  EOF alone does not
                #    drop pending commands – the client may have pipelined
                #    them before closing stdin – but an explicit ``cancel``
                #    does (pending_commands is cleared in its handler).
                if eof_seen and current_cmd_thread is None and not pending_commands:
                    return fatal_exit_code if fatal_exit_code is not None else 0

                # 3) Schedule the next regular command, but only when no
                #    previous worker is still alive and no cancel is pending.
                if (
                    current_cmd_thread is None
                    and pending_commands
                    and not cancel_event.is_set()
                ):
                    message = pending_commands.popleft()

                    def _run_command(msg: Mapping[str, Any] = message) -> None:
                        response = self.dispatcher.handle_message(msg)
                        _write_response_and_log(response)

                    current_cmd_thread = threading.Thread(target=_run_command, daemon=True)
                    current_cmd_thread.start()
                    # Loop again so we keep draining the inbox concurrently.
                    continue

                # 4) Drain the inbox.  While a command is running we poll with
                #    a short timeout so the loop can reap a finished worker
                #    even if no new frames arrive.  While idle we block
                #    indefinitely waiting for the next frame.
                if current_cmd_thread is not None:
                    try:
                        kind, item = inbox.get(timeout=_MAIN_LOOP_POLL_INTERVAL)
                    except queue.Empty:
                        continue
                else:
                    kind, item = inbox.get()
                _handle_item(kind, item)
        finally:
            # Signal the stdin reader to stop pushing to the inbox so it will
            # not block forever on ``put`` once the main loop is gone.
            shutdown_flag.set()

    def write_response(self, response: ResponseEnvelope) -> None:
        self.output_stream.write(encode_native_message(response))
        self.output_stream.flush()

    def _log(self, message: str) -> None:
        self.error_stream.write(f"python-input-control: {message}\n")
        self.error_stream.flush()


def _bounded_put(
    inbox: queue.Queue[tuple[str, Any]],
    item: tuple[str, Any],
    shutdown_flag: threading.Event,
) -> bool:
    """
    Put ``item`` on the bounded inbox, applying back-pressure to the writer.

    Returns True on success, False if the host is shutting down and the frame
    had to be dropped.  Under sustained overload the reader thread is held
    back (reducing stdin read rate) instead of growing the queue without
    bound.
    """
    while not shutdown_flag.is_set():
        try:
            inbox.put(item, timeout=_INBOX_PUT_TIMEOUT)
            return True
        except queue.Full:
            # Keep retrying while the host is still running so the frame is
            # eventually delivered rather than silently dropped.
            continue
    return False


def run_host(dispatcher: CommandDispatcher | None = None) -> int:
    host = NativeMessagingHost(
        dispatcher=dispatcher or CommandDispatcher(),
        input_stream=sys.stdin.buffer,
        output_stream=sys.stdout.buffer,
        error_stream=sys.stderr,
    )
    return host.serve_forever()
