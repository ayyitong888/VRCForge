from pathlib import Path

import httpx
import pytest

import unity_host_diagnostics as diagnostics


def test_editor_tail_and_earlier_pages_cover_source_without_starting_unity(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = tmp_path / "Unity/Editor/Editor.log"
    path.parent.mkdir(parents=True)
    source = "begin\n" + "normal 中文🙂 line\n" * 2000 + "Exception: original runtime failure\n"
    path.write_bytes(source.encode())
    tail = diagnostics.read_unity_log({})
    assert tail["content"].endswith("Exception: original runtime failure\n")
    assert tail["totalBytes"] == len(source.encode())
    assert tail["complete"] is False and tail["hasEarlier"] is True
    page = diagnostics.read_unity_log({"offset": 0})
    chunks = [page["content"]]
    while page["hasMore"]:
        page = diagnostics.read_unity_log(page["nextRequest"]["arguments"])
        chunks.append(page["content"])
    assert "".join(chunks) == source
    assert path.read_bytes() == source.encode()
    filtered = diagnostics.read_unity_log({"contains": "exception"})
    assert filtered["content"] == "Exception: original runtime failure"
    assert filtered["filterScope"] == "returned_window" and not filtered["complete"]


def test_crash_text_paging_keeps_selected_directory_and_does_not_decode_dump(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    directory = tmp_path / "Temp/Unity/Editor/Crashes/Crash_example"
    directory.mkdir(parents=True)
    (directory / "error.log").write_bytes(b"native stack\n" * 2000)
    (directory / "crash.dmp").write_bytes(b"\x00\xff\x00")
    result = diagnostics.get_last_unity_crash({})
    assert result["found"] and not result["dumpAnalyzed"]
    assert {f["name"] for f in result["files"]} == {"error.log", "crash.dmp"}
    request = result["log"]["previousRequest"]["arguments"]
    assert request["crashName"] == directory.name and request["log"] == "error.log"
    assert diagnostics.get_last_unity_crash(request)["log"]["startByte"] == request["offset"]
    assert diagnostics.get_last_unity_crash({"crashName": "../outside"})["ok"] is False


@pytest.mark.parametrize("status", [200, 403, 503])
def test_http_business_status_is_distinct_from_network_failure(monkeypatch, status):
    client_type = httpx.Client
    def respond(request):
        assert str(request.url) == diagnostics.API_URL
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return httpx.Response(status)
    monkeypatch.setattr(diagnostics.httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    result = diagnostics.check_vrchat_connectivity({})
    assert result["reachable"] is True and result["httpStatus"] == status
    assert result["apiResponding"] is (status == 200)
    assert result["unityUploadVerified"] is False


def test_network_timeout_is_not_an_http_response(monkeypatch):
    client_type = httpx.Client
    def respond(request):
        raise httpx.ConnectTimeout("connection timed out", request=request)
    monkeypatch.setattr(diagnostics.httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    result = diagnostics.check_vrchat_connectivity({})
    assert result["reachable"] is False and result["errorType"] == "ConnectTimeout"
    assert "httpStatus" not in result


def test_tools_registered_with_read_schemas_and_existing_diagnostic_blocks(tmp_path):
    from agent_gateway import AgentGateway
    from external_mcp_tool_blocks import EXTERNAL_MCP_READ_TOOL_BLOCKS
    from internal_tool_blocks import INTERNAL_GENERAL_TOOL_NAMES
    from unity_tool_schema_projection import canonical_unity_read_tool_input_schema
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    diagnostics.register_host_diagnostic_tools(gateway)
    for name, schema in diagnostics.INPUT_SCHEMAS.items():
        assert name in gateway._tools
        assert name in EXTERNAL_MCP_READ_TOOL_BLOCKS["diagnostics"]
        assert name in INTERNAL_GENERAL_TOOL_NAMES
        projected = canonical_unity_read_tool_input_schema(name)
        assert projected["properties"] == schema["properties"]
        assert "executionTarget" not in projected.get("required", [])
