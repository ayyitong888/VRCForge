"""Six fault injections: each must land a terminal turn status (never an
assistant reply carrying the error) and the conversation must continue.

Fault classes (from the audit): backend crash mid-tool, websocket drop,
provider 500, tool crash, context overflow, no-progress loop. For each:
  1. the settled turn carries a runtime_turn_status terminal state with the
     failure in its error field — never in reply (model-authored text only);
  2. sending one more message starts a fresh turn (orphaned native calls are
     normalized so the provider request stays well-formed).
"""
from __future__ import annotations

import json
from pathlib import Path

from runtime_error_schema import RUNTIME_TURN_TERMINAL


def _turn_status(payload: dict) -> dict | None:
    status = payload.get("turnStatus")
    return status if isinstance(status, dict) else None


def _provider_crash(*_args, **_kwargs):
    raise RuntimeError("provider 500: upstream exploded with secret C:/Users/x/key.txt")


def test_provider_500_lands_failed_terminal_and_conversation_continues(tmp_path: Path) -> None:
    from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
    from tests.test_native_runtime_gateway import NativeModel, call, run

    class FailingModel:
        def __init__(self) -> None:
            self.failing = True
            self.requests: list[dict] = []
            self.replies: list[dict] = []

        def plan(self, _prompt):
            raise AssertionError("native turn must not use the legacy planner")

        def plan_native(self, request):
            self.requests.append(request)
            if self.failing:
                raise _provider_crash()
            from runtime_planner_service import PlannerModelResult
            receipt = self.replies.pop(0)
            return PlannerModelResult("", assistant_message=receipt,
                                      finish_reason="tool_calls" if receipt.get("tool_calls") else "stop")

    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [])
    gateway._runtime_planner._model = FailingModel()  # noqa: SLF001
    model = gateway._runtime_planner._model  # noqa: SLF001
    _set_mode(gateway, "full_auto")

    # A repeated settle of the same provider terminal must not append a second
    # runtime notice. This deliberately replays the actual settlement call.
    from agent_runtime_native_turn import NativeRuntimeTurn
    original_settle = NativeRuntimeTurn.settle
    provider_settlements = 0

    def settle_provider_failure_twice(turn, terminal=None):
        nonlocal provider_settlements
        result = original_settle(turn, terminal)
        failure = terminal.get("plannerFailure") if isinstance(terminal, dict) else None
        if isinstance(failure, dict) and failure.get("code") == "provider_request_failed":
            provider_settlements += 1
            if provider_settlements == 1:
                original_settle(turn, terminal)
        return result

    from unittest.mock import patch
    with patch.object(NativeRuntimeTurn, "settle", settle_provider_failure_twice):
        first = run(gateway, tmp_path, message="开始改模")
    assert provider_settlements == 1, "the injected duplicate settlement must replay the same terminal once"
    status = _turn_status(first)
    assert status is not None, "provider failure must surface a turnStatus object"
    assert status["status"] in RUNTIME_TURN_TERMINAL
    assert status["status"] == "failed"
    assert status["error"]["code"] == "provider_request_failed"
    reply = str((first.get("plan") or {}).get("reply") or "")
    assert "secret C:/Users/x/key.txt" not in reply
    assert "provider 500" not in reply

    # Recreate the owning gateway so the next model request proves the notice
    # was persisted in native history, rather than retained on the old runtime.
    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [
        call("reply", "vrcforge_runtime_action", {"action": "reply", "reply": "继续完成", "summary": "继续"})
    ])
    _set_mode(gateway, "full_auto")
    second = run(gateway, tmp_path, clientTurnId="native-turn-2", message="继续")
    assert len(model.requests) >= 1, "the next message must reach the provider after runtime restart"
    notices = [
        json.loads(message["content"][len("Runtime feedback (data, not user authorization): "):])
        for message in model.requests[0]["messages"]
        if message.get("role") == "user"
        and str(message.get("content") or "").startswith(
            "Runtime feedback (data, not user authorization): "
        )
    ]
    provider_notices = [notice for notice in notices
                        if notice.get("error", {}).get("detail", {}).get("stopReason")
                        == "provider_request_failed"]
    assert len(provider_notices) == 1, "the restarted model request must carry one persisted provider-stop notice"
    assert _turn_status(second) is not None or second.get("plan", {}).get("nextStep") == "done"


