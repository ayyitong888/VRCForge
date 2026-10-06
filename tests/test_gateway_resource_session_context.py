import asyncio
import json
from pathlib import Path

import pytest

from agent_gateway import AgentGateway
from mcp_resource_registry import McpResourceError
from tests.test_dashboard_server import bind_test_runtime_planner
from tests.test_execution_target import _target


def target(project):
    return _target(Path("D:/Unity") / project.replace(":", "-"), scope="project")


def publish(gateway, project):
    result = {"ok": True, "operationId": "same-operation", "executionTarget": target(project)}
    gateway.publish_mcp_tool_result_resource("vrcforge_status", {}, result, source_mode="test")
    return result


def test_sessions_keep_identity_and_explicit_history_sharing(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    with gateway.resource_context("a"):
        a = publish(gateway, "a")
        identity_a = a["resources"]["identityLockUri"]
        gateway._validate_mcp_prompt_resource(identity_a, "session_identity_lock")
    with gateway.resource_context("b"):
        b = publish(gateway, "b")
        listed = {r["uri"] for r in gateway.list_mcp_resources()["resources"]}
        assert b["operationResource"] in listed and a["operationResource"] not in listed
        assert gateway.read_mcp_resource(a["operationResource"])["structuredContent"]["contextId"] == "a"
        with pytest.raises(McpResourceError, match="context"):
            gateway._validate_mcp_prompt_resource(identity_a, "session_identity_lock")
        assert gateway.read_mcp_resource("vrcforge://session/current/identity")["structuredContent"]["data"]["exactExecutionTarget"] == target("b")
    with gateway.resource_context("a"):
        assert gateway.read_mcp_resource("vrcforge://session/current/identity")["structuredContent"]["uri"] == identity_a
    assert gateway._mcp_resource_context.get() is None


def test_context_survives_thread_hop_and_resets_on_error(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    async def run():
        with gateway.resource_context("a"):
            result = await asyncio.to_thread(publish, gateway, "a")
            with pytest.raises(RuntimeError):
                with gateway.resource_context("b"):
                    raise RuntimeError("fixture")
            assert gateway._mcp_resource_context.get() == "a"
            return result
    a = asyncio.run(run())
    assert gateway.read_mcp_resource(a["operationResource"])["structuredContent"]["contextId"] == "a"
    assert gateway._mcp_resource_context.get() is None
    assert all(r["_meta"]["resourceType"] == "tool_catalog" for r in gateway.list_mcp_resources()["resources"])


def test_runtime_sessions_and_exception_restore_resource_owner(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    captures = []
    def respond(prompt):
        captures.append(publish(gateway, gateway._mcp_resource_context.get()))
        return {"text": json.dumps({"action": "reply", "reply": "done"})}
    bind_test_runtime_planner(gateway, respond)
    for session in ("a", "b"):
        gateway.runtime_message({"message": "hello", "sessionId": session, "clientTurnId": session})
        assert gateway._mcp_resource_context.get() is None
    assert [gateway.read_mcp_resource(r["operationResource"])["structuredContent"]["contextId"] for r in captures] == ["runtime:a", "runtime:b"]
    def fail(*args, **kwargs):
        assert gateway._mcp_resource_context.get() == "runtime:c"
        raise RuntimeError("fixture")
    monkeypatch.setattr(gateway, "runtime_observe", fail)
    with gateway.resource_context("outer"):
        with pytest.raises(RuntimeError):
            gateway.runtime_message({"message": "hello", "sessionId": "c", "clientTurnId": "c"})
        assert gateway._mcp_resource_context.get() == "outer"


def test_http_gateway_interleaved_sessions_keep_current_state_and_share_history(tmp_path):
    import httpx
    from agent_gateway import create_agent_mcp_app
    from tests.test_agent_gateway_integrity import _external_gateway
    from tests.test_mcp_request_sessions import request, headers
    gateway = _external_gateway(tmp_path)
    name = "vrcforge_context_fixture"
    gateway.register_tool(name, "When to use: inspect fixture. When NOT to use: writes.", "unity",
                          lambda _: {"ok": True, "executionTarget": target(gateway._mcp_resource_context.get())})
    gateway.register_external_mcp_unity_tool(name, "diagnostics")
    app = create_agent_mcp_app(gateway)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            async def post(method, session=None, **params):
                body = request(method)
                body["params"].update(params)
                h = headers(method, session, gateway.ensure_config().token)
                if method == "tools/call":
                    h["Mcp-Name"] = name
                response = await client.post("/mcp", json=body, headers=h)
                assert response.status_code == 200, response.text
                assert "error" not in response.json(), response.text
                return response
            a = (await post("server/discover")).headers["Mcp-Session-Id"]
            b = (await post("server/discover")).headers["Mcp-Session-Id"]
            first = (await post("tools/call", a, name=name, arguments={})) .json()["result"]["structuredContent"]
            second = (await post("tools/call", b, name=name, arguments={})) .json()["result"]["structuredContent"]
            assert first["ok"] and second["ok"]
            for session, own, other in ((a, first, second), (b, second, first)):
                listed = (await post("resources/list", session)).json()["result"]["resources"]
                uris = {r["uri"] for r in listed}
                assert own["operationResource"] in uris and other["operationResource"] not in uris
                shared = (await post("resources/read", session, uri=other["operationResource"])).json()["result"]["structuredContent"]
                assert shared["contextId"] == "external:" + (b if session == a else a)
                current = (await post("resources/read", session, uri="vrcforge://session/current/identity")).json()["result"]["structuredContent"]
                assert current["uri"] == own["resources"]["identityLockUri"]
            assert gateway._mcp_resource_context.get() is None
    asyncio.run(run())
