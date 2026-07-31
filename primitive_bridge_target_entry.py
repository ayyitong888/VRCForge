from __future__ import annotations

import hashlib
import hmac
import os
import struct
import sys
import threading
from collections.abc import Mapping, Sequence
from typing import BinaryIO

import primitive_bridge_target_adapter as adapter


BRIDGE_TARGET_STDIO_ENV = adapter.BRIDGE_TARGET_STDIO_ENV
BRIDGE_TARGET_PRIVATE_PIPE_RUNTIME_ENABLED = False
_HEADER_BYTES = 14


class BridgeTargetEntryError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class BridgeTargetPrivatePipeWriter:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self._ack_attempted = False
        self._acknowledged = False
        self._shutdown_guard = adapter.BridgeTargetShutdownReplayGuard()
        self._shutdown_request: adapter.BridgeTargetShutdownRequest | None = None
        self._accounting_attempted = False
        self._accounting_written = False
        self._eof_written = False

    @property
    def acknowledged(self) -> bool:
        with self._lock:
            return self._acknowledged

    @property
    def accounting_written(self) -> bool:
        with self._lock:
            return self._accounting_written

    @property
    def shutdown_verified(self) -> bool:
        with self._lock:
            return self._shutdown_request is not None

    @property
    def eof_written(self) -> bool:
        with self._lock:
            return self._eof_written

    def write_ack(self, value: bytes) -> None:
        with self._lock:
            if self._ack_attempted:
                raise BridgeTargetEntryError("bridge_target_ack_duplicate")
            self._ack_attempted = True
            _write_fixed_frame(
                self._stream,
                value,
                magic=adapter.BRIDGE_TARGET_ACK_MAGIC,
                payload_bytes=adapter.BRIDGE_TARGET_ACK_PAYLOAD_BYTES,
                domain=adapter.BRIDGE_TARGET_ACK_DOMAIN,
                error_code="bridge_target_ack_write_failed",
            )
            self._acknowledged = True

    def accept_shutdown(
        self,
        value: bytes | bytearray | memoryview,
        frame: adapter.BridgeTargetFrame,
    ) -> adapter.BridgeTargetShutdownRequest:
        with self._lock:
            if not self._acknowledged:
                raise BridgeTargetEntryError("bridge_target_shutdown_before_ack")
            try:
                request = self._shutdown_guard.consume(value, frame)
            except adapter.BridgeTargetProtocolError as exc:
                raise BridgeTargetEntryError("bridge_target_shutdown_invalid") from exc
            self._shutdown_request = request
            return request

    def write_accounting(self, value: bytes) -> None:
        with self._lock:
            if not self._acknowledged:
                raise BridgeTargetEntryError("bridge_target_accounting_before_ack")
            if self._accounting_attempted:
                raise BridgeTargetEntryError("bridge_target_accounting_duplicate")
            self._accounting_attempted = True
            if self._shutdown_request is None:
                raise BridgeTargetEntryError("bridge_target_shutdown_not_verified")
            try:
                adapter.decode_bridge_target_shutdown_accounting(
                    value, expected=self._shutdown_request
                )
            except adapter.BridgeTargetProtocolError as exc:
                raise BridgeTargetEntryError(
                    "bridge_target_accounting_write_failed"
                ) from exc
            _write_fixed_frame(
                self._stream,
                value,
                magic=adapter.BRIDGE_TARGET_ACCOUNTING_MAGIC,
                payload_bytes=adapter.BRIDGE_TARGET_ACCOUNTING_PAYLOAD_BYTES,
                domain=adapter.BRIDGE_TARGET_ACCOUNTING_DOMAIN,
                error_code="bridge_target_accounting_write_failed",
            )
            self._accounting_written = True
            try:
                self._stream.close()
            except Exception:
                raise BridgeTargetEntryError(
                    "bridge_target_accounting_eof_failed"
                ) from None
            self._eof_written = True


def read_bridge_target_frame(stream: BinaryIO) -> bytearray:
    header = _read_exact(stream, _HEADER_BYTES)
    try:
        if bytes(header[:8]) != adapter.BRIDGE_TARGET_FRAME_MAGIC:
            raise BridgeTargetEntryError("bridge_target_frame_invalid")
        version, payload_bytes = struct.unpack(">HI", header[8:])
        if (
            version != adapter.BRIDGE_TARGET_PROTOCOL_VERSION
            or payload_bytes < adapter.BRIDGE_TARGET_FRAME_FIXED_PAYLOAD_BYTES
            or payload_bytes > adapter.MAX_BRIDGE_TARGET_PAYLOAD_BYTES
        ):
            raise BridgeTargetEntryError("bridge_target_frame_invalid")
        header.extend(_read_exact(stream, payload_bytes))
        return header
    except BaseException:
        header[:] = b"\0" * len(header)
        raise


