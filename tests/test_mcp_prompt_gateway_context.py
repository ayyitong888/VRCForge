"""Gateway/Prompt adapter regressions; identities here are fixtures, not live Unity proof."""
import hashlib
import pytest

from agent_gateway import AgentGateway
from execution_target import project_identity
from mcp_prompt_registry import McpPromptError
from mcp_resource_registry import McpResourceError


def _target(path, *, pid=123):
    root = str(path.resolve())
    project_id = project_identity(root)
    return {"schema": "vrcforge.execution_target.v1", "scope": "project",
            "namespace": f"vrcforge://projects/{project_id}",
            "project": {"root": root, "projectId": project_id},
            "editor": {"unityPid": pid, "processStartTime": "1.0", "coreInstanceId": "core-1"}}


def _gateway(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    skill = {"name": "fixture-workflow", "source": "user", "skillType": "package",
             "instructions": "Inspect the exact target before acting.", "allowedTools": []}
    monkeypatch.setattr(type(gateway._skills), "build_skill_registry", lambda *a, **kw: {"skills": [skill]})
    return gateway, skill


def _publish(gateway, target, operation="read-1"):
    result = {"ok": True, "operationId": operation, "executionTarget": target}
    gateway.publish_mcp_tool_result_resource("vrcforge_get_property", {}, result, source_mode="test_fixture")
    return result["resources"]


def test_resource_listing_does_not_replace_verified_identity(tmp_path, monkeypatch):
    gateway, _ = _gateway(tmp_path, monkeypatch)
    handles = _publish(gateway, _target(tmp_path / "project"))
    gateway.list_mcp_resources()
    current = gateway.read_mcp_resource("vrcforge://session/current/identity")["structuredContent"]
    assert current["uri"] == handles["identityLockUri"]
    assert current["data"]["status"] == "bound"


def test_rejected_arguments_cannot_publish_verified_identity(tmp_path, monkeypatch):
    gateway, _ = _gateway(tmp_path, monkeypatch)
    result = {"ok": False, "operationId": "rejected", "error": "identity drifted"}
    gateway.publish_mcp_tool_result_resource("vrcforge_get_property", {"executionTarget": _target(tmp_path)},
                                             result, source_mode="test_fixture")
    assert "identityLockUri" not in result["resources"]
    gateway.list_mcp_resources()
    assert gateway.read_mcp_resource("vrcforge://session/current/identity")["structuredContent"]["data"]["status"] == "unbound"


@pytest.mark.parametrize("failure", [{"status": "failed"}, {"success": False}, {"isError": True}, {"outcome": {"status": "failed"}}])
def test_each_canonical_failure_shape_cannot_bind_identity(tmp_path, monkeypatch, failure):
    gateway, _ = _gateway(tmp_path, monkeypatch)
    response = {"operationId": "failure", "executionTarget": _target(tmp_path), **failure}
    gateway.publish_mcp_tool_result_resource("vrcforge_get_property", {}, response, source_mode="test_fixture")
    assert "identityLockUri" not in response["resources"]


def test_gateway_prompt_requires_current_bound_identity_and_matching_context(tmp_path, monkeypatch):
    gateway, _ = _gateway(tmp_path, monkeypatch)
    target = _target(tmp_path / "project")
    handles = _publish(gateway, target)
    arguments = {"identityLockUri": handles["identityLockUri"], "sessionContextUri": handles["operationReceiptUri"]}
    assert gateway.get_mcp_prompt("fixture-workflow", arguments)["structuredContent"]["context"]["status"] == "ready_for_planning"
    _publish(gateway, _target(tmp_path / "project", pid=456), "read-2")
    with pytest.raises((McpResourceError, McpPromptError)):
        gateway.get_mcp_prompt("fixture-workflow", arguments)
    # Historical reads stay available even though a Prompt cannot use the old current lock.
    assert gateway.read_mcp_resource(handles["operationReceiptUri"])["structuredContent"]["data"]["operationId"] == "read-1"


def test_another_session_resource_pair_cannot_self_authorize(tmp_path, monkeypatch):
    gateway, _ = _gateway(tmp_path, monkeypatch)
    _publish(gateway, _target(tmp_path / "current"))
    target = _target(tmp_path / "other")
    identity = gateway._mcp_resource_identity(target)
    other = gateway._mcp_resources.publish(base_uri="vrcforge://session/other/identity", name="Other",
        resource_type="session_identity_lock", identity=identity,
        data={"status": "bound", "exactExecutionTarget": target}, source_mode="test_fixture", refresh_rule="explicit")
    receipt = gateway._mcp_resources.publish(base_uri="vrcforge://operation/other/receipt", name="Other receipt",
        resource_type="operation_receipt", identity=identity, data={}, source_mode="test_fixture", refresh_rule="explicit")
    with pytest.raises((McpResourceError, McpPromptError)):
        gateway.get_mcp_prompt("fixture-workflow", {"identityLockUri": other["uri"], "sessionContextUri": receipt["uri"]})


def test_bind_tool_result_publishes_its_verified_target(tmp_path, monkeypatch):
    gateway, _ = _gateway(tmp_path, monkeypatch)
    target = _target(tmp_path)
    result = {"ok": True, "operationId": "bind-1", "result": {"status": "bound", "executionTarget": target}}
    gateway.publish_mcp_tool_result_resource("vrcforge_bind_execution_target", {}, result, source_mode="test_fixture")
    assert gateway.read_mcp_resource(result["resources"]["identityLockUri"])["structuredContent"]["data"]["exactExecutionTarget"] == target


def test_support_files_are_lazy_bound_once_and_checked_on_use(tmp_path, monkeypatch):
    gateway, skill = _gateway(tmp_path, monkeypatch)
    skill["supportFiles"] = ["guide.md"]
    calls = []
    text = {"value": "first\r\n中文"}
    def load(_service, _skill):
        calls.append(True)
        return [{"path": "guide.md", "content": text["value"]}]
    monkeypatch.setattr(type(gateway._skills), "load_runtime_skill_support_files", load)
    listed = gateway.list_mcp_prompts()["prompts"][0]["_meta"]
    assert calls == []
    prompt = gateway.get_mcp_prompt("fixture-workflow")
    assert len(calls) == 1
    support = prompt["structuredContent"]["skill"]["supportFiles"][0]
    assert support["sha256"] == hashlib.sha256(text["value"].encode("utf-8")).hexdigest()
    assert listed["contentHash"] == prompt["_meta"]["contentHash"]
    assert prompt["_meta"]["supportContentHash"]
    gateway._mcp_prompts.validate_provenance(prompt["_meta"])
    text["value"] = "changed"
    with pytest.raises(McpPromptError):
        gateway._mcp_prompts.validate_provenance(prompt["_meta"])
