import json

from agent_output_truncation import OutputTruncation


class _State:
    @staticmethod
    def _native_pending(snapshot):
        return False


class _Turn:
    _state = _State()

    def __init__(self, messages):
        self.messages = messages
        self.hidden = []

    def snapshot(self):
        return {"messages": self.messages}

    def append_hidden(self, message):
        self.hidden.append(message)


def _call(call_id="c1", name="vrcforge_read_text_file", arguments='{"path":"E:/notes.txt"}'):
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def test_truncated_batch_feedback_includes_completed_call_identity_arguments_and_real_result():
    result = {"ok": True, "text": "actual file contents", "status": "completed"}
    turn = _Turn([{"role": "tool", "tool_call_id": "c1", "content": json.dumps(result)}])
    truncation = OutputTruncation(pending_calls=[_call()])

    truncation.flush_feedback(turn)

    feedback = json.loads(turn.hidden[0].split("：", 1)[1])
    settled = feedback["settledCalls"][0]
    assert settled == {
        "id": "c1", "name": "vrcforge_read_text_file", "arguments": '{"path":"E:/notes.txt"}',
        "status": "completed", "result": result,
    }
    assert truncation.notices == []


def test_truncated_batch_completed_failure_has_safe_reason_suggestion_and_frontend_notice():
    result = {
        "observations": [{
            "runtimeTool": "vrcforge_read_text_file",
            "outcome": {"status": "failed", "errorCode": "path_not_found"},
            "observation": 'outcomeSummary=目标文件不存在; likelyCauses=路径拼写错误; nextActions=修正路径后重试',
        }],
        "debug": "Traceback (most recent call last):\n  File \"secret.py\"\nRuntimeError: raw detail",
    }
    turn = _Turn([{"role": "tool", "tool_call_id": "c1", "content": json.dumps(result)}])
    truncation = OutputTruncation(pending_calls=[_call()])

    truncation.flush_feedback(turn)

    feedback = json.loads(turn.hidden[0].split("：", 1)[1])
    settled = feedback["settledCalls"][0]
    assert settled["name"] == "vrcforge_read_text_file"
    assert settled["arguments"] == '{"path":"E:/notes.txt"}'
    assert settled["status"] == "failed"
    assert settled["reason"] == "路径拼写错误"
    assert settled["suggestion"] == "修正路径后重试"
    assert "Traceback" not in turn.hidden[0]
    assert "secret.py" not in turn.hidden[0]
    assert truncation.notices == [{
        "id": "c1", "name": "vrcforge_read_text_file",
        "arguments": '{"path":"E:/notes.txt"}', "status": "failed",
        "reason": "路径拼写错误", "suggestion": "修正路径后重试",
    }]


def test_truncated_batch_unwraps_completed_call_tool_arguments_for_feedback():
    wrapped = _call(
        name="call_tool",
        arguments=json.dumps({"tool": "vrcforge_read_text_file", "arguments": {"path": "E:/notes.txt"}}),
    )
    turn = _Turn([{"role": "tool", "tool_call_id": "c1", "content": json.dumps({"text": "read"})}])
    truncation = OutputTruncation(pending_calls=[wrapped])

    truncation.flush_feedback(turn)

    feedback = json.loads(turn.hidden[0].split("：", 1)[1])
    settled = feedback["settledCalls"][0]
    assert settled["name"] == "vrcforge_read_text_file"
    assert json.loads(settled["arguments"]) == {"path": "E:/notes.txt"}


def test_truncated_batch_failure_uses_nonempty_safe_fallback_when_diagnostics_missing():
    turn = _Turn([{"role": "tool", "tool_call_id": "c1", "content": json.dumps({
        "observations": [{"outcome": {"status": "error"}, "observation": ""}],
        "exception": "Traceback: private executor details",
        "summary": "Traceback (most recent call last): private executor details",
    })}])
    truncation = OutputTruncation(pending_calls=[_call()])

    truncation.flush_feedback(turn)

    feedback = json.loads(turn.hidden[0].split("：", 1)[1])
    settled = feedback["settledCalls"][0]
    assert settled["status"] == "error"
    assert settled["reason"]
    assert settled["suggestion"]
    assert "private executor details" not in turn.hidden[0]
    assert truncation.notices[0]["status"] == "error"
