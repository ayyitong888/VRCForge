from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from agent_gateway import AgentGateway, create_agent_mcp_app
from agent_mcp_2026 import PROTOCOL_VERSION


def _gateway(tmp_path: Path) -> AgentGateway:
    return AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")


def test_project_category_rule_is_exact_and_reviewer_gated(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    calls: list[str] = []
    gateway.approval_transactions.register_write_handler(
        "vrcforge_create_gameobject",
        "Create a scene object.",
        "medium",
        lambda arguments: calls.append(str(arguments["name"])) or {"ok": True},
        approval_category="scene-object-create",
        allow_future_category=True,
    )
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 2022.3", encoding="utf-8")
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _project: {"ok": True}
    first = gateway.approval_transactions.create_apply_request(
        {"target_tool": "vrcforge_create_gameobject", "arguments": {"projectRoot": str(project), "name": "One"}}
    )
    assert first["approval"]["projectRoot"] == str(project)
    assert first["approval"]["allowFutureEligible"] is True
    approval_id = first["approval"]["id"]
    approved = gateway.approval_transactions.approve_with_project_category_rule(
        approval_id, expected_project_root=str(project)
    )
    assert approved["ok"] is True
    saved = gateway.ensure_config().project_category_allow_rules
    assert saved == [{"projectRoot": project.resolve().as_posix().lower(), "category": "scene-object-create"}]

    gateway.approval_transactions.scoped_approval_reviewer = lambda _approval: "manual"
    pending = gateway.approval_transactions.create_apply_request(
        {"target_tool": "vrcforge_create_gameobject", "arguments": {"projectRoot": str(project), "name": "Two"}}
    )
    assert pending["status"] == "pending"
    assert calls == []

    gateway.approval_transactions.scoped_approval_reviewer = lambda _approval: "allow_auto"
    executed = gateway.approval_transactions.create_apply_request(
        {"target_tool": "vrcforge_create_gameobject", "arguments": {"projectRoot": str(project), "name": "Three"}}
    )
    assert executed["status"] == "executed"
    assert executed["scopedRuleAutoApproved"] is True
    assert calls == ["Three"]


def test_rule_never_calls_reviewer_for_unapproved_handler(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    reviewer_calls: list[dict] = []
    gateway.approval_transactions.scoped_approval_reviewer = lambda approval: reviewer_calls.append(approval) or "allow_auto"
    gateway.approval_transactions.register_write_handler("vrcforge_delete_gameobject", "Delete.", "high", lambda _arguments: {"ok": True})
    config = gateway.ensure_config()
    config.project_category_allow_rules = [
        {"projectRoot": (tmp_path / "Project").resolve().as_posix().lower(), "category": "scene-object-create"}
    ]
    gateway.save_config(config)

    request = gateway.approval_transactions.create_apply_request(
        {"target_tool": "vrcforge_delete_gameobject", "arguments": {"projectRoot": str(tmp_path / "Project")}}
    )
    assert request["status"] == "pending"
    assert reviewer_calls == []


def test_manual_allow_once_never_calls_reviewer(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    reviewer_calls: list[dict] = []
    gateway.approval_transactions.scoped_approval_reviewer = lambda approval: reviewer_calls.append(approval) or "allow_auto"
    gateway.approval_transactions.register_write_handler(
        "vrcforge_create_gameobject",
        "Create a scene object.",
        "medium",
        lambda _arguments: {"ok": True},
        approval_category="scene-object-create",
        allow_future_category=True,
    )
    project = tmp_path / "ManualProject"

    pending = gateway.approval_transactions.create_apply_request(
        {"target_tool": "vrcforge_create_gameobject", "arguments": {"projectRoot": str(project), "name": "One"}}
    )
    approved = gateway.approval_transactions.approve(pending["approval"]["id"], expected_project_root=str(project))

    assert pending["status"] == "pending"
    assert approved["ok"] is True
    assert reviewer_calls == []
    assert gateway.ensure_config().project_category_allow_rules == []


def test_agent_proposed_visual_capture_can_remember_exact_project_category(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    calls: list[list[str]] = []
    gateway.approval_transactions.register_write_handler(
        "vrcforge_capture_multi_screenshot",
        "Capture fixed-angle views.",
        "medium",
        lambda arguments: calls.append(list(arguments["angles"])) or {"ok": True},
        approval_category="visual-capture",
        allow_future_category=True,
    )
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _project: {"ok": True}
    project = tmp_path / "Project"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3",
        encoding="utf-8",
    )
    task_context = {
        "schema": "vrcforge.agent_task_loop.v2",
        "objective": "Capture front and back views.",
        "projectRoot": str(project),
        "taskId": "task-capture",
    }
    request = {
        "target_tool": "vrcforge_capture_multi_screenshot",
        "arguments": {"angles": ["front", "back"]},
        "requires_explicit_approval": True,
        "disable_auto_approval": True,
    }

    first = gateway.approval_transactions.create_apply_request(
        request,
        task_context=task_context,
    )

    assert first["approval"]["requiresExplicitApproval"] is True
    assert first["approval"]["allowFutureEligible"] is True
    assert first["approval"]["projectRoot"] == str(project)
    remembered = gateway.approval_transactions.approve_with_project_category_rule(
        first["approval"]["id"],
        expected_project_root=str(project),
    )
    assert remembered["ok"] is True

    gateway.approval_transactions.scoped_approval_reviewer = lambda _approval: "allow_auto"
    second = gateway.approval_transactions.create_apply_request(
        {
            **request,
            "arguments": {
                "angles": ["front", "back"],
                "projectRoot": str(project),
            },
        },
        task_context=task_context,
    )

    assert second["status"] == "executed", second
    assert second["scopedRuleAutoApproved"] is True
    assert calls == [["front", "back"]]


def test_future_category_option_requires_an_exact_project_root(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    gateway.approval_transactions.register_write_handler(
        "vrcforge_capture_screenshot",
        "Capture one view.",
        "medium",
        lambda _arguments: {"ok": True},
        approval_category="visual-capture",
        allow_future_category=True,
    )

    pending = gateway.approval_transactions.create_apply_request(
        {
            "target_tool": "vrcforge_capture_screenshot",
            "arguments": {"width": 960, "height": 960},
            "requires_explicit_approval": True,
        }
    )

    assert pending["status"] == "pending"
    assert "allowFutureEligible" not in pending["approval"]


def test_external_mcp_direct_write_never_uses_internal_pending_refresh_callback(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path)
    project = tmp_path / "Project"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    gateway.approval_transactions.register_write_handler(
        "vrcforge_create_gameobject",
        "Create a scene object.",
        "medium",
        lambda _arguments: {"ok": True},
        approval_category="scene-object-create",
        allow_future_category=True,
    )
    config = gateway.ensure_config()
    config.enabled = True
    config.token = "gateway-token"
    config.allow_write_requests = True
    gateway.save_config(config)
    observed: list[dict] = []
    app = create_agent_mcp_app(gateway, on_pending_approval=lambda approval: observed.append(approval))
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
            "name": "vrcforge_create_gameobject",
            "arguments": {
                "projectRoot": str(project),
                "name": "One",
            },
        },
    }
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "authorization": "Bearer gateway-token",
        "origin": "http://127.0.0.1:8757",
        "mcp-protocol-version": PROTOCOL_VERSION,
        "mcp-method": "tools/call",
        "mcp-name": "vrcforge_create_gameobject",
    }

    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/mcp", json=message, headers=headers)

    response = asyncio.run(call())

    assert response.status_code == 200
    response_text = response.text
    assert "taskContinuation" not in response_text
    assert "plannerObservation" not in response_text
    assert "terminalPlan" not in response_text
    assert response.json()["result"]["structuredContent"]["status"] == "executed"
    assert observed == []
