import io
import json
import pytest
import unity_mcp_core_client as core

class ReplayConnection:
    def __init__(self, incoming=b""):
        self.incoming = io.BytesIO(incoming)
        self.sent = []
    def recv(self, size):
        return self.incoming.read(size)
    def sendall(self, value):
        self.sent.append(value)

def test_large_read_inventory_survives_transport_without_dropping_items():
    rows = [{"object_path": "Avatar/" + str(i) + "/" + "x" * 700} for i in range(2000)]
    message = {"schema": core.TRANSPORT_SCHEMA, "message": {"jsonrpc": "2.0", "id": 2, "result": {"items": rows}}}
    wire = json.dumps(message, separators=(",", ":")).encode() + b"\n"
    assert len(wire) > 1024 * 1024
    actual = core.UnityMcpCoreClient._read_line(ReplayConnection(wire))
    assert actual == message
    assert len(actual["message"]["result"]["items"]) == 2000

def test_large_response_budget_does_not_expand_write_request_budget():
    connection = ReplayConnection()
    with pytest.raises(core.UnityMcpCoreError, match="too large"):
        core.UnityMcpCoreClient._write_line(connection, {"value": "x" * (1024 * 1024)})
    assert connection.sent == []

def test_response_budget_still_fails_closed(monkeypatch):
    monkeypatch.setattr(core, "MAX_RESPONSE_FRAME_BYTES", 1024)
    with pytest.raises(core.UnityMcpCoreError, match="invalid|too large"):
        core.UnityMcpCoreClient._read_line(ReplayConnection(b"x" * 1026))
