"""Regression tests for the write-operation receipt polling lane.

Root fix under test: a submitted managed write is never replayed; its outcome
is queried by executionId through server/write-operation and bound to the
submitted tool name. An in-flight or unknown receipt never proves failure.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import threading
from pathlib import Path

import pytest

from unity_mcp_core_auth import UnityMcpCoreConnectionError, UnityMcpCoreError
from unity_mcp_core_client import (
    MODERN_PROTOCOL_VERSION,
    TRANSPORT_SCHEMA,
    UnityMcpCoreClient,
)
from unity_mcp_tool_contract import (
    CORE_IDENTITY,
    EXPECTED_TOOL_COUNT,
    HANDSHAKE_PROTOCOL,
    PRODUCT_VERSION,
    TOOL_CONTRACT_VERSION,
)
from unity_write_operation_polling import write_operation_result


SCHEMA = "vrcforge.write-operation.v1"
EXECUTION_ID = "exec-abc123"
JOB_ID = "job-abc123"
TOOL = "vrc_add_wardrobe_outfit"


def _snapshot(status: str, **extra) -> dict:
    payload = {"schema": SCHEMA, "jobId": JOB_ID, "executionId": EXECUTION_ID, "status": status}
    payload.update(extra)
    return payload


def _tool_result() -> dict:
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": "ok"}],
        "structuredContent": {"success": True, "message": "ok"},
        "isError": False,
    }


def test_in_flight_receipt_never_proves_failure() -> None:
    for status in ("queued", "running", "unknown", "not_found", "expired"):
        snapshot = _snapshot(status)
        if status in {"queued", "running"}:
            snapshot["retryAfterMs"] = 100
        assert write_operation_result(
            snapshot, execution_id=EXECUTION_ID, tool_name=TOOL,
        ) is None, status


def test_terminal_receipt_binds_to_submitted_tool_name() -> None:
    assert write_operation_result(
        _snapshot("done", toolName=TOOL, result=_tool_result()),
        execution_id=EXECUTION_ID, tool_name=TOOL,
    ) == _tool_result()
    assert write_operation_result(
        _snapshot("failed", toolName=TOOL, result={**_tool_result(), "isError": True}),
        execution_id=EXECUTION_ID, tool_name=TOOL,
    )["isError"] is True


def test_mismatched_or_malformed_receipt_raises() -> None:
    with pytest.raises(UnityMcpCoreError):
        write_operation_result(
            _snapshot("done", toolName="vrc_other_tool", result=_tool_result()),
            execution_id=EXECUTION_ID, tool_name=TOOL,
        )
    with pytest.raises(UnityMcpCoreError):
        write_operation_result(
            _snapshot("done", toolName=TOOL, result=_tool_result()),
            execution_id="exec-other", tool_name=TOOL,
        )
    with pytest.raises(UnityMcpCoreError):
        write_operation_result(
            _snapshot("flying", toolName=TOOL), execution_id=EXECUTION_ID, tool_name=TOOL,
        )
    with pytest.raises(UnityMcpCoreError):
        write_operation_result(
            {"schema": "other", "status": "done"}, execution_id=EXECUTION_ID, tool_name=TOOL,
        )


# --- Client integration: submit once, poll by executionId, never replay. ---


def _discovery(project_id: str) -> dict:
    return {
        "resultType": "complete",
        "supportedVersions": [MODERN_PROTOCOL_VERSION],
        "coreIdentity": CORE_IDENTITY,
        "handshakeProtocol": HANDSHAKE_PROTOCOL,
        "productVersion": PRODUCT_VERSION,
        "toolContractVersion": TOOL_CONTRACT_VERSION,
        "instanceId": "instance-1",
        "projectId": project_id,
        "protocolRange": {"minimum": MODERN_PROTOCOL_VERSION, "maximum": MODERN_PROTOCOL_VERSION},
    }


def _read_message(connection) -> dict | None:
    connection.settimeout(5)
    buffer = bytearray()
    while not buffer.endswith(b"\n"):
        try:
            chunk = connection.recv(65536)
        except (socket.timeout, OSError):
            return None
        if not chunk:
            return None
        buffer.extend(chunk)
    envelope = json.loads(bytes(buffer).decode("utf-8"))
    return envelope.get("message") or {}


def _write_message(connection, payload: dict) -> None:
    connection.sendall(json.dumps(
        {"schema": TRANSPORT_SCHEMA, "message": payload},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8") + b"\n")


class _FakeMultiCore:
    """Serves each connection with discover plus scripted method replies."""

    def __init__(self, replies: dict[str, object]) -> None:
        self._replies = replies
        self.requests: list[str] = []
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(4)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        listener = self._listener
        try:
            while True:
                try:
                    connection, _ = listener.accept()
                except OSError:
                    return
                with connection:
                    self._serve(connection)
        finally:
            listener.close()

    def _serve(self, connection) -> None:
        request = _read_message(connection)
        if not request or request.get("method") != "server/discover":
            return
        self._write(connection, {"jsonrpc": "2.0", "id": request["id"], "result": self._discovery})
        while True:
            request = _read_message(connection)
            if not request:
                return
            method = request.get("method")
            self.requests.append(method)
            payload = self._replies.get(method)
            if isinstance(payload, list):
                payload = payload.pop(0) if payload else None
            if payload is None:
                return  # close without a reply: the response is lost in transit
            self._write(connection, {"jsonrpc": "2.0", "id": request["id"], "result": payload})

    def _write(self, connection, payload: dict) -> None:
        _write_message(connection, payload)

    def close(self) -> None:
        self._listener.close()
        self._thread.join(3)
        assert not self._thread.is_alive(), "fake Core thread did not stop"


@pytest.fixture
def core_endpoint(tmp_path: Path):
    project = tmp_path / "project"
    descriptor_dir = project / "Library" / "VRCForge"
    descriptor_dir.mkdir(parents=True)
    raw_project_path = str(project.resolve())
    descriptor = {
        "schema": TRANSPORT_SCHEMA,
        "transport": "tcp-newline-jsonrpc",
        "protocolVersion": MODERN_PROTOCOL_VERSION,
        "supportedProtocolVersions": [MODERN_PROTOCOL_VERSION],
        "minimumProtocolVersion": MODERN_PROTOCOL_VERSION,
        "maximumProtocolVersion": MODERN_PROTOCOL_VERSION,
        "coreIdentity": CORE_IDENTITY,
        "handshakeProtocol": HANDSHAKE_PROTOCOL,
        "productVersion": PRODUCT_VERSION,
        "toolContractVersion": TOOL_CONTRACT_VERSION,
        "authMode": "bearer-per-request",
        "executionPolicy": "read-direct-app-process-approved-writes",
        "host": "127.0.0.1",
        "port": 1,
        "authToken": base64.b64encode(b"d" * 32).decode("ascii"),
        "instanceId": "instance-1",
        "processId": 123,
        "projectPath": raw_project_path,
        "projectId": hashlib.sha256(raw_project_path.encode("utf-8")).hexdigest(),
        "projectIdSource": "normalized_project_path_sha256",
        "toolCount": EXPECTED_TOOL_COUNT,
    }
    return project, descriptor_dir / "mcp-core.json", descriptor


def _start_core(core_endpoint, replies: dict[str, object]):
    project, descriptor_path, descriptor = core_endpoint
    fake = _FakeMultiCore(replies)
    fake._discovery = _discovery(descriptor["projectId"])
    descriptor["port"] = fake.port
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    return fake


def test_call_tool_polls_write_operation_until_done(core_endpoint) -> None:
    fake = _start_core(core_endpoint, {
        "tools/call": _snapshot("queued", retryAfterMs=100),
        "server/write-operation": [_snapshot("done", toolName=TOOL, result=_tool_result())],
    })
    try:
        client = UnityMcpCoreClient(core_endpoint[0], timeout_seconds=5)
        result = client.call_tool(
            TOOL, {"outfitName": "Coat"},
            execution_context={"lane": "approved_write", "executionId": EXECUTION_ID},
        )
        assert {key: value for key, value in result.items() if key != "_meta"} == _tool_result()
        assert fake.requests.count("tools/call") == 1, "submission must not be replayed"
        assert "server/write-operation" in fake.requests
    finally:
        fake.close()


def test_lost_submission_response_recovers_by_execution_id_without_replay(core_endpoint) -> None:
    fake = _start_core(core_endpoint, {
        "tools/call": None,
        "server/write-operation": [_snapshot("done", toolName=TOOL, result=_tool_result())],
    })
    try:
        client = UnityMcpCoreClient(core_endpoint[0], timeout_seconds=5)
        result = client.call_tool(
            TOOL, {"outfitName": "Coat"},
            execution_context={"lane": "approved_write", "executionId": EXECUTION_ID},
        )
        assert {key: value for key, value in result.items() if key != "_meta"} == _tool_result()
        assert fake.requests.count("tools/call") == 1, "a lost receipt never justifies a replay"
    finally:
        fake.close()


def test_unknown_operation_raises_with_queryable_guidance(core_endpoint) -> None:
    fake = _start_core(core_endpoint, {"tools/call": _snapshot("unknown")})
    try:
        client = UnityMcpCoreClient(core_endpoint[0], timeout_seconds=2)
        with pytest.raises(UnityMcpCoreConnectionError) as raised:
            client.call_tool(
                TOOL, {"outfitName": "Coat"},
                execution_context={"lane": "approved_write", "executionId": EXECUTION_ID},
            )
        error = raised.value
        assert getattr(error, "cause_code", "") == "unity_write_operation_unresolved"
        assert getattr(error, "details", {}).get("executionId") == EXECUTION_ID
    finally:
        fake.close()
