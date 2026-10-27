import json
import threading

from agent_runtime_session_state import AgentRuntimeSessionState, AgentRuntimeSessionStatePorts


def snapshot(success="ok", corrected_project="D:/Project", correction_id="failed-action"):
    messages = [{"role": "user", "content": "Inspect this avatar"}]
    for call_id, action_id, paths, status, correction in (
        ("failed-call", "failed-action", ["Avatar/Outfit/Guess", "Avatar/Outfit/Outer"], "failed", ""),
        ("corrected-call", "corrected-action", ["Avatar/Outfit/Outer"], success, correction_id),
    ):
        args = {"tool": "unity_scan_blendshapes", "arguments": {
            "projectPath": "D:/Project" if not correction else corrected_project,
            "avatarPath": "Avatar", "rendererPaths": paths}}
        if correction:
            args.update(action="correct", correction_for_action_id=correction)
        messages.append({"role": "assistant", "content": None, "tool_calls": [{
            "id": call_id, "type": "function", "function": {
                "name": "vrcforge_runtime_action" if correction else "call_tool",
                "arguments": json.dumps(args)}}]})
        messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps({
            "observations": [{"actionId": action_id, "runtimeTool": "vrcforge_scan_blendshapes",
                "outcome": {"status": status}, "observation": (
                    "Unknown rendererPaths in selected avatar: [\"Avatar/Outfit/Guess\"]; "
                    if status == "failed" else "") + "large scan details " * 1000}]})})
    messages.append({"role": "user", "content": "Continue the same target"})
    return {"binding": "binding", "turnId": "second", "messages": messages,
            "activeTurnStart": 5, "humanUserMessageIndices": [0, 5]}


def test_successful_renderer_selection_correction_survives_compaction_and_next_turn():
    state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock()))
    original = snapshot()
    state._save_native("session", original)
    compacted = state.replace_native_compacted_history("session", binding="binding",
        expected_snapshot=original, summary="Short summary omitted renderer detail")
    receipts = [m for m in compacted["messages"] if m["role"] == "tool"]
    assert len(receipts) == 2
    assert sum(len(m["content"]) for m in receipts) < 1000
    calls = [json.loads(m["tool_calls"][0]["function"]["arguments"])
             for m in compacted["messages"] if m.get("tool_calls")]
    assert calls[0]["arguments"]["rendererPaths"] == ["Avatar/Outfit/Guess", "Avatar/Outfit/Outer"]
    assert calls[1]["arguments"]["rendererPaths"] == ["Avatar/Outfit/Outer"]
    assert calls[1]["correction_for_action_id"] == "failed-action"
    again = state.replace_native_compacted_history("session", binding="binding",
        expected_snapshot=compacted, summary="Another summary")
    continued = state.begin_native_turn("session", binding="binding", turn_id="third", message="Continue")
    assert [m for m in continued["messages"] if m["role"] == "tool"] == receipts
    assert len([m for m in again["messages"] if m.get("tool_calls")]) == 2


def test_failed_unlinked_or_other_target_corrections_are_not_retained():
    for original in (snapshot(success="failed"), snapshot(corrected_project="D:/OtherProject"),
                     snapshot(correction_id="unknown-action")):
        _, retained = AgentRuntimeSessionState.native_compaction_projection(original)
        assert not any(m.get("tool_calls") for m in retained)


def test_current_turn_correction_pairs_are_retained_once():
    for split_turn in (False, True):
        original = snapshot()
        if split_turn:
            original["messages"].insert(3, {"role": "user", "content": "Correct the selection"})
            original["activeTurnStart"] = 3
            original["humanUserMessageIndices"] = [0, 3, 6]
        else:
            original["activeTurnStart"] = 0
        state = AgentRuntimeSessionState(AgentRuntimeSessionStatePorts(shared_state_lock=threading.RLock()))
        state._save_native("session", original)
        compacted = state.replace_native_compacted_history("session", binding="binding",
            expected_snapshot=original, summary="Compacted")
        calls = [call["id"] for message in compacted["messages"] for call in message.get("tool_calls", [])]
        receipts = [message["tool_call_id"] for message in compacted["messages"] if message["role"] == "tool"]
        assert calls == receipts == ["failed-call", "corrected-call"]


def test_transport_failure_does_not_establish_invalid_renderer_paths():
    original = snapshot()
    failed = json.loads(original["messages"][2]["content"])
    failed["observations"][0]["observation"] = "Connection timeout"
    original["messages"][2]["content"] = json.dumps(failed)
    _, retained = AgentRuntimeSessionState.native_compaction_projection(original)
    assert not any(m.get("tool_calls") for m in retained)
