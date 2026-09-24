from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agent_gateway import AgentGateway
from agent_question_service import AgentQuestionServiceError
from runtime_planner_service import RuntimePlannerService
from tests.test_native_runtime_continuations import _run, _write_gateway
from tests.test_native_runtime_gateway import NativeModel, NativeTurn, call, finish
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop


MODES = ("approval", "auto", "roslyn_full_auto")


def _set_mode(gateway: AgentGateway, mode: str) -> None:
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
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


def _write_gateway_for_mode(tmp_path: Path, replies: list[object]) -> tuple[AgentGateway, NativeModel, list[dict]]:
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    invoked: list[dict] = []

    def handler(arguments: dict) -> dict:
        invoked.append(dict(arguments))
        return {"ok": True, "status": "written"}

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
        # Auto mode requires an explicit independent reviewer decision.  Keep
        # the fixture honest instead of relying on a missing reviewer callback.
        gateway.approval_transactions.auto_approval_reviewer = lambda _approval: "allow_auto"

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


@pytest.mark.parametrize("reviewer_setup", ["missing", "exception", "not_applicable"])
def test_native_auto_requires_a_positive_reviewer_decision(
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

    assert result["write"]["status"] == "approval_pending"
    assert invoked == []


def test_native_auto_reviewer_manual_keeps_low_risk_write_pending(tmp_path: Path) -> None:
    gateway, _model, invoked = _write_gateway_for_mode(tmp_path, [
        call("phase-1", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write-1", "fixture_write", {"projectRoot": str(tmp_path / "UnityProject"), "value": "review"}),
    ])
    _set_mode(gateway, "auto")
    gateway.approval_transactions.auto_approval_reviewer = lambda _approval: "manual"

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