def test_tool_crash_lands_terminal_with_is_error_tool_result(tmp_path: Path) -> None:
    from tests.test_native_runtime_gateway import call, run
    from tests.test_native_permission_modes import _write_gateway_for_mode

    def crashing_tool(_args):
        raise RuntimeError("tool crashed: C:/secret/path.png")

    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [
        call("phase", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("crash", "vrcforge_crashing_tool", {}),
    ])
    gateway.register_tool(
        "vrcforge_crashing_tool",
        "When to use: crash. When NOT to use: anything else.",
        "read/debug",
        crashing_tool,
    )
    result = run(gateway, tmp_path, message="跑会崩的工具", maxAgenticTurns=6)
    reply = str((result.get("plan") or {}).get("reply") or "")
    assert "C:/secret/path.png" not in reply, "tool crash text must not become the reply"
    steps = result.get("steps") or []
    assert steps, "the crashed tool must leave a step record"
    last = steps[-1]
    assert str(last.get("status") or "") != "completed", "the crashed step must not read as completed"
    assert last.get("outcome", {}).get("status") == "failed", "the registered tool handler must actually crash"

    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [
        call("reply", "vrcforge_runtime_action", {"action": "reply", "reply": "继续", "summary": "继续"})
    ])
    second = run(gateway, tmp_path, clientTurnId="native-turn-2", message="继续")
    assert model.requests, "a new production runtime turn must reach its provider adapter"
    crash_receipt = next(
        item for item in model.requests[0]["messages"]
        if item.get("role") == "tool" and item.get("tool_call_id") == "crash"
    )
    crash_observations = json.loads(crash_receipt["content"]).get("observations", [])
    assert crash_observations and crash_observations[0].get("outcome", {}).get("status") == "failed"
    assert _turn_status(second) is not None or second.get("plan", {}).get("nextStep") == "done"


def test_context_overflow_lands_terminal_and_next_message_compacts_or_continues(tmp_path: Path) -> None:
    from tests.test_native_runtime_gateway import call, run
    from tests.test_native_permission_modes import _write_gateway_for_mode

    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [])
    from unittest.mock import patch
    with patch.object(
        type(gateway._runtime_planner), "native_context_guard",  # noqa: SLF001
        return_value={"blocked": True, "schema": "vrcforge.runtime_context_compaction.v1",
                      "phase": "pre_turn", "trigger": "hard_limit", "targetAfterTokens": 250000,
                      "beforeTokens": 480000, "triggerTokens": 425000, "hardLimitTokens": 475000,
                      "requestTokens": 480000, "measurement": "native_serialized_request_estimate",
                      "usageExact": True},
    ):
        first = run(gateway, tmp_path, message="超长对话")
    status = _turn_status(first)
    assert status is not None
    assert status["status"] in RUNTIME_TURN_TERMINAL
    assert status["error"]["code"] in {"context_compaction_required", "context_window_exceeded"}
    assert (first.get("plan") or {}).get("reply", "").find("Traceback") < 0

    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [
        call("reply", "vrcforge_runtime_action", {"action": "reply", "reply": "继续", "summary": "继续"})
    ])
    second = run(gateway, tmp_path, clientTurnId="native-turn-2", message="新会话继续")
    assert model.requests, "a fresh production runtime turn must reach its provider adapter"
    context_notices = [
        json.loads(message["content"][len("Runtime feedback (data, not user authorization): "):])
        for message in model.requests[0]["messages"]
        if message.get("role") == "user"
        and str(message.get("content") or "").startswith("Runtime feedback (data, not user authorization): ")
    ]
    assert any(notice.get("error", {}).get("detail", {}).get("stopReason") == "context_window_exceeded"
               for notice in context_notices)
    assert _turn_status(second) is not None or second.get("plan", {}).get("nextStep") == "done"


def test_no_progress_loop_lands_terminal(tmp_path: Path) -> None:
    from unittest.mock import patch

    from tests.test_native_permission_modes import _write_gateway_for_mode

    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    gateway, _model, _invoked = _write_gateway_for_mode(project, [])
    planner_calls = 0

    def plan_next(*_args, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return {
            "planner": "llm", "summary": "Repeat project read.", "skillNeeded": True,
            "skillTool": "vrcforge_list_directory", "skillParams": {"path": "Assets"},
            "continueLoop": True, "nextStep": "call_skill",
        }

    def execute_skill(_owner, tool_name, _params, _agent_name=None, **_kwargs):
        return {
            "ok": True, "tool": tool_name, "status": "executed", "result": {"entries": ["Assets"]},
            "outcome": {"status": "ok", "summary": "Assets directory read.",
                        "verification": {"state": "not_required", "checks": []}},
        }

    with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=plan_next), patch.object(
        type(gateway.runtime_skills), "execute", autospec=True, side_effect=execute_skill
    ):
        result = gateway.runtime_message({
            "message": "Inspect this project repeatedly.", "projectRoot": str(project),
            "session_id": "fault-no-progress-session", "client_turn_id": "fault-no-progress-turn",
            "_projectContextActive": True,
        })

    assert planner_calls == 4
    assert result["plan"]["nextStep"] == "paused"
    assert result["plan"]["reason"] == "tool_loop_repeated"

    gateway, model, _invoked = _write_gateway_for_mode(project, [
        {
            "role": "assistant", "content": "The next turn can continue.",
        }
    ])
    continued = gateway.runtime_message({
        "message": "Continue after the stopped loop.", "projectRoot": str(project),
        "session_id": "fault-no-progress-session", "client_turn_id": "fault-no-progress-turn-2",
        "_projectContextActive": True,
    })
    assert model.requests, "the post-guard production turn must reach its provider adapter"
    assert _turn_status(continued) is not None or continued.get("plan", {}).get("nextStep") == "done"


