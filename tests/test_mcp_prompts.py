from __future__ import annotations

import json
import pytest

from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_mcp_standard import LATEST_PROTOCOL_VERSION, McpStandardRouter
from mcp_prompt_registry import McpPromptError, McpPromptRegistry, PROMPT_CONTEXT_SCHEMA
from mcp_resource_registry import McpResourceRegistry
from external_mcp_result_projection import project_prompt


def _skills(count: int = 9) -> dict:
    return {
        "skills": [
            {
                "name": f"avatar-workflow-{index}",
                "title": f"Avatar Workflow {index}",
                "description": f"Workflow {index}",
                "source": "user",
                "skillType": "package",
                "packageId": f"com.vrcforge.skill.{index}",
                "enabled": True,
                "available": True,
                "permissionMode": "approval_required",
                "riskLevel": "medium",
                "allowedTools": ["vrcforge_read_avatar", "vrcforge_write_avatar"],
                "toolBlocks": ["avatar", "integrations/gesture-manager"],
                "instructions": f"Inspect workflow {index}, then apply only with approval.",
                "acceptance": ["Fresh readback matches the requested state."],
            }
            for index in range(1, count + 1)
        ]
    }


def _tools(layer: str) -> list[dict]:
    tools = [
        {
            "name": "vrcforge_read_avatar",
            "_meta": {"permission": "ReadOnly", "toolBlock": "avatar"},
        }
    ]
    if layer == "execution":
        tools.append(
            {
                "name": "vrcforge_write_avatar",
                "write": True,
                "_meta": {"permission": "Write", "toolBlock": "avatar"},
            }
        )
    return tools


def _registry(*, resource_validate=None, support_files_loader=None) -> McpPromptRegistry:
    return McpPromptRegistry(
        lambda: _skills(),
        _tools,
        resource_validate=resource_validate,
        support_files_loader=support_files_loader,
    )


def _v2_request(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            **(params or {}),
        },
    }


def test_prompt_registry_lists_all_nine_skills_with_stable_provenance() -> None:
    registry = _registry()
    first = registry.list()
    second = registry.list()
    assert len(first["prompts"]) == 9
    assert first["promptGeneration"] == second["promptGeneration"]
    assert [item["name"] for item in first["prompts"]] == [
        f"avatar-workflow-{index}" for index in range(1, 10)
    ]
    for prompt in first["prompts"]:
        assert prompt["_meta"]["skillId"] == prompt["name"]
        assert prompt["_meta"]["source"] == "user"
        assert prompt["_meta"]["versionSource"] == "content-addressed"
        assert prompt["_meta"]["writeToolsCallableInPlanning"] is False


def test_prompt_descriptor_exposes_acceptance_criteria_consumed_by_get() -> None:
    registry = _registry()
    descriptor = registry.list()["prompts"][0]
    argument_names = {item["name"] for item in descriptor["arguments"]}
    assert "acceptanceCriteria" in argument_names

    context = registry.get(
        "avatar-workflow-1",
        {"acceptanceCriteria": ["Saved readback must prove the exact requested state."]},
    )["structuredContent"]["context"]
    assert context["acceptanceCriteria"] == ["Saved readback must prove the exact requested state."]


def test_prompt_checkpoint_policy_projects_required_workflow_support_contract() -> None:
    skills = _skills(1)
    skills["skills"][0]["backupRestore"] = "not required"
    skills["skills"][0]["supportFiles"] = ["workflows/wardrobe.json"]
    workflow = json.dumps(
        {
            "schema": "vrcforge.skill-package.workflow.v1",
            "checkpoint": {"required": True, "boundaries": ["before_each_write"]},
        }
    )
    registry = McpPromptRegistry(
        lambda: skills,
        _tools,
        support_files_loader=lambda _skill: [{"path": "workflows/wardrobe.json", "content": workflow}],
    )
    context = registry.get("avatar-workflow-1")["structuredContent"]["context"]
    assert "required" in context["checkpointPolicy"].lower()
    assert "before_each_write" in context["checkpointPolicy"]


def test_prompt_get_requires_resources_without_guessing_and_separates_tool_visibility() -> None:
    registry = _registry()
    missing = registry.get("avatar-workflow-1")
    payload = missing["structuredContent"]
    context = payload["context"]
    assert context["schema"] == PROMPT_CONTEXT_SCHEMA
    assert context["status"] == "awaiting_resources"
    assert context["missingRequiredResources"] == ["identityLockUri", "sessionContextUri"]
    assert context["planningTools"] == ["vrcforge_read_avatar"]
    assert context["executionWriteTools"] == ["vrcforge_write_avatar"]
    assert context["writeToolBlocksToLoad"] == ["avatar"]
    assert context["awaitingUserHardStop"] is True
    assert len(context["pausePoints"]) >= 3
    assert len(context["gmCases"]) >= 3
    assert context["gameOnlyAcceptance"]

    ready = registry.get(
        "avatar-workflow-1",
        {
            "identityLockUri": "vrcforge://session/current/identity?revision=2",
            "sessionContextUri": "vrcforge://operation/read-1/receipt?revision=1",
            "scope": json.dumps({"targetItems": ["avatar:1"], "doNotTouch": ["avatar:2"]}),
            "protectedState": json.dumps({"userAdjustedStates": ["object:1"]}),
        },
    )["structuredContent"]["context"]
    assert ready["status"] == "awaiting_resources"
    assert ready["scope"]["doNotTouch"] == ["avatar:2"]
    assert ready["protectedState"]["userAdjustedStates"] == ["object:1"]