def decode_parent_bound_bridge_target_frame(
    value: bytes | bytearray | memoryview,
) -> adapter.BridgeTargetFrame:
    try:
        frame = adapter.decode_bridge_target_frame(value)
    except adapter.BridgeTargetProtocolError as exc:
        raise BridgeTargetEntryError("bridge_target_frame_invalid") from exc
    if (
        len(frame.bridge_manifest_digest) != 32
        or not any(frame.bridge_manifest_digest)
        or len(frame.bridge_tree_digest) != 32
        or not any(frame.bridge_tree_digest)
    ):
        frame.clear_sensitive()
        raise BridgeTargetEntryError("bridge_target_parent_runtime_binding_invalid")
    return frame


def read_bridge_target_shutdown_request(
    stream: BinaryIO,
    frame: adapter.BridgeTargetFrame,
    guard: adapter.BridgeTargetShutdownReplayGuard,
) -> adapter.BridgeTargetShutdownRequest:
    encoded = _read_exact(stream, adapter.BRIDGE_TARGET_SHUTDOWN_FRAME_BYTES)
    try:
        return guard.consume(encoded, frame)
    except adapter.BridgeTargetProtocolError as exc:
        raise BridgeTargetEntryError("bridge_target_shutdown_invalid") from exc
    finally:
        encoded[:] = b"\0" * len(encoded)


def run_bridge_target_private_pipe(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    dependencies: adapter.BridgeTargetDependencies | None = None,
) -> adapter.BridgeTargetShutdownAccounting:
    if not BRIDGE_TARGET_PRIVATE_PIPE_RUNTIME_ENABLED:
        raise BridgeTargetEntryError("bridge_target_private_pipe_runtime_unverified")
    encoded_frame = read_bridge_target_frame(input_stream)
    frame: adapter.BridgeTargetFrame | None = None
    try:
        frame = decode_parent_bound_bridge_target_frame(encoded_frame)
        writer = BridgeTargetPrivatePipeWriter(output_stream)

        def await_shutdown(
            active_frame: adapter.BridgeTargetFrame,
        ) -> adapter.BridgeTargetShutdownRequest:
            encoded = _read_exact(
                input_stream, adapter.BRIDGE_TARGET_SHUTDOWN_FRAME_BYTES
            )
            try:
                return writer.accept_shutdown(encoded, active_frame)
            finally:
                encoded[:] = b"\0" * len(encoded)

        accounting = adapter.serve_adopted_bridge_target(
            frame,
            writer.write_ack,
            dependencies,
            await_shutdown=await_shutdown,
        )
        writer.write_accounting(
            adapter.encode_bridge_target_shutdown_accounting(accounting)
        )
        return accounting
    finally:
        encoded_frame[:] = b"\0" * len(encoded_frame)
        if frame is not None:
            frame.clear_sensitive()


def main(
    *,
    environ: Mapping[str, str] | None = None,
    argv: Sequence[str] | None = None,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
) -> int:
    environment = os.environ if environ is None else environ
    arguments = tuple(sys.argv if argv is None else argv)
    try:
        adapter.validate_minimal_bridge_target_child_environment(environment)
    except adapter.BridgeTargetRuntimeError:
        return 1
    if environment.get(BRIDGE_TARGET_STDIO_ENV) != "1" or len(arguments) != 1:
        return 1
    if environ is None:
        os.environ.pop(BRIDGE_TARGET_STDIO_ENV, None)
    source = sys.stdin.buffer if input_stream is None else input_stream
    target = sys.stdout.buffer if output_stream is None else output_stream
    try:
        run_bridge_target_private_pipe(source, target)
    except Exception:
        try:
            print(
                "VRCForge bridge target refused the protected private-pipe session.",
                file=sys.stderr,
            )
        except Exception:
            pass
        return 1
    return 0


def _read_exact(stream: BinaryIO, size: int) -> bytearray:
    value = bytearray()
    try:
        while len(value) < size:
            chunk = stream.read(size - len(value))
            if not isinstance(chunk, (bytes, bytearray, memoryview)) or not chunk:
                raise OSError("private pipe read made no progress")
            if len(chunk) > size - len(value):
                raise OSError("private pipe read exceeded the frame boundary")
            value.extend(chunk)
        return value
    except Exception as exc:
        value[:] = b"\0" * len(value)
        raise BridgeTargetEntryError("bridge_target_frame_read_failed") from exc


def _write_fixed_frame(
    stream: BinaryIO,
    value: bytes,
    *,
    magic: bytes,
    payload_bytes: int,
    domain: bytes,
    error_code: str,
) -> None:
    if (
        not isinstance(value, bytes)
        or len(value) != _HEADER_BYTES + payload_bytes
        or value[:8] != magic
        or struct.unpack(">H", value[8:10])[0]
        != adapter.BRIDGE_TARGET_PROTOCOL_VERSION
        or struct.unpack(">I", value[10:14])[0] != payload_bytes
        or not hmac.compare_digest(
            value[-32:], hashlib.sha256(domain + value[:-32]).digest()
        )
    ):
        raise BridgeTargetEntryError(error_code)
    offset = 0
    try:
        while offset < len(value):
            written = stream.write(value[offset:])
            if (
                not isinstance(written, int)
                or written <= 0
                or written > len(value) - offset
            ):
                raise OSError("private pipe write made no progress")
            offset += written
        stream.flush()
    except Exception:
        raise BridgeTargetEntryError(error_code) from None


if __name__ == "__main__":
    raise SystemExit(main())
