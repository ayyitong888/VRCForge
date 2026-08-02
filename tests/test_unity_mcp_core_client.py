from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
from pathlib import Path

import pytest

from unity_mcp_core_client import (
    MAX_FRAME_BYTES,
    MODERN_PROTOCOL_VERSION,
    TRANSPORT_SCHEMA,
    UnityMcpCoreClient,
    UnityMcpCoreError,
)
from unity_mcp_tool_contract import EXPECTED_TOOL_COUNT, EXPECTED_TOOL_NAMES, READ_ONLY_TOOL_NAMES


def _read_line(connection: socket.socket) -> dict:
    value = bytearray()
    while True:
        character = connection.recv(1)
        if not character:
            raise RuntimeError("connection closed")
        if character == b"\n":
            return json.loads(bytes(value).decode("utf-8"))
        value.extend(character)


def _write_line(connection: socket.socket, payload: dict) -> None:
    connection.sendall(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")


def _tool_entry(name: str) -> dict:
    is_read_only = name in READ_ONLY_TOOL_NAMES
    return {
        "name": name,
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True} if is_read_only else {"destructiveHint": True},
        "_meta": {"permission": "ReadOnly" if is_read_only else "RequiresApproval"},
    }


class FakeCore:
    def __init__(self, handler):
        self._handler = handler
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(2)
        self._listener.settimeout(2)
        self.port = self._listener.getsockname()[1]
        self.seen = []
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            connection, _ = self._listener.accept()
            with connection:
                connection.settimeout(2)
                self._handler(connection, self.seen)
        except Exception as error:
            self.error = error
        finally:
            self._listener.close()

    def close(self):
        self._thread.join(3)
        if self._thread.is_alive():
            self._listener.close()
            self._thread.join(1)
        assert not self._thread.is_alive(), "fake Core thread did not stop"
        if self.error:
            raise self.error


@pytest.fixture
def core_files(tmp_path: Path):
    project = tmp_path / "project"
    descriptor_dir = project / "Library" / "VRCForge"
    descriptor_dir.mkdir(parents=True)
    raw_project_path = str(project.resolve())
    descriptor = {
        "schema": TRANSPORT_SCHEMA,
        "transport": "tcp-newline-jsonrpc",
        "protocolVersion": MODERN_PROTOCOL_VERSION,
        "supportedProtocolVersions": [MODERN_PROTOCOL_VERSION],
        "authMode": "bearer-per-request",
        "executionPolicy": "read-direct-app-process-approved-writes",
        "host": "127.0.0.1",
        "port": 1,
        "authToken": base64.b64encode(b"d" * 32).decode("ascii"),
        "instanceId": "instance-1",
        "processId": 123,
        "projectPath": raw_project_path,
        "projectHash": hashlib.sha256(raw_project_path.encode("utf-8")).hexdigest(),
        "toolCount": EXPECTED_TOOL_COUNT,
    }
    return project, descriptor_dir / "mcp-core.json", descriptor


def _write_descriptor(path: Path, descriptor: dict, port: int) -> None:
    descriptor["port"] = port
    path.write_text(json.dumps(descriptor), encoding="utf-8")


def _modern_handler(connection, seen):
    discover = _read_line(connection)
    seen.append(discover)
    _write_line(connection, {"schema": TRANSPORT_SCHEMA, "message": {"jsonrpc": "2.0", "id": 1, "result": {
        "resultType": "complete", "supportedVersions": [MODERN_PROTOCOL_VERSION],
    }}})
    request = _read_line(connection)
    seen.append(request)
    params = request["message"]["params"]
    if request["message"]["method"] == "tools/list":
        result = {
            "resultType": "complete",
            "tools": [_tool_entry(name) for name in sorted(EXPECTED_TOOL_NAMES)],
        }
    else:
        result = {"resultType": "complete", "content": [{"type": "text", "text": "ok"}], "isError": False}
    _write_line(connection, {"schema": TRANSPORT_SCHEMA, "message": {"jsonrpc": "2.0", "id": 2, "result": result}})


