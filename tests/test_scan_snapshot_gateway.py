from agent_tool_result_reader import bind_tool_result_context, read_tool_result
from runtime_planner_service import sanitize_planner_observation_text as sanitize
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway


def test_native_scan_is_persisted_slim_and_legacy_ref_expires_normally_next_turn(tmp_path):
    payload = {"parameters": [{"name": f"Control{i}", "type": "Int"} for i in range(600)],
               "captureComplete": False}
    invoked = []
    gateway, model, _ = setup_gateway(tmp_path, [
        call("scan", "vrcforge_scan_fx_animator", {"avatarPath": "Avatar"}), finish,
        lambda request: call("old-ref", "vrcforge_read_tool_result", {"resultRef": ref}), finish,
    ])
    gateway.register_tool("vrcforge_scan_fx_animator", "When to use: scan. When NOT to use: write.",
                          "read/debug", lambda args: invoked.append(args) or payload)
    first = run(gateway, tmp_path)
    scan = next(row for row in first["steps"] if row["tool"] == "vrcforge_scan_fx_animator")
    ref = scan["resultRead"]["resultRef"]
    assert scan["scanSnapshot"]["persisted"] is True
    assert scan["scanSnapshot"]["capturedAt"]
    assert scan["scanSnapshot"]["scanScope"]["arguments"]["avatarPath"] == "Avatar"
    assert scan["scanSnapshot"]["scope"] == "current_turn"
    body = next(m["content"] for m in model.requests[1]["messages"] if m["role"] == "tool")
    assert "Control599" not in body and "scanSummary" in body
    with bind_tool_result_context(first["sessionId"], first["turnId"], str(tmp_path), [],
                                  snapshot_lookup=gateway._scan_results.lookup):
        page = read_tool_result({"resultRef": ref, "jsonPointer": "/parameters", "offset": 599, "limit": 1}, sanitize=sanitize)
    assert page["items"][0]["value"]["name"] == "Control599"
    old_history = model.requests[1]["messages"][:-1]
    second = run(gateway, tmp_path, message="Follow up", clientTurnId="later")
    assert len(invoked) == 1
    read = next(row for row in second["steps"] if row["tool"] == "vrcforge_read_tool_result")
    assert read["status"] == "executed"
    assert read["result"]["status"] == "needs_rescan" and read["result"]["ok"] is True
    assert read["result"]["reason"] == "core_scope_unavailable"
    assert model.requests[2]["messages"][:len(old_history)] == old_history
    assert model.requests[0]["instructions"] == model.requests[-1]["instructions"]
    assert model.requests[0]["tools"] == model.requests[-1]["tools"]
    assert first["steps"][0]["result"] == payload
    gateway._runtime_session_state.discard_session(first["sessionId"])
    assert gateway._scan_results.lookup(first["sessionId"], first["turnId"], str(tmp_path), ref) is None


def test_capture_failure_preserves_successful_read_without_durable_claim(tmp_path, monkeypatch):
    gateway, model, _ = setup_gateway(tmp_path, [call("scan", "vrcforge_scan_fx_animator", {}), finish])
    gateway.register_tool("vrcforge_scan_fx_animator", "When to use: scan. When NOT to use: write.",
                          "read/debug", lambda args: {"parameters": [{"name": "Kept"}]})
    def unavailable(*args, **kwargs):
        raise OSError("local storage unavailable")
    monkeypatch.setattr(gateway._scan_results, "capture", unavailable)
    response = run(gateway, tmp_path)
    step = response["steps"][0]
    assert step["status"] == "executed" and step["result"]["parameters"][0]["name"] == "Kept"
    assert step["scanSnapshot"]["persisted"] is False
    assert step["scanSnapshot"]["scope"] == "current_turn"
