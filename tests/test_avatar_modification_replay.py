"""End-to-end replay of an avatar-modification turn on the real gateway.

Drives the real runtime_message funnel (planner, skill executor, completion
verifier, native settlement, final-response recording) with a scripted model
and a stub Unity write tool, then verifies the full chain the fixes targeted:
terminal turn status, model-authored reply, continuable session, crash
recovery with an honest orphan settlement, and no machine text rendered as
the assistant's answer.
"""
from __future__ import annotations

import json
from pathlib import Path

from runtime_error_schema import RUNTIME_TURN_TERMINAL

from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
from tests.test_native_runtime_gateway import call, run


def _install_write_tool(gateway: object, receipts: dict[str, dict]) -> None:
    def write_handler(arguments: dict) -> dict:
        return receipts.get(str(arguments.get("phase") or "default"), receipts["default"])

    gateway.register_tool(
        "vrcforge_swap_body_texture",
        "When to use: swap the body texture per author instructions. When NOT to use: anything else.",
        "write/material",
        write_handler,
        write=True,
    )


def test_modification_turn_completes_with_clean_reply_and_continues(tmp_path: Path) -> None:
    receipts = {
        "default": {
            "ok": True, "status": "completed", "committed": True, "commitState": "complete",
            "checkpointRecoveryRequired": False,
            "result": {"materialPath": "Assets/Milltina/Body", "applied": "layer-composite"},
        }
    }
    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [])
    _install_write_tool(gateway, receipts)
    _set_mode(gateway, "full_auto")

    model.replies = iter([
        call("enter", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write", "vrcforge_swap_body_texture", {"phase": "default", "target": "_MainTex"}),
        call("final", "vrcforge_runtime_action", {
            "action": "reply",
            "reply": "已按作者说明以图层合成方式替换身体贴图,硬边问题不会复现。",
            "summary": "layer composite applied",
            "completion_claim": {"satisfied": True},
        }),
    ])
    first = run(gateway, tmp_path, message="按 BOOTH 作者说明修正身体贴图")
    status = first.get("turnStatus") or {}
    assert status.get("status") in RUNTIME_TURN_TERMINAL
    assert status.get("status") == "completed"
    reply = str((first.get("plan") or {}).get("reply") or "")
    assert "图层合成" in reply, "the model's authored reply survives the funnel"
    assert "Traceback" not in reply and "str(" not in reply

    model.replies = iter([
        call("next", "vrcforge_runtime_action", {"action": "reply", "reply": "继续处理脸部贴图。", "summary": "face next"}),
    ])
    second = run(gateway, tmp_path, clientTurnId="native-turn-2", message="继续脸部")
    assert (second.get("turnStatus") or {}).get("status") in RUNTIME_TURN_TERMINAL
    assert "脸部" in str((second.get("plan") or {}).get("reply") or "")


def test_simple_write_does_not_fabricate_author_document_read_evidence(tmp_path: Path) -> None:
    """A successful write proves its receipt, not that author instructions were read.

    Author-document guidance belongs to the existing workflow and actual read
    observations. A path substring in a prior result cannot certify that work.
    """
    receipts = {
        "default": {
            "ok": True, "status": "completed", "committed": True, "commitState": "complete",
            "checkpointRecoveryRequired": False,
            "result": {"materialPath": "Assets/Milltina/Body"},
        }
    }
    gateway, model, _invoked = _write_gateway_for_mode(tmp_path, [])
    _install_write_tool(gateway, receipts)
    _set_mode(gateway, "full_auto")

    model.replies = iter([
        call("enter", "vrcforge_runtime_action", {"action": "enter_execution"}),
        call("write", "vrcforge_swap_body_texture",
             {"phase": "default", "target": "_MainTex", "materialPath": "Assets/Milltina/Body"}),
        call("final", "vrcforge_runtime_action", {
            "action": "reply", "reply": "替换完成。",
            "completion_claim": {"satisfied": True},
        }),
    ])
    result = run(gateway, tmp_path, message="直接替换贴图")
    write_steps = [s for s in (result.get("steps") or []) if s.get("kind") == "write"]
    assert write_steps, "the write step must be recorded"
    assert write_steps[0]["status"] == "executed"
    assert write_steps[0]["result"]["committed"] is True
    assert "targetReadBeforeWrite" not in write_steps[0]


def test_crash_mid_modification_recovers_and_next_turn_continues(tmp_path: Path) -> None:
    """A process crash after the write dispatched settles honestly and the
    next message runs a well-formed provider request."""
    from agent_runtime_session_state import (
        AgentRuntimeSessionState,
        AgentRuntimeSessionStatePorts,
    )
    import threading

    lock = threading.RLock()
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=lock))
    state.begin_native_turn("mod-session", binding="mod-binding", turn_id="t0", message="改模中")
    snapshot = state.native_conversation("mod-session", binding="mod-binding")
    snapshot["messages"].append({
        "role": "assistant", "content": "替换 _MainTex 前先检查图层",
        "tool_calls": [{"id": "swap-1", "type": "function",
                        "function": {"name": "vrcforge_swap_body_texture",
                                     "arguments": json.dumps({"phase": "default"})}}],
    })
    snapshot["activeDispatchCallId"] = "swap-1"  # crashed while the write ran
    state._save_native("mod-session", snapshot)

    fresh = state.begin_native_turn("mod-session", binding="mod-binding", turn_id="t1", message="继续完成改模")
    settled = [
        json.loads(m["content"]) for m in fresh["messages"]
        if m.get("role") == "tool" and m.get("tool_call_id") == "swap-1"
    ]
    assert settled and settled[0]["status"] == "execution_unknown"
    assert settled[0]["isError"] is True
    # The next provider request carries paired tool results and the fresh turn.
    pending = [
        m for m in fresh["messages"]
        if m.get("role") == "tool" and m.get("tool_call_id") not in {"swap-1"}
    ] + [
        call for message in fresh["messages"] if message.get("role") == "assistant"
        for call in message.get("tool_calls", [])
        if call["id"] != "swap-1"
    ]
    assert not pending, "no unpaired tool calls remain for the provider request"
