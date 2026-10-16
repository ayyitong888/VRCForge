"""Public phase semantics through the real local gateway, without a provider."""
from copy import deepcopy
import json

from agent_runtime_event_projection import project_runtime_turn_event
from chat_snapshot_transport import snapshot_page
from dashboard_api_models import ChatTranscriptsRequest
from responses_native_protocol import parse_native_response
from session_store_integrity import is_valid_chat_record
from tests.test_native_runtime_gateway import call, run, setup_gateway


def message(item_id, text, phase):
    return {"id": item_id, "type": "message", "role": "assistant", "phase": phase,
            "status": "completed", "content": [{"type": "output_text", "text": text, "annotations": []}]}


def receipt(messages, calls=()):
    outputs = list(messages)
    for native_call in calls:
        outputs.append({"type": "function_call", "call_id": native_call["id"],
                        "name": native_call["function"]["name"], "namespace": "vrcforge",
                        "arguments": native_call["function"]["arguments"]})
    return parse_native_response({"output": outputs}, [
        {"type": "function", "function": {"name": name}}
        for name in ("call_tool", "vrcforge_runtime_action")
    ]).assistant_message


def test_multiple_messages_surround_queued_tools_once_and_preserve_replay(tmp_path):
    calls = [call(f"read-{n}", "vrcforge_read_text_file", {"path": f"{n}.txt"})["tool_calls"][0]
             for n in (1, 2)]
    before = receipt([message("c1", "First update.", "commentary"),
                      message("c2", "Second update.", "commentary")], calls)
    # Existing completion evidence must still be claimed through its control tool.
    finish_call = call("finish", "vrcforge_runtime_action", {
        "action": "reply", "reply": "Final result.", "completion_claim": {"satisfied": True}})["tool_calls"][0]
    after = receipt([message("c3", "Checked both.", "commentary"),
                     message("f1", "Final result.", "final_answer")], [finish_call])
    gateway, model, invoked = setup_gateway(tmp_path, [before, after])
    result = run(gateway, tmp_path)
    assert len(invoked) == 2 and len(model.requests) == 2
    assert [event["kind"] for event in result["timeline"]] == [
        "assistant", "assistant", "tool_call", "tool_result", "tool_call", "tool_result",
        "assistant", "assistant"]
    assistants = [event["payload"] for event in result["timeline"] if event["kind"] == "assistant"]
    assert [item["messageId"] for item in assistants] == ["c1", "c2", "c3", "f1"]
    assert [item["phase"] for item in assistants] == ["commentary"] * 3 + ["final_answer"]
    assert [item["status"] for item in assistants] == ["progress"] * 3 + ["done"]
    assert result["plan"]["reply"] == "Final result."
    replay = next(item for item in model.requests[-1]["messages"] if item.get("responses_output"))
    assert replay == before


def test_commentary_only_stops_without_pending_or_extra_request_and_accepts_new_turn(tmp_path):
    gateway, model, _ = setup_gateway(tmp_path, [
        receipt([message("c", "Checking.", "commentary")]),
        receipt([message("f", "Done.", "final_answer")]),
    ])
    result = run(gateway, tmp_path)
    assert len(model.requests) == 1
    assert result["plan"]["nextStep"] == "needs_user_action"
    assert result["plan"]["reason"] == "native_final_answer_missing"
    assert result["plan"]["reply"] == ""
    assert [event["payload"]["status"] for event in result["timeline"]] == ["progress"]
    assert not gateway.runtime_sessions.runtime_waiting_for_configuration()
    next_result = run(gateway, tmp_path, message="Continue", clientTurnId="next-turn")
    assert next_result["plan"]["reply"] == "Done."
    assert len(model.requests) == 2


def test_phase_projection_storage_schema_and_snapshot_text_paths(tmp_path):
    gateway, _, _ = setup_gateway(tmp_path, [receipt([
        message("c", "Checking.", "commentary"), message("f", "Final.", "final_answer")])])
    result = run(gateway, tmp_path)
    projected = project_runtime_turn_event({**result, "continuationSource": "foreground_completed"})
    assert projected["plan"]["publicMessages"] == result["plan"]["publicMessages"]
    assert [event["payload"]["messageId"] for event in projected["timeline"]] == ["c", "f"]
    chat = {"id": "phase-chat", "items": [{"id": "a", "type": "agent", "response": result,
                                              "timeline": result["timeline"]}]}
    assert is_valid_chat_record(chat)
    assert ChatTranscriptsRequest(chats=[chat]).chats == [chat]
    restored = json.loads(snapshot_page({"chats": [chat]}, text_offset=0)["text"])
    assert restored["chats"] == [chat]
    assert "/chats/0/items/0/response/plan/publicMessages/0/text" in restored["_validatedChatTextPaths"]
    invalid = deepcopy(chat)
    invalid["items"][0]["response"]["plan"]["publicMessages"][0]["phase"] = "private_reasoning"
    assert not is_valid_chat_record(invalid)


