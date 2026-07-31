from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.metadata
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

import primitive_bridge_target_adapter as adapter


def _digest(value: int) -> bytes:
    return bytes([value]) * 32


def _identity() -> adapter.BridgeTargetProcessIdentity:
    return adapter.BridgeTargetProcessIdentity(4242, 9_999, _digest(6), _digest(7))


def _shutdown_request() -> adapter.BridgeTargetShutdownRequest:
    return adapter.BridgeTargetShutdownRequest(
        run_binding_digest=_digest(1),
        ticket_digest=_digest(2),
        bridge_launch_binding_digest=_digest(3),
        private_pipe_binding_digest=_digest(4),
        private_pipe_instance_id=0x0102_0304_0506_0708,
        sequence=1,
        requested_at=0x1112_1314_1516_1718,
    )


def _shutdown_accounting() -> adapter.BridgeTargetShutdownAccounting:
    return adapter.BridgeTargetShutdownAccounting(
        run_binding_digest=_digest(1),
        ticket_digest=_digest(2),
        bridge_launch_binding_digest=_digest(3),
        private_pipe_binding_digest=_digest(4),
        private_pipe_instance_id=0x0102_0304_0506_0708,
        target_port=49_221,
        listener_socket_object_id=99,
        request_auth_key_digest=_digest(8),
        request_auth=adapter.BridgeTargetRequestAuthSnapshot(
            controlled_health_requests=1,
            proxy_http_requests=2,
            proxy_websocket_requests=3,
            rejected_requests=0,
            bypass_requests=0,
            credentials_zeroized=True,
        ),
        observed_at_shutdown=0x2122_2324_2526_2728,
        owner=_identity(),
        request_auth_header_stripped=True,
    )


def _startup_receipt(material: memoryview):
    return adapter.BridgeTargetStartupConfigurationReceipt(
        material_digest=hashlib.sha256(material).digest(),
        applied_in_memory=True,
        retained_material=False,
        exposed_to_argv=False,
        exposed_to_environment=False,
        exposed_to_log=False,
        startup_connection_disabled=True,
        allowed_environment=adapter.FIXED_STARTUP_ENVIRONMENT,
        environment_before_digest=_digest(21),
        environment_after_digest=_digest(22),
        argv_digest=_digest(23),
        connector_entry_verified=True,
        runtime_dependency_set_verified=False,
    )


class _ExplicitRuntimeDependencyLease:
    def __init__(self, frame: adapter.BridgeTargetFrame) -> None:
        self.bridge_manifest_digest = frame.bridge_manifest_digest
        self.bridge_tree_digest = frame.bridge_tree_digest
        self.adapter_executable_digest = frame.adapter_executable_digest
        self.verifications = 0
        self.closed = False

    def verify_unchanged(self) -> None:
        assert not self.closed
        self.verifications += 1

    def close(self) -> None:
        assert not self.closed
        self.closed = True


def _runtime_dependency_preflight(
    frame: adapter.BridgeTargetFrame,
) -> _ExplicitRuntimeDependencyLease:
    return _ExplicitRuntimeDependencyLease(frame)


def _temporary_preflight(path: Path) -> adapter.FixedConnectorModulePreflight:
    fd = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0),
    )
    digest, identity = adapter._read_open_file_identity(fd)
    return adapter.FixedConnectorModulePreflight(
        path=path.resolve(strict=True),
        source_digest=digest,
        record_digest=digest,
        file_identity=identity,
        fd=fd,
    )


def _run_asgi(app, scope: dict[str, object]) -> list[dict[str, object]]:
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def _http_scope(
    path: str = "/rpc",
    *,
    method: str = "POST",
    raw_path: bytes | None = None,
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, object]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode("utf-8") if raw_path is None else raw_path,
        "query_string": query_string,
        "headers": list(headers or ()),
    }


def _websocket_scope(
    *,
    raw_path: bytes = b"/ws",
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, object]:
    return {
        "type": "websocket",
        "path": "/ws",
        "raw_path": raw_path,
        "query_string": query_string,
        "headers": list(headers or ()),
    }


def _proxy_headers(
    auth: adapter.BridgeTargetRequestAuthState,
    nonce: bytes,
    *,
    scope_type: str = "http",
    method: str | None = "POST",
    path: str = "/rpc",
    raw_path: bytes | None = None,
    query_string: bytes = b"",
) -> list[tuple[bytes, bytes]]:
    return [
        (
            adapter.BRIDGE_TARGET_REQUEST_AUTH_HEADER,
            auth.proxy_bearer_value(
                nonce,
                scope_type=scope_type,
                method=method,
                path=path,
                raw_path=path.encode("utf-8") if raw_path is None else raw_path,
                query_string=query_string,
            ),
        ),
        (adapter.BRIDGE_TARGET_REQUEST_NONCE_HEADER, nonce),
    ]


def _frame_bytes(
    *,
    target_port: int = 49_221,
    socket_share: bytes = b"service-owned-socket-share",
    startup_material: bytes = b"0123456789abcdef0123456789abcdef",
    bridge_manifest_digest: bytes = _digest(7),
    bridge_tree_digest: bytes = _digest(8),
) -> bytes:
    values = (
        *tuple(_digest(value) for value in range(1, 7)),
        bridge_manifest_digest,
        bridge_tree_digest,
    )
    payload = bytearray()
    for value in values:
        payload.extend(value)
    payload.extend(struct.pack(">Q", 0x0102_0304_0506_0708))
    payload.extend(struct.pack(">BHHHIHQ", 1, 2, 1, 6, 0x7F00_0001, target_port, 99))
    payload.extend(struct.pack(">I", len(socket_share)))
    payload.extend(socket_share)
    payload.extend(struct.pack(">I", len(startup_material)))
    payload.extend(startup_material)
    socket_share_digest = hashlib.sha256(socket_share).digest()
    startup_material_digest = hashlib.sha256(startup_material).digest()
    payload.extend(socket_share_digest)
    payload.extend(startup_material_digest)
    request_auth = hmac.new(startup_material, digestmod=hashlib.sha256)
    request_auth.update(b"vrcforge-authority-bridge-target-request-auth-key-v1\0")
    for value in values:
        request_auth.update(value)
    request_auth.update(struct.pack(">Q", 0x0102_0304_0506_0708))
    request_auth.update(struct.pack(">H", target_port))
    request_auth.update(struct.pack(">Q", 99))
    request_auth.update(socket_share_digest)
    request_auth.update(startup_material_digest)
    payload.extend(
        hashlib.sha256(
            b"vrcforge-authority-bridge-target-request-auth-key-digest-v1\0"
            + request_auth.digest()
        ).digest()
    )
    header = adapter.BRIDGE_TARGET_FRAME_MAGIC + struct.pack(">HI", 1, len(payload) + 32)
    payload.extend(hashlib.sha256(adapter.BRIDGE_TARGET_FRAME_DOMAIN + header + payload).digest())
    return header + payload


