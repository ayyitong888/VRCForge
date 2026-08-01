from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
from pathlib import Path

import pytest

from unity_mcp_core_client import (
    MAX_FRAME_BYTES,
    TRANSPORT_SCHEMA,
    UnityMcpCoreClient,
    UnityMcpCoreError,
)


def _read_exactly(connection: socket.socket, size: int) -> bytes:
    chunks = []
    while size:
        chunk = connection.recv(size)
        if not chunk:
            raise RuntimeError("connection closed")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _read_frame(connection: socket.socket) -> dict:
    size = struct.unpack(">I", _read_exactly(connection, 4))[0]
    return json.loads(_read_exactly(connection, size).decode("utf-8"))


def _write_frame(connection: socket.socket, payload: dict) -> None:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    connection.sendall(struct.pack(">I", len(raw)) + raw)


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
        except Exception as error:  # test helper must expose server failures.
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
    project_hash = hashlib.sha256(raw_project_path.encode("utf-8")).hexdigest()
    descriptor = {
        "schema": TRANSPORT_SCHEMA,
        "transport": "tcp-length-prefixed-jsonrpc",
        "protocolVersion": "2025-11-25",
        "authMode": "bearer",
        "executionPolicy": "read-only-direct-writes-rejected",
        "host": "127.0.0.1",
        "port": 1,
        "authToken": base64.b64encode(b"d" * 32).decode("ascii"),
        "instanceId": "instance-1",
        "processId": 123,
        "projectPath": raw_project_path,
        "projectHash": project_hash,
    }
    return project, descriptor_dir / "mcp-core.json", descriptor


def _write_descriptor(descriptor_path: Path, descriptor: dict, port: int) -> None:
    descriptor["port"] = port
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")


def _normal_handler(connection, seen):
    initialize = _read_frame(connection)
    seen.append(initialize)
    _write_frame(connection, {
        "schema": TRANSPORT_SCHEMA,
        "message": {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
    })
    seen.append(_read_frame(connection))
    request = _read_frame(connection)
    seen.append(request)
    if request["message"]["method"] == "tools/list":
        result = {"tools": [{"name": "vrc_read", "inputSchema": {"type": "object"}}]}
    else:
        result = {"content": [{"type": "text", "text": "ok"}], "isError": False}
    _write_frame(connection, {
        "schema": TRANSPORT_SCHEMA,
        "message": {"jsonrpc": "2.0", "id": 2, "result": result},
    })


def test_list_tools_and_call_use_only_standard_mcp_methods(core_files):
    project, descriptor_path, descriptor = core_files
    first = FakeCore(_normal_handler)
    _write_descriptor(descriptor_path, descriptor, first.port)
    try:
        assert UnityMcpCoreClient(project).list_tools() == [
            {"name": "vrc_read", "inputSchema": {"type": "object"}}
        ]
    finally:
        first.close()
    assert first.seen[0]["authorization"] == "Bearer " + descriptor["authToken"]
    assert first.seen[2]["message"]["method"] == "tools/list"

    second = FakeCore(_normal_handler)
    _write_descriptor(descriptor_path, descriptor, second.port)
    try:
        result = UnityMcpCoreClient(project).call_tool("vrc_read", {"value": "汉字"})
        assert result["content"][0]["text"] == "ok"
    finally:
        second.close()
    assert second.seen[2]["message"]["method"] == "tools/call"
    assert second.seen[2]["message"]["params"] == {
        "name": "vrc_read",
        "arguments": {"value": "汉字"},
    }
    assert all("executionGrant" not in json.dumps(envelope) for envelope in second.seen)


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "localhost"),
        ("authToken", "not-base64"),
        ("executionPolicy", "vrcforge-fastapi-one-time-grants"),
    ],
)
def test_descriptor_mismatch_fails_closed(core_files, field, value):
    project, descriptor_path, descriptor = core_files
    descriptor[field] = value
    _write_descriptor(descriptor_path, descriptor, 1)
    with pytest.raises(UnityMcpCoreError) as error:
        UnityMcpCoreClient(project)
    assert "token" not in str(error.value).lower()


def test_client_has_no_private_approved_execution_api(core_files):
    project, descriptor_path, descriptor = core_files
    _write_descriptor(descriptor_path, descriptor, 1)
    with pytest.raises(TypeError):
        UnityMcpCoreClient(project).call_tool(
            "vrc_write",
            {},
            execution_context=object(),
        )


def test_wrong_project_and_mcp_error_fail_closed(core_files, tmp_path: Path):
    project, descriptor_path, descriptor = core_files
    other_project = tmp_path / "other"
    other_project.mkdir()
    _write_descriptor(descriptor_path, descriptor, 1)
    with pytest.raises(UnityMcpCoreError):
        UnityMcpCoreClient(other_project)

    def error_handler(connection, _seen):
        _read_frame(connection)
        _write_frame(connection, {
            "schema": TRANSPORT_SCHEMA,
            "message": {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
        })
        _read_frame(connection)
        _read_frame(connection)
        _write_frame(connection, {
            "schema": TRANSPORT_SCHEMA,
            "message": {"jsonrpc": "2.0", "id": 2, "error": {"code": -32601, "message": "no"}},
        })

    server = FakeCore(error_handler)
    _write_descriptor(descriptor_path, descriptor, server.port)
    try:
        with pytest.raises(UnityMcpCoreError, match="rejected"):
            UnityMcpCoreClient(project).list_tools()
    finally:
        server.close()


def test_oversized_frame_fails_closed(core_files):
    project, descriptor_path, descriptor = core_files

    def oversized_handler(connection, _seen):
        _read_frame(connection)
        connection.sendall(struct.pack(">I", MAX_FRAME_BYTES + 1))

    server = FakeCore(oversized_handler)
    _write_descriptor(descriptor_path, descriptor, server.port)
    try:
        with pytest.raises(UnityMcpCoreError, match="invalid"):
            UnityMcpCoreClient(project).list_tools()
    finally:
        server.close()