def test_legacy_phase_missing_keeps_concatenated_reply(tmp_path):
    items = [message("a", "First. ", None), message("b", "Second.", None)]
    for item in items:
        item.pop("phase")
    gateway, _, _ = setup_gateway(tmp_path, [receipt(items)])
    result = run(gateway, tmp_path)
    assert result["plan"]["reply"] == "First. Second."
    assert "publicMessages" not in result["plan"]
    assert len(result["timeline"]) == 1


def test_commentary_only_after_tool_does_not_trigger_completion_correction(tmp_path):
    native_call = call("read", "vrcforge_read_text_file", {"path": "1.txt"})["tool_calls"][0]
    gateway, model, invoked = setup_gateway(tmp_path, [
        receipt([message("before", "Checking.", "commentary")], [native_call]),
        receipt([message("after", "Still checking.", "commentary")]),
    ])
    result = run(gateway, tmp_path)
    assert len(invoked) == 1 and len(model.requests) == 2
    assert result["plan"]["reason"] == "native_final_answer_missing"
    assert result["plan"]["reply"] == ""
    assert [event["payload"]["phase"] for event in result["timeline"] if event["kind"] == "assistant"] == ["commentary", "commentary"]


def test_unaccepted_final_retains_original_phase_until_completion_claim(tmp_path):
    from tests.test_native_runtime_gateway import finish
    read = call("read", "vrcforge_read_text_file", {"path": "1.txt"})["tool_calls"][0]
    candidate = receipt([message("candidate", "Original proposed final.", "final_answer")])
    gateway, model, invoked = setup_gateway(tmp_path, [receipt([], [read]), candidate, finish])
    result = run(gateway, tmp_path)
    public = [event["payload"] for event in result["timeline"] if event["kind"] == "assistant"]
    assert len(invoked) == 1 and len(model.requests) == 3
    assert public[0] == {"label": "Assistant", "summary": "Original proposed final.",
                         "status": "completion_unverified", "phase": "final_answer", "messageId": "candidate"}
    assert [item["status"] for item in public] == ["completion_unverified", "done"]
    assert result["plan"]["nextStep"] == "done"
    assert is_valid_chat_record({"id": "acceptance", "items": [{"id": "a", "type": "agent", "response": result, "timeline": result["timeline"]}]})


def test_unaccepted_final_is_not_promoted_when_turn_budget_stops(tmp_path):
    read = call("read", "vrcforge_read_text_file", {"path": "1.txt"})["tool_calls"][0]
    gateway, model, _ = setup_gateway(tmp_path, [receipt([], [read]), receipt([
        message("candidate", "Original unaccepted text.", "final_answer")])])
    result = run(gateway, tmp_path, maxAgenticTurns=2)
    public = [event["payload"] for event in result["timeline"] if event["kind"] == "assistant"]
    assert len(model.requests) == 2
    assert public[0]["phase"] == "final_answer"
    assert public[0]["summary"] == "Original unaccepted text."
    assert public[0]["status"] == "completion_unverified"
    assert not any(item.get("status") == "done" for item in public)


def test_terminal_blocked_final_retains_visible_original_message(tmp_path):
    read = call("read", "vrcforge_read_text_file", {"path": "1.txt"})["tool_calls"][0]
    gateway, model, invoked = setup_gateway(tmp_path, [receipt([], [read]), receipt([
        message("blocked", "The read failed; I cannot complete this task.", "final_answer")])])
    gateway.register_tool("vrcforge_read_text_file", "When to use: read. When NOT to use: write.",
                          "plan/preview", lambda args: {"ok": False, "error": "fixture-read-failed"})
    result = run(gateway, tmp_path)
    assert len(model.requests) == 2
    assert result["plan"]["nextStep"] != "done"
    public = [event["payload"] for event in result["timeline"] if event["kind"] == "assistant"]
    assert public[0]["status"] == "done"
    assert public[0]["phase"] == "final_answer"
    assert public[0]["messageId"] == "blocked"
    assert public[0]["summary"] == "The read failed; I cannot complete this task."