def test_import_does_not_load_runner_or_connector_modules() -> None:
    script = (
        "import http.client, socket, sys; "
        "forbidden=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network')); "
        "socket.socket=forbidden; "
        "http.client.HTTPConnection=forbidden; "
        "assert 'uvicorn' not in sys.modules; "
        "assert 'main' not in sys.modules; "
        "import primitive_bridge_target_adapter; "
        "assert 'uvicorn' not in sys.modules; "
        "assert 'main' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_shutdown_and_accounting_wire_layouts_are_exact_and_round_trip() -> None:
    request = _shutdown_request()
    shutdown = adapter.encode_bridge_target_shutdown_request(request)
    assert len(shutdown) == adapter.BRIDGE_TARGET_SHUTDOWN_FRAME_BYTES == 196
    assert shutdown[:14] == b"VRCBSD01\x00\x01\x00\x00\x00\xb6"
    assert shutdown[14:46] == _digest(1)
    assert shutdown[46:78] == _digest(2)
    assert shutdown[78:110] == _digest(3)
    assert shutdown[110:142] == _digest(4)
    assert struct.unpack(">QIQH", shutdown[142:164]) == (
        0x0102_0304_0506_0708,
        1,
        0x1112_1314_1516_1718,
        adapter.BRIDGE_TARGET_SHUTDOWN_REQUIRED_FLAGS,
    )
    assert shutdown[-32:] == hashlib.sha256(
        adapter.BRIDGE_TARGET_SHUTDOWN_DOMAIN + shutdown[:-32]
    ).digest()
    assert hashlib.sha256(shutdown).hexdigest() == (
        "50bb2ea74b8e7f778fe18c12c284e5bd7f1ae5e55187e19499795e885eb16775"
    )
    assert adapter.decode_bridge_target_shutdown_request(shutdown) == request

    accounting = _shutdown_accounting()
    encoded_accounting = adapter.encode_bridge_target_shutdown_accounting(accounting)
    assert len(encoded_accounting) == adapter.BRIDGE_TARGET_ACCOUNTING_FRAME_BYTES == 332
    assert encoded_accounting[:14] == b"VRCBAC01\x00\x01\x00\x00\x01>"
    assert hashlib.sha256(encoded_accounting).hexdigest() == (
        "fc090b81cf5911096ad158f4a525953e901e33b145c749fb724e4dc488e0b8b7"
    )
    assert adapter.decode_bridge_target_shutdown_accounting(
        encoded_accounting, expected=request
    ) == accounting


def test_shutdown_guard_rejects_truncation_oversize_replay_and_binding_drift() -> None:
    frame = adapter.decode_bridge_target_frame(_frame_bytes())
    encoded = adapter.encode_bridge_target_shutdown_request(_shutdown_request())
    for drifted, match in (
        (encoded[:-1], "truncated"),
        (encoded + b"\0", "oversized"),
    ):
        with pytest.raises(adapter.BridgeTargetProtocolError, match=match):
            adapter.decode_bridge_target_shutdown_request(drifted)

    guard = adapter.BridgeTargetShutdownReplayGuard()
    assert guard.consume(encoded, frame) == _shutdown_request()
    assert guard.consumed
    assert guard.request_digest == hashlib.sha256(encoded).digest()
    with pytest.raises(adapter.BridgeTargetProtocolError, match="replayed"):
        guard.consume(encoded, frame)

    drifted = bytearray(encoded)
    drifted[14] ^= 0x40
    drifted[-32:] = hashlib.sha256(
        adapter.BRIDGE_TARGET_SHUTDOWN_DOMAIN + drifted[:-32]
    ).digest()
    with pytest.raises(adapter.BridgeTargetProtocolError, match="binding drifted"):
        adapter.BridgeTargetShutdownReplayGuard().consume(drifted, frame)

    flag_drift = bytearray(encoded)
    flag_drift[163] ^= adapter.SHUTDOWN_FLAG_CLOSE_AFTER_ACCOUNTING
    flag_drift[-32:] = hashlib.sha256(
        adapter.BRIDGE_TARGET_SHUTDOWN_DOMAIN + flag_drift[:-32]
    ).digest()
    with pytest.raises(adapter.BridgeTargetProtocolError, match="invalid"):
        adapter.decode_bridge_target_shutdown_request(flag_drift)


def test_accounting_decoder_requires_one_frame_followed_by_eof() -> None:
    request = _shutdown_request()
    encoded = adapter.encode_bridge_target_shutdown_accounting(
        _shutdown_accounting()
    )
    decoder = adapter.BridgeTargetAccountingEofDecoder(request)
    decoder.feed(encoded[:17])
    decoder.feed(encoded[17:])
    assert decoder.finish_eof() == _shutdown_accounting()
    with pytest.raises(adapter.BridgeTargetProtocolError, match="replayed"):
        decoder.finish_eof()
    with pytest.raises(adapter.BridgeTargetProtocolError, match="replayed"):
        decoder.feed(encoded)

    truncated = adapter.BridgeTargetAccountingEofDecoder(request)
    truncated.feed(encoded[:-1])
    with pytest.raises(adapter.BridgeTargetProtocolError, match="truncated"):
        truncated.finish_eof()

    oversized = adapter.BridgeTargetAccountingEofDecoder(request)
    with pytest.raises(adapter.BridgeTargetProtocolError, match="oversized"):
        oversized.feed(encoded + b"\0")

    drifted = bytearray(encoded)
    drifted[149] ^= 0x01
    drifted[-32:] = hashlib.sha256(
        adapter.BRIDGE_TARGET_ACCOUNTING_DOMAIN + drifted[:-32]
    ).digest()
    semantic_drift = adapter.BridgeTargetAccountingEofDecoder(request)
    semantic_drift.feed(drifted)
    with pytest.raises(adapter.BridgeTargetProtocolError, match="binding drifted"):
        semantic_drift.finish_eof()

    noncanonical = bytearray(encoded)
    noncanonical[212] = 2
    noncanonical[-32:] = hashlib.sha256(
        adapter.BRIDGE_TARGET_ACCOUNTING_DOMAIN + noncanonical[:-32]
    ).digest()
    field_drift = adapter.BridgeTargetAccountingEofDecoder(request)
    field_drift.feed(noncanonical)
    with pytest.raises(adapter.BridgeTargetProtocolError, match="noncanonical"):
        field_drift.finish_eof()


def test_preflight_failure_cannot_call_environment_preparer_or_module_loader() -> None:
    calls: list[str] = []

    def fail_preflight():
        calls.append("preflight")
        raise adapter.BridgeTargetRuntimeError("rejected before import")

    dependencies = replace(
        adapter.default_dependencies(),
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_preflight=fail_preflight,
        startup_environment_preparer=lambda material: calls.append("environment"),
        module_loader=lambda name: calls.append("loader"),
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="before import"):
        adapter._load_fixed_connector_module(
            dependencies,
            memoryview(bytearray(range(1, 33))).toreadonly(),
        )

    assert calls == ["preflight"]


def test_minimal_child_environment_is_exact_and_does_not_inherit_parent_values(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PATH", r"C:\hostile-parent-path")
    monkeypatch.setenv("VRCFORGE_API_TOKEN", "must-not-cross-boundary")

    environment = adapter.build_minimal_bridge_target_child_environment(
        windows_directory=r"C:\Windows",
        private_temp_directory=r"C:\ProgramData\VRCForge\runs\fixed\tmp",
    )

    assert tuple(environment) == adapter.BRIDGE_TARGET_CHILD_ENVIRONMENT_KEYS
    assert environment == {
        "SystemRoot": r"C:\Windows",
        "WINDIR": r"C:\Windows",
        "TEMP": r"C:\ProgramData\VRCForge\runs\fixed\tmp",
        "TMP": r"C:\ProgramData\VRCForge\runs\fixed\tmp",
        adapter.BRIDGE_TARGET_STDIO_ENV: "1",
        "UNITY_MCP_SKIP_STARTUP_CONNECT": "1",
        "UNITY_MCP_DISABLE_TELEMETRY": "1",
    }
    assert "PATH" not in environment
    assert "VRCFORGE_API_TOKEN" not in environment
    assert adapter.validate_minimal_bridge_target_child_environment(environment) == environment


@pytest.mark.parametrize(
    ("windows_directory", "private_temp_directory"),
    [
        (r"Windows", r"C:\ProgramData\VRCForge\tmp"),
        (r"\\server\share\Windows", r"C:\ProgramData\VRCForge\tmp"),
        (r"C:/Windows", r"C:\ProgramData\VRCForge\tmp"),
        (r"%SystemRoot%", r"C:\ProgramData\VRCForge\tmp"),
        (r"C:\Windows", r"C:\Windows\Temp"),
        (r"C:\Windows", r"C:\ProgramData\VRCForge\tmp."),
        (r"C:\Windows", r"C:\ProgramData\CON\tmp"),
        (r"C:\Windows", r"C:\ProgramData\VRCForge\..\tmp"),
    ],
)
def test_minimal_child_environment_rejects_ambiguous_or_shared_paths(
    windows_directory: str,
    private_temp_directory: str,
) -> None:
    with pytest.raises(adapter.BridgeTargetRuntimeError, match="path is invalid"):
        adapter.build_minimal_bridge_target_child_environment(
            windows_directory=windows_directory,
            private_temp_directory=private_temp_directory,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"PATH": r"C:\Windows\System32"}),
        lambda value: value.__setitem__("WINDIR", r"D:\Windows"),
        lambda value: value.__setitem__("TMP", r"C:\ProgramData\VRCForge\other"),
        lambda value: value.__setitem__(adapter.BRIDGE_TARGET_STDIO_ENV, "0"),
        lambda value: value.__setitem__("systemroot", value.pop("SystemRoot")),
    ],
)
def test_minimal_child_environment_validation_rejects_drift(mutate) -> None:
    environment = adapter.build_minimal_bridge_target_child_environment(
        windows_directory=r"C:\Windows",
        private_temp_directory=r"C:\ProgramData\VRCForge\runs\fixed\tmp",
    )
    mutate(environment)

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="environment is invalid"):
        adapter.validate_minimal_bridge_target_child_environment(environment)


