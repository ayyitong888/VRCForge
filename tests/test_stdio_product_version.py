import pytest

from agent_mcp_2026 import PROTOCOL_VERSION
from tools import vrcforge_agent_mcp_stdio as stdio
from unity_mcp_tool_contract import PRODUCT_VERSION


@pytest.mark.parametrize("profile", ["mcp-1x", "vrcforge-2026"])
def test_stdio_handshake_reports_product_version(monkeypatch, profile):
    captured = {}
    loop = "run_standard_stdio_loop" if profile == "mcp-1x" else "run_stdio_loop"
    monkeypatch.setattr(stdio, loop, lambda router: captured.setdefault("router", router))
    stdio.run_stdio_server(object(), protocol_profile=profile, exposure_layer="planning")
    router = captured["router"]
    if profile == "mcp-1x":
        response = router.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "version-test", "version": "1"},
        }})
        info = response["result"]["serverInfo"]
    else:
        response = router.handle({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "version-test", "version": "1"},
        }}})
        info = response[0]["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert info["version"] == PRODUCT_VERSION


def test_backend_discovery_client_identity_uses_product_version(monkeypatch, tmp_path):
    bridge = stdio.VRCForgeBridge(base_url="http://127.0.0.1:8757", config_path=tmp_path / "unused.json",
                                 timeout_seconds=0.01, start_runtime=False)
    requests = []

    def request(_method, _path, **kwargs):
        requests.append(kwargs["payload"])
        kwargs["response_headers"]["Mcp-Session-Id"] = "fixture-session"
        return {"result": {}}

    monkeypatch.setattr(bridge, "request_json", request)
    bridge._mcp_request("server/discover", {}, token="fixture-token")
    assert requests[0]["params"]["_meta"]["io.modelcontextprotocol/clientInfo"]["version"] == PRODUCT_VERSION