def test_modern_default_discovers_and_sends_metadata_and_bearer_every_request(core_files):
    project, descriptor_path, descriptor = core_files
    server = FakeCore(_modern_handler)
    _write_descriptor(descriptor_path, descriptor, server.port)
    try:
        assert [tool["name"] for tool in UnityMcpCoreClient(project).list_tools()] == sorted(EXPECTED_TOOL_NAMES)
    finally:
        server.close()
    assert [item["message"]["method"] for item in server.seen] == ["server/discover", "tools/list"]
    for envelope in server.seen:
        assert envelope["authorization"] == "Bearer " + descriptor["authToken"]
        metadata = envelope["message"]["params"]["_meta"]
        assert metadata["io.modelcontextprotocol/protocolVersion"] == MODERN_PROTOCOL_VERSION
        assert metadata["io.modelcontextprotocol/clientCapabilities"] == {}
        assert metadata["io.modelcontextprotocol/clientInfo"]["name"] == "VRCForge FastAPI"


def test_modern_call_keeps_execution_context_out_of_arguments(core_files):
    project, descriptor_path, descriptor = core_files
    server = FakeCore(_modern_handler)
    _write_descriptor(descriptor_path, descriptor, server.port)
    try:
        result = UnityMcpCoreClient(project).call_tool("vrc_write", {"value": "汉字"}, execution_context={"approvalId": "a1"})
        assert result["content"][0]["text"] == "ok"
    finally:
        server.close()
    params = server.seen[1]["message"]["params"]
    assert params["arguments"] == {"value": "汉字"}
    approved = params["_meta"]["io.vrcforge/approvedExecution"]
    assert approved["approvalId"] == "a1"
    assert approved["clientProcessId"] == os.getpid()
    assert approved["projectHash"] == descriptor["projectHash"]
    assert approved["instanceId"] == descriptor["instanceId"]


def test_setup_outfit_poll_lane_requires_exact_job_id_shape(core_files, monkeypatch):
    project, descriptor_path, descriptor = core_files
    _write_descriptor(descriptor_path, descriptor, 1)
    client = UnityMcpCoreClient(project)
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: {"resultType": "complete"})
    context = {"lane": "app_setup_outfit_poll"}

    for name, arguments in (
        ("vrc_setup_outfit", {}),
        ("vrc_setup_outfit", {"jobId": "a" * 32, "avatarPath": "Avatar"}),
        ("vrc_setup_outfit", {"jobId": "not-a-guid"}),
        ("vrc_create_gameobject", {"jobId": "a" * 32}),
    ):
        with pytest.raises(ValueError, match="exact jobId"):
            client.call_tool(name, arguments, execution_context=context)

    assert client.call_tool(
        "vrc_setup_outfit",
        {"jobId": "a" * 32},
        execution_context=context,
    ) == {"resultType": "complete"}


def test_modern_requires_complete_result_and_descriptor_bindings(core_files):
    project, descriptor_path, descriptor = core_files
    descriptor["host"] = "localhost"
    _write_descriptor(descriptor_path, descriptor, 1)
    with pytest.raises(UnityMcpCoreError):
        UnityMcpCoreClient(project)

    descriptor["host"] = "127.0.0.1"
    descriptor["transport"] = "tcp-length-prefixed-jsonrpc"
    _write_descriptor(descriptor_path, descriptor, 1)
    with pytest.raises(UnityMcpCoreError, match="transport"):
        UnityMcpCoreClient(project)
    descriptor["transport"] = "tcp-newline-jsonrpc"

    descriptor["protocolVersion"] = "2025-11-25"
    _write_descriptor(descriptor_path, descriptor, 1)
    with pytest.raises(UnityMcpCoreError, match="protocol"):
        UnityMcpCoreClient(project)
    descriptor["protocolVersion"] = MODERN_PROTOCOL_VERSION

    descriptor["toolCount"] = EXPECTED_TOOL_COUNT - 1
    _write_descriptor(descriptor_path, descriptor, 1)
    with pytest.raises(UnityMcpCoreError, match="tool contract"):
        UnityMcpCoreClient(project)
    descriptor["toolCount"] = EXPECTED_TOOL_COUNT

    def incomplete_handler(connection, _seen):
        _read_line(connection)
        _write_line(connection, {"schema": TRANSPORT_SCHEMA, "message": {"jsonrpc": "2.0", "id": 1, "result": {"supportedVersions": [MODERN_PROTOCOL_VERSION]}}})

    server = FakeCore(incomplete_handler)
    _write_descriptor(descriptor_path, descriptor, server.port)
    try:
        with pytest.raises(UnityMcpCoreError, match="incomplete"):
            UnityMcpCoreClient(project).list_tools()
    finally:
        server.close()


