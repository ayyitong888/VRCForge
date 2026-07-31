from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import importlib
import importlib.metadata
import json
import os
import socket
import stat
import struct
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath
from typing import Any, Protocol

import primitive_bridge_target_runtime_verifier as runtime_verifier


BRIDGE_TARGET_FRAME_DOMAIN = b"vrcforge-authority-bridge-target-frame-v1\0"
BRIDGE_TARGET_ACK_DOMAIN = b"vrcforge-authority-bridge-target-ack-frame-v1\0"
BRIDGE_TARGET_SHUTDOWN_DOMAIN = (
    b"vrcforge-authority-bridge-target-shutdown-request-v1\0"
)
BRIDGE_TARGET_ACCOUNTING_DOMAIN = (
    b"vrcforge-authority-bridge-target-shutdown-accounting-v1\0"
)
BRIDGE_TARGET_FRAME_MAGIC = b"VRCBTF01"
BRIDGE_TARGET_ACK_MAGIC = b"VRCBTA01"
BRIDGE_TARGET_SHUTDOWN_MAGIC = b"VRCBSD01"
BRIDGE_TARGET_ACCOUNTING_MAGIC = b"VRCBAC01"
BRIDGE_TARGET_PROTOCOL_VERSION = 1
BRIDGE_TARGET_REQUEST_AUTH_KEY_DOMAIN = (
    b"vrcforge-authority-bridge-target-request-auth-key-v1\0"
)
BRIDGE_TARGET_REQUEST_AUTH_KEY_DIGEST_DOMAIN = (
    b"vrcforge-authority-bridge-target-request-auth-key-digest-v1\0"
)
BRIDGE_TARGET_REQUEST_AUTH_HEALTH_DOMAIN = (
    b"vrcforge-authority-bridge-target-request-auth-health-v1\0"
)
BRIDGE_TARGET_REQUEST_AUTH_PROXY_DOMAIN = (
    b"vrcforge-authority-bridge-target-request-auth-proxy-v1\0"
)
BRIDGE_TARGET_REQUEST_AUTH_PROXY_BEARER_DOMAIN = (
    b"vrcforge-authority-bridge-target-request-auth-proxy-bearer-v2\0"
)
BRIDGE_TARGET_REQUEST_AUTH_HEADER = b"x-vrcforge-bridge-auth"
BRIDGE_TARGET_REQUEST_NONCE_HEADER = b"x-vrcforge-bridge-nonce"
BRIDGE_TARGET_STARTUP_ENV_DIGEST_DOMAIN = (
    b"vrcforge-authority-bridge-target-startup-env-v1\0"
)
BRIDGE_TARGET_STARTUP_ARGV_DIGEST_DOMAIN = (
    b"vrcforge-authority-bridge-target-startup-argv-v1\0"
)
FIXED_STARTUP_ENVIRONMENT = (
    ("UNITY_MCP_SKIP_STARTUP_CONNECT", "1"),
    ("UNITY_MCP_DISABLE_TELEMETRY", "1"),
)
BRIDGE_TARGET_STDIO_ENV = "VRCFORGE_BRIDGE_TARGET_STDIO"
BRIDGE_TARGET_CHILD_ENVIRONMENT_KEYS = (
    "SystemRoot",
    "WINDIR",
    "TEMP",
    "TMP",
    BRIDGE_TARGET_STDIO_ENV,
    *(name for name, _value in FIXED_STARTUP_ENVIRONMENT),
)
_FIXED_CONNECTOR_IMPORT_ENVIRONMENT = (
    ("UNITY_MCP_TELEMETRY_TIMEOUT", "5.0"),
)
_STARTUP_ENVIRONMENT_LOCK = threading.Lock()

FIXED_CONNECTOR_DISTRIBUTION = "mcpforunityserver"
FIXED_CONNECTOR_VERSION = "9.6.8"
FIXED_CONNECTOR_MODULE = "main"
FIXED_CONNECTOR_FACTORY = "create_mcp_server"
FIXED_CONNECTOR_MODULE_SHA256 = bytes.fromhex(
    "e8effb923d0fbd1427f1d89ea6f1d6a69914658b1ba18cd86a52f37ccd269fa4"
)
FIXED_CONNECTOR_MODULE_BYTES = 39_869

ADDRESS_FAMILY_IPV4 = socket.AF_INET
SOCKET_TYPE_STREAM = socket.SOCK_STREAM
PROTOCOL_TCP = socket.IPPROTO_TCP
LOOPBACK_IPV4_NETWORK_ORDER = 0x7F00_0001
LOOPBACK_IPV4_TEXT = "127.0.0.1"
PUBLIC_BRIDGE_PORT = 8_080
APP_PORT = 8_757
MIN_PRIVATE_TARGET_PORT = 1_024
MAX_SOCKET_SHARE_BYTES = 8 * 1_024
MIN_STARTUP_MATERIAL_BYTES = 32
MAX_STARTUP_MATERIAL_BYTES = 4 * 1_024
MAX_BRIDGE_TARGET_PAYLOAD_BYTES = 64 * 1_024
MAX_HEALTH_RESPONSE_BYTES = 4 * 1_024

SOCKET_OPTION_EXCLUSIVE_ADDRESS_USE = getattr(socket, "SO_EXCLUSIVEADDRUSE", -5)
SOCKET_OPTION_REUSE_ADDRESS = socket.SO_REUSEADDR
SOCKET_OPTION_ACCEPT_CONNECTION = socket.SO_ACCEPTCONN

ACK_FLAG_SOCKET_FROM_SHARE = 1 << 0
ACK_FLAG_GETSOCKNAME_VERIFIED = 1 << 1
ACK_FLAG_TYPE_PROTOCOL_VERIFIED = 1 << 2
ACK_FLAG_OPTIONS_VERIFIED = 1 << 3
ACK_FLAG_FACTORY_CREATED = 1 << 4
ACK_FLAG_HTTP_APP_MOUNTED = 1 << 5
ACK_FLAG_HEALTH_READY = 1 << 6
ACK_FLAG_ORDINARY_BIND_DISABLED = 1 << 7
ACK_FLAG_FRAME_COMPLETE = 1 << 8
ACK_FLAG_STARTUP_CONFIGURATION_APPLIED = 1 << 9
ACK_FLAG_REQUEST_AUTH_ENABLED = 1 << 10
BRIDGE_TARGET_ACK_REQUIRED_FLAGS = (
    ACK_FLAG_SOCKET_FROM_SHARE
    | ACK_FLAG_GETSOCKNAME_VERIFIED
    | ACK_FLAG_TYPE_PROTOCOL_VERIFIED
    | ACK_FLAG_OPTIONS_VERIFIED
    | ACK_FLAG_FACTORY_CREATED
    | ACK_FLAG_HTTP_APP_MOUNTED
    | ACK_FLAG_HEALTH_READY
    | ACK_FLAG_ORDINARY_BIND_DISABLED
    | ACK_FLAG_FRAME_COMPLETE
    | ACK_FLAG_STARTUP_CONFIGURATION_APPLIED
    | ACK_FLAG_REQUEST_AUTH_ENABLED
)

_HEADER_BYTES = 14
BRIDGE_TARGET_FRAME_FIXED_PAYLOAD_BYTES = 421
BRIDGE_TARGET_ACK_PAYLOAD_BYTES = 511
BRIDGE_TARGET_SHUTDOWN_PAYLOAD_BYTES = 182
BRIDGE_TARGET_ACCOUNTING_PAYLOAD_BYTES = 318
BRIDGE_TARGET_SHUTDOWN_FRAME_BYTES = _HEADER_BYTES + BRIDGE_TARGET_SHUTDOWN_PAYLOAD_BYTES
BRIDGE_TARGET_ACCOUNTING_FRAME_BYTES = _HEADER_BYTES + BRIDGE_TARGET_ACCOUNTING_PAYLOAD_BYTES
_ROLE_BRIDGE_TARGET = 1

SHUTDOWN_FLAG_GRACEFUL = 1 << 0
SHUTDOWN_FLAG_ACCOUNTING_REQUIRED = 1 << 1
SHUTDOWN_FLAG_CLOSE_AFTER_ACCOUNTING = 1 << 2
BRIDGE_TARGET_SHUTDOWN_REQUIRED_FLAGS = (
    SHUTDOWN_FLAG_GRACEFUL
    | SHUTDOWN_FLAG_ACCOUNTING_REQUIRED
    | SHUTDOWN_FLAG_CLOSE_AFTER_ACCOUNTING
)

ACCOUNTING_FLAG_RUNNER_STOPPED = 1 << 0
ACCOUNTING_FLAG_REQUEST_AUTH_HEADER_STRIPPED = 1 << 1
ACCOUNTING_FLAG_CREDENTIALS_ZEROIZED = 1 << 2
ACCOUNTING_FLAG_FINAL_SNAPSHOT = 1 << 3
BRIDGE_TARGET_ACCOUNTING_REQUIRED_FLAGS = (
    ACCOUNTING_FLAG_RUNNER_STOPPED
    | ACCOUNTING_FLAG_REQUEST_AUTH_HEADER_STRIPPED
    | ACCOUNTING_FLAG_CREDENTIALS_ZEROIZED
    | ACCOUNTING_FLAG_FINAL_SNAPSHOT
)

_WINDOWS_RESERVED_PATH_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)


def _canonical_child_environment_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 32_767
        or value.startswith(("\\\\", "\\\\?\\", "\\\\.\\"))
        or "%" in value
        or any(ord(character) < 32 or character in '<>"|?*' for character in value)
    ):
        raise BridgeTargetRuntimeError(
            "bridge target child environment path is invalid"
        )
    path = PureWindowsPath(value)
    if (
        not path.is_absolute()
        or len(path.drive) != 2
        or path.drive[1] != ":"
        or not path.drive[0].isalpha()
        or path.root != "\\"
        or len(path.parts) < 2
        or str(path) != value
    ):
        raise BridgeTargetRuntimeError(
            "bridge target child environment path is invalid"
        )
    for part in path.parts[1:]:
        if (
            part in (".", "..")
            or part.endswith((" ", "."))
            or ":" in part
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_PATH_NAMES
        ):
            raise BridgeTargetRuntimeError(
                "bridge target child environment path is invalid"
            )
    return value


def build_minimal_bridge_target_child_environment(
    *,
    windows_directory: object,
    private_temp_directory: object,
) -> dict[str, str]:
    """Build the exact non-secret environment for the protected child."""

    windows_path = _canonical_child_environment_path(windows_directory)
    private_temp_path = _canonical_child_environment_path(private_temp_directory)
    windows_folded = windows_path.casefold()
    private_temp_folded = private_temp_path.casefold()
    if private_temp_folded == windows_folded or private_temp_folded.startswith(
        windows_folded + "\\"
    ):
        raise BridgeTargetRuntimeError(
            "bridge target child environment path is invalid"
        )
    environment = {
        "SystemRoot": windows_path,
        "WINDIR": windows_path,
        "TEMP": private_temp_path,
        "TMP": private_temp_path,
        BRIDGE_TARGET_STDIO_ENV: "1",
    }
    environment.update(FIXED_STARTUP_ENVIRONMENT)
    if tuple(environment) != BRIDGE_TARGET_CHILD_ENVIRONMENT_KEYS:
        raise BridgeTargetRuntimeError(
            "bridge target child environment contract is invalid"
        )
    return environment


