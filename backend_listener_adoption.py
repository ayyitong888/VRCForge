from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import socket
import struct
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping

from primitive_basis_live_attestation import (
    LIVE_STDIN_ENV,
    PrimitiveBasisLiveSession,
    create_packaged_live_session_from_bytes,
)


BACKEND_ADOPTION_ENV = "VRCFORGE_BACKEND_ADOPTION_STDIO"
BACKEND_ADOPTION_FRAME_DOMAIN = b"vrcforge-authority-backend-adoption-frame-v1\0"
BACKEND_ADOPTION_ACK_DOMAIN = b"vrcforge-authority-backend-adoption-ack-v1\0"
BACKEND_ADOPTION_FRAME_MAGIC = b"VRCBSH01"
BACKEND_ADOPTION_ACK_MAGIC = b"VRCBAK01"
BACKEND_ADOPTION_PROTOCOL_VERSION = 1
INNER_LIVE_BOOTSTRAP_VERSION = 4
INNER_LIVE_BOOTSTRAP_MAGIC = b"VRCFPRIMLIVE4\0\0\0"
INNER_LIVE_BOOTSTRAP_BYTES = 400
MAX_BACKEND_ADOPTION_PAYLOAD_BYTES = 256 * 1024
MAX_SOCKET_SHARE_BYTES = 8 * 1024
BACKEND_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES = 191
BACKEND_ADOPTION_ACK_PAYLOAD_BYTES = 261
BACKEND_ADOPTION_ROLE_APP = 1
ADDRESS_FAMILY_IPV4 = 2
SOCKET_TYPE_STREAM = 1
PROTOCOL_TCP = 6
LOOPBACK_IPV4_NETWORK_ORDER = 0x7F00_0001
APP_LOOPBACK_PORT = 8757
SO_EXCLUSIVEADDRUSE_OPTION = getattr(socket, "SO_EXCLUSIVEADDRUSE", -5)

ACK_FLAG_SOCKET_FROM_SHARE = 1 << 0
ACK_FLAG_GETSOCKNAME_VERIFIED = 1 << 1
ACK_FLAG_TYPE_PROTOCOL_VERIFIED = 1 << 2
ACK_FLAG_OPTIONS_VERIFIED = 1 << 3
ACK_FLAG_BOOTSTRAP_PARSED = 1 << 4
ACK_FLAG_ORDINARY_BIND_DISABLED = 1 << 5
ACK_FLAG_FRAME_COMPLETE = 1 << 6
BACKEND_ADOPTION_ACK_REQUIRED_FLAGS = (
    ACK_FLAG_SOCKET_FROM_SHARE
    | ACK_FLAG_GETSOCKNAME_VERIFIED
    | ACK_FLAG_TYPE_PROTOCOL_VERIFIED
    | ACK_FLAG_OPTIONS_VERIFIED
    | ACK_FLAG_BOOTSTRAP_PARSED
    | ACK_FLAG_ORDINARY_BIND_DISABLED
    | ACK_FLAG_FRAME_COMPLETE
)


class BackendListenerAdoptionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BackendAdoptionFrame:
    run_binding_digest: bytes
    private_pipe_binding_digest: bytes
    listener_socket_object_id: int
    socket_share_bytes: bytearray = field(repr=False)
    inner_live_bootstrap_bytes: bytearray = field(repr=False)
    inner_live_bootstrap_digest: bytes
    challenge: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _valid_digest(self.run_binding_digest)
            or not _valid_digest(self.private_pipe_binding_digest)
            or not 0 < self.listener_socket_object_id <= 0xFFFF_FFFF_FFFF_FFFF
            or not 0 < len(self.socket_share_bytes) <= MAX_SOCKET_SHARE_BYTES
            or len(self.inner_live_bootstrap_bytes) != INNER_LIVE_BOOTSTRAP_BYTES
            or not self.inner_live_bootstrap_bytes.startswith(INNER_LIVE_BOOTSTRAP_MAGIC)
            or not _valid_digest(self.inner_live_bootstrap_digest)
            or not hmac.compare_digest(
                hashlib.sha256(self.inner_live_bootstrap_bytes).digest(),
                self.inner_live_bootstrap_digest,
            )
            or not _valid_digest(self.challenge)
        ):
            raise BackendListenerAdoptionError("backend_adoption_frame_invalid")


