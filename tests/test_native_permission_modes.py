from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import AgentGateway, AgentGatewayError
from agent_question_service import AgentQuestionServiceError
from package_install_workflow_service import PackageInstallWorkflowService
from runtime_planner_service import RuntimePlannerService
from tests.test_native_runtime_continuations import _run, _write_gateway
from tests.test_native_runtime_gateway import NativeModel, NativeTurn, call, finish, run
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop


MODES = ("approval", "auto", "roslyn_full_auto")


@pytest.mark.parametrize("mode", MODES)
def test_native_vpm_install_uses_local_permission_with_external_gateway_disabled(
    tmp_path: Path, monkeypatch, mode: str,
) -> None:
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True)
    tool_name = "vrcforge_package_install_request"
    model_tool_name = "unity_package_install_request"
    package_id = "com.vrcfury.vrcfury"
    arguments = {"projectPath": str(project), "packageId": package_id, "preferredManager": "vrc-get"}
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    model = NativeModel([
        call("load-package-tool", "load_internal_tool_block", {
            "block": "project_environment/assets_packages", "tools": [model_tool_name],
        }),
        call("request-install", model_tool_name, arguments), finish,
    ])
    original = dashboard_server.AGENT_GATEWAY._tools[tool_name]
    loader = dashboard_server.AGENT_GATEWAY._tools["vrcforge_load_internal_tool_block"]
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    gateway.bind_runtime_planner(RuntimePlannerService(
        catalog=dashboard_server._RuntimePlannerCatalog(),
        desktop=_TestRuntimePlannerDesktop(gateway),
        model=model,
        turn=NativeTurn(),
    ))
    config = gateway.ensure_config()
    config.enabled = False
    config.execution_mode = mode
    config.roslyn_risk_acknowledged = mode == "roslyn_full_auto"
    gateway.save_config(config)
    legacy_config = json.loads(gateway.config_path.read_text(encoding="utf-8"))
    legacy_config["allow_write_requests"] = False
    gateway.config_path.write_text(json.dumps(legacy_config), encoding="utf-8")
    workflow = PackageInstallWorkflowService(replace(
        dashboard_server.PACKAGE_INSTALL_WORKFLOWS._ports,
        create_apply_request=gateway.approval_transactions.create_apply_request,
    ))
    monkeypatch.setattr(workflow, "plan_install", lambda _params: {
        "ok": True, "projectPath": str(project), "packageId": package_id,
        "packageState": {"installed": False}, "canExecuteCommandInstall": True,
    })
    installed = []
    gateway.register_tool(
        tool_name, original.description, original.category,
        workflow.request_install, write=True,
    )
    gateway.register_tool(loader.name, loader.description, loader.category, loader.handler)
    gateway.approval_transactions.register_write_handler(
        "vrcforge_install_vpm_package", "Install the fixture package.", "medium",
        lambda args: installed.append((args["packageId"], args["preferredManager"])) or {"ok": True},
        pre_write_checkpoint_required=False,
    )
    reviewed = []
    if mode == "auto":
        def allow_review(approval):
            assert not installed
            reviewed.append(approval["targetTool"])
            return "allow_auto"
        gateway.approval_transactions.auto_approval_reviewer = allow_review

    with pytest.raises(AgentGatewayError, match="Agent Gateway is disabled"):
        gateway.call_external_mcp_tool(tool_name, arguments, agent_name="internal-runtime")
    assert not installed
    response = run(gateway, tmp_path, projectRoot=str(project), message=f"Install {package_id}.")
    attempts = [step for step in response["steps"] if step["tool"] == tool_name]
    assert len(attempts) == 1, (response["plan"], response["steps"])
    step, = attempts
    if mode == "approval":
        assert step["status"] == "approval_pending", step
        assert not installed
        assert gateway.approval_transactions.list_approvals()
    else:
        assert installed == [(package_id, "vrc-get")], step
    if mode == "auto":
        assert reviewed == ["vrcforge_install_vpm_package"]


def _set_mode(gateway: AgentGateway, mode: str) -> None:
    config = gateway.ensure_config()
    config.enabled = True
    config.execution_mode = mode
    config.roslyn_risk_acknowledged = mode == "roslyn_full_auto"
    gateway.save_config(config)