def validate_minimal_bridge_target_child_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(environment, Mapping):
        raise BridgeTargetRuntimeError(
            "bridge target child environment is invalid"
        )
    items = list(environment.items())
    if (
        len(items) != len(BRIDGE_TARGET_CHILD_ENVIRONMENT_KEYS)
        or any(type(name) is not str or type(value) is not str for name, value in items)
        or {name for name, _value in items} != set(BRIDGE_TARGET_CHILD_ENVIRONMENT_KEYS)
    ):
        raise BridgeTargetRuntimeError(
            "bridge target child environment is invalid"
        )
    expected = build_minimal_bridge_target_child_environment(
        windows_directory=environment.get("SystemRoot"),
        private_temp_directory=environment.get("TEMP"),
    )
    if dict(items) != expected:
        raise BridgeTargetRuntimeError(
            "bridge target child environment is invalid"
        )
    return expected


class BridgeTargetProtocolError(ValueError):
    pass


class BridgeTargetRuntimeError(RuntimeError):
    pass


class BridgeTargetForcedContainmentRequired(BridgeTargetRuntimeError):
    graceful_accounting_allowed = False
    runner_still_alive = True


class _AdoptedSocket(Protocol):
    family: int
    type: int
    proto: int

    def getsockname(self) -> tuple[str, int]: ...

    def getsockopt(self, level: int, option: int) -> int: ...

    def close(self) -> None: ...


class _RuntimeDependencyLease(Protocol):
    bridge_manifest_digest: bytes
    bridge_tree_digest: bytes
    adapter_executable_digest: bytes

    def verify_unchanged(self) -> None: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class BridgeTargetFrame:
    run_binding_digest: bytes
    ticket_digest: bytes
    bridge_launch_binding_digest: bytes
    private_pipe_binding_digest: bytes
    challenge: bytearray = field(repr=False)
    adapter_executable_digest: bytes
    bridge_manifest_digest: bytes
    bridge_tree_digest: bytes
    private_pipe_instance_id: int
    target_port: int
    listener_socket_object_id: int
    socket_share_digest: bytes
    startup_material_digest: bytes
    request_auth_key_digest: bytes
    socket_share: bytearray = field(repr=False)
    startup_material: bytearray = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.challenge, bytearray):
            self.challenge = bytearray(self.challenge)
        if not isinstance(self.socket_share, bytearray):
            self.socket_share = bytearray(self.socket_share)
        if not isinstance(self.startup_material, bytearray):
            self.startup_material = bytearray(self.startup_material)
        invalid = (
            not _valid_digest(self.run_binding_digest)
            or not _valid_digest(self.ticket_digest)
            or not _valid_digest(self.bridge_launch_binding_digest)
            or not _valid_digest(self.private_pipe_binding_digest)
            or not _valid_digest(self.challenge)
            or not _valid_digest(self.adapter_executable_digest)
            or not _valid_digest(self.bridge_manifest_digest)
            or not _valid_digest(self.bridge_tree_digest)
            or self.private_pipe_instance_id <= 0
            or self.private_pipe_instance_id > 0xFFFF_FFFF_FFFF_FFFF
            or self.target_port < MIN_PRIVATE_TARGET_PORT
            or self.target_port > 0xFFFF
            or self.target_port in (PUBLIC_BRIDGE_PORT, APP_PORT)
            or self.listener_socket_object_id <= 0
            or self.listener_socket_object_id > 0xFFFF_FFFF_FFFF_FFFF
            or not 0 < len(self.socket_share) <= MAX_SOCKET_SHARE_BYTES
            or not MIN_STARTUP_MATERIAL_BYTES
            <= len(self.startup_material)
            <= MAX_STARTUP_MATERIAL_BYTES
            or not _valid_digest(self.socket_share_digest)
            or not _valid_digest(self.startup_material_digest)
            or not _valid_digest(self.request_auth_key_digest)
            or hashlib.sha256(self.socket_share).digest() != self.socket_share_digest
            or hashlib.sha256(self.startup_material).digest()
            != self.startup_material_digest
            or _derive_request_auth_key_digest(self)
            != self.request_auth_key_digest
        )
        if invalid:
            self.clear_sensitive()
            raise BridgeTargetProtocolError("bridge target frame fields are invalid")

    def clear_sensitive(self) -> None:
        self.clear_challenge()
        self.clear_socket_share()
        self.clear_startup_material()

    def clear_challenge(self) -> None:
        self.challenge[:] = b"\0" * len(self.challenge)

    def clear_socket_share(self) -> None:
        self.socket_share[:] = b"\0" * len(self.socket_share)

    def clear_startup_material(self) -> None:
        self.startup_material[:] = b"\0" * len(self.startup_material)


@dataclass(frozen=True, slots=True)
class BridgeTargetProcessIdentity:
    pid: int
    creation_time: int
    executable_digest: bytes
    image_identity_digest: bytes


class FixedConnectorModulePreflight:
    __slots__ = (
        "path",
        "source_digest",
        "record_digest",
        "file_identity",
        "_fd",
    )

    def __init__(
        self,
        *,
        path: Path,
        source_digest: bytes,
        record_digest: bytes,
        file_identity: tuple[int, int, int, int, int],
        fd: int,
    ) -> None:
        self.path = path
        self.source_digest = source_digest
        self.record_digest = record_digest
        self.file_identity = file_identity
        self._fd = fd

    def verify_held_file(self) -> bool:
        if self._fd < 0:
            return False
        digest, identity = _read_open_file_identity(self._fd)
        path_identity = _validated_path_identity(self.path)
        return (
            digest == self.source_digest
            and identity == self.file_identity
            and path_identity == self.file_identity
        )

    def close(self) -> None:
        if self._fd < 0:
            return
        fd = self._fd
        self._fd = -1
        os.close(fd)


