import json

from tests.test_native_runtime_gateway import budgeted_request_history, call, finish, setup_gateway


def test_native_budget_pause_resumes_same_session_without_replaying_tool(tmp_path):
    gateway, model, invoked = setup_gateway(
        tmp_path,
        [call("read-1", "vrcforge_read_text_file", {"path": "a.txt"}), finish],
    )

    first = gateway.runtime_message({
        "message": "Read this file",
        "sessionId": "native-budget-session",
        "clientTurnId": "native-budget-turn-1",
        "projectRoot": str(tmp_path),
        "maxAgenticTurns": 1,
    })

    assert first["plan"]["nextStep"] == "paused"
    assert first["plan"]["reason"] == "model_turn_budget_exhausted"
    assert first["plan"]["stepLimitReached"] is True
    assert "1 次模型轮次上限" in first["plan"]["reply"]
    assert "任务尚未完成" in first["plan"]["reply"]
    assert "当前聊天" in first["plan"]["reply"]
    assert "modelTurnBudget" not in json.dumps(model.requests)
    assert len(model.requests) == 1
    assert len(invoked) == 1

    second = gateway.runtime_message({
        "message": "Continue",
        "sessionId": "native-budget-session",
        "clientTurnId": "native-budget-turn-2",
        "projectRoot": str(tmp_path),
        "maxAgenticTurns": 2,
    })

    assert second["plan"]["nextStep"] == "done"
    assert len(invoked) == 1
    messages = budgeted_request_history(model.requests[1])
    assert [item["role"] for item in messages] == ["user", "assistant", "tool", "user"]
    assert messages[0] == {"role": "user", "content": "Read this file"}
    assert messages[1]["tool_calls"][0]["id"] == "read-1"
    assert messages[1]["reasoning_content"] == "private-fixture-replay"
    assert messages[2]["tool_call_id"] == "read-1"
    assert "fixture-body" in messages[2]["content"]
    assert messages[3] == {"role": "user", "content": "Continue"}
    assert "private-fixture-replay" not in json.dumps(second)


def test_resume_preserves_sent_history_prefix_tools_and_runtime_state(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("read-b", "vrcforge_read_text_file", {"path": "b.txt"}),
        finish,
    ])
    common = {"sessionId": "prefix-resume", "projectRoot": str(tmp_path)}
    stopped = gateway.runtime_message({**common, "message": "Inspect two files",
        "clientTurnId": "prefix-before", "maxAgenticTurns": 2})
    assert stopped["plan"]["nextStep"] == "paused"
    assert len(model.requests) == len(invoked) == 2
    previous = model.requests[-1]

    # Ordinary user continuation needs no explicit round-count setting.
    resumed = gateway.runtime_message({**common, "message": "Continue",
        "clientTurnId": "prefix-after"})
    current = model.requests[-1]
    assert resumed["plan"]["nextStep"] == "done"
    assert len(model.requests) == 3 and len(invoked) == 2
    prior_history = budgeted_request_history(previous)
    next_history = budgeted_request_history(current)
    assert next_history[:len(prior_history)] == prior_history
    assert current["instructions"] == previous["instructions"]
    assert current["tools"] == previous["tools"]
    assert current["messages"][-1] == previous["messages"][-1]
    assert "modelTurnBudget" not in json.dumps(model.requests)
    assert stopped["plan"]["reply"] not in json.dumps(current, ensure_ascii=False)