def _question_gateway(tmp_path: Path, replies: list[object]) -> tuple[AgentGateway, NativeModel]:
    gateway, model = _write_gateway(tmp_path, replies, [])
    gateway.register_tool(
        "vrcforge_ask_user",
        "When to use: ask one bounded user question. When NOT to use: otherwise.",
        "plan/preview",
        gateway.create_runtime_question,
    )
    return gateway, model


def _write_gateway_for_mode(
    tmp_path: Path, replies: list[object], *, handler_result: dict | None = None,
) -> tuple[AgentGateway, NativeModel, list[dict]]:
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    invoked: list[dict] = []

    def handler(arguments: dict) -> dict:
        invoked.append(dict(arguments))
        if handler_result is not None:
            return dict(handler_result)
        target = project / "after.txt"
        value = str(arguments.get("value", ""))
        target.write_text(value, encoding="utf-8")
        verified = target.read_text(encoding="utf-8") == value
        return {"ok": verified, "status": "written", "commitState": "complete", "committed": True, "persistedReadback": verified}

    gateway.register_tool(
        "fixture_write",
        "When to use: write the fixture. When NOT to use: when no write is requested.",
        "write/fixture",
        handler,
        write=True,
    )
    gateway.approval_transactions.register_write_handler(
        "fixture_write", "Write the fixture.", "medium", handler,
    )
    gateway.approval_transactions.checkpoint_prepare_handler = lambda _root: {"ok": True}
    model = NativeModel(replies)
    gateway.bind_runtime_planner(RuntimePlannerService(
        catalog=_TestRuntimePlannerCatalog(gateway),
        desktop=_TestRuntimePlannerDesktop(gateway),
        model=model,
        turn=NativeTurn(),
    ))
    return gateway, model, invoked


def _question_run(gateway: AgentGateway, tmp_path: Path, *, client_turn_id: str) -> dict:
    return gateway.runtime_message({
        "message": "ask before continuing",
        "sessionId": "permission-mode-question",
        "clientTurnId": client_turn_id,
        "projectRoot": str(tmp_path),
        "maxAgenticTurns": 5,
    })


@pytest.mark.parametrize("mode", MODES)
def test_native_question_answer_continuation_uses_same_owner_in_all_permission_modes(
    tmp_path: Path, mode: str,
) -> None:
    completed: list[dict] = []
    completed_event = threading.Event()
    gateway, model = _question_gateway(tmp_path, [
        call("question-1", "vrcforge_ask_user", {"question": "Continue?"}),
        {"role": "assistant", "content": "continued"},
    ])
    _set_mode(gateway, mode)
    gateway._runtime_turn_completed = lambda payload: (completed.append(payload), completed_event.set())

    first = _question_run(gateway, tmp_path, client_turn_id=f"question-{mode}")
    assert first["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(
        session_id="permission-mode-question", project_root=str(tmp_path), include_answered=True,
    )["questions"][0]
    answered = gateway.questions.answer(question["questionId"], {
        "sessionId": "permission-mode-question",
        "projectRoot": str(tmp_path),
        "answer": "yes",
    })

    assert answered["question"]["runtimeContinuationStatus"] in {"queued", "claimed", "delivered"}
    assert completed_event.wait(5)
    assert gateway.shutdown_runtime_continuations(5)["ok"]
    assert completed and completed[0]["continuationSource"] == "question_answered"
    assert len(model.requests) >= 2


@pytest.mark.parametrize("mode", MODES)
def test_native_runtime_write_uses_real_approval_owner_for_each_permission_mode(
    tmp_path: Path, mode: str,
) -> None:
    gateway, _model, invoked = _write_gateway_for_mode(tmp_path, [
        call("phase-1", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-1", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": mode}),
        finish,
    ])
    _set_mode(gateway, mode)
    if mode == "auto":
        def allow_review(_approval):
            assert gateway.approval_transactions.list_approvals() == []
            assert invoked == []
            return "allow_auto"
        gateway.approval_transactions.auto_approval_reviewer = allow_review

    initial = _run(gateway, tmp_path, clientTurnId=f"write-{mode}")
    if mode == "approval":
        assert initial["write"]["status"] == "approval_pending"
        approval_id = initial["approval_id"]
        approval = gateway._approvals[approval_id]
        assert approval["permissionMode"] == "approval"
        gateway.approval_transactions.approve(approval_id)
        applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
        assert applied["status"] == "applied"
        resumed = gateway.resume_runtime_task_after_approval(approval, applied)
        assert resumed["plan"]["nextStep"] == "done"
    else:
        assert initial["write"]["status"] in {"executed", "completed"}
        assert initial["plan"]["nextStep"] == "done"

    assert invoked == [{"projectRoot": str(tmp_path / "UnityProject"), "value": mode}]