class BridgeTargetStartupEnvironmentLease:
    __slots__ = (
        "_argv_digest",
        "_before",
        "_before_digest",
        "_closed",
        "_verified_after_digest",
    )

    def __init__(
        self,
        before: dict[str, str],
        argv_digest: bytes,
    ) -> None:
        self._before = before
        self._before_digest = _digest_string_mapping(
            BRIDGE_TARGET_STARTUP_ENV_DIGEST_DOMAIN, before
        )
        self._argv_digest = argv_digest
        self._verified_after_digest: bytes | None = None
        self._closed = False

    def verify_after_import(self, material: memoryview) -> None:
        if self._closed or self._verified_after_digest is not None:
            raise BridgeTargetRuntimeError(
                "bridge target startup environment lease is invalid"
            )
        current = dict(os.environ)
        for name, value in _FIXED_CONNECTOR_IMPORT_ENVIRONMENT:
            if name not in self._before and current.get(name) == value:
                os.environ.pop(name, None)
        current = dict(os.environ)
        expected = dict(self._before)
        expected.update(FIXED_STARTUP_ENVIRONMENT)
        material_encodings = _startup_material_encodings(material)
        if (
            current != expected
            or _strings_contain_material(current.values(), material_encodings)
            or _digest_string_sequence(
                BRIDGE_TARGET_STARTUP_ARGV_DIGEST_DOMAIN, tuple(sys.argv)
            )
            != self._argv_digest
        ):
            self.restore()
            raise BridgeTargetRuntimeError(
                "bridge target startup environment changed unexpectedly"
            )
        self._verified_after_digest = _digest_string_mapping(
            BRIDGE_TARGET_STARTUP_ENV_DIGEST_DOMAIN, current
        )

    def receipt_fields(self) -> tuple[bytes, bytes, bytes]:
        if self._closed or self._verified_after_digest is None:
            raise BridgeTargetRuntimeError(
                "bridge target startup environment lease is invalid"
            )
        return (
            self._before_digest,
            self._verified_after_digest,
            self._argv_digest,
        )

    def restore(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            os.environ.clear()
            os.environ.update(self._before)
        finally:
            self._before.clear()
            _STARTUP_ENVIRONMENT_LOCK.release()


@dataclass(slots=True)
class AppliedBridgeTargetStartupConfiguration:
    receipt: BridgeTargetStartupConfigurationReceipt
    _module: object = field(repr=False)
    _original_config: dict[str, object] = field(repr=False)
    _restored: bool = field(default=False, repr=False)

    def restore(self) -> None:
        if self._restored:
            return
        self._restored = True
        config = getattr(self._module, "config", None)
        if config is not None:
            for name, value in self._original_config.items():
                setattr(config, name, value)


@dataclass(frozen=True, slots=True)
class BridgeTargetStartupConfigurationReceipt:
    material_digest: bytes
    applied_in_memory: bool
    retained_material: bool
    exposed_to_argv: bool
    exposed_to_environment: bool
    exposed_to_log: bool
    startup_connection_disabled: bool = False
    allowed_environment: tuple[tuple[str, str], ...] = ()
    environment_before_digest: bytes = b""
    environment_after_digest: bytes = b""
    argv_digest: bytes = b""
    connector_entry_verified: bool = False
    runtime_dependency_set_verified: bool = False


@dataclass(frozen=True, slots=True)
class BridgeTargetRequestAuthSnapshot:
    controlled_health_requests: int
    proxy_http_requests: int
    proxy_websocket_requests: int
    rejected_requests: int
    bypass_requests: int
    credentials_zeroized: bool

    @property
    def total_target_requests(self) -> int:
        return (
            self.controlled_health_requests
            + self.proxy_http_requests
            + self.proxy_websocket_requests
            + self.rejected_requests
        )


@dataclass(frozen=True, slots=True)
class BridgeTargetShutdownRequest:
    run_binding_digest: bytes
    ticket_digest: bytes
    bridge_launch_binding_digest: bytes
    private_pipe_binding_digest: bytes
    private_pipe_instance_id: int
    sequence: int
    requested_at: int


@dataclass(frozen=True, slots=True)
class BridgeTargetShutdownAccounting:
    run_binding_digest: bytes
    ticket_digest: bytes
    bridge_launch_binding_digest: bytes
    private_pipe_binding_digest: bytes
    private_pipe_instance_id: int
    target_port: int
    listener_socket_object_id: int
    request_auth_key_digest: bytes
    request_auth: BridgeTargetRequestAuthSnapshot
    observed_at_shutdown: int
    owner: BridgeTargetProcessIdentity
    request_auth_header_stripped: bool


class BridgeTargetShutdownReplayGuard:
    __slots__ = ("_attempted", "_request_digest")

    def __init__(self) -> None:
        self._attempted = False
        self._request_digest: bytes | None = None

    @property
    def consumed(self) -> bool:
        return self._request_digest is not None

    @property
    def request_digest(self) -> bytes | None:
        return self._request_digest

    def consume(
        self,
        value: bytes | bytearray | memoryview,
        expected: BridgeTargetFrame,
    ) -> BridgeTargetShutdownRequest:
        if self._attempted:
            raise BridgeTargetProtocolError("bridge target shutdown request replayed")
        self._attempted = True
        request = decode_bridge_target_shutdown_request(value)
        if not _shutdown_request_matches_frame(request, expected):
            raise BridgeTargetProtocolError(
                "bridge target shutdown request binding drifted"
            )
        self._request_digest = hashlib.sha256(value).digest()
        return request


class BridgeTargetAccountingEofDecoder:
    __slots__ = ("_buffer", "_finished", "_request")

    def __init__(self, request: BridgeTargetShutdownRequest) -> None:
        if not isinstance(request, BridgeTargetShutdownRequest):
            raise BridgeTargetProtocolError(
                "bridge target shutdown request is invalid"
            )
        self._request = request
        self._buffer = bytearray()
        self._finished = False

    def feed(self, value: bytes | bytearray | memoryview) -> None:
        if self._finished:
            raise BridgeTargetProtocolError(
                "bridge target shutdown accounting replayed"
            )
        if not isinstance(value, (bytes, bytearray, memoryview)) or not value:
            raise BridgeTargetProtocolError(
                "bridge target shutdown accounting chunk is invalid"
            )
        if len(self._buffer) + len(value) > BRIDGE_TARGET_ACCOUNTING_FRAME_BYTES:
            self._finished = True
            self._buffer[:] = b"\0" * len(self._buffer)
            self._buffer.clear()
            raise BridgeTargetProtocolError(
                "bridge target shutdown accounting is oversized"
            )
        self._buffer.extend(value)

    def finish_eof(self) -> BridgeTargetShutdownAccounting:
        if self._finished:
            raise BridgeTargetProtocolError(
                "bridge target shutdown accounting replayed"
            )
        self._finished = True
        try:
            if len(self._buffer) != BRIDGE_TARGET_ACCOUNTING_FRAME_BYTES:
                raise BridgeTargetProtocolError(
                    "bridge target shutdown accounting is truncated"
                )
            return decode_bridge_target_shutdown_accounting(
                self._buffer, expected=self._request
            )
        finally:
            self._buffer[:] = b"\0" * len(self._buffer)
            self._buffer.clear()


class BridgeTargetRequestAuthState:
    __slots__ = (
        "_bypass_requests",
        "_cleared",
        "_consumed_proxy_nonces",
        "_controlled_health_requests",
        "_health_header",
        "_key_digest",
        "_lock",
        "_proxy_key",
        "_proxy_http_requests",
        "_proxy_websocket_requests",
        "_rejected_requests",
    )

    def __init__(
        self,
        key_digest: bytes,
        health_header: bytearray,
        proxy_key: bytearray,
    ) -> None:
        if (
            not _valid_digest(key_digest)
            or len(health_header) != 64
            or len(proxy_key) != 32
            or not any(proxy_key)
        ):
            health_header[:] = b"\0" * len(health_header)
            proxy_key[:] = b"\0" * len(proxy_key)
            raise BridgeTargetRuntimeError("bridge target request auth is invalid")
        self._key_digest = bytes(key_digest)
        self._health_header = health_header
        self._proxy_key = proxy_key
        self._lock = threading.Lock()
        self._consumed_proxy_nonces: set[bytes] = set()
        self._controlled_health_requests = 0
        self._proxy_http_requests = 0
        self._proxy_websocket_requests = 0
        self._rejected_requests = 0
        self._bypass_requests = 0
        self._cleared = False

    @classmethod
    def from_frame(cls, frame: BridgeTargetFrame) -> BridgeTargetRequestAuthState:
        master = _derive_request_auth_key(frame)
        try:
            key_digest = hashlib.sha256(
                BRIDGE_TARGET_REQUEST_AUTH_KEY_DIGEST_DOMAIN + master
            ).digest()
            if not hmac.compare_digest(key_digest, frame.request_auth_key_digest):
                raise BridgeTargetRuntimeError(
                    "bridge target request auth binding is invalid"
                )
            health = _derive_request_auth_header(
                master, BRIDGE_TARGET_REQUEST_AUTH_HEALTH_DOMAIN
            )
            try:
                proxy_key = bytearray(
                    hmac.new(
                        master,
                        BRIDGE_TARGET_REQUEST_AUTH_PROXY_DOMAIN,
                        hashlib.sha256,
                    ).digest()
                )
            except BaseException:
                health[:] = b"\0" * len(health)
                raise
            return cls(key_digest, health, proxy_key)
        finally:
            master[:] = b"\0" * len(master)

    def __repr__(self) -> str:
        return (
            "BridgeTargetRequestAuthState("
            f"key_digest={self._key_digest.hex()!r}, credentials='[redacted]')"
        )

    @property
    def key_digest(self) -> bytes:
        return self._key_digest

    def health_header_value(self) -> bytes:
        with self._lock:
            return bytes(self._health_header)

    def proxy_bearer_value(
        self,
        nonce: bytes,
        *,
        scope_type: str,
        method: object,
        path: object,
        raw_path: object,
        query_string: object,
    ) -> bytes:
        with self._lock:
            if self._cleared:
                raise BridgeTargetRuntimeError("bridge target request auth is cleared")
            bearer = _derive_proxy_request_bearer(
                self._proxy_key,
                nonce,
                scope_type,
                method,
                path,
                raw_path,
                query_string,
            )
            if bearer is None:
                raise BridgeTargetRuntimeError(
                    "bridge target proxy request binding is invalid"
                )
            return bearer

    def health_header_view(self) -> memoryview:
        with self._lock:
            if self._cleared:
                raise BridgeTargetRuntimeError("bridge target request auth is cleared")
            return memoryview(self._health_header).toreadonly()

    def authorize(
        self,
        scope_type: str,
        method: object,
        path: object,
        raw_path: object,
        query_string: object,
        supplied: bytes,
        supplied_nonce: bytes,
    ) -> str | None:
        with self._lock:
            if self._cleared:
                self._rejected_requests += 1
                return None
            health_match = hmac.compare_digest(supplied, self._health_header)
            if (
                health_match
                and self._controlled_health_requests == 0
                and scope_type == "http"
                and method == "GET"
                and path == "/health"
                and raw_path == b"/health"
                and query_string == b""
                and not supplied_nonce
            ):
                self._controlled_health_requests += 1
                self._health_header[:] = b"\0" * len(self._health_header)
                return "health"
            expected = _derive_proxy_request_bearer(
                self._proxy_key,
                supplied_nonce,
                scope_type,
                method,
                path,
                raw_path,
                query_string,
            )
            nonce_digest = _canonical_proxy_nonce_digest(supplied_nonce)
            if (
                expected is not None
                and nonce_digest is not None
                and nonce_digest not in self._consumed_proxy_nonces
                and hmac.compare_digest(supplied, expected)
            ):
                self._consumed_proxy_nonces.add(nonce_digest)
                if scope_type == "http":
                    self._proxy_http_requests += 1
                else:
                    self._proxy_websocket_requests += 1
                return "proxy"
            self._rejected_requests += 1
            return None

    def reject_bypass(self) -> None:
        with self._lock:
            self._rejected_requests += 1
            self._bypass_requests += 1

    def snapshot(self) -> BridgeTargetRequestAuthSnapshot:
        with self._lock:
            return BridgeTargetRequestAuthSnapshot(
                controlled_health_requests=self._controlled_health_requests,
                proxy_http_requests=self._proxy_http_requests,
                proxy_websocket_requests=self._proxy_websocket_requests,
                rejected_requests=self._rejected_requests,
                bypass_requests=self._bypass_requests,
                credentials_zeroized=self._cleared
                and not any(self._health_header)
                and not any(self._proxy_key)
                and not self._consumed_proxy_nonces,
            )

    def clear(self) -> None:
        with self._lock:
            self._cleared = True
            self._health_header[:] = b"\0" * len(self._health_header)
            self._proxy_key[:] = b"\0" * len(self._proxy_key)
            self._consumed_proxy_nonces.clear()


class BridgeTargetAuthenticatedApp:
    __slots__ = ("_app", "_auth")

    def __init__(self, app: object, auth: BridgeTargetRequestAuthState) -> None:
        if not isinstance(auth, BridgeTargetRequestAuthState):
            raise BridgeTargetRuntimeError("bridge target authenticated app is invalid")
        self._app = app
        self._auth = auth

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._app(scope, receive, send)
            return
        if scope_type not in ("http", "websocket"):
            self._auth.reject_bypass()
            raise BridgeTargetRuntimeError("bridge target ASGI scope is invalid")

        headers = scope.get("headers")
        cleaned_headers: list[tuple[bytes, bytes]] = []
        credentials: list[bytes] = []
        nonces: list[bytes] = []
        malformed = not isinstance(headers, (list, tuple))
        if not malformed:
            for header in headers:
                if (
                    not isinstance(header, (list, tuple))
                    or len(header) != 2
                    or not isinstance(header[0], bytes)
                    or not isinstance(header[1], bytes)
                ):
                    malformed = True
                    break
                name, value = header
                if name.lower() == BRIDGE_TARGET_REQUEST_AUTH_HEADER:
                    credentials.append(value)
                elif name.lower() == BRIDGE_TARGET_REQUEST_NONCE_HEADER:
                    nonces.append(value)
                else:
                    cleaned_headers.append((name, value))

        supplied = credentials[0] if not malformed and len(credentials) == 1 else b""
        supplied_nonce = nonces[0] if not malformed and len(nonces) == 1 else b""
        authorized = self._auth.authorize(
            str(scope_type),
            scope.get("method"),
            scope.get("path"),
            scope.get("raw_path"),
            scope.get("query_string"),
            supplied,
            supplied_nonce,
        )
        if (
            authorized is None
            or malformed
            or len(credentials) != 1
            or len(nonces) > 1
        ):
            if scope_type == "http":
                body = b'{"error":"forbidden"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
            else:
                await send(
                    {"type": "websocket.close", "code": 4403, "reason": "forbidden"}
                )
            return

        guarded_scope = dict(scope)
        guarded_scope["headers"] = cleaned_headers
        await self._app(guarded_scope, receive, send)


@dataclass(frozen=True, slots=True)
class BridgeTargetDependencies:
    socket_from_share: Callable[[bytes], _AdoptedSocket]
    identity_provider: Callable[[], BridgeTargetProcessIdentity]
    package_version: Callable[[str], str]
    module_loader: Callable[[str], object]
    module_verifier: Callable[[object, FixedConnectorModulePreflight | None], bool]
    startup_configurer: Callable[
        [object, memoryview, BridgeTargetStartupEnvironmentLease | None],
        BridgeTargetStartupConfigurationReceipt
        | AppliedBridgeTargetStartupConfiguration,
    ]
    config_factory: Callable[..., object]
    server_factory: Callable[[object], object]
    health_probe: Callable[[int, float, memoryview], bool]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    shutdown_clock: Callable[[], int] = time.monotonic_ns
    readiness_timeout_seconds: float = 15.0
    runner_shutdown_timeout_seconds: float = 15.0
    module_preflight: Callable[[], FixedConnectorModulePreflight] | None = None
    startup_environment_preparer: Callable[
        [memoryview], BridgeTargetStartupEnvironmentLease
    ] | None = None
    runtime_dependency_preflight: Callable[
        [BridgeTargetFrame], _RuntimeDependencyLease
    ] | None = None


class _Reader:
    def __init__(
        self, value: bytes | bytearray | memoryview, offset: int, limit: int
    ) -> None:
        self._value = value
        self.offset = offset
        self._limit = limit

    def take(self, size: int) -> bytes | bytearray | memoryview:
        if size < 0 or self.offset + size > self._limit:
            raise BridgeTargetProtocolError("bridge target frame is truncated")
        value = self._value[self.offset : self.offset + size]
        self.offset += size
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack(">Q", self.take(8))[0]


def _take_digest(reader: _Reader) -> bytes:
    value = bytes(reader.take(32))
    if not any(value):
        raise BridgeTargetProtocolError("bridge target frame contains an empty binding")
    return value


def _valid_digest(value: object) -> bool:
    return isinstance(value, (bytes, bytearray)) and len(value) == 32 and any(value)


def _derive_request_auth_key(frame: BridgeTargetFrame) -> bytearray:
    mac = hmac.new(frame.startup_material, digestmod=hashlib.sha256)
    mac.update(BRIDGE_TARGET_REQUEST_AUTH_KEY_DOMAIN)
    mac.update(frame.run_binding_digest)
    mac.update(frame.ticket_digest)
    mac.update(frame.bridge_launch_binding_digest)
    mac.update(frame.private_pipe_binding_digest)
    mac.update(frame.challenge)
    mac.update(frame.adapter_executable_digest)
    mac.update(frame.bridge_manifest_digest)
    mac.update(frame.bridge_tree_digest)
    mac.update(struct.pack(">Q", frame.private_pipe_instance_id))
    mac.update(struct.pack(">H", frame.target_port))
    mac.update(struct.pack(">Q", frame.listener_socket_object_id))
    mac.update(frame.socket_share_digest)
    mac.update(frame.startup_material_digest)
    return bytearray(mac.digest())


def _derive_request_auth_key_digest(frame: BridgeTargetFrame) -> bytes:
    master = _derive_request_auth_key(frame)
    try:
        return hashlib.sha256(
            BRIDGE_TARGET_REQUEST_AUTH_KEY_DIGEST_DOMAIN + master
        ).digest()
    finally:
        master[:] = b"\0" * len(master)


def _derive_request_auth_header(master: bytearray, domain: bytes) -> bytearray:
    return bytearray(hmac.new(master, domain, hashlib.sha256).hexdigest().encode("ascii"))


def _canonical_proxy_nonce_digest(value: object) -> bytes | None:
    if (
        not isinstance(value, bytes)
        or len(value) != 64
        or any(byte not in b"0123456789abcdef" for byte in value)
    ):
        return None
    try:
        decoded = bytes.fromhex(value.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return None
    if len(decoded) != 32 or not any(decoded):
        return None
    return hashlib.sha256(
        BRIDGE_TARGET_REQUEST_AUTH_PROXY_BEARER_DOMAIN + decoded
    ).digest()


def _canonical_proxy_request_binding(
    scope_type: object,
    method: object,
    path: object,
    raw_path: object,
    query_string: object,
) -> bytes | None:
    if scope_type == "http":
        if not isinstance(method, str) or not 1 <= len(method) <= 16:
            return None
        try:
            method_bytes = method.encode("ascii")
        except UnicodeEncodeError:
            return None
        if any(byte < ord("A") or byte > ord("Z") for byte in method_bytes):
            return None
        scope_tag = 1
    elif scope_type == "websocket":
        if method not in (None, ""):
            return None
        method_bytes = b""
        scope_tag = 2
    else:
        return None
    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        return None
    try:
        path_bytes = path.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    if not path_bytes or len(path_bytes) > 4_096:
        return None
    if (
        not isinstance(raw_path, bytes)
        or not raw_path.startswith(b"/")
        or b"\0" in raw_path
        or len(raw_path) > 4_096
        or not isinstance(query_string, bytes)
        or b"\0" in query_string
        or len(query_string) > 8_192
    ):
        return None
    return (
        bytes([scope_tag, len(method_bytes)])
        + method_bytes
        + struct.pack(">H", len(path_bytes))
        + path_bytes
        + struct.pack(">H", len(raw_path))
        + raw_path
        + struct.pack(">H", len(query_string))
        + query_string
    )


def _derive_proxy_request_bearer(
    proxy_key: bytes | bytearray,
    nonce: object,
    scope_type: object,
    method: object,
    path: object,
    raw_path: object,
    query_string: object,
) -> bytes | None:
    nonce_digest = _canonical_proxy_nonce_digest(nonce)
    request_binding = _canonical_proxy_request_binding(
        scope_type,
        method,
        path,
        raw_path,
        query_string,
    )
    if (
        not isinstance(proxy_key, (bytes, bytearray))
        or len(proxy_key) != 32
        or not any(proxy_key)
        or nonce_digest is None
        or request_binding is None
    ):
        return None
    mac = hmac.new(proxy_key, digestmod=hashlib.sha256)
    mac.update(BRIDGE_TARGET_REQUEST_AUTH_PROXY_BEARER_DOMAIN)
    mac.update(nonce_digest)
    mac.update(request_binding)
    return mac.hexdigest().encode("ascii")


def decode_bridge_target_frame(
    value: bytes | bytearray | memoryview,
) -> BridgeTargetFrame:
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) < _HEADER_BYTES:
        raise BridgeTargetProtocolError("bridge target frame header is invalid")
    if value[:8] != BRIDGE_TARGET_FRAME_MAGIC:
        raise BridgeTargetProtocolError("bridge target frame magic is invalid")
    version, payload_size = struct.unpack(">HI", value[8:_HEADER_BYTES])
    if (
        version != BRIDGE_TARGET_PROTOCOL_VERSION
        or payload_size != len(value) - _HEADER_BYTES
        or payload_size < BRIDGE_TARGET_FRAME_FIXED_PAYLOAD_BYTES
        or payload_size > MAX_BRIDGE_TARGET_PAYLOAD_BYTES
    ):
        raise BridgeTargetProtocolError("bridge target frame boundary is invalid")

    digest_offset = len(value) - 32
    frame_digest = hashlib.sha256()
    frame_digest.update(BRIDGE_TARGET_FRAME_DOMAIN)
    frame_digest.update(value[:digest_offset])
    expected_digest = frame_digest.digest()
    if not hmac.compare_digest(bytes(value[digest_offset:]), expected_digest):
        raise BridgeTargetProtocolError("bridge target frame digest is invalid")

    socket_share = bytearray()
    startup_material = bytearray()
    challenge = bytearray()
    try:
        reader = _Reader(value, _HEADER_BYTES, digest_offset)
        run_binding_digest = _take_digest(reader)
        ticket_digest = _take_digest(reader)
        bridge_launch_binding_digest = _take_digest(reader)
        private_pipe_binding_digest = _take_digest(reader)
        challenge = bytearray(_take_digest(reader))
        adapter_executable_digest = _take_digest(reader)
        bridge_manifest_digest = _take_digest(reader)
        bridge_tree_digest = _take_digest(reader)
        private_pipe_instance_id = reader.u64()
        role = reader.u8()
        address_family = reader.u16()
        socket_type = reader.u16()
        protocol = reader.u16()
        address = reader.u32()
        target_port = reader.u16()
        listener_socket_object_id = reader.u64()
        socket_share_size = reader.u32()
        if socket_share_size == 0 or socket_share_size > MAX_SOCKET_SHARE_BYTES:
            raise BridgeTargetProtocolError(
                "bridge target socket share boundary is invalid"
            )
        socket_share = bytearray(reader.take(socket_share_size))
        startup_material_size = reader.u32()
        if (
            startup_material_size < MIN_STARTUP_MATERIAL_BYTES
            or startup_material_size > MAX_STARTUP_MATERIAL_BYTES
        ):
            raise BridgeTargetProtocolError("bridge target startup boundary is invalid")
        startup_material = bytearray(reader.take(startup_material_size))
        socket_share_digest = bytes(reader.take(32))
        startup_material_digest = bytes(reader.take(32))
        request_auth_key_digest = bytes(reader.take(32))
        if reader.offset != digest_offset:
            raise BridgeTargetProtocolError("bridge target frame has trailing fields")

        invalid = (
            role != _ROLE_BRIDGE_TARGET
            or address_family != ADDRESS_FAMILY_IPV4
            or socket_type != SOCKET_TYPE_STREAM
            or protocol != PROTOCOL_TCP
            or address != LOOPBACK_IPV4_NETWORK_ORDER
            or target_port < MIN_PRIVATE_TARGET_PORT
            or target_port in (PUBLIC_BRIDGE_PORT, APP_PORT)
            or listener_socket_object_id == 0
            or private_pipe_instance_id == 0
            or hashlib.sha256(socket_share).digest() != socket_share_digest
            or hashlib.sha256(startup_material).digest() != startup_material_digest
        )
        if invalid:
            raise BridgeTargetProtocolError("bridge target frame fields are invalid")
    except BaseException:
        challenge[:] = b"\0" * len(challenge)
        socket_share[:] = b"\0" * len(socket_share)
        startup_material[:] = b"\0" * len(startup_material)
        raise

    return BridgeTargetFrame(
        run_binding_digest=run_binding_digest,
        ticket_digest=ticket_digest,
        bridge_launch_binding_digest=bridge_launch_binding_digest,
        private_pipe_binding_digest=private_pipe_binding_digest,
        challenge=challenge,
        adapter_executable_digest=adapter_executable_digest,
        bridge_manifest_digest=bridge_manifest_digest,
        bridge_tree_digest=bridge_tree_digest,
        private_pipe_instance_id=private_pipe_instance_id,
        target_port=target_port,
        listener_socket_object_id=listener_socket_object_id,
        socket_share_digest=socket_share_digest,
        startup_material_digest=startup_material_digest,
        request_auth_key_digest=request_auth_key_digest,
        socket_share=socket_share,
        startup_material=startup_material,
    )


def _validate_process_identity(
    frame: BridgeTargetFrame, identity: BridgeTargetProcessIdentity
) -> None:
    if (
        not isinstance(identity, BridgeTargetProcessIdentity)
        or identity.pid <= 0
        or identity.pid > 0xFFFF_FFFF
        or identity.creation_time <= 0
        or identity.creation_time > 0xFFFF_FFFF_FFFF_FFFF
        or len(identity.executable_digest) != 32
        or not any(identity.executable_digest)
        or identity.executable_digest != frame.adapter_executable_digest
        or len(identity.image_identity_digest) != 32
        or not any(identity.image_identity_digest)
    ):
        raise BridgeTargetRuntimeError("bridge target process identity is invalid")


def encode_bridge_target_ack(
    frame: BridgeTargetFrame,
    identity: BridgeTargetProcessIdentity,
    request_auth: BridgeTargetRequestAuthSnapshot,
    flags: int = BRIDGE_TARGET_ACK_REQUIRED_FLAGS,
) -> bytes:
    _validate_process_identity(frame, identity)
    if flags != BRIDGE_TARGET_ACK_REQUIRED_FLAGS:
        raise BridgeTargetRuntimeError("bridge target acknowledgement is incomplete")
    if (
        not isinstance(request_auth, BridgeTargetRequestAuthSnapshot)
        or request_auth.controlled_health_requests != 1
        or request_auth.proxy_http_requests != 0
        or request_auth.proxy_websocket_requests != 0
        or request_auth.rejected_requests != 0
        or request_auth.bypass_requests != 0
        or request_auth.credentials_zeroized
    ):
        raise BridgeTargetRuntimeError(
            "bridge target authenticated health was not observed"
        )
    payload = bytearray()
    payload.extend(frame.run_binding_digest)
    payload.extend(frame.ticket_digest)
    payload.extend(frame.bridge_launch_binding_digest)
    payload.extend(frame.private_pipe_binding_digest)
    payload.extend(frame.challenge)
    payload.extend(frame.adapter_executable_digest)
    payload.extend(frame.bridge_manifest_digest)
    payload.extend(frame.bridge_tree_digest)
    payload.extend(struct.pack(">Q", frame.private_pipe_instance_id))
    payload.extend(
        struct.pack(
            ">BHHHIHQ",
            _ROLE_BRIDGE_TARGET,
            ADDRESS_FAMILY_IPV4,
            SOCKET_TYPE_STREAM,
            PROTOCOL_TCP,
            LOOPBACK_IPV4_NETWORK_ORDER,
            frame.target_port,
            frame.listener_socket_object_id,
        )
    )
    payload.extend(frame.socket_share_digest)
    payload.extend(frame.startup_material_digest)
    payload.extend(frame.request_auth_key_digest)
    payload.extend(
        struct.pack(
            ">IIIII",
            request_auth.controlled_health_requests,
            request_auth.proxy_http_requests,
            request_auth.proxy_websocket_requests,
            request_auth.rejected_requests,
            request_auth.bypass_requests,
        )
    )
    payload.extend(struct.pack(">IQ", identity.pid, identity.creation_time))
    payload.extend(identity.executable_digest)
    payload.extend(identity.image_identity_digest)
    payload.extend(struct.pack(">H", flags))
    header = BRIDGE_TARGET_ACK_MAGIC + struct.pack(
        ">HI", 1, BRIDGE_TARGET_ACK_PAYLOAD_BYTES
    )
    payload.extend(hashlib.sha256(BRIDGE_TARGET_ACK_DOMAIN + header + payload).digest())
    if len(payload) != BRIDGE_TARGET_ACK_PAYLOAD_BYTES:
        raise BridgeTargetRuntimeError("bridge target acknowledgement layout drifted")
    return header + payload


def encode_bridge_target_shutdown_request(
    request: BridgeTargetShutdownRequest,
    flags: int = BRIDGE_TARGET_SHUTDOWN_REQUIRED_FLAGS,
) -> bytes:
    if (
        not isinstance(request, BridgeTargetShutdownRequest)
        or flags != BRIDGE_TARGET_SHUTDOWN_REQUIRED_FLAGS
        or not _valid_digest(request.run_binding_digest)
        or not _valid_digest(request.ticket_digest)
        or not _valid_digest(request.bridge_launch_binding_digest)
        or not _valid_digest(request.private_pipe_binding_digest)
        or not isinstance(request.private_pipe_instance_id, int)
        or isinstance(request.private_pipe_instance_id, bool)
        or not 0 < request.private_pipe_instance_id <= 0xFFFF_FFFF_FFFF_FFFF
        or request.sequence != 1
        or not isinstance(request.requested_at, int)
        or isinstance(request.requested_at, bool)
        or not 0 < request.requested_at <= 0xFFFF_FFFF_FFFF_FFFF
    ):
        raise BridgeTargetProtocolError("bridge target shutdown request is invalid")
    payload = bytearray()
    payload.extend(request.run_binding_digest)
    payload.extend(request.ticket_digest)
    payload.extend(request.bridge_launch_binding_digest)
    payload.extend(request.private_pipe_binding_digest)
    payload.extend(struct.pack(">QIQH", request.private_pipe_instance_id, 1, request.requested_at, flags))
    header = BRIDGE_TARGET_SHUTDOWN_MAGIC + struct.pack(
        ">HI",
        BRIDGE_TARGET_PROTOCOL_VERSION,
        BRIDGE_TARGET_SHUTDOWN_PAYLOAD_BYTES,
    )
    payload.extend(
        hashlib.sha256(BRIDGE_TARGET_SHUTDOWN_DOMAIN + header + payload).digest()
    )
    if len(payload) != BRIDGE_TARGET_SHUTDOWN_PAYLOAD_BYTES:
        raise BridgeTargetProtocolError("bridge target shutdown request layout drifted")
    return header + payload


def decode_bridge_target_shutdown_request(
    value: bytes | bytearray | memoryview,
) -> BridgeTargetShutdownRequest:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise BridgeTargetProtocolError("bridge target shutdown request is invalid")
    encoded = bytes(value)
    if len(encoded) != BRIDGE_TARGET_SHUTDOWN_FRAME_BYTES:
        qualifier = "truncated" if len(encoded) < BRIDGE_TARGET_SHUTDOWN_FRAME_BYTES else "oversized"
        raise BridgeTargetProtocolError(
            f"bridge target shutdown request is {qualifier}"
        )
    if (
        encoded[:8] != BRIDGE_TARGET_SHUTDOWN_MAGIC
        or struct.unpack(">H", encoded[8:10])[0] != BRIDGE_TARGET_PROTOCOL_VERSION
        or struct.unpack(">I", encoded[10:14])[0]
        != BRIDGE_TARGET_SHUTDOWN_PAYLOAD_BYTES
        or not hmac.compare_digest(
            encoded[-32:],
            hashlib.sha256(
                BRIDGE_TARGET_SHUTDOWN_DOMAIN + encoded[:-32]
            ).digest(),
        )
    ):
        raise BridgeTargetProtocolError("bridge target shutdown request is invalid")
    reader = _Reader(encoded, _HEADER_BYTES, len(encoded) - 32)
    request = BridgeTargetShutdownRequest(
        run_binding_digest=_take_digest(reader),
        ticket_digest=_take_digest(reader),
        bridge_launch_binding_digest=_take_digest(reader),
        private_pipe_binding_digest=_take_digest(reader),
        private_pipe_instance_id=reader.u64(),
        sequence=reader.u32(),
        requested_at=reader.u64(),
    )
    flags = reader.u16()
    if reader.offset != len(encoded) - 32:
        raise BridgeTargetProtocolError("bridge target shutdown request layout drifted")
    encode_bridge_target_shutdown_request(request, flags=flags)
    return request


def _shutdown_request_matches_frame(
    request: BridgeTargetShutdownRequest,
    frame: BridgeTargetFrame,
) -> bool:
    return (
        isinstance(frame, BridgeTargetFrame)
        and request.run_binding_digest == frame.run_binding_digest
        and request.ticket_digest == frame.ticket_digest
        and request.bridge_launch_binding_digest == frame.bridge_launch_binding_digest
        and request.private_pipe_binding_digest == frame.private_pipe_binding_digest
        and request.private_pipe_instance_id == frame.private_pipe_instance_id
    )


def encode_bridge_target_shutdown_accounting(
    accounting: BridgeTargetShutdownAccounting,
    flags: int = BRIDGE_TARGET_ACCOUNTING_REQUIRED_FLAGS,
) -> bytes:
    if not isinstance(accounting, BridgeTargetShutdownAccounting):
        raise BridgeTargetRuntimeError("bridge target shutdown accounting is invalid")
    request_auth = accounting.request_auth
    counts = (
        request_auth.controlled_health_requests,
        request_auth.proxy_http_requests,
        request_auth.proxy_websocket_requests,
        request_auth.rejected_requests,
        request_auth.bypass_requests,
    )
    if (
        flags != BRIDGE_TARGET_ACCOUNTING_REQUIRED_FLAGS
        or not _valid_digest(accounting.run_binding_digest)
        or not _valid_digest(accounting.ticket_digest)
        or not _valid_digest(accounting.bridge_launch_binding_digest)
        or not _valid_digest(accounting.private_pipe_binding_digest)
        or not 0 < accounting.private_pipe_instance_id <= 0xFFFF_FFFF_FFFF_FFFF
        or accounting.target_port < MIN_PRIVATE_TARGET_PORT
        or accounting.target_port > 0xFFFF
        or accounting.target_port in (PUBLIC_BRIDGE_PORT, APP_PORT)
        or not 0 < accounting.listener_socket_object_id <= 0xFFFF_FFFF_FFFF_FFFF
        or not _valid_digest(accounting.request_auth_key_digest)
        or not isinstance(request_auth, BridgeTargetRequestAuthSnapshot)
        or request_auth.controlled_health_requests != 1
        or any(not isinstance(value, int) or not 0 <= value <= 0xFFFF_FFFF for value in counts)
        or request_auth.total_target_requests > 0xFFFF_FFFF
        or not request_auth.credentials_zeroized
        or not accounting.request_auth_header_stripped
        or not isinstance(accounting.observed_at_shutdown, int)
        or isinstance(accounting.observed_at_shutdown, bool)
        or not 0 < accounting.observed_at_shutdown <= 0xFFFF_FFFF_FFFF_FFFF
    ):
        raise BridgeTargetRuntimeError("bridge target shutdown accounting is invalid")
    _validate_process_identity_for_accounting(accounting.owner)
    payload = bytearray()
    payload.extend(accounting.run_binding_digest)
    payload.extend(accounting.ticket_digest)
    payload.extend(accounting.bridge_launch_binding_digest)
    payload.extend(accounting.private_pipe_binding_digest)
    payload.extend(struct.pack(">QHQ", accounting.private_pipe_instance_id, accounting.target_port, accounting.listener_socket_object_id))
    payload.extend(accounting.request_auth_key_digest)
    payload.extend(struct.pack(">IIIII", *counts))
    payload.extend(bytes((1, 1)))
    payload.extend(struct.pack(">Q", accounting.observed_at_shutdown))
    payload.extend(struct.pack(">IQ", accounting.owner.pid, accounting.owner.creation_time))
    payload.extend(accounting.owner.executable_digest)
    payload.extend(accounting.owner.image_identity_digest)
    payload.extend(struct.pack(">H", flags))
    header = BRIDGE_TARGET_ACCOUNTING_MAGIC + struct.pack(
        ">HI",
        BRIDGE_TARGET_PROTOCOL_VERSION,
        BRIDGE_TARGET_ACCOUNTING_PAYLOAD_BYTES,
    )
    payload.extend(
        hashlib.sha256(BRIDGE_TARGET_ACCOUNTING_DOMAIN + header + payload).digest()
    )
    if len(payload) != BRIDGE_TARGET_ACCOUNTING_PAYLOAD_BYTES:
        raise BridgeTargetRuntimeError(
            "bridge target shutdown accounting layout drifted"
        )
    return header + payload


def decode_bridge_target_shutdown_accounting(
    value: bytes | bytearray | memoryview,
    *,
    expected: BridgeTargetShutdownRequest | None = None,
) -> BridgeTargetShutdownAccounting:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise BridgeTargetProtocolError(
            "bridge target shutdown accounting is invalid"
        )
    encoded = bytes(value)
    if len(encoded) != BRIDGE_TARGET_ACCOUNTING_FRAME_BYTES:
        qualifier = (
            "truncated"
            if len(encoded) < BRIDGE_TARGET_ACCOUNTING_FRAME_BYTES
            else "oversized"
        )
        raise BridgeTargetProtocolError(
            f"bridge target shutdown accounting is {qualifier}"
        )
    if (
        encoded[:8] != BRIDGE_TARGET_ACCOUNTING_MAGIC
        or struct.unpack(">H", encoded[8:10])[0]
        != BRIDGE_TARGET_PROTOCOL_VERSION
        or struct.unpack(">I", encoded[10:14])[0]
        != BRIDGE_TARGET_ACCOUNTING_PAYLOAD_BYTES
        or not hmac.compare_digest(
            encoded[-32:],
            hashlib.sha256(
                BRIDGE_TARGET_ACCOUNTING_DOMAIN + encoded[:-32]
            ).digest(),
        )
    ):
        raise BridgeTargetProtocolError(
            "bridge target shutdown accounting is invalid"
        )
    reader = _Reader(encoded, _HEADER_BYTES, len(encoded) - 32)
    run_binding_digest = _take_digest(reader)
    ticket_digest = _take_digest(reader)
    bridge_launch_binding_digest = _take_digest(reader)
    private_pipe_binding_digest = _take_digest(reader)
    private_pipe_instance_id = reader.u64()
    target_port = reader.u16()
    listener_socket_object_id = reader.u64()
    request_auth_key_digest = _take_digest(reader)
    counts = tuple(reader.u32() for _ in range(5))
    credentials_zeroized = reader.u8()
    request_auth_header_stripped = reader.u8()
    observed_at_shutdown = reader.u64()
    owner = BridgeTargetProcessIdentity(
        pid=reader.u32(),
        creation_time=reader.u64(),
        executable_digest=_take_digest(reader),
        image_identity_digest=_take_digest(reader),
    )
    flags = reader.u16()
    if (
        reader.offset != len(encoded) - 32
        or credentials_zeroized != 1
        or request_auth_header_stripped != 1
    ):
        raise BridgeTargetProtocolError(
            "bridge target shutdown accounting is noncanonical"
        )
    accounting = BridgeTargetShutdownAccounting(
        run_binding_digest=run_binding_digest,
        ticket_digest=ticket_digest,
        bridge_launch_binding_digest=bridge_launch_binding_digest,
        private_pipe_binding_digest=private_pipe_binding_digest,
        private_pipe_instance_id=private_pipe_instance_id,
        target_port=target_port,
        listener_socket_object_id=listener_socket_object_id,
        request_auth_key_digest=request_auth_key_digest,
        request_auth=BridgeTargetRequestAuthSnapshot(
            controlled_health_requests=counts[0],
            proxy_http_requests=counts[1],
            proxy_websocket_requests=counts[2],
            rejected_requests=counts[3],
            bypass_requests=counts[4],
            credentials_zeroized=True,
        ),
        observed_at_shutdown=observed_at_shutdown,
        owner=owner,
        request_auth_header_stripped=True,
    )
    try:
        canonical = encode_bridge_target_shutdown_accounting(accounting, flags=flags)
    except BridgeTargetRuntimeError as exc:
        raise BridgeTargetProtocolError(str(exc)) from exc
    if not hmac.compare_digest(canonical, encoded):
        raise BridgeTargetProtocolError(
            "bridge target shutdown accounting is noncanonical"
        )
    if expected is not None and not _accounting_matches_shutdown(
        accounting, expected
    ):
        raise BridgeTargetProtocolError(
            "bridge target shutdown accounting binding drifted"
        )
    return accounting


def _accounting_matches_shutdown(
    accounting: BridgeTargetShutdownAccounting,
    request: BridgeTargetShutdownRequest,
) -> bool:
    return (
        isinstance(request, BridgeTargetShutdownRequest)
        and accounting.run_binding_digest == request.run_binding_digest
        and accounting.ticket_digest == request.ticket_digest
        and accounting.bridge_launch_binding_digest
        == request.bridge_launch_binding_digest
        and accounting.private_pipe_binding_digest
        == request.private_pipe_binding_digest
        and accounting.private_pipe_instance_id == request.private_pipe_instance_id
    )


def _validate_process_identity_for_accounting(
    identity: BridgeTargetProcessIdentity,
) -> None:
    if (
        not isinstance(identity, BridgeTargetProcessIdentity)
        or not 0 < identity.pid <= 0xFFFF_FFFF
        or not 0 < identity.creation_time <= 0xFFFF_FFFF_FFFF_FFFF
        or not _valid_digest(identity.executable_digest)
        or not _valid_digest(identity.image_identity_digest)
    ):
        raise BridgeTargetRuntimeError("bridge target process identity is invalid")


def _socket_from_share(value: bytes) -> _AdoptedSocket:
    from_share = getattr(socket, "fromshare", None)
    if not callable(from_share):
        raise BridgeTargetRuntimeError("socket share adoption is unavailable")
    return from_share(value)


def current_bridge_target_process_identity() -> BridgeTargetProcessIdentity:
    try:
        from backend_listener_adoption import current_backend_process_identity

        identity = current_backend_process_identity()
        return BridgeTargetProcessIdentity(
            pid=identity.process_id,
            creation_time=identity.process_creation_time,
            executable_digest=identity.executable_digest,
            image_identity_digest=identity.image_identity_digest,
        )
    except Exception as exc:
        raise BridgeTargetRuntimeError(
            "bridge target process identity is unavailable"
        ) from exc


def prepare_fixed_startup_environment(
    material: memoryview,
) -> BridgeTargetStartupEnvironmentLease:
    if (
        not isinstance(material, memoryview)
        or not material.readonly
        or material.ndim != 1
        or material.itemsize != 1
        or not MIN_STARTUP_MATERIAL_BYTES
        <= material.nbytes
        <= MAX_STARTUP_MATERIAL_BYTES
        or not any(material)
    ):
        raise BridgeTargetRuntimeError("bridge target startup material is invalid")
    if not _STARTUP_ENVIRONMENT_LOCK.acquire(blocking=False):
        raise BridgeTargetRuntimeError(
            "bridge target startup environment is already leased"
        )
    before = dict(os.environ)
    argv = tuple(sys.argv)
    lease = BridgeTargetStartupEnvironmentLease(
        before,
        _digest_string_sequence(BRIDGE_TARGET_STARTUP_ARGV_DIGEST_DOMAIN, argv),
    )
    try:
        for name, value in FIXED_STARTUP_ENVIRONMENT:
            os.environ[name] = value
        material_encodings = _startup_material_encodings(material)
        if (
            _strings_contain_material(os.environ.values(), material_encodings)
            or _strings_contain_material(argv, material_encodings)
        ):
            raise BridgeTargetRuntimeError(
                "bridge target startup material exposure is invalid"
            )
        return lease
    except BaseException:
        lease.restore()
        raise


def apply_fixed_in_memory_startup_configuration(
    module: object,
    material: memoryview,
    environment: BridgeTargetStartupEnvironmentLease | None,
) -> AppliedBridgeTargetStartupConfiguration:
    if not isinstance(environment, BridgeTargetStartupEnvironmentLease):
        raise BridgeTargetRuntimeError(
            "bridge target startup environment lease is invalid"
        )
    config = getattr(module, "config", None)
    lifespan = getattr(module, "server_lifespan", None)
    factory = getattr(module, FIXED_CONNECTOR_FACTORY, None)
    required_config = (
        "transport_mode",
        "http_remote_hosted",
        "api_key_validation_url",
        "api_key_login_url",
        "api_key_service_token_header",
        "api_key_service_token",
        "telemetry_enabled",
    )
    if (
        config is None
        or not callable(lifespan)
        or not callable(factory)
        or any(not hasattr(config, name) for name in required_config)
    ):
        raise BridgeTargetRuntimeError("fixed connector startup API is invalid")

    original_config = {name: getattr(config, name) for name in required_config}
    try:
        config.transport_mode = "http"
        config.http_remote_hosted = False
        config.api_key_validation_url = None
        config.api_key_login_url = None
        config.api_key_service_token_header = None
        config.api_key_service_token = None
        config.telemetry_enabled = False
        environment_before_digest, environment_after_digest, argv_digest = (
            environment.receipt_fields()
        )
        if (
            config.transport_mode != "http"
            or config.http_remote_hosted is not False
            or config.api_key_validation_url is not None
            or config.api_key_login_url is not None
            or config.api_key_service_token_header is not None
            or config.api_key_service_token is not None
            or config.telemetry_enabled is not False
            or any(os.environ.get(name) != value for name, value in FIXED_STARTUP_ENVIRONMENT)
        ):
            raise BridgeTargetRuntimeError(
                "fixed connector startup configuration is invalid"
            )
    except BaseException:
        for name, value in original_config.items():
            setattr(config, name, value)
        raise

    return AppliedBridgeTargetStartupConfiguration(
        receipt=BridgeTargetStartupConfigurationReceipt(
            material_digest=hashlib.sha256(material).digest(),
            applied_in_memory=True,
            retained_material=False,
            exposed_to_argv=False,
            exposed_to_environment=False,
            exposed_to_log=False,
            startup_connection_disabled=True,
            allowed_environment=FIXED_STARTUP_ENVIRONMENT,
            environment_before_digest=environment_before_digest,
            environment_after_digest=environment_after_digest,
            argv_digest=argv_digest,
            connector_entry_verified=True,
            runtime_dependency_set_verified=False,
        ),
        _module=module,
        _original_config=original_config,
    )


def _startup_material_encodings(material: memoryview) -> tuple[str, ...]:
    owned = bytearray(material)
    try:
        values = {
            bytes(owned).hex(),
            base64.b64encode(owned).decode("ascii"),
        }
        try:
            values.add(bytes(owned).decode("utf-8"))
        except UnicodeDecodeError:
            pass
        return tuple(value for value in values if value)
    finally:
        owned[:] = b"\0" * len(owned)


def _strings_contain_material(
    values: Any, encodings: tuple[str, ...]
) -> bool:
    return any(
        encoding in str(value)
        for value in values
        for encoding in encodings
    )


def _digest_string_mapping(domain: bytes, values: dict[str, str]) -> bytes:
    hasher = hashlib.sha256()
    hasher.update(domain)
    for name in sorted(values):
        name_bytes = name.encode("utf-8", errors="surrogatepass")
        value_bytes = values[name].encode("utf-8", errors="surrogatepass")
        hasher.update(struct.pack(">I", len(name_bytes)))
        hasher.update(name_bytes)
        hasher.update(struct.pack(">I", len(value_bytes)))
        hasher.update(value_bytes)
    return hasher.digest()


def _digest_string_sequence(domain: bytes, values: tuple[str, ...]) -> bytes:
    hasher = hashlib.sha256()
    hasher.update(domain)
    for value in values:
        encoded = value.encode("utf-8", errors="surrogatepass")
        hasher.update(struct.pack(">I", len(encoded)))
        hasher.update(encoded)
    return hasher.digest()


def preflight_fixed_connector_module() -> FixedConnectorModulePreflight:
    if FIXED_CONNECTOR_MODULE in sys.modules:
        raise BridgeTargetRuntimeError("fixed connector module was loaded before preflight")
    distribution = importlib.metadata.distribution(FIXED_CONNECTOR_DISTRIBUTION)
    matches = [
        item
        for item in distribution.files or ()
        if str(item).replace("\\", "/") == f"{FIXED_CONNECTOR_MODULE}.py"
    ]
    if len(matches) != 1:
        raise BridgeTargetRuntimeError("fixed connector module identity is invalid")
    package_item = matches[0]
    located_path = Path(distribution.locate_file(package_item))
    _validated_path_identity(located_path)
    module_path = located_path.resolve(strict=True)
    record_hash = getattr(package_item, "hash", None)
    record_size = getattr(package_item, "size", None)
    if (
        record_hash is None
        or getattr(record_hash, "mode", None) != "sha256"
        or record_size != FIXED_CONNECTOR_MODULE_BYTES
    ):
        raise BridgeTargetRuntimeError("fixed connector module identity is invalid")
    encoded_record_digest = str(getattr(record_hash, "value", ""))
    padding = "=" * (-len(encoded_record_digest) % 4)
    record_digest = base64.urlsafe_b64decode(encoded_record_digest + padding)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(module_path, flags)
    try:
        source_digest, file_identity = _read_open_file_identity(fd)
        if (
            record_digest != FIXED_CONNECTOR_MODULE_SHA256
            or source_digest != FIXED_CONNECTOR_MODULE_SHA256
            or file_identity[2] != FIXED_CONNECTOR_MODULE_BYTES
            or _validated_path_identity(module_path) != file_identity
        ):
            raise BridgeTargetRuntimeError(
                "fixed connector module identity is invalid"
            )
        return FixedConnectorModulePreflight(
            path=module_path,
            source_digest=source_digest,
            record_digest=record_digest,
            file_identity=file_identity,
            fd=fd,
        )
    except BaseException:
        os.close(fd)
        raise


def verify_fixed_connector_module(
    module: object, preflight: FixedConnectorModulePreflight | None
) -> bool:
    try:
        if (
            not isinstance(preflight, FixedConnectorModulePreflight)
            or getattr(module, "__name__", None) != FIXED_CONNECTOR_MODULE
        ):
            return False
        origin_value = getattr(getattr(module, "__spec__", None), "origin", None)
        if not isinstance(origin_value, str) or not origin_value:
            return False
        module_path = Path(origin_value).resolve(strict=True)
        return (
            module_path == preflight.path
            and preflight.source_digest == FIXED_CONNECTOR_MODULE_SHA256
            and preflight.record_digest == FIXED_CONNECTOR_MODULE_SHA256
            and preflight.verify_held_file()
            and _callable_origin_matches(
                getattr(module, FIXED_CONNECTOR_FACTORY, None), preflight.path
            )
        )
    except Exception:
        return False


def _read_open_file_identity(
    fd: int,
) -> tuple[bytes, tuple[int, int, int, int, int]]:
    before = os.fstat(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    hasher = hashlib.sha256()
    while chunk := os.read(fd, 1024 * 1024):
        hasher.update(chunk)
    after = os.fstat(fd)
    before_identity = _stat_identity(before)
    after_identity = _stat_identity(after)
    if before_identity != after_identity:
        raise BridgeTargetRuntimeError("fixed connector module identity changed")
    return hasher.digest(), before_identity


def _validated_path_identity(path: Path) -> tuple[int, int, int, int, int]:
    value = path.lstat()
    attributes = int(getattr(value, "st_file_attributes", 0))
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or attributes & 0x400
    ):
        raise BridgeTargetRuntimeError("fixed connector module path is invalid")
    return _stat_identity(value)


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_nlink),
    )