def test_descriptor_bound_to_another_project_is_rejected(core_files, tmp_path: Path):
    project, descriptor_path, descriptor = core_files
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    other_path = str(other_project.resolve())
    descriptor["projectPath"] = other_path
    descriptor["projectHash"] = hashlib.sha256(other_path.encode("utf-8")).hexdigest()
    _write_descriptor(descriptor_path, descriptor, 1)

    with pytest.raises(UnityMcpCoreError, match="different project"):
        UnityMcpCoreClient(project)


def test_stale_descriptor_with_dead_listener_fails_closed_without_fallback(core_files):
    project, descriptor_path, descriptor = core_files
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    dead_port = listener.getsockname()[1]
    listener.close()
    descriptor["processId"] = 2_147_483_647
    _write_descriptor(descriptor_path, descriptor, dead_port)

    with pytest.raises(UnityMcpCoreError, match="connection failed"):
        UnityMcpCoreClient(project, timeout_seconds=1).list_tools()


@pytest.mark.parametrize(
    "tool_names",
    [
        sorted(EXPECTED_TOOL_NAMES - {"vrc_add_component"}),
        sorted(EXPECTED_TOOL_NAMES) + ["vrc_unowned_tool"],
        ["vrc_add_component"] * EXPECTED_TOOL_COUNT,
    ],
)
def test_tools_list_requires_the_exact_fixed_64_name_contract(core_files, tool_names: list[str]):
    project, descriptor_path, descriptor = core_files

    def handler(connection, _seen):
        _read_line(connection)
        _write_line(connection, {"schema": TRANSPORT_SCHEMA, "message": {"jsonrpc": "2.0", "id": 1, "result": {
            "resultType": "complete", "supportedVersions": [MODERN_PROTOCOL_VERSION],
        }}})
        _read_line(connection)
        _write_line(connection, {"schema": TRANSPORT_SCHEMA, "message": {"jsonrpc": "2.0", "id": 2, "result": {
            "resultType": "complete", "tools": [_tool_entry(name) for name in tool_names],
        }}})

    server = FakeCore(handler)
    _write_descriptor(descriptor_path, descriptor, server.port)
    try:
        with pytest.raises(UnityMcpCoreError):
            UnityMcpCoreClient(project).list_tools()
    finally:
        server.close()


@pytest.mark.parametrize("field", ["inputSchema", "annotations", "_meta", "permission"])
def test_tools_list_rejects_permission_or_schema_drift(core_files, field: str):
    project, descriptor_path, descriptor = core_files
    entries = [_tool_entry(name) for name in sorted(EXPECTED_TOOL_NAMES)]
    target = next(entry for entry in entries if entry["name"] == "vrc_add_component")
    if field == "permission":
        target["_meta"]["permission"] = "ReadOnly"
    else:
        target.pop(field)

    def handler(connection, _seen):
        _read_line(connection)
        _write_line(connection, {"schema": TRANSPORT_SCHEMA, "message": {"jsonrpc": "2.0", "id": 1, "result": {
            "resultType": "complete", "supportedVersions": [MODERN_PROTOCOL_VERSION],
        }}})
        _read_line(connection)
        _write_line(connection, {"schema": TRANSPORT_SCHEMA, "message": {"jsonrpc": "2.0", "id": 2, "result": {
            "resultType": "complete", "tools": entries,
        }}})

    server = FakeCore(handler)
    _write_descriptor(descriptor_path, descriptor, server.port)
    try:
        with pytest.raises(UnityMcpCoreError, match="metadata|permissions"):
            UnityMcpCoreClient(project).list_tools()
    finally:
        server.close()


def test_oversized_modern_line_fails_closed(core_files):
    project, descriptor_path, descriptor = core_files

    def oversized_handler(connection, _seen):
        _read_line(connection)
        connection.sendall(b"x" * (MAX_FRAME_BYTES + 1))

    server = FakeCore(oversized_handler)
    _write_descriptor(descriptor_path, descriptor, server.port)
    try:
        with pytest.raises(UnityMcpCoreError, match="invalid"):
            UnityMcpCoreClient(project).list_tools()
    finally:
        server.close()