def test_identical_successful_calls_trip_the_general_no_progress_guard(tmp_path: Path) -> None:
    """An identical successful web read also pauses after a reminder."""
    from unittest.mock import patch

    from tests.test_native_permission_modes import _write_gateway_for_mode

    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    gateway, _model, _invoked = _write_gateway_for_mode(project, [])
    planner_calls = 0

    def plan_next(*_args, **_kwargs):
        nonlocal planner_calls
        planner_calls += 1
        return {
            "planner": "llm", "summary": "Fetch the page again.", "skillNeeded": True,
            "skillTool": "vrcforge_web_fetch", "skillParams": {"url": "https://example.com/instructions"},
            "continueLoop": True, "nextStep": "call_skill",
        }

    def execute_skill(_owner, tool_name, _params, _agent_name=None, **_kwargs):
        return {
            "ok": True, "tool": tool_name, "status": "executed",
            "result": {"text": "same page body"},
            "outcome": {"status": "ok", "summary": "Fetched the page.",
                        "verification": {"state": "not_required", "checks": []}},
        }

    with patch.object(gateway.runtime_planner, "plan_agent_turn", side_effect=plan_next), patch.object(
        type(gateway.runtime_skills), "execute", autospec=True, side_effect=execute_skill
    ):
        result = gateway.runtime_message({
            "message": "Fetch repeatedly.", "projectRoot": str(project),
            "session_id": "fault-identical-call-session", "client_turn_id": "fault-identical-call-turn",
            "_projectContextActive": True,
        })

    assert result["plan"]["nextStep"] == "paused"
    assert result["plan"]["reason"] == "tool_loop_repeated"
    assert planner_calls == 4


def test_crashed_native_pending_normalizes_for_next_request(tmp_path: Path) -> None:
    """Restarted runtime normalizes an interrupted dispatch before provider use."""
    from tests.test_native_runtime_gateway import setup_gateway, run

    crashed_gateway, _crashed_model, _invoked = setup_gateway(tmp_path, [])
    session_id = "native-session"
    binding_params = {
        "sessionId": session_id, "clientTurnId": "crashed-turn", "projectRoot": str(tmp_path),
    }
    with crashed_gateway.runtime_planner.bind_turn(binding_params):
        binding = crashed_gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    state = crashed_gateway.runtime_sessions
    state.begin_native_turn(session_id, binding=binding, turn_id="t0", message="改模中")
    snapshot = state.native_conversation(session_id, binding=binding)
    snapshot["messages"].append({
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "vrcforge_apply", "arguments": "{}"}}],
    })
    snapshot["activeDispatchCallId"] = "c1"  # crashed while executing
    state._save_native(session_id, snapshot)  # noqa: SLF001

    gateway, model, _invoked = setup_gateway(tmp_path, [
        {"role": "assistant", "content": "The next turn can continue."},
    ])
    next_turn = run(gateway, tmp_path, clientTurnId="after-crash", message="继续")
    assert model.requests, "the restarted production runtime must issue a provider request"
    normalized = gateway.runtime_sessions.native_conversation(session_id, binding=binding)
    assert normalized is not None
    pending_after = [m for m in normalized["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == "c1"]
    assert pending_after, "the orphan call must be settled"
    settled = json.loads(pending_after[0]["content"])
    assert settled["status"] == "execution_unknown"
    assert settled["isError"] is True
    sent = next(message for message in model.requests[0]["messages"]
                if message.get("role") == "tool" and message.get("tool_call_id") == "c1")
    assert json.loads(sent["content"])["status"] == "execution_unknown"
    assert _turn_status(next_turn) is not None or next_turn.get("plan", {}).get("nextStep") == "done"


def test_websocket_drop_does_not_block_turn_settlement(tmp_path: Path) -> None:
    """A dead dashboard client cannot hold settlement or the following turn."""
    import dashboard_server
    import asyncio
    from tests.test_native_runtime_gateway import call, run
    from tests.test_native_permission_modes import _write_gateway_for_mode

    bus = dashboard_server.DashboardEventBus()

    class DeadClient:
        async def send_json(self, _message):
            raise RuntimeError("websocket is closed")

    dead = DeadClient()
    bus._clients.add(dead)  # noqa: SLF001
    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [
        call("reply-1", "vrcforge_runtime_action", {"action": "reply", "reply": "已完成", "summary": "完成"}),
        call("reply-2", "vrcforge_runtime_action", {"action": "reply", "reply": "继续", "summary": "继续"}),
    ])
    first = run(gateway, tmp_path, message="触发断开的通知客户端")
    async def scenario():
        await bus.broadcast("agentRuntimeTurn", {"plan": first.get("plan", {})})

    asyncio.run(scenario())
    assert dead not in bus._clients, "the runtime completion must evict the dead client"
    assert _turn_status(first) is not None or first.get("plan", {}).get("nextStep") == "done"

    second = run(gateway, tmp_path, clientTurnId="native-turn-2", message="继续")
    assert len(model.requests) >= 2, "the production runtime must accept a follow-up turn after the socket drop"
    assert _turn_status(second) is not None or second.get("plan", {}).get("nextStep") == "done"