def _callable_origin_matches(value: object, expected_path: Path) -> bool:
    code = getattr(value, "__code__", None)
    filename = getattr(code, "co_filename", None)
    return (
        callable(value)
        and isinstance(filename, str)
        and Path(filename).resolve(strict=True) == expected_path
    )


def _preflight_default_runtime_dependency_set(
    frame: BridgeTargetFrame,
) -> runtime_verifier.VerifiedBridgeTargetRuntimeDependencies:
    return runtime_verifier.preflight_frozen_bridge_target_runtime(
        frame.bridge_manifest_digest,
        frame.bridge_tree_digest,
        frame.adapter_executable_digest,
    )


def _verify_runtime_dependency_lease(
    frame: BridgeTargetFrame,
    lease: _RuntimeDependencyLease,
    *,
    revalidate: bool = True,
) -> None:
    if (
        lease is None
        or getattr(lease, "bridge_manifest_digest", None)
        != frame.bridge_manifest_digest
        or getattr(lease, "bridge_tree_digest", None) != frame.bridge_tree_digest
        or getattr(lease, "adapter_executable_digest", None)
        != frame.adapter_executable_digest
        or not callable(getattr(lease, "verify_unchanged", None))
        or not callable(getattr(lease, "close", None))
    ):
        raise BridgeTargetRuntimeError(
            "bridge target runtime dependency proof is invalid"
        )
    if not revalidate:
        return
    try:
        lease.verify_unchanged()
    except BridgeTargetRuntimeError:
        raise
    except Exception as exc:
        raise BridgeTargetRuntimeError(
            "bridge target runtime dependency set is invalid"
        ) from exc


