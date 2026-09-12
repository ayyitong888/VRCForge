"""Public protocol regression tests; callback results remain lossless resources."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_mcp_standard import McpStandardRouter, LATEST_PROTOCOL_VERSION
from external_mcp_result_projection import project_prompt

MODE = "io.vrcforge/resultMode"
URI = "vrcforge://operation/projection-test/receipt?revision=1"
ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = Path(os.environ["VRCFORGE_REGRESSION_ARCHIVE_ROOT"]) if os.environ.get("VRCFORGE_REGRESSION_ARCHIVE_ROOT") else ROOT / ".tmp" / "regression-archives"


def protocol(protocol, payload, *, descriptor=None):
    calls = []
    descriptor = descriptor or {"name": "fixture", "inputSchema": {"type": "object"}, "write": True}
    def call(name, args):
        calls.append((name, deepcopy(args)))
        return deepcopy(payload)
    def read(uri):
        assert uri == payload["operationResource"]
        return {"contents": [{"uri": uri, "text": json.dumps(payload)}], "structuredContent": deepcopy(payload)}
    cls = Mcp2026Router if protocol == "2026" else McpStandardRouter
    router = cls(lambda *_: [descriptor], call, resource_read=read)
    if protocol == "standard":
        router.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
    def request(method="tools/call", mode=None, **params):
        meta = {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {}} if protocol == "2026" else {}
        if mode is not None:
            meta[MODE] = mode
        body = {"jsonrpc": "2.0", "id": 1, "method": method,
                "params": {"_meta": meta, **({"name": "fixture", "arguments": {"value": 3}} if method == "tools/call" else {}), **params}}
        result = router.handle(body)
        return result[0] if protocol == "2026" else result
    return request, calls


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_large_public_result_is_compact_and_resource_expansion_never_replays_write(transport):
    raw = {"ok": True, "status": "success", "mutationStarted": True, "committed": True,
           "commitState": "complete", "executionTarget": {"project": {"root": "D:/Project"}},
           "operationResource": URI, "result": {"summary": {"bindingCount": 2000},
           "bindings": [{"path": f"Object/{i}", "property": "value", "value": i} for i in range(2000)]}}
    request, calls = protocol(transport, raw)
    compact = request()["result"]["structuredContent"]
    assert compact["resultPresentation"]["mode"] == "compact"
    assert compact["resultPresentation"]["fullResultUri"] == URI
    for field in ("mutationStarted", "committed", "commitState", "executionTarget", "operationResource"):
        assert compact[field] == raw[field]
    full = request("resources/read", uri=URI)["result"]["structuredContent"]
    assert full == raw
    assert calls == [("fixture", {"value": 3})]
    assert len(json.dumps(compact)) < len(json.dumps(raw)) * 0.1


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_full_mode_is_discoverable_validated_and_not_forwarded(transport):
    raw = {"ok": True, "operationResource": URI, "result": {"items": list(range(10000))}}
    request, calls = protocol(transport, raw)
    tool = request("tools/list")["result"]["tools"][0]
    assert tool["_meta"]["resultPresentation"]["requestMetaKey"] == MODE
    full = request(mode="full")["result"]["structuredContent"]
    assert full["result"] == raw["result"]
    assert request(mode="invalid")["error"]["code"] == -32602
    assert calls == [("fixture", {"value": 3})]


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_without_readable_resource_no_payload_is_hidden(transport):
    raw = {"ok": False, "status": "failed", "mutationStarted": None, "committed": None,
           "commitState": "unknown", "result": {"items": list(range(10000))}}
    request, _ = protocol(transport, raw)
    public = request()["result"]["structuredContent"]
    assert public["result"] == raw["result"]
    assert public["mutationStarted"] is None


@pytest.mark.parametrize("transport", ["2026", "standard"])
@pytest.mark.parametrize("archive", ["551", "571", "580"])
def test_real_failure_or_preview_keeps_actionable_facts(transport, archive):
    path = ARCHIVE_ROOT / f"{archive}.json"
    if not path.exists():
        pytest.skip("Local public archive not distributed")
    raw = json.loads(path.read_text("utf-8-sig"))["results"][2]["structuredContent"]
    request, _ = protocol(transport, raw)
    full = request(mode="full")["result"]["structuredContent"]
    compact = request()["result"]["structuredContent"]
    for field in ("ok", "status", "operationStatus", "mutationStarted", "committed", "commitState", "executionTarget", "nextAction", "operationResource"):
        assert compact.get(field) == full.get(field)
    for field in ("errorCode", "failureLayer", "failurePhase", "mutationStarted", "committed", "commitState", "safeToRetry", "nextAction", "recovery"):
        if field in full.get("errorDetails", {}):
            assert compact["errorDetails"][field] == full["errorDetails"][field]


def test_catalog_deduplicates_only_exact_copies_and_full_restores_metadata():
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    identity = {"scope": "avatar", "required": ["project", "editor", "avatar"]}
    descriptor = {"name": "fixture", "inputSchema": {"type": "object"}, "outputSchema": schema,
                  "requiredIdentity": identity, "_meta": {"requiredIdentity": identity, "resultContract": schema,
                  "permission": "Write", "checkpoint": {"required": True}}}
    request, _ = protocol("2026", {}, descriptor=descriptor)
    compact = request("tools/list")["result"]["tools"][0]
    full = request("tools/list", mode="full")["result"]["tools"][0]
    assert compact["outputSchema"] == schema
    assert compact["_meta"]["requiredIdentity"] == identity
    assert compact["_meta"]["checkpoint"]["required"] is True
    assert "requiredIdentity" not in compact and "resultContract" not in compact["_meta"]
    assert full["requiredIdentity"] == identity and full["_meta"]["resultContract"] == schema


@pytest.mark.parametrize("method", ["tools/call", "tools/list"])
def test_stdio_internal_hop_keeps_full_data_for_outer_presentation(monkeypatch, method):
    from tools.vrcforge_agent_mcp_stdio import VRCForgeBridge
    bridge = VRCForgeBridge.__new__(VRCForgeBridge)
    bridge.timeout_seconds = 30
    bridge.tool_call_timeout_seconds = 120
    requests = []
    def send(*args, **kwargs):
        requests.append(kwargs["payload"])
        return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    monkeypatch.setattr(bridge, "request_json", send)
    bridge._mcp_request(method, {"name": "fixture", "arguments": {}}, token="test")
    meta = requests[0]["params"]["_meta"]
    assert meta[MODE] == "full"
    assert meta["io.modelcontextprotocol/protocolVersion"] == PROTOCOL_VERSION


@pytest.mark.parametrize("transport", ["2026", "standard"])
@pytest.mark.parametrize("mode", ["compact", "full"])
def test_actual_backend_router_bridge_and_outer_router_preserve_full_option(monkeypatch, transport, mode):
    from tools.vrcforge_agent_mcp_stdio import VRCForgeBridge
    raw = {"ok": True, "status": "success", "operationResource": URI,
           "result": {"items": list(range(10000))}}
    dispatches = []
    def handler(name, arguments):
        dispatches.append((name, arguments))
        return deepcopy(raw)
    backend = Mcp2026Router(lambda *_: [{"name": "fixture", "write": True}], handler,
                            resource_read=lambda _: {"contents": []})
    bridge = VRCForgeBridge.__new__(VRCForgeBridge)
    bridge.timeout_seconds = 30
    bridge.tool_call_timeout_seconds = 120
    monkeypatch.setattr(bridge, "require_token", lambda: "test")
    monkeypatch.setattr(bridge, "request_json", lambda *_, **kwargs: backend.handle(kwargs["payload"])[0])
    inner = bridge.call_tool("fixture", {"value": 3})
    assert inner["result"] == raw["result"]
    outer, calls = protocol(transport, inner)
    result = outer(mode=mode)["result"]["structuredContent"]
    assert dispatches == calls == [("fixture", {"value": 3})]
    if mode == "full":
        assert result["result"] == raw["result"]
    else:
        assert result["resultPresentation"]["mode"] == "compact"


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_593_nested_transport_duplicates_compact_without_losing_console_facts(transport):
    path = ARCHIVE_ROOT / "593.json"
    if not path.exists():
        pytest.skip("Local public archive not distributed")
    raw = json.loads(path.read_text("utf-8-sig"))["results"][4]["structuredContent"]
    original = deepcopy(raw)
    nested = raw["result"]["result"]
    assert json.loads(nested["stdout"]) == nested["payload"]
    request, calls = protocol(transport, raw)
    compact = request()["result"]["structuredContent"]
    saved = compact["result"]["result"]
    assert "stdout" not in saved
    assert saved["payload"]["structuredContent"] == nested["payload"]["structuredContent"]
    # The structured copy adds operationId; this text is not exactly equal.
    assert json.loads(nested["payload"]["content"][0]["text"]) != nested["payload"]["structuredContent"]
    assert saved["payload"]["content"] == nested["payload"]["content"]
    assert saved["exitCode"] == nested["exitCode"] and saved["stderr"] == nested["stderr"]
    assert request("resources/read", uri=raw["operationResource"])["result"]["structuredContent"] == original
    assert request(mode="full")["result"]["structuredContent"]["result"] == original["result"]
    assert raw == original
    assert len(calls) == 2  # Resource expansion did not dispatch a tool.


@pytest.mark.parametrize("transport", ["2026", "standard"])
@pytest.mark.parametrize("stdout", ['{"value":2}', 'not JSON', '{"value":true}', '{"value":NaN}'])
def test_transport_stdout_is_retained_unless_exactly_equal(transport, stdout):
    nested = {"exitCode": 0, "stderr": "keep diagnostic", "stdout": stdout, "payload": {"value": 1}}
    request, _ = protocol(transport, {"ok": True, "operationResource": URI, "result": nested})
    assert request()["result"]["structuredContent"]["result"] == nested


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_equal_transport_copies_stay_full_without_a_resource(transport):
    nested = {"exitCode": 0, "stderr": "", "stdout": '{"value":1}', "payload": {"value": 1}}
    request, _ = protocol(transport, {"ok": True, "result": nested})
    assert request()["result"]["structuredContent"]["result"] == nested


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_exact_nested_content_copy_removed_but_diagnostics_and_annotations_retained(transport):
    facts = {"error": "write outcome unknown", "mutationStarted": None, "warnings": ["read back first"]}
    duplicate = {"type": "text", "text": json.dumps(facts)}
    retained = [
        {**duplicate, "annotations": {"audience": ["user"]}},
        {"type": "text", "text": "additional diagnostic"},
        {"type": "text", "text": '{"mutationStarted":false}'},
    ]
    payload = {"content": [duplicate, *retained], "structuredContent": facts, "isError": True}
    nested = {"exitCode": 1, "stderr": "transport warning", "stdout": json.dumps(payload), "payload": payload}
    request, _ = protocol(transport, {"ok": False, "operationResource": URI, "result": nested})
    compact = request()["result"]["structuredContent"]
    assert compact["result"] == {"exitCode": 1, "stderr": "transport warning", "payload": {**payload, "content": retained}}
    assert compact["resultPresentation"]["omittedFields"] == ["result.stdout", "result.payload.content[0]"]
    assert request(mode="full")["result"]["structuredContent"]["result"] == nested


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_public_prompt_dispatcher_compacts_only_exact_structured_duplicate(transport):
    structured = {
        "schema": "vrcforge.skill_prompt.v1",
        "skill": {
            "id": "fixture-skill",
            "title": "Fixture",
            "description": "desc",
            "instructions": "large canonical instructions",
            "supportFiles": [{"path": "workflow.json", "content": "large support"}],
        },
        "context": {"status": "awaiting_resources", "requiredResources": ["identity", "session"]},
        "provenance": {"skillId": "fixture-skill", "contentHash": "a" * 64},
        "rules": ["preserve approval"],
    }
    raw = {
        "description": "fixture prompt",
        "messages": [{
            "role": "user",
            "content": {"type": "text", "text": json.dumps(structured, ensure_ascii=False)},
        }],
        "structuredContent": structured,
        "_meta": {"skillId": "fixture-skill"},
        "resultType": "complete",
    }
    cls = Mcp2026Router if transport == "2026" else McpStandardRouter
    router = cls(
        lambda *_: [],
        lambda _name, _arguments: {},
        prompt_list=lambda _params: {"prompts": []},
        prompt_get=lambda _name, _arguments: deepcopy(raw),
    )
    if transport == "standard":
        assert router.handle({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"}},
        })["result"]

    def request(meta=None):
        params = {"name": "fixture", "arguments": {}}
        if meta is not None:
            params["_meta"] = meta
        message = {"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": params}
        if transport == "2026":
            message["params"].setdefault("_meta", {})["io.modelcontextprotocol/protocolVersion"] = PROTOCOL_VERSION
            message["params"]["_meta"].setdefault("io.modelcontextprotocol/clientCapabilities", {})
        response = router.handle(message)
        return response[0] if transport == "2026" else response

    compact = request()["result"]
    assert compact["messages"] == raw["messages"]
    assert compact["structuredContent"]["context"] == structured["context"]
    assert compact["structuredContent"]["provenance"] == structured["provenance"]
    assert "instructions" not in compact["structuredContent"]["skill"]
    assert "content" not in compact["structuredContent"]["skill"]["supportFiles"][0]
    assert compact["resultPresentation"] == {
        "mode": "compact",
        "fullContentPath": "messages[0].content.text",
        "nextAction": "Use the complete prompt body in messages[0].content.text; structuredContent is concise state and provenance metadata.",
    }

    explicit_compact = request({"io.vrcforge/resultMode": "compact"})["result"]
    assert explicit_compact == compact
    full = request({"io.vrcforge/resultMode": "full"})["result"]
    assert full["messages"] == raw["messages"]
    assert full["structuredContent"] == raw["structuredContent"]
    assert full["_meta"]["skillId"] == raw["_meta"]["skillId"]

    invalid = request({"io.vrcforge/resultMode": "invalid"})
    assert invalid["error"]["code"] == -32602


@pytest.mark.parametrize("transport", ["2026", "standard"])
def test_public_prompt_dispatcher_preserves_nonduplicate_structured_content(transport):
    raw = {
        "messages": [{"role": "user", "content": {"type": "text", "text": "human-facing prompt"}}],
        "structuredContent": {"schema": "vrcforge.skill_prompt.v1", "context": {"status": "ready"}},
    }
    cls = Mcp2026Router if transport == "2026" else McpStandardRouter
    router = cls(lambda *_: [], lambda _name, _arguments: {}, prompt_get=lambda *_: deepcopy(raw))
    if transport == "standard":
        router.handle({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"}},
        })
    params = {"name": "fixture", "arguments": {}}
    if transport == "2026":
        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                            "io.modelcontextprotocol/clientCapabilities": {}}
    response = router.handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": params})
    response = response[0] if transport == "2026" else response
    assert response["result"]["messages"] == raw["messages"]
    assert response["result"]["structuredContent"] == raw["structuredContent"]


def test_prompt_projection_requires_text_content_type():
    structured = {"schema": "vrcforge.skill_prompt.v1", "context": {"status": "ready"}}
    value = {
        "messages": [{"role": "user", "content": {"type": "resource", "text": json.dumps(structured)}}],
        "structuredContent": structured,
    }
    assert project_prompt(value, mode="compact") == value
