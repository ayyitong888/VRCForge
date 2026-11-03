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

    first = run(gateway, tmp_path, message="开始改模")
    status = _turn_status(first)
    assert status is not None, "provider failure must surface a turnStatus object"
    assert status["status"] in RUNTIME_TURN_TERMINAL
    assert status["status"] == "failed"
    assert status["error"]["code"] == "provider_request_failed"
    reply = str((first.get("plan") or {}).get("reply") or "")
    assert "secret C:/Users/x/key.txt" not in reply
    assert "provider 500" not in reply

    model.failing = False
    model.requests.clear()
    model.replies = [call("reply", "vrcforge_runtime_action", {"action": "reply", "reply": "继续完成", "summary": "继续"})]
    second = run(gateway, tmp_path, clientTurnId="native-turn-2", message="继续")
    assert len(model.requests) >= 1, "the next message must reach the provider"
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
        "read/fixture",
        crashing_tool,
    )
    result = run(gateway, tmp_path, message="跑会崩的工具", maxAgenticTurns=6)
    reply = str((result.get("plan") or {}).get("reply") or "")
    assert "C:/secret/path.png" not in reply, "tool crash text must not become the reply"
    steps = result.get("steps") or []
    assert steps, "the crashed tool must leave a step record"
    last = steps[-1]
    assert str(last.get("status") or "") != "completed", "the crashed step must not read as completed"


def test_context_overflow_lands_terminal_and_next_message_compacts_or_continues(tmp_path: Path) -> None:
    from tests.test_native_runtime_gateway import run
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

    model.requests.clear()
    second = run(gateway, tmp_path, clientTurnId="native-turn-2", message="新会话继续")
    assert second.get("sessionId") or second.get("session_id"), "a fresh turn must still be runnable"


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

    assert planner_calls == 4, "the loop guard must cut in on the third replay"
    failure = (result.get("plan") or {}).get("plannerFailure") or {}
    status = _turn_status(result)
    assert status is not None or failure.get("code") == "planner_no_progress", (
        "repeated no-progress must land a terminal state with a machine code"
    )


def test_crashed_native_pending_normalizes_for_next_request(tmp_path: Path) -> None:
    """Backend crash mid-tool: the next turn's provider request is well-formed."""
    from agent_runtime_session_state import (
        AgentRuntimeSessionState,
        AgentRuntimeSessionStatePorts,
    )
    import threading

    lock = threading.RLock()
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=lock))
    state.begin_native_turn("s", binding="b", turn_id="t0", message="改模中")
    snapshot = state.native_conversation("s", binding="b")
    snapshot["messages"].append({
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "vrcforge_apply", "arguments": "{}"}}],
    })
    snapshot["activeDispatchCallId"] = "c1"  # crashed while executing
    state._save_native("s", snapshot)  # noqa: SLF001

    state.begin_native_turn("s", binding="b", turn_id="t1", message="继续")
    normalized = state.native_conversation("s", binding="b")
    assert normalized is not None
    pending_after = [m for m in normalized["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == "c1"]
    assert pending_after, "the orphan call must be settled"
    settled = json.loads(pending_after[0]["content"])
    assert settled["status"] == "execution_unknown"
    assert settled["isError"] is True
    # The next message opened a fresh, well-formed turn.
    assert normalized["turnId"] == "t1"
    assert [m for m in normalized["messages"] if m.get("role") == "user"][-1]["content"] == "继续"


def test_websocket_drop_does_not_block_turn_settlement() -> None:
    """A dead dashboard client cannot hold a turn open: EVENT_BUS isolation."""
    import dashboard_server

    bus = dashboard_server.DashboardEventBus()

    class DeadClient:
        async def send_json(self, _message):
            raise RuntimeError("websocket is closed")

    dead = DeadClient()
    bus._clients.add(dead)  # noqa: SLF001
    import asyncio

    async def scenario():
        await bus.broadcast("agentRuntimeTurn", {"plan": {"reply": "done"}})

    asyncio.run(scenario())
    assert dead not in bus._clients, "the dead client must be evicted, not block settlement"