def test_native_approved_prewrite_failure_resumes_planner_without_replaying_write(tmp_path: Path) -> None:
    failure = {
        "ok": False, "status": "failed", "error": "fixture preflight failure",
        "mutationStarted": False, "saved": False, "safeToRetry": True,
        "committed": False, "commitState": "not_started",
    }
    decision = "The write was rejected before mutation; I can explain the next step."
    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [
        call("phase-1", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-1", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "retry"}),
        {"role": "assistant", "content": decision},
    ], handler_result=failure)
    _set_mode(gateway, "approval")

    initial = _run(gateway, tmp_path, clientTurnId="write-approval-recoverable")
    assert initial["write"]["status"] == "approval_pending"
    approval_id = initial["approval_id"]
    approval = gateway._approvals[approval_id]
    gateway.approval_transactions.approve(approval_id)
    applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
    assert applied["status"] == "failed"
    assert applied["error"] == failure["error"]
    assert applied["writeFailure"]["safeToRetry"] is True
    assert applied["writeFailure"]["checkpointRecoveryRequired"] is False
    resumed = gateway.resume_runtime_task_after_approval(approval, applied)

    assert len(model.requests) == 3
    assert invoked == [{"projectRoot": str(tmp_path / "UnityProject"), "value": "retry"}]
    write_messages = [
        row for row in model.requests[-1]["messages"]
        if row["role"] == "tool" and row["tool_call_id"] == "write-1"
    ]
    assert len(write_messages) == 1
    write_observation = json.loads(write_messages[0]["content"])
    assert write_observation["status"] == "failed"
    assert any(failure["error"] in item["observation"] for item in write_observation["observations"])
    assert resumed["plan"]["reply"] == decision
    assert resumed["plan"].get("completionClaim", {}).get("satisfied") is not True


@pytest.mark.parametrize("mode", ["approval", "roslyn_full_auto"])
def test_native_texture_prewrite_rejection_reaches_model_and_app_without_retry(tmp_path, monkeypatch, mode):
    message = "该文件在预览后被修改"
    core_reply = {"isError": True, "structuredContent": {"success": False,
        "code": "texture_import_settings_rejected", "error": message, "data": {
            "failureLayer": "unity_core_tool", "failurePhase": "pre_mutation_validation",
            "toolRoutingStarted": True, "mutationStarted": False, "committed": False,
            "commitState": "not_started", "commitStateKnown": True, "retryable": False,
            "checkpointRecoveryRequired": False, "temporaryCleanupRequired": False,
        }}}
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _: None)
    core_calls = []

    def invoke(_settings, name, arguments, *, preserve_tool_error):
        assert preserve_tool_error
        core_calls.append((name, arguments))
        return dashboard_server.McpResult(exit_code=1, stdout="", stderr="", payload=core_reply)

    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    decision = "文件在预览后有变更，我先停在这里。"

    def decide(request):
        tool_messages = [row for row in request["messages"] if row["role"] == "tool"]
        assert any(message in json.dumps(row, ensure_ascii=False) for row in tool_messages)
        return {"role": "assistant", "content": decision}

    project = str(tmp_path / "UnityProject")
    gateway, model, _ = _write_gateway_for_mode(tmp_path, [
        call("enter", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write", "fixture_write", {"projectRoot": project, "value": "texture"}),
        decide,
    ])
    _set_mode(gateway, mode)
    gateway.approval_transactions._ports.state.write_handlers["fixture_write"].handler = (
        lambda _: dashboard_server.unity_mcp_write_sync({"toolName": "vrc_set_texture_import_settings",
            "projectPath": project, "arguments": {"textureAssetPath": "Assets/Cloth.png"}})
    )
    first = _run(gateway, tmp_path, clientTurnId="texture-file-modified")
    if mode == "approval":
        approval_id = first["approval_id"]
        gateway.approval_transactions.approve(approval_id)
        applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
        resumed = gateway.resume_runtime_task_after_approval(applied["approval"], applied)
    else:
        resumed = first
        applied = first["write"]
    assert applied["error"] == message
    failure = applied.get("writeFailure") or applied.get("result") or {}
    assert failure.get("safeToRetry") is not True
    assert failure.get("checkpointRecoveryRequired") is False, applied
    assert gateway.approval_transactions._ports.checkpoint.active_apply_recoveries() == []

    assert len(model.requests) == 3
    assert len(core_calls) == 1
    assert resumed["plan"]["reply"] == decision
    assert gateway.questions.list(session_id=first["sessionId"])["questions"] == []
    projected = dashboard_server.public_approval_http_payload({"ok": True, "execution": applied, "continuation": resumed})
    assert projected["execution"]["error"] == message
    assert projected["continuation"]["plan"]["reply"] == decision


@pytest.mark.parametrize("reviewer_setup", ["missing", "exception", "not_applicable"])
def test_native_auto_unavailable_reviewer_cannot_publish_or_execute(
    tmp_path: Path, reviewer_setup: str,
) -> None:
    gateway, _model, invoked = _write_gateway_for_mode(tmp_path, [
        call("phase-1", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-1", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": reviewer_setup}),
    ])
    _set_mode(gateway, "auto")
    if reviewer_setup == "exception":
        def fail_review(_approval):
            raise RuntimeError("reviewer unavailable")
        gateway.approval_transactions.auto_approval_reviewer = fail_review
    elif reviewer_setup == "not_applicable":
        gateway.approval_transactions.auto_approval_reviewer = lambda _approval: "not_applicable"

    result = _run(gateway, tmp_path, clientTurnId=f"write-auto-{reviewer_setup}")

    assert result["write"]["status"] == "failed"
    assert "reviewer did not return a decision" in result["write"]["outcome"]["summary"]
    assert gateway.approval_transactions.list_approvals() == []
    assert invoked == []