def _acquire_runtime_dependency_lease(
    frame: BridgeTargetFrame,
    dependencies: BridgeTargetDependencies,
) -> _RuntimeDependencyLease:
    if dependencies.runtime_dependency_preflight is None:
        raise BridgeTargetRuntimeError(
            "bridge target runtime dependency verifier is unavailable"
        )
    lease: _RuntimeDependencyLease | None = None
    try:
        try:
            lease = dependencies.runtime_dependency_preflight(frame)
        except BridgeTargetRuntimeError:
            raise
        except Exception as exc:
            raise BridgeTargetRuntimeError(
                "bridge target runtime dependency preflight failed"
            ) from exc
        already_verified = (
            isinstance(
                lease,
                runtime_verifier.VerifiedBridgeTargetRuntimeDependencies,
            )
            and lease.verification_count > 0
        )
        _verify_runtime_dependency_lease(
            frame,
            lease,
            revalidate=not already_verified,
        )
        return lease
    except BaseException:
        if lease is not None and callable(getattr(lease, "close", None)):
            try:
                lease.close()
            except Exception:
                pass
        raise


def default_dependencies() -> BridgeTargetDependencies:
    import uvicorn

    return BridgeTargetDependencies(
        socket_from_share=_socket_from_share,
        identity_provider=current_bridge_target_process_identity,
        package_version=importlib.metadata.version,
        module_loader=importlib.import_module,
        module_verifier=verify_fixed_connector_module,
        startup_configurer=apply_fixed_in_memory_startup_configuration,
        config_factory=uvicorn.Config,
        server_factory=uvicorn.Server,
        health_probe=probe_bridge_target_health,
        monotonic=time.monotonic,
        sleep=time.sleep,
        module_preflight=preflight_fixed_connector_module,
        startup_environment_preparer=prepare_fixed_startup_environment,
        runtime_dependency_preflight=_preflight_default_runtime_dependency_set,
    )