def test_prompt_get_without_resource_validator_fails_closed() -> None:
    context = _registry().get(
        "avatar-workflow-1",
        {
            "identityLockUri": "vrcforge://session/current/identity?revision=2",
            "sessionContextUri": "vrcforge://operation/read-1/receipt?revision=1",
        },
    )["structuredContent"]["context"]
    assert context["status"] == "awaiting_resources"
    assert context["missingRequiredResources"] == ["identityLockUri", "sessionContextUri"]


def test_prompt_get_accepts_only_matching_live_resources_from_real_registry(tmp_path) -> None:
    resources = McpResourceRegistry(tmp_path / "resources")
    identity = resources.publish(
        base_uri="vrcforge://session/current/identity",
        name="Identity", resource_type="session_identity_lock",
        data={"status": "bound"}, identity={"projectId": "p1", "namespace": "n1"},
        source_mode="test", refresh_rule="Replace explicitly.",
    )
    receipt = resources.publish(
        base_uri="vrcforge://operation/read-1/receipt",
        name="Receipt", resource_type="operation_receipt",
        data={"ok": True}, identity={"projectId": "p1", "namespace": "n1"},
        source_mode="test", refresh_rule="Invoke again.",
    )
    cross = resources.publish(
        base_uri="vrcforge://operation/cross/receipt",
        name="Cross", resource_type="operation_receipt",
        data={"ok": True}, identity={"projectId": "p2", "namespace": "n2"},
        source_mode="test", refresh_rule="Invoke again.",
    )
    unbound = resources.publish(
        base_uri="vrcforge://session/unbound/identity",
        name="Unbound", resource_type="session_identity_lock",
        data={"status": "unbound"}, identity={},
        source_mode="test", refresh_rule="Replace explicitly.",
    )
    def validate(uri: str, expected_type: str, expected_identity=None) -> dict:
        return resources.validate_reference(
            uri, expected_type=expected_type or None, expected_identity=expected_identity
        )

    registry = _registry(resource_validate=validate)
    ready = registry.get("avatar-workflow-1", {"identityLockUri": identity["uri"], "sessionContextUri": receipt["uri"]})
    assert ready["structuredContent"]["context"]["status"] == "ready_for_planning"
    for bad_identity in (
        identity["uri"].replace("revision=1", "revision=2"),
        "vrcforge://session/current/identity",
    ):
        with pytest.raises(Exception):
            registry.get("avatar-workflow-1", {"identityLockUri": bad_identity, "sessionContextUri": receipt["uri"]})
    with pytest.raises(Exception):
        registry.get("avatar-workflow-1", {"identityLockUri": unbound["uri"], "sessionContextUri": receipt["uri"]})
    with pytest.raises(Exception):
        registry.get("avatar-workflow-1", {"identityLockUri": identity["uri"], "sessionContextUri": cross["uri"]})


def test_prompt_get_rejects_unverified_resource_references() -> None:
    def validate(uri: str, expected_type: str, _expected_identity=None) -> dict:
        if uri != "vrcforge://session/current/identity?revision=2":
            raise McpPromptError("Resource reference is stale or unavailable")
        if expected_type != "session_identity_lock":
            raise McpPromptError("Resource type mismatch")
        return {"resourceType": expected_type, "identity": {"projectId": "p1"}}

    registry = McpPromptRegistry(lambda: _skills(), _tools, resource_validate=validate)
    try:
        registry.get(
            "avatar-workflow-1",
            {
                "identityLockUri": "vrcforge://session/other/identity?revision=1",
                "sessionContextUri": "vrcforge://operation/read-1/receipt?revision=1",
            },
        )
    except McpPromptError:
        pass
    else:  # pragma: no cover - fail-closed invariant
        raise AssertionError("unverified Resource references must fail")


def test_prompt_provenance_changes_when_support_file_declaration_changes() -> None:
    skills = _skills(1)
    skills["skills"][0]["supportFiles"] = ["workflows/one.json"]
    registry = McpPromptRegistry(lambda: skills, _tools)
    first = registry.list()["prompts"][0]["_meta"]["contentHash"]
    skills["skills"][0]["supportFiles"] = ["workflows/two.json"]
    second = registry.list()["prompts"][0]["_meta"]["contentHash"]
    assert first != second


