import hashlib
import json

import pytest

from agent_native_history_store import NativeHistoryStore
from scripts.recover_native_applied_fact import apply, candidate


def fixture(tmp_path):
    store = NativeHistoryStore(tmp_path / "native-history")
    native = {"binding": "route", "turnId": "turn", "activeTurnStart": 0,
              "messages": [{"role": "assistant", "tool_calls": [
                  {"id": "first", "function": {"name": "call_tool", "arguments": json.dumps({"tool": "toggle", "arguments": {"objectPath": "Avatar/WristCuffs", "active": True}})}},
                  {"id": "later", "function": {"name": "call_tool", "arguments": json.dumps({"tool": "toggle", "arguments": {"objectPath": "Avatar/Other", "active": True}})}},
              ]}]}
    store.save("session", native, {})
    store.save_approval("approval", "session", native, {})
    summary = {"ok": True, "active": True, "before": False, "after": True, "saved": False,
               "objectPath": "Avatar/WristCuffs", "result": {"type": "object", "keyCount": 7}}
    approval = {"id": "approval", "status": "applied", "targetTool": "toggle", "resultSummary": summary,
                "taskContext": {"sessionId": "session", "turnId": "turn", "actionId": "action",
                                "requestedActionId": "action", "tool": "toggle",
                                "requestedArguments": {"objectPath": "Avatar/WristCuffs", "active": True}}}
    (tmp_path / "approvals.jsonl").write_text(json.dumps({"event": "approval_applied", "approval": approval}) + "\n")
    (tmp_path / "runtime-runs.jsonl").write_text(json.dumps({"event": "approval_applied", "approvalId": "approval",
        "targetTool": "toggle", "transactionStatus": "applied", "resultSummary": summary}) + "\n")
    return store._path("session", "route")


def test_restore_only_historical_facts_and_apply_twice(tmp_path):
    path = fixture(tmp_path)
    before = path.read_bytes()
    result, report = candidate(path, tmp_path, "approval")
    assert path.read_bytes() == before
    assert report["recovered"]["facts"]["saved"] is False
    assert "result" not in report["recovered"]["facts"]
    assert "observations" not in report["recovered"]
    applied = apply(path, result, report, hashlib.sha256(before).hexdigest())
    assert applied["status"] == "repaired"
    assert len(result["native"]["messages"]) == 2
    result2, report2 = candidate(path, tmp_path, "approval")
    after = path.read_bytes()
    assert apply(path, result2, report2, report["beforeSha256"])["status"] == "already_repaired"
    assert path.read_bytes() == after
    assert not any(m.get("tool_call_id") == "later" for m in result2["native"]["messages"])


def test_changed_history_is_preserved(tmp_path):
    path = fixture(tmp_path)
    result, report = candidate(path, tmp_path, "approval")
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="History changed"):
        apply(path, result, report, report["beforeSha256"])
    assert not list(path.parent.glob("*.bak"))


def test_mismatched_approval_session_stays_unknown(tmp_path):
    path = fixture(tmp_path)
    audit = tmp_path / "approvals.jsonl"
    data = json.loads(audit.read_text())
    data["approval"]["taskContext"]["sessionId"] = "different"
    audit.write_text(json.dumps(data) + "\n")
    with pytest.raises(ValueError):
        candidate(path, tmp_path, "approval")


@pytest.mark.parametrize("change", ["tool", "target", "pending_order"])
def test_wrong_pending_call_tool_or_target_is_rejected(tmp_path, change):
    path = fixture(tmp_path)
    frozen = next(path.parent.glob("approval-*.json"))
    for target in (path, frozen):
        value = json.loads(target.read_text())
        calls = value["native"]["messages"][0]["tool_calls"]
        if change == "pending_order":
            calls.reverse()
        else:
            request = json.loads(calls[0]["function"]["arguments"])
            if change == "tool":
                request["tool"] = "delete"
            else:
                request["arguments"]["objectPath"] = "Avatar/Other"
            calls[0]["function"]["arguments"] = json.dumps(request)
        target.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Pending call"):
        candidate(path, tmp_path, "approval")