def test_loader_observes_only_fixed_safety_environment_and_lease_restores(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "fixed_entry.py"
    module_path.write_bytes(b"fixed connector entry\n")
    preflight = _temporary_preflight(module_path)
    before = dict(os.environ)
    observed: list[tuple[tuple[str | None, ...], bool]] = []

    def load_module(name: str) -> object:
        observed.append(
            (
                tuple(os.environ.get(key) for key, _ in adapter.FIXED_STARTUP_ENVIRONMENT),
                preflight.verify_held_file(),
            )
        )
        os.environ.setdefault("UNITY_MCP_TELEMETRY_TIMEOUT", "5.0")
        return SimpleNamespace(__name__=name)

    dependencies = replace(
        adapter.default_dependencies(),
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_preflight=lambda: preflight,
        startup_environment_preparer=adapter.prepare_fixed_startup_environment,
        module_loader=load_module,
        module_verifier=lambda module, held: held is preflight
        and held.verify_held_file(),
    )
    material = bytearray(range(1, 33))

    module, environment = adapter._load_fixed_connector_module(
        dependencies, memoryview(material).toreadonly()
    )
    try:
        assert module.__name__ == adapter.FIXED_CONNECTOR_MODULE
        assert observed == [(('1', '1'), True)]
        assert os.environ.get("UNITY_MCP_TELEMETRY_TIMEOUT") == before.get(
            "UNITY_MCP_TELEMETRY_TIMEOUT"
        )
        assert tuple(
            (name, os.environ.get(name)) for name, _ in adapter.FIXED_STARTUP_ENVIRONMENT
        ) == adapter.FIXED_STARTUP_ENVIRONMENT
        assert environment is not None
        assert all(adapter._valid_digest(value) for value in environment.receipt_fields())
        assert not preflight.verify_held_file()
    finally:
        if environment is not None:
            environment.restore()
        material[:] = b"\0" * len(material)

    assert dict(os.environ) == before


def test_unknown_import_environment_mutation_fails_and_restores(
    tmp_path: Path, monkeypatch
) -> None:
    unexpected_name = "VRCFORGE_BRIDGE_TARGET_UNEXPECTED_TEST"
    monkeypatch.delenv(unexpected_name, raising=False)
    module_path = tmp_path / "fixed_entry.py"
    module_path.write_bytes(b"fixed connector entry\n")
    preflight = _temporary_preflight(module_path)
    before = dict(os.environ)

    def load_module(name: str) -> object:
        os.environ[unexpected_name] = "unexpected"
        return SimpleNamespace(__name__=name)

    dependencies = replace(
        adapter.default_dependencies(),
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_preflight=lambda: preflight,
        startup_environment_preparer=adapter.prepare_fixed_startup_environment,
        module_loader=load_module,
        module_verifier=lambda module, held: True,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="environment changed"):
        adapter._load_fixed_connector_module(
            dependencies,
            memoryview(bytearray(range(1, 33))).toreadonly(),
        )

    assert dict(os.environ) == before
    assert not preflight.verify_held_file()


def test_module_swap_during_import_fails_before_startup_configuration(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "fixed_entry.py"
    module_path.write_bytes(b"fixed connector entry\n")
    preflight = _temporary_preflight(module_path)
    before = dict(os.environ)

    def load_module(name: str) -> object:
        module_path.write_bytes(b"hostile connector entry")
        return SimpleNamespace(__name__=name)

    dependencies = replace(
        adapter.default_dependencies(),
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_preflight=lambda: preflight,
        startup_environment_preparer=adapter.prepare_fixed_startup_environment,
        module_loader=load_module,
        module_verifier=lambda module, held: bool(
            held is not None and held.verify_held_file()
        ),
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="module identity"):
        adapter._load_fixed_connector_module(
            dependencies,
            memoryview(bytearray(range(1, 33))).toreadonly(),
        )

    assert dict(os.environ) == before
    assert not preflight.verify_held_file()


def test_preflight_handle_closes_when_environment_preparation_fails(
    tmp_path: Path,
) -> None:
    module_path = tmp_path / "fixed_entry.py"
    module_path.write_bytes(b"fixed connector entry\n")
    preflight = _temporary_preflight(module_path)
    loader_calls: list[str] = []
    dependencies = replace(
        adapter.default_dependencies(),
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_preflight=lambda: preflight,
        startup_environment_preparer=lambda material: (_ for _ in ()).throw(
            RuntimeError("environment failure")
        ),
        module_loader=lambda name: loader_calls.append(name),
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="startup environment"):
        adapter._load_fixed_connector_module(
            dependencies,
            memoryview(bytearray(range(1, 33))).toreadonly(),
        )

    assert loader_calls == []
    assert not preflight.verify_held_file()


def test_fixed_connector_public_startup_api_is_safe_but_dependency_set_stays_blocked() -> None:
    try:
        installed_version = importlib.metadata.version(adapter.FIXED_CONNECTOR_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("fixed connector fixture is not installed")
    assert installed_version == adapter.FIXED_CONNECTOR_VERSION
    script = r'''
import asyncio
import importlib
import os
import sys
from types import SimpleNamespace

import primitive_bridge_target_adapter as adapter

assert adapter.FIXED_CONNECTOR_MODULE not in sys.modules
before = dict(os.environ)
material = bytearray(range(1, 33))
preflight = adapter.preflight_fixed_connector_module()
environment = None
applied = None
module = None
try:
    environment = adapter.prepare_fixed_startup_environment(
        memoryview(material).toreadonly()
    )
    module = importlib.import_module(adapter.FIXED_CONNECTOR_MODULE)
    assert adapter.verify_fixed_connector_module(module, preflight)
    environment.verify_after_import(memoryview(material).toreadonly())
    preflight.close()
    preflight = None
    applied = adapter.apply_fixed_in_memory_startup_configuration(
        module, memoryview(material).toreadonly(), environment
    )
    receipt = applied.receipt
    assert receipt.connector_entry_verified is True
    assert receipt.runtime_dependency_set_verified is False
    assert receipt.allowed_environment == adapter.FIXED_STARTUP_ENVIRONMENT
    assert module.config.transport_mode == "http"
    assert module.config.http_remote_hosted is False
    assert module.config.telemetry_enabled is False
    assert module.config.api_key_service_token is None
    assert all(os.environ.get(name) == value for name, value in receipt.allowed_environment)
    forbidden = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("startup connection attempted")
    )
    original_pool = module._unity_connection_pool
    original_registry = module._plugin_registry
    original_get_pool = module.get_unity_connection_pool
    original_timer = module.threading.Timer
    original_logger = module.logger
    class NoTimer:
        def start(self):
            return None
    module._unity_connection_pool = None
    module._plugin_registry = object()
    module.get_unity_connection_pool = forbidden
    module.threading.Timer = lambda *args, **kwargs: NoTimer()
    module.logger = SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
    )
    try:
        connector = module.create_mcp_server(True)
        assert connector.__class__.__module__ == "fastmcp.server.server"
        assert callable(connector.http_app)
        assert connector.http_app() is not None
        async def exercise_lifespan():
            async with module.server_lifespan(connector) as state:
                assert state["pool"] is None
        asyncio.run(exercise_lifespan())
    finally:
        module._unity_connection_pool = original_pool
        module._plugin_registry = original_registry
        module.get_unity_connection_pool = original_get_pool
        module.threading.Timer = original_timer
        module.logger = original_logger
finally:
    if applied is not None:
        applied.restore()
    if environment is not None:
        environment.restore()
    if preflight is not None:
        preflight.close()
    material[:] = b"\0" * len(material)

assert dict(os.environ) == before
print("fixed-startup-safe-blocked")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "fixed-startup-safe-blocked"


def test_process_identity_is_obtained_internally_before_connector_or_socket_work(
    monkeypatch,
) -> None:
    frame = adapter.decode_bridge_target_frame(_frame_bytes())
    calls: list[str] = []
    monkeypatch.setattr(adapter, "decode_bridge_target_frame", lambda value: frame)
    dependencies = replace(
        adapter.default_dependencies(),
        identity_provider=lambda: (
            calls.append("identity")
            or (_ for _ in ()).throw(RuntimeError("private identity detail"))
        ),
        package_version=lambda name: calls.append("package") or "0.0.0",
        socket_from_share=lambda value: calls.append("socket"),
    )

    with pytest.raises(
        adapter.BridgeTargetRuntimeError,
        match="bridge target process identity is unavailable",
    ) as error:
        adapter.serve_adopted_bridge_target(
            b"patched decoder",
            lambda value: calls.append("ack"),
            dependencies,
        )

    assert "private identity detail" not in str(error.value)
    assert calls == ["identity"]
    assert frame.challenge and not any(frame.challenge)
    assert frame.socket_share and not any(frame.socket_share)
    assert frame.startup_material and not any(frame.startup_material)


def test_runtime_dependency_verifier_is_required_before_connector_import() -> None:
    calls: list[str] = []
    dependencies = replace(
        adapter.default_dependencies(),
        identity_provider=_identity,
        runtime_dependency_preflight=None,
        package_version=lambda name: calls.append(name) or adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: calls.append(name),
    )

    with pytest.raises(
        adapter.BridgeTargetRuntimeError,
        match="dependency verifier is unavailable",
    ):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            lambda value: calls.append("ack"),
            dependencies,
        )

    assert calls == []


def test_default_runtime_dependency_verifier_rejects_source_mode_before_connector_import(
    monkeypatch,
) -> None:
    calls: list[str] = []
    monkeypatch.delattr(sys, "frozen", raising=False)
    dependencies = replace(
        adapter.default_dependencies(),
        identity_provider=_identity,
        package_version=lambda name: calls.append(name) or adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: calls.append(name),
    )

    with pytest.raises(
        adapter.BridgeTargetRuntimeError,
        match="dependency preflight failed",
    ):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            lambda value: calls.append("ack"),
            dependencies,
        )

    assert calls == []


def test_default_runtime_dependency_preflight_uses_only_parent_frame_bindings(
    monkeypatch,
) -> None:
    frame = adapter.decode_bridge_target_frame(_frame_bytes())
    observed: list[tuple[bytes, bytes, bytes]] = []

    def preflight(
        manifest_digest: bytes,
        tree_digest: bytes,
        executable_digest: bytes,
    ) -> _ExplicitRuntimeDependencyLease:
        observed.append((manifest_digest, tree_digest, executable_digest))
        return _ExplicitRuntimeDependencyLease(frame)

    monkeypatch.setattr(
        adapter.runtime_verifier,
        "preflight_frozen_bridge_target_runtime",
        preflight,
    )
    dependencies = adapter.default_dependencies()

    lease = adapter._acquire_runtime_dependency_lease(frame, dependencies)
    try:
        assert observed == [
            (
                frame.bridge_manifest_digest,
                frame.bridge_tree_digest,
                frame.adapter_executable_digest,
            )
        ]
        assert lease.verifications == 1
    finally:
        lease.close()


def test_explicit_runtime_dependency_lease_is_verified_before_package_import_and_closed() -> None:
    frame = adapter.decode_bridge_target_frame(_frame_bytes())
    events: list[str] = []

    class OrderedLease(_ExplicitRuntimeDependencyLease):
        def verify_unchanged(self) -> None:
            events.append("verify")
            super().verify_unchanged()

        def close(self) -> None:
            events.append("close")
            super().close()

    dependencies = replace(
        adapter.default_dependencies(),
        identity_provider=_identity,
        runtime_dependency_preflight=lambda active_frame: (
            events.append("preflight") or OrderedLease(active_frame)
        ),
        package_version=lambda name: events.append("package") or "0.0.0",
        module_loader=lambda name: events.append("module"),
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="package version"):
        adapter.serve_adopted_bridge_target(
            frame,
            lambda value: events.append("ack"),
            dependencies,
        )

    assert events == ["preflight", "verify", "package", "close"]


class _FakeSocket:
    family = 2
    type = 1
    proto = 6

    def __init__(
        self,
        port: int,
        *,
        host: str = "127.0.0.1",
        exclusive: int = 1,
        reuse: int = 0,
        accepting: int = 1,
    ) -> None:
        self.host = host
        self.port = port
        self.closed = False
        self.bind_calls: list[object] = []
        self.options = {
            adapter.SOCKET_OPTION_EXCLUSIVE_ADDRESS_USE: exclusive,
            adapter.SOCKET_OPTION_REUSE_ADDRESS: reuse,
            adapter.SOCKET_OPTION_ACCEPT_CONNECTION: accepting,
        }

    def getsockname(self) -> tuple[str, int]:
        return (self.host, self.port)

    def getsockopt(self, level: int, option: int) -> int:
        del level
        return self.options[option]

    def bind(self, value: object) -> None:
        self.bind_calls.append(value)
        raise AssertionError("ordinary bind must stay disabled")

    def close(self) -> None:
        self.closed = True


def test_frame_decode_uses_independent_domain_and_exact_big_endian_boundaries() -> None:
    encoded = _frame_bytes()

    frame = adapter.decode_bridge_target_frame(encoded)

    assert adapter.BRIDGE_TARGET_FRAME_FIXED_PAYLOAD_BYTES == 421
    assert encoded[:8] == b"VRCBTF01"
    assert struct.unpack(">H", encoded[8:10])[0] == 1
    assert struct.unpack(">I", encoded[10:14])[0] == len(encoded) - 14
    assert frame.run_binding_digest == _digest(1)
    assert frame.ticket_digest == _digest(2)
    assert frame.bridge_launch_binding_digest == _digest(3)
    assert frame.private_pipe_binding_digest == _digest(4)
    assert frame.challenge == _digest(5)
    assert frame.adapter_executable_digest == _digest(6)
    assert frame.bridge_manifest_digest == _digest(7)
    assert frame.bridge_tree_digest == _digest(8)
    assert frame.private_pipe_instance_id == 0x0102_0304_0506_0708
    assert frame.target_port == 49_221
    assert frame.listener_socket_object_id == 99
    assert bytes(frame.socket_share) == b"service-owned-socket-share"
    assert bytes(frame.startup_material) == b"0123456789abcdef0123456789abcdef"
    assert frame.request_auth_key_digest.hex() == (
        "a9e83f05f9bc955ffab3d2385ba53f0335974519a87b1755b15c59d0f02c409a"
    )
    assert "service-owned-socket-share" not in repr(frame)
    assert "0123456789abcdef0123456789abcdef" not in repr(frame)
    assert repr(_digest(5)) not in repr(frame)
    assert hashlib.sha256(encoded).hexdigest() == (
        "b7d7c26b95a7488a0095aeede4f42ddfe82f1cb644cc1b9a463b822427ed7572"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value[:-1],
        lambda value: value[:10] + struct.pack(">I", len(value)) + value[14:],
        lambda value: value[:14] + bytes([value[14] ^ 1]) + value[15:],
        lambda value: value[:-1] + bytes([value[-1] ^ 1]),
    ],
)
def test_frame_rejects_truncation_length_drift_and_tamper(mutation) -> None:
    with pytest.raises(adapter.BridgeTargetProtocolError):
        adapter.decode_bridge_target_frame(mutation(_frame_bytes()))


@pytest.mark.parametrize("port", [0, 1_023, 8_080, 8_757])
def test_frame_rejects_public_reserved_or_non_private_target_ports(port: int) -> None:
    with pytest.raises(adapter.BridgeTargetProtocolError):
        adapter.decode_bridge_target_frame(_frame_bytes(target_port=port))


def test_frame_rejects_oversized_socket_share_and_empty_startup_material() -> None:
    with pytest.raises(adapter.BridgeTargetProtocolError):
        adapter.decode_bridge_target_frame(
            _frame_bytes(socket_share=b"x" * (adapter.MAX_SOCKET_SHARE_BYTES + 1))
        )
    with pytest.raises(adapter.BridgeTargetProtocolError):
        adapter.decode_bridge_target_frame(_frame_bytes(startup_material=b""))
    with pytest.raises(adapter.BridgeTargetProtocolError, match="empty binding"):
        adapter.decode_bridge_target_frame(
            _frame_bytes(bridge_manifest_digest=b"\0" * 32)
        )
    with pytest.raises(adapter.BridgeTargetProtocolError, match="empty binding"):
        adapter.decode_bridge_target_frame(_frame_bytes(bridge_tree_digest=b"\0" * 32))


def test_adopted_runner_never_binds_and_acknowledges_only_after_health_ready() -> None:
    frame_bytes = _frame_bytes()
    adopted = _FakeSocket(49_221)
    from_share_calls: list[bytes] = []
    factory_calls: list[bool] = []
    config_calls: list[dict[str, object]] = []
    config_apps: list[object] = []
    run_sockets: list[list[object]] = []
    ack_bytes: list[bytes] = []
    package_queries: list[str] = []
    module_queries: list[str] = []
    startup_materials: list[bytes] = []
    health_probes: list[tuple[int, float, bytes]] = []
    shutdown_callbacks: list[int] = []
    runtime_leases: list[_ExplicitRuntimeDependencyLease] = []
    ack_emitted = threading.Event()

    class _HealthApp:
        routes = [SimpleNamespace(path="/health")]

        async def __call__(self, scope, receive, send) -> None:
            del scope, receive
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    class _Connector:
        def http_app(self):
            return _HealthApp()

    def create_mcp_server(project_scoped_tools: bool):
        factory_calls.append(project_scoped_tools)
        return _Connector()

    class _Server:
        def __init__(self, config: object) -> None:
            self.config = config
            self.started = False
            self.should_exit = False

        def run(self, *, sockets: list[object]) -> None:
            run_sockets.append(sockets)
            self.started = True
            ack_emitted.wait(timeout=1)

    def config_factory(app: object, **kwargs: object) -> object:
        config_apps.append(app)
        config_calls.append(dict(kwargs))
        return SimpleNamespace(app=app, kwargs=kwargs)

    def emit_ack(value: bytes) -> None:
        ack_bytes.append(value)
        ack_emitted.set()

    def await_shutdown(
        active_frame: adapter.BridgeTargetFrame,
    ) -> adapter.BridgeTargetShutdownRequest:
        assert ack_emitted.is_set()
        shutdown_callbacks.append(active_frame.private_pipe_instance_id)
        return _shutdown_request()

    def configure_startup(module: object, material: memoryview, environment):
        del environment
        assert getattr(module, "create_mcp_server") is create_mcp_server
        startup_materials.append(bytes(material))
        return _startup_receipt(material)

    def preflight_runtime_dependencies(
        frame: adapter.BridgeTargetFrame,
    ) -> _ExplicitRuntimeDependencyLease:
        lease = _ExplicitRuntimeDependencyLease(frame)
        runtime_leases.append(lease)
        return lease

    def probe_health(port: int, timeout: float, credential: memoryview) -> bool:
        health_probes.append((port, timeout, bytes(credential)))
        messages = _run_asgi(
            config_apps[0],
            _http_scope(
                "/health",
                method="GET",
                headers=[
                    (
                        adapter.BRIDGE_TARGET_REQUEST_AUTH_HEADER,
                        bytes(credential),
                    )
                ],
            ),
        )
        return messages[0]["status"] == 200

    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda value: (from_share_calls.append(bytes(value)) or adopted),
        identity_provider=_identity,
        package_version=lambda name: (
            package_queries.append(name) or adapter.FIXED_CONNECTOR_VERSION
        ),
        module_loader=lambda name: (
            module_queries.append(name)
            or SimpleNamespace(create_mcp_server=create_mcp_server)
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=configure_startup,
        config_factory=config_factory,
        server_factory=_Server,
        health_probe=probe_health,
        monotonic=lambda: 0.0,
        sleep=lambda _: None,
        shutdown_clock=lambda: 123_456,
        readiness_timeout_seconds=1.0,
        runtime_dependency_preflight=preflight_runtime_dependencies,
    )
    accounting = adapter.serve_adopted_bridge_target(
        frame_bytes,
        emit_ack,
        dependencies,
        await_shutdown=await_shutdown,
    )

    assert from_share_calls == [b"service-owned-socket-share"]
    assert package_queries == [adapter.FIXED_CONNECTOR_DISTRIBUTION]
    assert module_queries == [adapter.FIXED_CONNECTOR_MODULE]
    assert startup_materials == [b"0123456789abcdef0123456789abcdef"]
    assert factory_calls == [True]
    assert run_sockets == [[adopted]]
    assert adopted.bind_calls == []
    assert adopted.closed
    assert len(ack_bytes) == 1
    assert adapter.BRIDGE_TARGET_ACK_PAYLOAD_BYTES == 511
    assert len(ack_bytes[0]) == 525
    assert shutdown_callbacks == [0x0102_0304_0506_0708]
    assert len(runtime_leases) == 1
    assert runtime_leases[0].verifications == 2
    assert runtime_leases[0].closed
    assert len(health_probes) == 1
    assert health_probes[0][0] == 49_221
    assert 0 < health_probes[0][1] <= 1.0
    assert len(health_probes[0][2]) == 64
    assert health_probes[0][2] != b"0" * 64
    assert ack_bytes[0][:8] == b"VRCBTA01"
    assert struct.unpack(">H", ack_bytes[0][8:10])[0] == 1
    assert struct.unpack(">I", ack_bytes[0][10:14])[0] == len(ack_bytes[0]) - 14
    flags = struct.unpack(">H", ack_bytes[0][-34:-32])[0]
    assert flags == adapter.BRIDGE_TARGET_ACK_REQUIRED_FLAGS
    assert flags & adapter.ACK_FLAG_HTTP_APP_MOUNTED
    assert flags & adapter.ACK_FLAG_HEALTH_READY
    assert flags & adapter.ACK_FLAG_ORDINARY_BIND_DISABLED
    assert flags & adapter.ACK_FLAG_STARTUP_CONFIGURATION_APPLIED
    assert flags & adapter.ACK_FLAG_REQUEST_AUTH_ENABLED
    digest_offset = len(ack_bytes[0]) - 32
    assert ack_bytes[0][digest_offset:] == hashlib.sha256(
        adapter.BRIDGE_TARGET_ACK_DOMAIN + ack_bytes[0][:digest_offset]
    ).digest()
    assert ack_bytes[0][14:46] == _digest(1)
    assert ack_bytes[0][46:78] == _digest(2)
    assert ack_bytes[0][78:110] == _digest(3)
    assert ack_bytes[0][110:142] == _digest(4)
    assert ack_bytes[0][142:174] == _digest(5)
    assert ack_bytes[0][174:206] == _digest(6)
    assert ack_bytes[0][206:238] == _digest(7)
    assert ack_bytes[0][238:270] == _digest(8)
    assert struct.unpack(">Q", ack_bytes[0][270:278])[0] == 0x0102_0304_0506_0708
    assert ack_bytes[0][363:395] == bytes.fromhex(
        "a9e83f05f9bc955ffab3d2385ba53f0335974519a87b1755b15c59d0f02c409a"
    )
    assert struct.unpack(">IIIII", ack_bytes[0][395:415]) == (1, 0, 0, 0, 0)
    assert hashlib.sha256(ack_bytes[0]).hexdigest() == (
        "e66cdb00eae97ca8cb0f67cadb605d1763753aa7627cd0d07809c477cd95d833"
    )
    assert len(config_apps) == 1
    assert isinstance(config_apps[0], adapter.BridgeTargetAuthenticatedApp)
    assert config_apps[0]._auth.snapshot().credentials_zeroized
    assert "host" not in config_calls[0]
    assert "port" not in config_calls[0]
    assert "fd" not in config_calls[0]
    assert "uds" not in config_calls[0]
    assert accounting.request_auth.credentials_zeroized
    assert accounting.request_auth.controlled_health_requests == 1
    assert accounting.request_auth.total_target_requests == 1
    assert accounting.observed_at_shutdown == 123_456
    encoded_accounting = adapter.encode_bridge_target_shutdown_accounting(accounting)
    assert encoded_accounting[:8] == adapter.BRIDGE_TARGET_ACCOUNTING_MAGIC
    assert struct.unpack(">H", encoded_accounting[8:10])[0] == 1
    assert struct.unpack(">I", encoded_accounting[10:14])[0] == len(
        encoded_accounting
    ) - 14
    accounting_digest_offset = len(encoded_accounting) - 32
    assert encoded_accounting[accounting_digest_offset:] == hashlib.sha256(
        adapter.BRIDGE_TARGET_ACCOUNTING_DOMAIN
        + encoded_accounting[:accounting_digest_offset]
    ).digest()
    assert encoded_accounting[14:46] == _digest(1)
    assert encoded_accounting[46:78] == _digest(2)
    assert encoded_accounting[78:110] == _digest(3)
    assert encoded_accounting[110:142] == _digest(4)
    assert struct.unpack(">Q", encoded_accounting[142:150])[0] == (
        0x0102_0304_0506_0708
    )
    assert struct.unpack(">H", encoded_accounting[150:152])[0] == 49_221
    assert struct.unpack(">Q", encoded_accounting[152:160])[0] == 99
    assert encoded_accounting[160:192] == accounting.request_auth_key_digest
    assert struct.unpack(">IIIII", encoded_accounting[192:212]) == (1, 0, 0, 0, 0)
    assert encoded_accounting[212:214] == b"\x01\x01"
    assert struct.unpack(">Q", encoded_accounting[214:222])[0] == 123_456
    assert struct.unpack(">I", encoded_accounting[222:226])[0] == 4_242
    assert struct.unpack(">Q", encoded_accounting[226:234])[0] == 9_999
    assert struct.unpack(">H", encoded_accounting[-34:-32])[0] == (
        adapter.BRIDGE_TARGET_ACCOUNTING_REQUIRED_FLAGS
    )


def test_sensitive_material_is_cleared_at_each_last_use_before_ack(monkeypatch) -> None:
    frame = adapter.decode_bridge_target_frame(_frame_bytes())
    adopted = _FakeSocket(49_221)
    ack_emitted = threading.Event()
    events: list[str] = []
    guarded_apps: list[object] = []

    class _HealthApp:
        routes = [SimpleNamespace(path="/health")]

        async def __call__(self, scope, receive, send) -> None:
            del scope, receive
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    class _ObservedSocket(_FakeSocket):
        def getsockname(self) -> tuple[str, int]:
            assert frame.socket_share and not any(frame.socket_share)
            events.append("socket_share_cleared")
            return super().getsockname()

    adopted = _ObservedSocket(49_221)

    def configure_startup(module: object, material: memoryview, environment):
        del module
        del environment
        assert bytes(material) == b"0123456789abcdef0123456789abcdef"
        events.append("startup_consumed")
        return _startup_receipt(material)

    def create_mcp_server(enabled: bool):
        assert enabled
        assert frame.startup_material and not any(frame.startup_material)
        events.append("factory_after_startup_clear")
        return SimpleNamespace(
            http_app=lambda: _HealthApp()
        )

    def from_share(value: bytes):
        assert value == b"service-owned-socket-share"
        assert frame.startup_material and not any(frame.startup_material)
        events.append("socket_share_consumed")
        return adopted

    class _Server:
        def __init__(self, config: object) -> None:
            del config
            self.started = False
            self.should_exit = False

        def run(self, *, sockets: list[object]) -> None:
            assert sockets == [adopted]
            self.started = True
            ack_emitted.wait(timeout=1)

    def probe_health(port: int, timeout: float, credential: memoryview) -> bool:
        assert len(credential) == 64
        assert frame.startup_material and not any(frame.startup_material)
        assert frame.socket_share and not any(frame.socket_share)
        events.append("health_after_input_clear")
        messages = _run_asgi(
            guarded_apps[0],
            _http_scope(
                "/health",
                method="GET",
                headers=[
                    (
                        adapter.BRIDGE_TARGET_REQUEST_AUTH_HEADER,
                        bytes(credential),
                    )
                ],
            ),
        )
        return messages[0]["status"] == 200

    def config_factory(app: object, **kwargs: object) -> object:
        del kwargs
        guarded_apps.append(app)
        return SimpleNamespace(app=app)

    def emit_ack(value: bytes) -> None:
        assert value[:8] == adapter.BRIDGE_TARGET_ACK_MAGIC
        assert frame.challenge and not any(frame.challenge)
        assert frame.startup_material and not any(frame.startup_material)
        assert frame.socket_share and not any(frame.socket_share)
        events.append("ack_after_challenge_clear")
        ack_emitted.set()

    def await_shutdown(
        active_frame: adapter.BridgeTargetFrame,
    ) -> adapter.BridgeTargetShutdownRequest:
        assert active_frame is frame
        assert ack_emitted.is_set()
        events.append("shutdown_after_ack")
        return _shutdown_request()

    monkeypatch.setattr(adapter, "decode_bridge_target_frame", lambda value: frame)
    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=from_share,
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=create_mcp_server
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=configure_startup,
        config_factory=config_factory,
        server_factory=_Server,
        health_probe=probe_health,
        monotonic=lambda: 0.0,
        sleep=lambda value: None,
        readiness_timeout_seconds=1.0,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    adapter.serve_adopted_bridge_target(
        b"patched decoder",
        emit_ack,
        dependencies,
        await_shutdown=await_shutdown,
    )

    assert events == [
        "startup_consumed",
        "factory_after_startup_clear",
        "socket_share_consumed",
        "socket_share_cleared",
        "health_after_input_clear",
        "ack_after_challenge_clear",
        "shutdown_after_ack",
    ]


def test_runner_stop_timeout_requires_process_containment_without_accounting_cleanup() -> None:
    adopted = _FakeSocket(49_221)
    guarded_apps: list[object] = []
    servers: list[object] = []
    emitted: list[bytes] = []
    release = threading.Event()
    stopped = threading.Event()
    connector_module = SimpleNamespace(config=SimpleNamespace(marker="active"))
    applied_configuration: adapter.AppliedBridgeTargetStartupConfiguration | None = None

    class _HealthApp:
        routes = [SimpleNamespace(path="/health")]

        async def __call__(self, scope, receive, send) -> None:
            del scope, receive
            await send(
                {"type": "http.response.start", "status": 200, "headers": []}
            )
            await send({"type": "http.response.body", "body": b"ok"})

    class _Server:
        def __init__(self, config: object) -> None:
            del config
            self.started = False
            self.should_exit = False
            servers.append(self)

        def run(self, *, sockets: list[object]) -> None:
            assert sockets == [adopted]
            self.started = True
            release.wait(timeout=2)
            stopped.set()

    def config_factory(app: object, **kwargs: object) -> object:
        del kwargs
        guarded_apps.append(app)
        return SimpleNamespace(app=app)

    def probe_health(port: int, timeout: float, credential: memoryview) -> bool:
        del port, timeout
        messages = _run_asgi(
            guarded_apps[0],
            _http_scope(
                "/health",
                method="GET",
                headers=[
                    (
                        adapter.BRIDGE_TARGET_REQUEST_AUTH_HEADER,
                        bytes(credential),
                    )
                ],
            ),
        )
        return messages[0]["status"] == 200

    def configure_startup(module: object, material: memoryview, environment):
        nonlocal applied_configuration
        del environment
        assert module is connector_module
        applied_configuration = adapter.AppliedBridgeTargetStartupConfiguration(
            receipt=_startup_receipt(material),
            _module=connector_module,
            _original_config={"marker": "restored"},
        )
        return applied_configuration

    connector_module.create_mcp_server = lambda enabled: SimpleNamespace(
        http_app=lambda: _HealthApp()
    )

    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda value: adopted,
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: connector_module,
        module_verifier=lambda module, preflight: True,
        startup_configurer=configure_startup,
        config_factory=config_factory,
        server_factory=_Server,
        health_probe=probe_health,
        monotonic=lambda: 0.0,
        sleep=lambda _: None,
        readiness_timeout_seconds=1.0,
        runner_shutdown_timeout_seconds=0.01,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    try:
        with pytest.raises(
            adapter.BridgeTargetForcedContainmentRequired,
            match="process containment",
        ) as error:
            adapter.serve_adopted_bridge_target(
                _frame_bytes(),
                emitted.append,
                dependencies,
                await_shutdown=lambda frame: _shutdown_request(),
            )

        assert not error.value.graceful_accounting_allowed
        assert error.value.runner_still_alive
        assert len(emitted) == 1
        assert servers[0].should_exit
        assert not adopted.closed
        assert not guarded_apps[0]._auth.snapshot().credentials_zeroized
        assert connector_module.config.marker == "active"
        assert applied_configuration is not None
        assert applied_configuration.receipt.runtime_dependency_set_verified
    finally:
        release.set()
        assert stopped.wait(timeout=1)
        guarded_apps[0]._auth.clear()
        adopted.close()
        assert applied_configuration is not None
        applied_configuration.restore()
        assert connector_module.config.marker == "restored"


def test_health_route_absence_fails_without_ack_and_stops_runner() -> None:
    adopted = _FakeSocket(49_221)
    emitted: list[bytes] = []

    class _Connector:
        def http_app(self):
            return SimpleNamespace(routes=[])

    class _Server:
        def __init__(self, config: object) -> None:
            del config
            self.started = False
            self.should_exit = False

        def run(self, *, sockets: list[object]) -> None:
            del sockets
            self.started = True
            while not self.should_exit:
                threading.Event().wait(0.001)

    ticks = iter([0.0, 0.0, 2.0, 2.0, 2.0])
    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda value: adopted,
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=lambda enabled: _Connector()
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=lambda module, material, environment: _startup_receipt(material),
        config_factory=lambda app, **kwargs: SimpleNamespace(app=app),
        server_factory=_Server,
        health_probe=lambda port, timeout, credential: (_ for _ in ()).throw(
            AssertionError("health probe must not run without the fixed route")
        ),
        monotonic=lambda: next(ticks, 2.0),
        sleep=lambda _: None,
        readiness_timeout_seconds=1.0,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="health"):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            emitted.append,
            dependencies,
        )

    assert emitted == []
    assert adopted.closed


def test_listener_health_probe_failure_times_out_without_ack_and_cleans() -> None:
    adopted = _FakeSocket(49_221)
    emitted: list[bytes] = []
    health_calls: list[tuple[int, float]] = []

    class _Server:
        def __init__(self, config: object) -> None:
            del config
            self.started = False
            self.should_exit = False

        def run(self, *, sockets: list[object]) -> None:
            del sockets
            self.started = True
            while not self.should_exit:
                threading.Event().wait(0.001)

    ticks = iter([0.0, 0.0, 0.2, 2.0, 2.0])
    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda value: adopted,
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=lambda enabled: SimpleNamespace(
                http_app=lambda: SimpleNamespace(
                    routes=[SimpleNamespace(path="/health")]
                )
            )
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=lambda module, material, environment: _startup_receipt(material),
        config_factory=lambda app, **kwargs: SimpleNamespace(app=app),
        server_factory=_Server,
        health_probe=lambda port, timeout, credential: (
            health_calls.append((port, timeout)) or False
        ),
        monotonic=lambda: next(ticks, 2.0),
        sleep=lambda _: threading.Event().wait(0.001),
        readiness_timeout_seconds=1.0,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="health"):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            emitted.append,
            dependencies,
        )

    assert health_calls
    assert emitted == []
    assert adopted.closed


def test_health_probe_true_without_authenticated_asgi_request_cannot_ack() -> None:
    adopted = _FakeSocket(49_221)
    emitted: list[bytes] = []

    class _Server:
        def __init__(self, config: object) -> None:
            del config
            self.started = False
            self.should_exit = False

        def run(self, *, sockets: list[object]) -> None:
            del sockets
            self.started = True
            while not self.should_exit and not emitted:
                threading.Event().wait(0.001)

    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda value: adopted,
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=lambda enabled: SimpleNamespace(
                http_app=lambda: SimpleNamespace(
                    routes=[SimpleNamespace(path="/health")]
                )
            )
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=lambda module, material, environment: _startup_receipt(material),
        config_factory=lambda app, **kwargs: SimpleNamespace(app=app),
        server_factory=_Server,
        health_probe=lambda port, timeout, credential: True,
        monotonic=lambda: 0.0,
        sleep=lambda _: None,
        readiness_timeout_seconds=1.0,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="authenticated health"):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            emitted.append,
            dependencies,
        )

    assert emitted == []
    assert adopted.closed


def test_production_health_probe_uses_exact_target_loopback_get_and_payload() -> None:
    calls: list[tuple[object, ...]] = []

    class _Response:
        status = 200

        def getheader(self, name: str, default: str = "") -> str:
            calls.append(("header", name, default))
            return "application/json; charset=utf-8"

        def read(self, limit: int) -> bytes:
            calls.append(("read", limit))
            return b'{"status":"healthy","version":"9.6.8"}'

    class _Connection:
        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            calls.append(("request", method, path, headers))

        def getresponse(self) -> _Response:
            calls.append(("response",))
            return _Response()

        def close(self) -> None:
            calls.append(("close",))

    def connection_factory(host: str, port: int, timeout: float) -> _Connection:
        calls.append(("connect", host, port, timeout))
        return _Connection()

    assert adapter.probe_bridge_target_health(
        49_221,
        0.5,
        memoryview(b"a" * 64),
        connection_factory=connection_factory,
    )
    assert calls[0] == ("connect", "127.0.0.1", 49_221, 0.5)
    assert calls[1] == (
        "request",
        "GET",
        "/health",
        {
            "Host": "127.0.0.1",
            "Connection": "close",
            "Accept": "application/json",
            "x-vrcforge-bridge-auth": "a" * 64,
        },
    )
    assert calls[-1] == ("close",)


@pytest.mark.parametrize(
    ("status", "content_type", "body"),
    [
        (503, "application/json", b'{"status":"healthy"}'),
        (200, "text/plain", b'{"status":"healthy"}'),
        (200, "application/json", b'{"status":"starting"}'),
        (200, "application/json", b"not-json"),
        (200, "application/json", b"x" * 4_097),
    ],
)
def test_production_health_probe_rejects_non_exact_response(
    status: int, content_type: str, body: bytes
) -> None:
    closed: list[bool] = []

    class _Response:
        def getheader(self, name: str, default: str = "") -> str:
            return content_type

        def read(self, limit: int) -> bytes:
            return body

    response = _Response()
    response.status = status
    connection = SimpleNamespace(
        request=lambda method, path, headers: None,
        getresponse=lambda: response,
        close=lambda: closed.append(True),
    )

    assert not adapter.probe_bridge_target_health(
        49_221,
        0.5,
        memoryview(b"a" * 64),
        connection_factory=lambda host, port, timeout: connection,
    )
    assert closed == [True]


def test_runner_that_stops_after_started_never_receives_a_health_ack() -> None:
    adopted = _FakeSocket(49_221)
    emitted: list[bytes] = []

    class _Server:
        def __init__(self, config: object) -> None:
            del config
            self.started = False
            self.should_exit = False

        def run(self, *, sockets: list[object]) -> None:
            del sockets
            self.started = True

    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda value: adopted,
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=lambda enabled: SimpleNamespace(
                http_app=lambda: SimpleNamespace(
                    routes=[SimpleNamespace(path="/health")]
                )
            )
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=lambda module, material, environment: _startup_receipt(material),
        config_factory=lambda app, **kwargs: SimpleNamespace(app=app),
        server_factory=_Server,
        health_probe=lambda port, timeout, credential: True,
        monotonic=lambda: 0.0,
        sleep=lambda _: threading.Event().wait(0.001),
        readiness_timeout_seconds=1.0,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="stopped"):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            emitted.append,
            dependencies,
        )

    assert emitted == []
    assert adopted.closed


def test_unsupported_startup_configuration_fails_before_factory_or_socket() -> None:
    adopted = _FakeSocket(49_221)
    factory_calls: list[bool] = []
    from_share_calls: list[bytes] = []
    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda value: (from_share_calls.append(bytes(value)) or adopted),
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=lambda enabled: factory_calls.append(enabled)
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=adapter.reject_unsupported_startup_configuration,
        config_factory=lambda app, **kwargs: SimpleNamespace(app=app),
        server_factory=lambda config: config,
        health_probe=lambda port, timeout, credential: True,
        monotonic=lambda: 0.0,
        sleep=lambda _: None,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="startup configuration"):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            lambda value: None,
            dependencies,
        )

    assert factory_calls == []
    assert from_share_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("material_digest", _digest(99)),
        ("applied_in_memory", False),
        ("retained_material", True),
        ("exposed_to_argv", True),
        ("exposed_to_environment", True),
        ("exposed_to_log", True),
        ("startup_connection_disabled", False),
        ("allowed_environment", ()),
        ("environment_before_digest", b"\0" * 32),
        ("environment_after_digest", b"\0" * 32),
        ("argv_digest", b"\0" * 32),
        ("connector_entry_verified", False),
        ("runtime_dependency_set_verified", True),
    ],
)
def test_startup_configuration_receipt_must_prove_in_memory_use_without_exposure(
    field: str, value: object
) -> None:
    adopted = _FakeSocket(49_221)
    from_share_calls: list[bytes] = []

    def invalid_receipt(module: object, material: memoryview, environment):
        del module
        del environment
        return replace(_startup_receipt(material), **{field: value})

    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda data: (from_share_calls.append(bytes(data)) or adopted),
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=lambda enabled: SimpleNamespace(
                http_app=lambda: SimpleNamespace(routes=[])
            )
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=invalid_receipt,
        config_factory=lambda app, **kwargs: SimpleNamespace(app=app),
        server_factory=lambda config: config,
        health_probe=lambda port, timeout, credential: True,
        monotonic=lambda: 0.0,
        sleep=lambda value: None,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="startup configuration"):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            lambda value: None,
            dependencies,
        )

    assert from_share_calls == []


def test_fixed_package_version_mismatch_fails_before_socket_adoption() -> None:
    adopted = _FakeSocket(49_221)
    from_share_calls: list[bytes] = []
    dependencies = replace(
        adapter.default_dependencies(),
        socket_from_share=lambda value: (from_share_calls.append(bytes(value)) or adopted),
        identity_provider=_identity,
        package_version=lambda name: "0.0.0",
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="package"):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            lambda _: None,
            dependencies,
        )

    assert from_share_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("family", 23),
        ("type", 2),
        ("proto", 17),
        ("host", "127.0.0.2"),
        ("port", 49_222),
    ],
)
def test_adopted_socket_identity_drift_fails_before_runner(
    field: str, value: object
) -> None:
    adopted = _FakeSocket(49_221)
    setattr(adopted, field, value)
    server_calls: list[object] = []
    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda data: adopted,
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=lambda enabled: SimpleNamespace(
                http_app=lambda: SimpleNamespace(
                    routes=[SimpleNamespace(path="/health")]
                )
            )
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=lambda module, material, environment: _startup_receipt(material),
        config_factory=lambda app, **kwargs: SimpleNamespace(app=app),
        server_factory=lambda config: server_calls.append(config),
        health_probe=lambda port, timeout, credential: True,
        monotonic=lambda: 0.0,
        sleep=lambda value: None,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="socket identity"):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            lambda value: None,
            dependencies,
        )

    assert server_calls == []
    assert adopted.closed


@pytest.mark.parametrize(
    "adopted",
    [
        _FakeSocket(49_221, exclusive=0),
        _FakeSocket(49_221, reuse=1),
        _FakeSocket(49_221, accepting=0),
    ],
)
def test_adopted_socket_option_drift_fails_closed(adopted: _FakeSocket) -> None:
    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda data: adopted,
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=lambda enabled: SimpleNamespace(
                http_app=lambda: SimpleNamespace(
                    routes=[SimpleNamespace(path="/health")]
                )
            )
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=lambda module, material, environment: _startup_receipt(material),
        config_factory=lambda app, **kwargs: SimpleNamespace(app=app),
        server_factory=lambda config: (_ for _ in ()).throw(
            AssertionError("runner must not be created")
        ),
        health_probe=lambda port, timeout, credential: True,
        monotonic=lambda: 0.0,
        sleep=lambda value: None,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(adapter.BridgeTargetRuntimeError, match="socket identity"):
        adapter.serve_adopted_bridge_target(
            _frame_bytes(),
            lambda value: None,
            dependencies,
        )

    assert adopted.closed


def test_socket_close_failure_cannot_skip_sensitive_zeroization(monkeypatch) -> None:
    frame = adapter.decode_bridge_target_frame(_frame_bytes())

    class _CloseFailureSocket(_FakeSocket):
        family = 23

        def close(self) -> None:
            super().close()
            raise OSError("close failed")

    adopted = _CloseFailureSocket(49_221)
    monkeypatch.setattr(adapter, "decode_bridge_target_frame", lambda value: frame)
    dependencies = adapter.BridgeTargetDependencies(
        socket_from_share=lambda data: adopted,
        identity_provider=_identity,
        package_version=lambda name: adapter.FIXED_CONNECTOR_VERSION,
        module_loader=lambda name: SimpleNamespace(
            create_mcp_server=lambda enabled: SimpleNamespace(
                http_app=lambda: SimpleNamespace(
                    routes=[SimpleNamespace(path="/health")]
                )
            )
        ),
        module_verifier=lambda module, preflight: True,
        startup_configurer=lambda module, material, environment: _startup_receipt(material),
        config_factory=lambda app, **kwargs: SimpleNamespace(app=app),
        server_factory=lambda config: (_ for _ in ()).throw(
            AssertionError("runner must not be created")
        ),
        health_probe=lambda port, timeout, credential: True,
        monotonic=lambda: 0.0,
        sleep=lambda value: None,
        runtime_dependency_preflight=_runtime_dependency_preflight,
    )

    with pytest.raises(OSError, match="close failed"):
        adapter.serve_adopted_bridge_target(
            b"ignored by patched decoder",
            lambda value: None,
            dependencies,
        )

    assert adopted.closed
    assert frame.challenge and not any(frame.challenge)
    assert frame.socket_share and not any(frame.socket_share)
    assert frame.startup_material and not any(frame.startup_material)


def test_request_auth_is_run_bound_domain_separated_and_zeroizable() -> None:
    first_frame = adapter.decode_bridge_target_frame(_frame_bytes())
    second_frame = adapter.decode_bridge_target_frame(_frame_bytes(target_port=49_222))
    manifest_frame = adapter.decode_bridge_target_frame(
        _frame_bytes(bridge_manifest_digest=_digest(31))
    )
    tree_frame = adapter.decode_bridge_target_frame(
        _frame_bytes(bridge_tree_digest=_digest(32))
    )
    first = adapter.BridgeTargetRequestAuthState.from_frame(first_frame)
    second = adapter.BridgeTargetRequestAuthState.from_frame(second_frame)
    manifest_bound = adapter.BridgeTargetRequestAuthState.from_frame(manifest_frame)
    tree_bound = adapter.BridgeTargetRequestAuthState.from_frame(tree_frame)
    nonce = b"01" * 32
    first_bearer = first.proxy_bearer_value(
        nonce,
        scope_type="http",
        method="POST",
        path="/rpc",
        raw_path=b"/rpc",
        query_string=b"",
    )
    second_bearer = second.proxy_bearer_value(
        nonce,
        scope_type="http",
        method="POST",
        path="/rpc",
        raw_path=b"/rpc",
        query_string=b"",
    )

    assert first.key_digest == first_frame.request_auth_key_digest
    assert first.health_header_value() != first_bearer
    assert first_bearer != second_bearer
    assert first.key_digest != manifest_bound.key_digest
    assert first.key_digest != tree_bound.key_digest
    assert len(first.health_header_value()) == 64
    assert len(first_bearer) == 64
    assert "0123456789abcdef0123456789abcdef" not in repr(first)
    assert first_bearer.decode("ascii") not in repr(first)

    first.clear()
    snapshot = first.snapshot()
    assert snapshot.credentials_zeroized
    assert first.health_header_value() == b"\0" * 64
    with pytest.raises(adapter.BridgeTargetRuntimeError, match="cleared"):
        first.proxy_bearer_value(
            nonce,
            scope_type="http",
            method="POST",
            path="/rpc",
            raw_path=b"/rpc",
            query_string=b"",
        )


def test_http_target_auth_rejects_direct_bypass_and_strips_proxy_header() -> None:
    frame = adapter.decode_bridge_target_frame(_frame_bytes())
    auth = adapter.BridgeTargetRequestAuthState.from_frame(frame)
    downstream_scopes: list[dict[str, object]] = []

    async def downstream(scope, receive, send) -> None:
        del receive
        downstream_scopes.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guarded = adapter.BridgeTargetAuthenticatedApp(downstream, auth)
    header = adapter.BRIDGE_TARGET_REQUEST_AUTH_HEADER
    nonce_header = adapter.BRIDGE_TARGET_REQUEST_NONCE_HEADER
    nonce = b"02" * 32
    proxy_headers = _proxy_headers(auth, nonce)

    missing = _run_asgi(guarded, _http_scope())
    wrong = _run_asgi(
        guarded,
        _http_scope(headers=[(header, b"0" * 64), (nonce_header, nonce)]),
    )
    duplicate = _run_asgi(
        guarded,
        _http_scope(
            headers=[
                proxy_headers[0],
                (header.upper(), proxy_headers[0][1]),
                proxy_headers[1],
            ]
        ),
    )
    accepted = _run_asgi(
        guarded,
        _http_scope(
            headers=[
                (b"accept", b"application/json"),
                *proxy_headers,
            ]
        ),
    )
    replay = _run_asgi(guarded, _http_scope(headers=proxy_headers))

    assert [message["status"] for message in (missing[0], wrong[0], duplicate[0])] == [
        403,
        403,
        403,
    ]
    assert accepted[0]["status"] == 204
    assert replay[0]["status"] == 403
    assert len(downstream_scopes) == 1
    assert downstream_scopes[0]["headers"] == [(b"accept", b"application/json")]
    snapshot = auth.snapshot()
    assert snapshot.controlled_health_requests == 0
    assert snapshot.proxy_http_requests == 1
    assert snapshot.proxy_websocket_requests == 0
    assert snapshot.rejected_requests == 4
    assert snapshot.bypass_requests == 0
    assert snapshot.total_target_requests == 5


def test_proxy_bearer_is_bound_to_exact_request_and_nonce_is_consumed_once() -> None:
    auth = adapter.BridgeTargetRequestAuthState.from_frame(
        adapter.decode_bridge_target_frame(_frame_bytes())
    )
    downstream_paths: list[str] = []

    async def downstream(scope, receive, send) -> None:
        del receive
        downstream_paths.append(str(scope["path"]))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guarded = adapter.BridgeTargetAuthenticatedApp(downstream, auth)
    nonce = b"03" * 32
    headers = _proxy_headers(
        auth,
        nonce,
        raw_path=b"/r%70c",
        query_string=b"mode=exact",
    )

    wrong_method = _run_asgi(
        guarded,
        _http_scope(method="GET", headers=headers),
    )
    wrong_path = _run_asgi(
        guarded,
        _http_scope(
            "/other",
            raw_path=b"/r%70c",
            query_string=b"mode=exact",
            headers=headers,
        ),
    )
    wrong_raw_path = _run_asgi(
        guarded,
        _http_scope(query_string=b"mode=exact", headers=headers),
    )
    wrong_query = _run_asgi(
        guarded,
        _http_scope(raw_path=b"/r%70c", query_string=b"mode=other", headers=headers),
    )
    exact_scope = _http_scope(
        raw_path=b"/r%70c",
        query_string=b"mode=exact",
        headers=headers,
    )
    accepted = _run_asgi(guarded, exact_scope)
    second_use = _run_asgi(guarded, exact_scope)

    assert [
        wrong_method[0]["status"],
        wrong_path[0]["status"],
        wrong_raw_path[0]["status"],
        wrong_query[0]["status"],
        accepted[0]["status"],
        second_use[0]["status"],
    ] == [403, 403, 403, 403, 204, 403]
    assert downstream_paths == ["/rpc"]
    snapshot = auth.snapshot()
    assert snapshot.proxy_http_requests == 1
    assert snapshot.rejected_requests == 5
    assert snapshot.total_target_requests == 6


def test_health_and_websocket_auth_are_scoped_and_lifespan_is_preserved() -> None:
    auth = adapter.BridgeTargetRequestAuthState.from_frame(
        adapter.decode_bridge_target_frame(_frame_bytes())
    )
    downstream_scopes: list[dict[str, object]] = []

    async def downstream(scope, receive, send) -> None:
        del receive
        downstream_scopes.append(scope)
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        elif scope["type"] == "websocket":
            await send({"type": "websocket.accept"})

    guarded = adapter.BridgeTargetAuthenticatedApp(downstream, auth)
    header = adapter.BRIDGE_TARGET_REQUEST_AUTH_HEADER
    nonce_header = adapter.BRIDGE_TARGET_REQUEST_NONCE_HEADER

    health = _run_asgi(
        guarded,
        _http_scope(
            "/health",
            method="GET",
            headers=[(header, auth.health_header_value())],
        ),
    )
    health_wrong_path = _run_asgi(
        guarded,
        _http_scope(headers=[(header, auth.health_header_value())]),
    )
    websocket_missing = _run_asgi(guarded, _websocket_scope())
    websocket_nonce = b"04" * 32
    websocket = _run_asgi(
        guarded,
        _websocket_scope(
            headers=_proxy_headers(
                auth,
                websocket_nonce,
                scope_type="websocket",
                method=None,
                path="/ws",
                raw_path=b"/ws",
                query_string=b"",
            )
        ),
    )
    lifespan = _run_asgi(guarded, {"type": "lifespan"})

    assert health[0]["status"] == 200
    assert health_wrong_path[0]["status"] == 403
    assert websocket_missing == [
        {"type": "websocket.close", "code": 4403, "reason": "forbidden"}
    ]
    assert websocket == [{"type": "websocket.accept"}]
    assert lifespan == []
    assert [scope["type"] for scope in downstream_scopes] == [
        "http",
        "websocket",
        "lifespan",
    ]
    assert all(
        name.lower() not in (header, nonce_header)
        for scope in downstream_scopes[:2]
        for name, _ in scope["headers"]
    )
    snapshot = auth.snapshot()
    assert snapshot.controlled_health_requests == 1
    assert snapshot.proxy_http_requests == 0
    assert snapshot.proxy_websocket_requests == 1
    assert snapshot.rejected_requests == 2
    assert snapshot.bypass_requests == 0
    assert snapshot.total_target_requests == 4


def test_controlled_health_credential_is_one_use_and_replay_fails_closed() -> None:
    auth = adapter.BridgeTargetRequestAuthState.from_frame(
        adapter.decode_bridge_target_frame(_frame_bytes())
    )
    downstream_calls: list[str] = []

    async def downstream(scope, receive, send) -> None:
        del receive
        downstream_calls.append(str(scope["path"]))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guarded = adapter.BridgeTargetAuthenticatedApp(downstream, auth)
    health_credential = auth.health_header_value()
    scope = _http_scope(
        "/health",
        method="GET",
        headers=[(adapter.BRIDGE_TARGET_REQUEST_AUTH_HEADER, health_credential)],
    )

    first = _run_asgi(guarded, scope)
    replay = _run_asgi(guarded, scope)

    assert first[0]["status"] == 200
    assert replay[0]["status"] == 403
    assert downstream_calls == ["/health"]
    assert auth.health_header_value() == b"\0" * 64
    assert len(
        auth.proxy_bearer_value(
            b"05" * 32,
            scope_type="http",
            method="POST",
            path="/rpc",
            raw_path=b"/rpc",
            query_string=b"",
        )
    ) == 64
    snapshot = auth.snapshot()
    assert snapshot.controlled_health_requests == 1
    assert snapshot.rejected_requests == 1
    assert snapshot.total_target_requests == 2


def test_unknown_request_scope_fails_closed_and_records_bypass_attempt() -> None:
    auth = adapter.BridgeTargetRequestAuthState.from_frame(
        adapter.decode_bridge_target_frame(_frame_bytes())
    )

    async def downstream(scope, receive, send) -> None:
        raise AssertionError("unknown request scope must not reach the fixed app")

    guarded = adapter.BridgeTargetAuthenticatedApp(downstream, auth)
    with pytest.raises(adapter.BridgeTargetRuntimeError, match="scope"):
        _run_asgi(
            guarded,
            {
                "type": "http.request",
                "path": "/rpc",
                "headers": [
                    (
                        adapter.BRIDGE_TARGET_REQUEST_AUTH_HEADER,
                        b"0" * 64,
                    ),
                    (adapter.BRIDGE_TARGET_REQUEST_NONCE_HEADER, b"06" * 32),
                ],
            },
        )

    snapshot = auth.snapshot()
    assert snapshot.total_target_requests == 1
    assert snapshot.rejected_requests == 1
    assert snapshot.bypass_requests == 1