@dataclass(frozen=True)
class BackendProcessIdentity:
    process_id: int
    process_creation_time: int
    executable_digest: bytes
    image_identity_digest: bytes

    def __post_init__(self) -> None:
        if (
            not 0 < self.process_id <= 0xFFFF_FFFF
            or not 0 < self.process_creation_time <= 0xFFFF_FFFF_FFFF_FFFF
            or not _valid_digest(self.executable_digest)
            or not _valid_digest(self.image_identity_digest)
        ):
            raise BackendListenerAdoptionError("backend_adoption_identity_invalid")


class BackendListenerAdoption:
    def __init__(
        self,
        *,
        listener_socket: Any,
        live_session: PrimitiveBasisLiveSession,
        ack_bytes: bytes,
        ack_stream: BinaryIO | None = None,
    ) -> None:
        self.listener_socket = listener_socket
        self.live_session = live_session
        self.ack_bytes = ack_bytes
        self._ack_stream = ack_stream
        self._state_lock = threading.Lock()
        self._ack_attempted = False
        self._acknowledged = False
        self._closed = False

    @property
    def acknowledged(self) -> bool:
        with self._state_lock:
            return self._acknowledged

    def acknowledge(self, stream: BinaryIO | None = None) -> None:
        with self._state_lock:
            if self._ack_attempted:
                raise BackendListenerAdoptionError("backend_adoption_ack_duplicate")
            if self._closed:
                raise BackendListenerAdoptionError("backend_adoption_closed")
            target = self._ack_stream if stream is None else stream
            if target is None:
                raise BackendListenerAdoptionError("backend_adoption_ack_stream_missing")
            self._ack_attempted = True
            write_backend_adoption_ack(target, self.ack_bytes)
            self._acknowledged = True

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        try:
            _close_socket(self.listener_socket)
        finally:
            self.live_session.close()


def backend_listener_adoption_requested(
    environ: Mapping[str, str] | None = None,
) -> bool:
    environment = os.environ if environ is None else environ
    mode = environment.get(BACKEND_ADOPTION_ENV)
    if mode is not None and environment.get(LIVE_STDIN_ENV) is not None:
        raise BackendListenerAdoptionError("backend_adoption_mode_conflict")
    if mode is None:
        return False
    if mode != "1":
        raise BackendListenerAdoptionError("backend_adoption_mode_invalid")
    return True


def parse_backend_adoption_frame(
    payload: bytes | bytearray | memoryview,
) -> BackendAdoptionFrame:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
    return _parse_owned_backend_adoption_frame(bytearray(payload))


