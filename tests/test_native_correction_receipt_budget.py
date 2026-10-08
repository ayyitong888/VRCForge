"""A rejected batch must allow correction without admitting its invalid calls."""
import json

import pytest

from agent_runtime_native_turn import NativeRuntimeTurn
from tests.test_native_runtime_gateway import call, finish, run, setup_gateway


def invalid_batch(label, count):
    return {"role": "assistant", "tool_calls": [
        {"id": f"{label}-{index}", "type": "function", "function": {
            "name": "vrcforge_read_text_file", "arguments": json.dumps({"path": "rejected.txt"})}}
        for index in range(count)]}


def corrected(label):
    return call(label, "vrcforge_read_text_file", {"path": label + ".txt"})


@pytest.mark.parametrize("count", [1, 2, 4])
def test_invalid_receipt_gets_one_correction_opportunity(tmp_path, count):
    gateway, model, invoked = setup_gateway(tmp_path, [invalid_batch("bad", count), corrected("good"), finish])
    result = run(gateway, tmp_path, maxAgenticTurns=10)
    assert result["plan"]["nextStep"] == "done"
    assert len(model.requests) == 3
    assert [item["path"] for item in invoked] == ["good.txt"]
    errors = [step for step in result["steps"] if step.get("kind") == "planner_validation"]
    assert len(errors) == count
    assert all(step["issues"][0]["code"] == "tool_not_visible" for step in errors)
    for previous, current in zip(model.requests, model.requests[1:]):
        assert current["tools"] == previous["tools"]
        assert current["instructions"] == previous["instructions"]
        retained = previous["messages"][:-1]
        assert current["messages"][:len(retained)] == retained


def test_second_invalid_provider_receipt_still_stops(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [invalid_batch("first", 3),
        invalid_batch("second", 2), corrected("unreachable"), finish])
    result = run(gateway, tmp_path, maxAgenticTurns=10)
    assert result["plan"]["nextStep"] == "planner_failed"
    assert len(model.requests) == 2
    assert not invoked


def test_successful_correction_resets_receipt_budget(tmp_path):
    gateway, model, invoked = setup_gateway(tmp_path, [invalid_batch("first", 2), corrected("good1"),
        invalid_batch("later", 2), corrected("good2"), finish])
    result = run(gateway, tmp_path, maxAgenticTurns=10)
    assert result["plan"]["nextStep"] == "done"
    assert len(model.requests) == 5
    assert [item["path"] for item in invoked] == ["good1.txt", "good2.txt"]


def test_legacy_argument_failures_keep_per_action_counting():
    turn = NativeRuntimeTurn.__new__(NativeRuntimeTurn)
    turn._binding = ""
    assert turn.count_argument_failure(0) == 1
    assert turn.count_argument_failure(1) == 2