def _load_fixed_connector_module(
    dependencies: BridgeTargetDependencies,
    startup_material: memoryview,
) -> tuple[object, BridgeTargetStartupEnvironmentLease | None]:
    try:
        version = dependencies.package_version(FIXED_CONNECTOR_DISTRIBUTION)
    except Exception as exc:
        raise BridgeTargetRuntimeError("fixed connector package is unavailable") from exc
    if version != FIXED_CONNECTOR_VERSION:
        raise BridgeTargetRuntimeError("fixed connector package version is invalid")
    if (dependencies.module_preflight is None) != (
        dependencies.startup_environment_preparer is None
    ):
        raise BridgeTargetRuntimeError("fixed connector pre-import policy is invalid")
    preflight: FixedConnectorModulePreflight | None = None
    environment: BridgeTargetStartupEnvironmentLease | None = None
    try:
        if dependencies.module_preflight is not None:
            try:
                preflight = dependencies.module_preflight()
            except BridgeTargetRuntimeError:
                raise
            except Exception as exc:
                raise BridgeTargetRuntimeError(
                    "fixed connector module preflight failed"
                ) from exc
            if not isinstance(preflight, FixedConnectorModulePreflight):
                raise BridgeTargetRuntimeError(
                    "fixed connector module preflight is invalid"
                )
            try:
                environment = dependencies.startup_environment_preparer(
                    startup_material
                )
            except BridgeTargetRuntimeError:
                raise
            except Exception as exc:
                raise BridgeTargetRuntimeError(
                    "fixed connector startup environment failed"
                ) from exc
        try:
            module = dependencies.module_loader(FIXED_CONNECTOR_MODULE)
        except Exception as exc:
            raise BridgeTargetRuntimeError(
                "fixed connector module is unavailable"
            ) from exc
        try:
            verified = dependencies.module_verifier(module, preflight)
        except Exception as exc:
            raise BridgeTargetRuntimeError(
                "fixed connector module identity is invalid"
            ) from exc
        if verified is not True:
            raise BridgeTargetRuntimeError(
                "fixed connector module identity is invalid"
            )
        if environment is not None:
            environment.verify_after_import(startup_material)
        if preflight is not None:
            try:
                preflight.close()
            except Exception as exc:
                raise BridgeTargetRuntimeError(
                    "fixed connector module preflight cleanup failed"
                ) from exc
            preflight = None
        return module, environment
    except BaseException:
        if environment is not None:
            environment.restore()
        raise
    finally:
        if preflight is not None:
            try:
                preflight.close()
            except Exception:
                pass