def _parse_owned_backend_adoption_frame(frame: bytearray) -> BackendAdoptionFrame:
    socket_share_bytes: bytearray | None = None
    inner_bootstrap: bytearray | None = None
    try:
        if len(frame) < 14 + BACKEND_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES:
            raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
        if frame[:8] != BACKEND_ADOPTION_FRAME_MAGIC:
            raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
        version, payload_length = struct.unpack(">HI", frame[8:14])
        if (
            version != BACKEND_ADOPTION_PROTOCOL_VERSION
            or payload_length > MAX_BACKEND_ADOPTION_PAYLOAD_BYTES
            or payload_length != len(frame) - 14
        ):
            raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
        digest_offset = len(frame) - 32
        frame_digest = hashlib.sha256()
        frame_digest.update(BACKEND_ADOPTION_FRAME_DOMAIN)
        frame_digest.update(memoryview(frame)[:digest_offset])
        if not hmac.compare_digest(frame[digest_offset:], frame_digest.digest()):
            raise BackendListenerAdoptionError("backend_adoption_frame_digest_invalid")

        offset = 14
        raw, offset = _take(frame, offset, 32)
        run_binding_digest = bytes(raw)
        raw, offset = _take(frame, offset, 32)
        private_pipe_binding_digest = bytes(raw)
        fields, offset = _take(frame, offset, 25)
        role, address_family, socket_type, protocol, address, port, listener_id, share_length = (
            struct.unpack(">BHHHIHQI", fields)
        )
        if not 0 < share_length <= MAX_SOCKET_SHARE_BYTES:
            raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
        raw, offset = _take(frame, offset, share_length)
        socket_share_bytes = bytearray(raw)
        fields, offset = _take(frame, offset, 6)
        bootstrap_version, bootstrap_length = struct.unpack(">HI", fields)
        if bootstrap_length != INNER_LIVE_BOOTSTRAP_BYTES:
            raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
        raw, offset = _take(frame, offset, bootstrap_length)
        inner_bootstrap = bytearray(raw)
        raw, offset = _take(frame, offset, 32)
        bootstrap_digest = bytes(raw)
        raw, offset = _take(frame, offset, 32)
        challenge = bytes(raw)
        if offset != digest_offset:
            raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
        if (
            role != BACKEND_ADOPTION_ROLE_APP
            or address_family != ADDRESS_FAMILY_IPV4
            or socket_type != SOCKET_TYPE_STREAM
            or protocol != PROTOCOL_TCP
            or address != LOOPBACK_IPV4_NETWORK_ORDER
            or port != APP_LOOPBACK_PORT
            or listener_id == 0
            or bootstrap_version != INNER_LIVE_BOOTSTRAP_VERSION
            or not inner_bootstrap.startswith(INNER_LIVE_BOOTSTRAP_MAGIC)
            or not _valid_digest(run_binding_digest)
            or not _valid_digest(private_pipe_binding_digest)
            or not _valid_digest(challenge)
            or not hmac.compare_digest(
                hashlib.sha256(inner_bootstrap).digest(), bootstrap_digest
            )
        ):
            raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
        result = BackendAdoptionFrame(
            run_binding_digest=run_binding_digest,
            private_pipe_binding_digest=private_pipe_binding_digest,
            listener_socket_object_id=listener_id,
            socket_share_bytes=socket_share_bytes,
            inner_live_bootstrap_bytes=inner_bootstrap,
            inner_live_bootstrap_digest=bootstrap_digest,
            challenge=challenge,
        )
        socket_share_bytes = None
        inner_bootstrap = None
        return result
    finally:
        _erase_mutable(frame)
        if socket_share_bytes is not None:
            _erase_mutable(socket_share_bytes)
        if inner_bootstrap is not None:
            _erase_mutable(inner_bootstrap)


def read_backend_adoption_frame(stream: BinaryIO) -> BackendAdoptionFrame:
    header = _read_exact_mutable(stream, 14)
    if header[:8] != BACKEND_ADOPTION_FRAME_MAGIC:
        raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
    version, payload_length = struct.unpack(">HI", header[8:14])
    if (
        version != BACKEND_ADOPTION_PROTOCOL_VERSION
        or payload_length < BACKEND_ADOPTION_FRAME_FIXED_PAYLOAD_BYTES
        or payload_length > MAX_BACKEND_ADOPTION_PAYLOAD_BYTES
    ):
        raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
    header.extend(_read_exact_mutable(stream, payload_length))
    return _parse_owned_backend_adoption_frame(header)


