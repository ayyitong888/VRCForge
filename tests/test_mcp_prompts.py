from __future__ import annotations

import json

from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
from agent_mcp_standard import LATEST_PROTOCOL_VERSION, McpStandardRouter
from mcp_prompt_registry import McpPromptError, McpPromptRegistry, PROMPT_CONTEXT_SCHEMA


def _skills(count: int = 9) -> dict:
    return {
        "skills": [
            {
                "name": f"avatar-workflow-{index}",
                "title": f"Avatar Workflow {index}",
                "description": f"Workflow {index}",
                "source": "user",
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


def _registry() -> McpPromptRegistry:
    return McpPromptRegistry(lambda: _skills(), _tools)


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
    assert ready["status"] == "ready_for_planning"
    assert ready["scope"]["doNotTouch"] == ["avatar:2"]
    assert ready["protectedState"]["userAdjustedStates"] == ["object:1"]


def test_prompt_get_rejects_unknown_id_and_invalid_context() -> None:
    registry = _registry()
    for name, arguments in (("missing", {}), ("avatar-workflow-1", {"scope": "[]"})):
        try:
            registry.get(name, arguments)
        except McpPromptError:
            pass
        else:  # pragma: no cover - fail-closed invariant
            raise AssertionError("invalid Prompt request must fail")


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
    assert prompt["result"]["structuredContent"]["context"]["status"] == "ready_for_planning"