def test_native_auto_reviewer_manual_keeps_low_risk_write_pending(tmp_path: Path) -> None:
    gateway, _model, invoked = _write_gateway_for_mode(tmp_path, [
        call("phase-1", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-1", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "review"}),
    ])
    _set_mode(gateway, "auto")
    def deny_review(_approval):
        assert gateway.approval_transactions.list_approvals() == []
        return "manual"

    gateway.approval_transactions.auto_approval_reviewer = deny_review

    initial = _run(gateway, tmp_path, clientTurnId="write-auto-review")

    assert initial["write"]["status"] == "approval_pending"
    assert gateway._approvals[next(iter(gateway._approvals))]["permissionMode"] == "auto"
    assert invoked == []


def test_native_auto_still_honors_handler_manual_policy(tmp_path: Path) -> None:
    gateway, _model, invoked = _write_gateway_for_mode(tmp_path, [
        call("phase-1", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-1", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "manual"}),
    ])
    gateway.approval_transactions._ports.state.write_handlers["fixture_write"].manual_approval_resolver = (
        lambda _arguments, _preview: "This fixture always requires manual approval."
    )
    _set_mode(gateway, "auto")

    initial = _run(gateway, tmp_path, clientTurnId="write-full-manual")

    assert initial["write"]["status"] == "approval_pending"
    approval = gateway._approvals[next(iter(gateway._approvals))]
    assert approval["explicitApprovalReason"] == "This fixture always requires manual approval."
    assert invoked == []


@pytest.mark.parametrize("mode", ("auto", "roslyn_full_auto"))
def test_native_question_stop_is_exact_and_blocks_only_that_continuation(
    tmp_path: Path, mode: str,
) -> None:
    gateway, _model = _question_gateway(tmp_path, [
        call("question-stop", "vrcforge_ask_user", {"question": "Stop me?"}),
    ])
    _set_mode(gateway, mode)
    client_turn_id = f"stop-{mode}"
    result = _question_run(gateway, tmp_path, client_turn_id=client_turn_id)
    assert result["plan"]["nextStep"] == "needs_user_action"
    question = gateway.questions.list(
        session_id="permission-mode-question", include_answered=True,
    )["questions"][0]

    cancelled = gateway.request_runtime_cancel({
        "sessionId": "permission-mode-question",
        "clientTurnId": client_turn_id,
    })
    assert cancelled["status"] == "cancel_requested"
    with pytest.raises(AgentQuestionServiceError, match="stopped"):
        gateway.questions.answer(question["questionId"], {
            "sessionId": "permission-mode-question", "answer": "yes",
        })
    state = gateway.questions.list(include_answered=True)["questions"][0]
    assert state["status"] == "cancelled"