def adopt_backend_listener(
    frame: BackendAdoptionFrame,
    *,
    socket_fromshare: Callable[[bytes | bytearray], Any] | None = None,
    identity_provider: Callable[[], BackendProcessIdentity] | None = None,
    executable_path: Path | str | None = None,
    frozen: bool | None = None,
    now: Callable[[], datetime] | None = None,
    ack_stream: BinaryIO | None = None,
) -> BackendListenerAdoption:
    factory = socket_fromshare
    if factory is None:
        if os.name != "nt" or not hasattr(socket, "fromshare"):
            raise BackendListenerAdoptionError("backend_adoption_platform_unsupported")
        factory = socket.fromshare
    provider = identity_provider or (
        lambda: current_backend_process_identity(executable_path=executable_path)
    )
    try:
        identity = provider()
    except BackendListenerAdoptionError:
        raise
    except Exception as exc:
        raise BackendListenerAdoptionError("backend_adoption_identity_unavailable") from exc
    if not isinstance(identity, BackendProcessIdentity):
        raise BackendListenerAdoptionError("backend_adoption_identity_invalid")
    if identity.executable_digest != _inner_backend_executable_digest(frame):
        raise BackendListenerAdoptionError("backend_adoption_identity_mismatch")
    try:
        live_session = create_packaged_live_session_from_bytes(
            frame.inner_live_bootstrap_bytes,
            executable_path=executable_path,
            frozen=frozen,
            now=now,
        )
    except Exception as exc:
        _erase_mutable(frame.socket_share_bytes)
        raise BackendListenerAdoptionError("backend_adoption_bootstrap_invalid") from exc
    finally:
        _erase_mutable(frame.inner_live_bootstrap_bytes)
    try:
        # CPython's Windows socket API requires an immutable bytes object at
        # this FFI boundary. Keep that single copy scoped to the call and erase
        # the retained mutable frame immediately afterwards.
        listener = factory(bytes(frame.socket_share_bytes))
    except Exception as exc:
        live_session.close()
        raise BackendListenerAdoptionError("backend_adoption_socket_share_invalid") from exc
    finally:
        _erase_mutable(frame.socket_share_bytes)
    try:
        _validate_adopted_listener(listener)
        ack_bytes = encode_backend_adoption_ack(frame, identity)
        return BackendListenerAdoption(
            listener_socket=listener,
            live_session=live_session,
            ack_bytes=ack_bytes,
            ack_stream=ack_stream,
        )
    except BackendListenerAdoptionError:
        _close_socket(listener)
        live_session.close()
        raise
    except Exception as exc:
        _close_socket(listener)
        live_session.close()
        raise BackendListenerAdoptionError("backend_adoption_ack_invalid") from exc


def load_backend_listener_adoption(
    *,
    environ: Mapping[str, str] | None = None,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    socket_fromshare: Callable[[bytes | bytearray], Any] | None = None,
    identity_provider: Callable[[], BackendProcessIdentity] | None = None,
    executable_path: Path | str | None = None,
    frozen: bool | None = None,
    now: Callable[[], datetime] | None = None,
) -> BackendListenerAdoption | None:
    if not backend_listener_adoption_requested(environ):
        return None
    source = sys.stdin.buffer if input_stream is None else input_stream
    target = sys.stdout.buffer if output_stream is None else output_stream
    result = adopt_backend_listener(
        read_backend_adoption_frame(source),
        socket_fromshare=socket_fromshare,
        identity_provider=identity_provider,
        executable_path=executable_path,
        frozen=frozen,
        now=now,
        ack_stream=target,
    )
    if environ is None:
        os.environ.pop(BACKEND_ADOPTION_ENV, None)
        os.environ.pop(LIVE_STDIN_ENV, None)
    return result