def reject_unsupported_startup_configuration(
    module: object,
    material: memoryview,
    environment: BridgeTargetStartupEnvironmentLease | None,
) -> BridgeTargetStartupConfigurationReceipt:
    del module, material, environment
    raise BridgeTargetRuntimeError(
        "fixed connector in-memory startup configuration is not supported"
    )


def _apply_startup_configuration(
    frame: BridgeTargetFrame,
    module: object,
    dependencies: BridgeTargetDependencies,
    environment: BridgeTargetStartupEnvironmentLease | None,
    runtime_dependencies: _RuntimeDependencyLease,
) -> AppliedBridgeTargetStartupConfiguration | None:
    applied: AppliedBridgeTargetStartupConfiguration | None = None
    try:
        try:
            result = dependencies.startup_configurer(
                module,
                memoryview(frame.startup_material).toreadonly(),
                environment,
            )
        except BridgeTargetRuntimeError:
            raise
        except Exception as exc:
            raise BridgeTargetRuntimeError(
                "bridge target startup configuration failed"
            ) from exc
        if isinstance(result, AppliedBridgeTargetStartupConfiguration):
            applied = result
            receipt = result.receipt
        else:
            receipt = result
        expected_environment_receipt = (
            environment.receipt_fields() if environment is not None else None
        )
        if (
            not isinstance(receipt, BridgeTargetStartupConfigurationReceipt)
            or receipt.material_digest != frame.startup_material_digest
            or not receipt.applied_in_memory
            or receipt.retained_material
            or receipt.exposed_to_argv
            or receipt.exposed_to_environment
            or receipt.exposed_to_log
            or not receipt.startup_connection_disabled
            or receipt.allowed_environment != FIXED_STARTUP_ENVIRONMENT
            or not _valid_digest(receipt.environment_before_digest)
            or not _valid_digest(receipt.environment_after_digest)
            or not _valid_digest(receipt.argv_digest)
            or (
                expected_environment_receipt is not None
                and (
                    receipt.environment_before_digest,
                    receipt.environment_after_digest,
                    receipt.argv_digest,
                )
                != expected_environment_receipt
            )
            or not receipt.connector_entry_verified
            or receipt.runtime_dependency_set_verified
        ):
            raise BridgeTargetRuntimeError(
                "bridge target startup configuration receipt is invalid"
            )
        _verify_runtime_dependency_lease(
            frame,
            runtime_dependencies,
            revalidate=False,
        )
        verified_receipt = replace(
            receipt,
            runtime_dependency_set_verified=True,
        )
        if applied is not None:
            applied.receipt = verified_receipt
        return applied
    except BaseException:
        if applied is not None:
            applied.restore()
        raise
    finally:
        frame.clear_startup_material()


