from __future__ import annotations

import json
import struct
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, BinaryIO, TextIO

from .dispatch import CommandDispatcher
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

    def serve_forever(self) -> int:
        while True:
            try:
                payload = read_native_message(self.input_stream)
            except RecoverableFramingError as exc:
                self._log(str(exc))
                self.write_response(ResponseEnvelope.error_response(None, str(exc)))
                continue
            except FramingError as exc:
                self._log(str(exc))
                self.write_response(ResponseEnvelope.error_response(None, str(exc)))
                return 1

            if payload is None:
                return 0

            response = self._handle_payload(payload)
            self.write_response(response)

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
