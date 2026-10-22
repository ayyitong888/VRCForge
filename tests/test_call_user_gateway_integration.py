from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
from execution_target import EXECUTION_TARGET_SCHEMA, canonical_namespace, project_identity
from execution_target import process_start_time


def _target(root: Path) -> dict[str, object]:
    project_root = str(root.resolve())
    target: dict[str, object] = {
        "schema": EXECUTION_TARGET_SCHEMA,
        "scope": "project",
        "project": {"root": project_root, "projectId": project_identity(project_root)},
        "editor": {
            "unityPid": 1234,
            "processStartTime": "2026-09-02T00:00:00Z",
            "coreInstanceId": "core-1",
        },
    }
    target["namespace"] = canonical_namespace(target)
    return target


def _gateway(tmp_path: Path) -> AgentGateway:
    gateway = AgentGateway(tmp_path / "config" / "agent_gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    gateway.register_tool(
        "vrcforge_call_user",
        "when-to-use: request human visual acceptance. when-NOT-to-use: routine checks.",
        "plan/preview",
        gateway.call_user,
    )
    return gateway


def test_external_call_user_is_visible_and_uses_project_root_from_execution_target(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    listed = gateway.build_external_mcp_tools("execution", ["*"])
    assert "vrcforge_call_user" in {item["name"] for item in listed}

    project = tmp_path / "UnityProject"
    project.mkdir()
    target = _target(project)
    start = process_start_time(os.getpid())
    (project / "Library" / "VRCForge").mkdir(parents=True)
    (project / "Library" / "VRCForge" / "mcp-core.json").write_text(json.dumps({
        "projectPath": str(project.resolve()), "projectId": project_identity(str(project.resolve())),
        "processId": os.getpid(), "processStartTime": start, "instanceId": "core-1",
    }), encoding="utf-8")
    target["editor"] = {"unityPid": os.getpid(), "processStartTime": start, "coreInstanceId": "core-1"}
    target["namespace"] = canonical_namespace(target)
    with gateway.resource_context("external:trusted-chat"):
        reference = gateway.bind_execution_target(target, project_root=str(project))
        result = gateway.call_external_mcp_tool(
            "vrcforge_call_user",
            {
                "kind": "visual_checkpoint",
                "question": "Confirm the current visual checkpoint",
                "projectRoot": str(project),
                "executionTarget": reference["executionTarget"],
            },
        )
    assert result["ok"] is True
    question = result["result"]["question"]
    assert question["projectRoot"] == str(project.resolve())
    assert question["sessionId"] == "external:trusted-chat"
    assert question["sourceTool"] == "call_user"
    assert [option["id"] for option in question["options"]] == ["pass", "fail", "uncertain", "later"]


def test_external_call_user_rejects_unbound_or_forged_scope(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = tmp_path / "UnboundProject"
    project.mkdir()
    with gateway.resource_context("external:unbound-chat"):
        with pytest.raises(AgentGatewayError, match="authenticated project binding"):
            gateway.call_external_mcp_tool(
                "vrcforge_call_user",
                {"question": "Cannot claim this project", "projectRoot": str(project)},
            )


def test_external_call_user_uses_existing_non_unity_project_binding(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = tmp_path / "ChatProject"
    project.mkdir()
    gateway._register_runtime_project_scope(str(project))
    with gateway.resource_context("external:chat-only"):
        result = gateway.call_external_mcp_tool(
            "vrcforge_call_user",
            {"question": "Need a human visual check", "projectRoot": str(project)},
        )
    question = result["result"]["question"]
    assert question["sessionId"] == "external:chat-only"
    assert question["projectRoot"] == str(project.resolve())