def _build_fixed_http_app(module: object) -> object:
    try:
        factory = getattr(module, FIXED_CONNECTOR_FACTORY)
        connector = factory(True)
        return getattr(connector, "http_app")()
    except Exception as exc:
        raise BridgeTargetRuntimeError(
            "fixed connector HTTP application is unavailable"
        ) from exc


def probe_bridge_target_health(
    target_port: int,
    timeout_seconds: float,
    health_header_value: memoryview,
    *,
    connection_factory: Callable[..., Any] = http.client.HTTPConnection,
) -> bool:
    if (
        target_port < MIN_PRIVATE_TARGET_PORT
        or target_port > 0xFFFF
        or target_port in (PUBLIC_BRIDGE_PORT, APP_PORT)
        or timeout_seconds <= 0
        or timeout_seconds > 5.0
        or len(health_header_value) != 64
    ):
        return False
    credential = bytes(health_header_value)
    if any(value not in b"0123456789abcdef" for value in credential):
        return False
    connection: Any | None = None
    try:
        connection = connection_factory(
            LOOPBACK_IPV4_TEXT,
            target_port,
            timeout_seconds,
        )
        connection.request(
            "GET",
            "/health",
            headers={
                "Host": LOOPBACK_IPV4_TEXT,
                "Connection": "close",
                "Accept": "application/json",
                BRIDGE_TARGET_REQUEST_AUTH_HEADER.decode("ascii"): credential.decode(
                    "ascii"
                ),
            },
        )
        response = connection.getresponse()
        content_type = str(response.getheader("Content-Type", "")).lower()
        body = response.read(MAX_HEALTH_RESPONSE_BYTES + 1)
        if (
            response.status != 200
            or content_type.split(";", 1)[0].strip() != "application/json"
            or not body
            or len(body) > MAX_HEALTH_RESPONSE_BYTES
        ):
            return False
        payload = json.loads(body.decode("utf-8"))
        return isinstance(payload, dict) and payload.get("status") == "healthy"
    except Exception:
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def _validate_adopted_socket(frame: BridgeTargetFrame, adopted: _AdoptedSocket) -> None:
    try:
        local_address = adopted.getsockname()
        exclusive = adopted.getsockopt(
            socket.SOL_SOCKET, SOCKET_OPTION_EXCLUSIVE_ADDRESS_USE
        )
        reuse = adopted.getsockopt(socket.SOL_SOCKET, SOCKET_OPTION_REUSE_ADDRESS)
        accepting = adopted.getsockopt(
            socket.SOL_SOCKET, SOCKET_OPTION_ACCEPT_CONNECTION
        )
    except Exception as exc:
        raise BridgeTargetRuntimeError("adopted bridge target socket cannot be verified") from exc
    if (
        adopted.family != ADDRESS_FAMILY_IPV4
        or adopted.type != SOCKET_TYPE_STREAM
        or adopted.proto != PROTOCOL_TCP
        or not isinstance(local_address, tuple)
        or len(local_address) < 2
        or local_address[0] != LOOPBACK_IPV4_TEXT
        or local_address[1] != frame.target_port
        or exclusive != 1
        or reuse != 0
        or accepting != 1
    ):
        raise BridgeTargetRuntimeError("adopted bridge target socket identity is invalid")


def _has_fixed_health_route(app: object) -> bool:
    for route in getattr(app, "routes", ()):
        if getattr(route, "path", None) != "/health":
            continue
        methods = getattr(route, "methods", None)
        if methods is None or "GET" in methods:
            return True
    return False


def serve_adopted_bridge_target(
    frame_bytes: bytes | bytearray | memoryview | BridgeTargetFrame,
    emit_ack: Callable[[bytes], None],
    dependencies: BridgeTargetDependencies | None = None,
    *,
    await_shutdown: Callable[[BridgeTargetFrame], BridgeTargetShutdownRequest]
    | None = None,
) -> BridgeTargetShutdownAccounting:
    dependencies = dependencies or default_dependencies()
    frame = (
        frame_bytes
        if isinstance(frame_bytes, BridgeTargetFrame)
        else decode_bridge_target_frame(frame_bytes)
    )
    adopted: _AdoptedSocket | None = None
    server: Any | None = None
    request_auth: BridgeTargetRequestAuthState | None = None
    startup_environment: BridgeTargetStartupEnvironmentLease | None = None
    startup_configuration: AppliedBridgeTargetStartupConfiguration | None = None
    runtime_dependencies: _RuntimeDependencyLease | None = None
    server_errors: list[BaseException] = []
    runner: threading.Thread | None = None
    runner_abandoned_to_process_containment = False
    try:
        if (
            not 0 < dependencies.readiness_timeout_seconds <= 60.0
            or not 0 < dependencies.runner_shutdown_timeout_seconds <= 60.0
        ):
            raise BridgeTargetRuntimeError("bridge target timeout policy is invalid")
        try:
            identity = dependencies.identity_provider()
        except BridgeTargetRuntimeError:
            raise
        except Exception as exc:
            raise BridgeTargetRuntimeError(
                "bridge target process identity is unavailable"
            ) from exc
        _validate_process_identity(frame, identity)
        runtime_dependencies = _acquire_runtime_dependency_lease(frame, dependencies)
        request_auth = BridgeTargetRequestAuthState.from_frame(frame)
        connector_module, startup_environment = _load_fixed_connector_module(
            dependencies,
            memoryview(frame.startup_material).toreadonly(),
        )
        startup_configuration = _apply_startup_configuration(
            frame,
            connector_module,
            dependencies,
            startup_environment,
            runtime_dependencies,
        )
        app = _build_fixed_http_app(connector_module)
        authenticated_app = BridgeTargetAuthenticatedApp(app, request_auth)
        try:
            try:
                adopted = dependencies.socket_from_share(bytes(frame.socket_share))
            except Exception as exc:
                raise BridgeTargetRuntimeError("bridge target socket adoption failed") from exc
        finally:
            frame.clear_socket_share()
        _validate_adopted_socket(frame, adopted)

        config = dependencies.config_factory(
            authenticated_app,
            log_level="warning",
            access_log=False,
            proxy_headers=False,
            server_header=False,
        )
        server = dependencies.server_factory(config)
        entered = threading.Event()

        def run_server() -> None:
            entered.set()
            try:
                server.run(sockets=[adopted])
            except BaseException as exc:  # recorded for the controlling thread
                server_errors.append(exc)

        runner = threading.Thread(
            target=run_server,
            name="vrcforge-bridge-target",
            daemon=False,
        )
        runner.start()
        entered.wait(timeout=dependencies.readiness_timeout_seconds)
        deadline = dependencies.monotonic() + dependencies.readiness_timeout_seconds
        while (now := dependencies.monotonic()) <= deadline:
            if server_errors:
                raise BridgeTargetRuntimeError("bridge target runner failed before health")
            if not runner.is_alive():
                raise BridgeTargetRuntimeError("bridge target runner stopped before health")
            if bool(getattr(server, "started", False)) and _has_fixed_health_route(app):
                probe_timeout = min(0.5, max(0.01, deadline - now))
                if dependencies.health_probe(
                    frame.target_port,
                    probe_timeout,
                    request_auth.health_header_view(),
                ):
                    break
            dependencies.sleep(0.01)
        else:
            raise BridgeTargetRuntimeError("bridge target health did not become ready")

        if not runner.is_alive():
            raise BridgeTargetRuntimeError("bridge target runner stopped before acknowledgement")
        _verify_runtime_dependency_lease(frame, runtime_dependencies)
        try:
            ack_bytes = encode_bridge_target_ack(
                frame, identity, request_auth.snapshot()
            )
        finally:
            frame.clear_challenge()
        emit_ack(ack_bytes)
        if await_shutdown is None:
            raise BridgeTargetRuntimeError(
                "bridge target shutdown control is unavailable"
            )
        try:
            shutdown = await_shutdown(frame)
        except BridgeTargetProtocolError:
            raise
        except Exception as exc:
            raise BridgeTargetRuntimeError(
                "bridge target shutdown request is unavailable"
            ) from exc
        if (
            not isinstance(shutdown, BridgeTargetShutdownRequest)
            or not _shutdown_request_matches_frame(shutdown, frame)
            or shutdown.sequence != 1
            or not 0 < shutdown.requested_at <= 0xFFFF_FFFF_FFFF_FFFF
        ):
            raise BridgeTargetRuntimeError(
                "bridge target shutdown request is invalid"
            )
        setattr(server, "should_exit", True)
        runner.join(timeout=dependencies.runner_shutdown_timeout_seconds)
        if runner.is_alive():
            runner_abandoned_to_process_containment = True
            raise BridgeTargetForcedContainmentRequired(
                "bridge target runner requires process containment"
            )
        if server_errors:
            raise BridgeTargetRuntimeError("bridge target runner failed") from server_errors[0]
        request_auth.clear()
        observed_at_shutdown = dependencies.shutdown_clock()
        accounting = BridgeTargetShutdownAccounting(
            run_binding_digest=frame.run_binding_digest,
            ticket_digest=frame.ticket_digest,
            bridge_launch_binding_digest=frame.bridge_launch_binding_digest,
            private_pipe_binding_digest=frame.private_pipe_binding_digest,
            private_pipe_instance_id=frame.private_pipe_instance_id,
            target_port=frame.target_port,
            listener_socket_object_id=frame.listener_socket_object_id,
            request_auth_key_digest=frame.request_auth_key_digest,
            request_auth=request_auth.snapshot(),
            observed_at_shutdown=observed_at_shutdown,
            owner=identity,
            request_auth_header_stripped=True,
        )
        encode_bridge_target_shutdown_accounting(accounting)
        return accounting
    finally:
        if (
            not runner_abandoned_to_process_containment
            and runner is not None
            and runner.is_alive()
        ):
            if server is not None:
                setattr(server, "should_exit", True)
            runner.join(timeout=dependencies.runner_shutdown_timeout_seconds)
            runner_abandoned_to_process_containment = runner.is_alive()
        if runner_abandoned_to_process_containment:
            frame.clear_sensitive()
            raise BridgeTargetForcedContainmentRequired(
                "bridge target runner requires process containment"
            ) from None
        try:
            if adopted is not None:
                adopted.close()
        finally:
            try:
                if request_auth is not None:
                    request_auth.clear()
            finally:
                try:
                    if startup_configuration is not None:
                        startup_configuration.restore()
                finally:
                    try:
                        if startup_environment is not None:
                            startup_environment.restore()
                    finally:
                        try:
                            if runtime_dependencies is not None:
                                runtime_dependencies.close()
                        finally:
                            frame.clear_sensitive()