def test_prompt_provenance_can_bind_support_file_content_hash() -> None:
    skills = _skills(1)
    skills["skills"][0]["supportFiles"] = ["workflows/one.json"]
    content = {"workflows/one.json": "v1"}
    registry = McpPromptRegistry(lambda: skills, _tools, support_files_loader=lambda _skill: [{"path": "workflows/one.json", "content": content["workflows/one.json"]}])
    first = registry.get("avatar-workflow-1")["_meta"]["supportContentHash"]
    content["workflows/one.json"] = "v2"
    second = registry.get("avatar-workflow-1")["_meta"]["supportContentHash"]
    assert first != second


def test_prompt_get_rejects_unknown_id_and_invalid_context() -> None:
    registry = _registry()
    for name, arguments in (("missing", {}), ("avatar-workflow-1", {"scope": "[]"})):
        try:
            registry.get(name, arguments)
        except McpPromptError:
            pass
        else:  # pragma: no cover - fail-closed invariant
            raise AssertionError("invalid Prompt request must fail")


def test_prompt_provenance_is_exact_and_cannot_authorize_an_undeclared_tool() -> None:
    registry = _registry()
    provenance = registry.get("avatar-workflow-1")["_meta"]
    verified = registry.validate_provenance(provenance, tool_name="vrcforge_read_avatar")
    assert verified["status"] == "verified"
    assert verified["skillId"] == "avatar-workflow-1"
    for changed, tool_name in (
        ({**provenance, "contentHash": "0" * 64}, "vrcforge_read_avatar"),
        (provenance, "vrcforge_delete_project"),
    ):
        try:
            registry.validate_provenance(changed, tool_name=tool_name)
        except McpPromptError:
            pass
        else:  # pragma: no cover - fail-closed invariant
            raise AssertionError("stale or out-of-scope Prompt provenance must fail")


def test_standard_mcp_lists_and_gets_native_prompts() -> None:
    prompts = _registry()
    router = McpStandardRouter(
        lambda: [],
        lambda _name, _arguments: {},
        prompt_list=prompts.list,
        prompt_get=prompts.get,
        prompt_list_revision=lambda: prompts.list({"pageSize": 1})["promptGeneration"],
    )
    initialized = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "prompt-test", "version": "1"},
            },
        }
    )
    assert initialized["result"]["capabilities"]["prompts"] == {"listChanged": True}
    listed = router.handle({"jsonrpc": "2.0", "id": 2, "method": "prompts/list", "params": {}})
    assert len(listed["result"]["prompts"]) == 9
    prompt = router.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "prompts/get",
            "params": {"name": "avatar-workflow-1", "arguments": {}},
        }
    )
    assert prompt["result"]["structuredContent"]["context"]["status"] == "awaiting_resources"


def test_vrcforge_2026_lists_and_gets_same_native_prompts() -> None:
    prompts = _registry()
    router = Mcp2026Router(
        lambda _params: [],
        lambda _name, _arguments: {},
        prompt_list=prompts.list,
        prompt_get=prompts.get,
        prompt_list_revision=lambda: prompts.list({"pageSize": 1})["promptGeneration"],
    )
    discover, status = router.handle(_v2_request("server/discover"))
    assert status == 200
    assert discover["result"]["capabilities"]["prompts"] == {"listChanged": True}
    listed, status = router.handle(_v2_request("prompts/list", request_id=2))
    assert status == 200
    assert len(listed["result"]["prompts"]) == 9
    prompt, status = router.handle(
        _v2_request(
            "prompts/get",
            {
                "name": "avatar-workflow-1",
                "arguments": {
                    "identityLockUri": "vrcforge://session/current/identity?revision=1",
                    "sessionContextUri": "vrcforge://operation/read/receipt?revision=1",
                },
            },
            request_id=3,
        )
    )
    assert status == 200
    assert prompt["result"]["structuredContent"]["context"]["status"] == "awaiting_resources"


def test_compact_prompt_keeps_full_messages_once_and_projects_structured_state() -> None:
    structured = {
        "schema": "vrcforge.skill_prompt.v1",
        "skill": {
            "id": "wardrobe", "title": "Wardrobe", "description": "desc",
            "instructions": "full body", "steps": ["full step"],
            "supportFiles": [{"path": "workflow.json", "content": "full support"}],
        },
        "context": {"status": "awaiting_resources", "requiredResources": ["r1"]},
        "provenance": {"skillId": "wardrobe", "contentHash": "a" * 64},
        "rules": ["rule"],
    }
    value = {"messages": [{"role": "user", "content": {"type": "text", "text": json.dumps(structured)}}], "structuredContent": structured}
    compact = project_prompt(value, mode="compact")
    assert json.loads(compact["messages"][0]["content"]["text"]) == structured
    assert compact["structuredContent"]["context"] == structured["context"]
    assert compact["structuredContent"]["provenance"] == structured["provenance"]
    assert "instructions" not in compact["structuredContent"]["skill"]
    assert "content" not in compact["structuredContent"]["skill"]["supportFiles"][0]
    assert project_prompt(value, mode="full") == value