def encode_backend_adoption_ack(
    frame: BackendAdoptionFrame,
    identity: BackendProcessIdentity,
) -> bytes:
    if not isinstance(identity, BackendProcessIdentity):
        raise BackendListenerAdoptionError("backend_adoption_identity_invalid")
    payload_without_digest = b"".join(
        (
            frame.run_binding_digest,
            frame.private_pipe_binding_digest,
            frame.challenge,
            struct.pack(
                ">BHHHIHQH",
                BACKEND_ADOPTION_ROLE_APP,
                ADDRESS_FAMILY_IPV4,
                SOCKET_TYPE_STREAM,
                PROTOCOL_TCP,
                LOOPBACK_IPV4_NETWORK_ORDER,
                APP_LOOPBACK_PORT,
                frame.listener_socket_object_id,
                INNER_LIVE_BOOTSTRAP_VERSION,
            ),
            frame.inner_live_bootstrap_digest,
            struct.pack(">IQ", identity.process_id, identity.process_creation_time),
            identity.executable_digest,
            identity.image_identity_digest,
            struct.pack(">H", BACKEND_ADOPTION_ACK_REQUIRED_FLAGS),
        )
    )
    prefix = (
        BACKEND_ADOPTION_ACK_MAGIC
        + struct.pack(
            ">HI", BACKEND_ADOPTION_PROTOCOL_VERSION, BACKEND_ADOPTION_ACK_PAYLOAD_BYTES
        )
        + payload_without_digest
    )
    ack = prefix + hashlib.sha256(BACKEND_ADOPTION_ACK_DOMAIN + prefix).digest()
    if len(ack) != 14 + BACKEND_ADOPTION_ACK_PAYLOAD_BYTES:
        raise BackendListenerAdoptionError("backend_adoption_ack_invalid")
    return ack


def write_backend_adoption_ack(stream: BinaryIO, ack: bytes) -> None:
    if (
        len(ack) != 14 + BACKEND_ADOPTION_ACK_PAYLOAD_BYTES
        or ack[:8] != BACKEND_ADOPTION_ACK_MAGIC
        or struct.unpack(">H", ack[8:10])[0] != BACKEND_ADOPTION_PROTOCOL_VERSION
        or struct.unpack(">I", ack[10:14])[0] != BACKEND_ADOPTION_ACK_PAYLOAD_BYTES
        or not hmac.compare_digest(
            ack[-32:],
            hashlib.sha256(BACKEND_ADOPTION_ACK_DOMAIN + ack[:-32]).digest(),
        )
    ):
        raise BackendListenerAdoptionError("backend_adoption_ack_invalid")
    offset = 0
    try:
        while offset < len(ack):
            written = stream.write(ack[offset:])
            if (
                not isinstance(written, int)
                or written <= 0
                or written > len(ack) - offset
            ):
                raise OSError("ack write made no progress")
            offset += written
        stream.flush()
    except Exception as exc:
        raise BackendListenerAdoptionError("backend_adoption_ack_write_failed") from exc


def current_backend_process_identity(
    *, executable_path: Path | str | None = None
) -> BackendProcessIdentity:
    if os.name != "nt":
        raise BackendListenerAdoptionError("backend_adoption_platform_unsupported")
    path = Path(sys.executable if executable_path is None else executable_path)
    executable_digest, image_identity_digest = _windows_executable_identity(path)
    return BackendProcessIdentity(
        process_id=os.getpid(),
        process_creation_time=_windows_process_creation_time(),
        executable_digest=executable_digest,
        image_identity_digest=image_identity_digest,
    )


def _validate_adopted_listener(listener: Any) -> None:
    try:
        address = listener.getsockname()
        valid = (
            int(listener.family) == socket.AF_INET
            and int(listener.type) == socket.SOCK_STREAM
            and int(listener.proto) == socket.IPPROTO_TCP
            and isinstance(address, tuple)
            and len(address) >= 2
            and address[0] == "127.0.0.1"
            and int(address[1]) == APP_LOOPBACK_PORT
            and listener.getsockopt(socket.SOL_SOCKET, SO_EXCLUSIVEADDRUSE_OPTION) == 1
            and listener.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) == 0
            and listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        )
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        valid = False
    if not valid:
        raise BackendListenerAdoptionError("backend_adoption_socket_invalid")


def _windows_process_creation_time() -> int:
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

    creation = FileTime()
    exit_time = FileTime()
    kernel = FileTime()
    user = FileTime()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = ()
    get_current_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    )
    get_process_times.restype = wintypes.BOOL
    process = get_current_process()
    if not get_process_times(
        process,
        ctypes.byref(creation),
        ctypes.byref(exit_time),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise BackendListenerAdoptionError("backend_adoption_identity_unavailable")
    value = (int(creation.high) << 32) | int(creation.low)
    if value == 0:
        raise BackendListenerAdoptionError("backend_adoption_identity_unavailable")
    return value


def _windows_executable_identity(path: Path) -> tuple[bytes, bytes]:
    import msvcrt
    from ctypes import wintypes

    class FileId128(ctypes.Structure):
        _fields_ = (("identifier", ctypes.c_ubyte * 16),)

    class FileIdInfo(ctypes.Structure):
        _fields_ = (("volume_serial", ctypes.c_ulonglong), ("file_id", FileId128))

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query = kernel32.GetFileInformationByHandleEx
    query.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    query.restype = wintypes.BOOL

    try:
        with path.open("rb") as stream:
            before = _query_file_id(query, msvcrt.get_osfhandle(stream.fileno()), FileIdInfo)
            digest = hashlib.sha256()
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = _query_file_id(query, msvcrt.get_osfhandle(stream.fileno()), FileIdInfo)
    except (OSError, ValueError) as exc:
        raise BackendListenerAdoptionError("backend_adoption_identity_unavailable") from exc
    if before != after:
        raise BackendListenerAdoptionError("backend_adoption_identity_changed")
    volume_serial, file_id = before
    identity_digest = hashlib.sha256(
        b"vrcforge-authority-file-identity-v1\0"
        + volume_serial.to_bytes(8, "big")
        + file_id
    ).digest()
    return digest.digest(), identity_digest


def _query_file_id(query: Any, handle: int, file_id_type: Any) -> tuple[int, bytes]:
    value = file_id_type()
    if not query(handle, 18, ctypes.byref(value), ctypes.sizeof(value)):
        raise BackendListenerAdoptionError("backend_adoption_identity_unavailable")
    volume_serial = int(value.volume_serial)
    file_id = bytes(value.file_id.identifier)
    if volume_serial == 0 or not any(file_id):
        raise BackendListenerAdoptionError("backend_adoption_identity_unavailable")
    return volume_serial, file_id


def _take(
    payload: bytes | bytearray, offset: int, length: int
) -> tuple[bytes | bytearray, int]:
    end = offset + length
    if length < 0 or end > len(payload):
        raise BackendListenerAdoptionError("backend_adoption_frame_invalid")
    return payload[offset:end], end


def _read_exact_mutable(stream: BinaryIO, length: int) -> bytearray:
    result = bytearray()
    remaining = length
    try:
        while remaining:
            chunk = stream.read(remaining)
            if not isinstance(chunk, (bytes, bytearray, memoryview)) or not chunk:
                raise EOFError
            if len(chunk) > remaining:
                raise ValueError
            result.extend(chunk)
            remaining -= len(chunk)
    except (EOFError, OSError, ValueError) as exc:
        raise BackendListenerAdoptionError("backend_adoption_frame_truncated") from exc
    return result


def _valid_digest(value: bytes) -> bool:
    return isinstance(value, bytes) and len(value) == 32 and any(value)


def _inner_backend_executable_digest(frame: BackendAdoptionFrame) -> bytes:
    # magic, key, challenge, runtime digest, desktop digest, then backend digest
    offset = len(INNER_LIVE_BOOTSTRAP_MAGIC) + (4 * 32)
    digest = bytes(frame.inner_live_bootstrap_bytes[offset : offset + 32])
    if not _valid_digest(digest):
        raise BackendListenerAdoptionError("backend_adoption_bootstrap_invalid")
    return digest


def _close_socket(listener: Any) -> None:
    try:
        listener.close()
    except (AttributeError, OSError):
        pass


def _erase_mutable(value: bytes | bytearray) -> None:
    if isinstance(value, bytearray):
        for index in range(len(value)):
            value[index] = 0
