from copy import deepcopy
import json
import pytest

from tests.test_native_context_compaction import Compactor, fixture
from tests.test_native_context_compaction import Compactor, fixture, plan


class IncompleteCompactor(Compactor):
    def compact(self, history, metadata):
        result = super().compact(history, metadata)
        return {**result, "providerAttempts": 2,
            "completeness": {"summarizerInputComplete": False}}


def test_native_keeps_history_when_summarizer_did_not_read_all_input():
    state, planner, model, turn = fixture(IncompleteCompactor())
    before = deepcopy(state.native_conversation("s", binding="b"))
    plan(planner, turn)
    after = state.native_conversation("s", binding="b")
    assert after["messages"] == before["messages"]
    assert turn.compaction["applied"] is False
    assert turn.compaction["attempts"] == 2
    assert turn.compaction["failureClass"] == "incomplete_input"
    assert model.requests == []


def test_legacy_keeps_history_when_summarizer_did_not_read_all_input():
    _, planner, _, _ = fixture(IncompleteCompactor())
    history = [{"role": "user", "text": "retained history " * 5000}]
    with planner.bind_turn({}):
        changed, metadata, blocked = planner.maybe_compact_runtime_history(
            message="continue", params={"_contextCompactionLimit": 8000}, observe={},
            history=history, loop_state=[], context_usage={})
    assert changed == history
    assert metadata["applied"] is False
    assert metadata["attempts"] == 2
    assert metadata["failureClass"] == "incomplete_input"
    assert blocked is True


def test_legacy_compacts_current_turn_observations_without_changing_execution_state():
    _, planner, _, _ = fixture(Compactor("verified current-turn summary"))
    loop_state = [
        {"tool": "unity_read", "status": "executed", "actionId": f"a-{index}",
         "modelInformation": {"retainedBy": "agent_tool_result_reader", "payload": {"text": "x" * 3000}},
         "modelInformationRead": {"source": "owner_model", "page": {"ok": True, "hasMore": False}}}
        for index in range(30)
    ]
    loop_state.append({"tool": "vrcforge_ask_user", "status": "pending", "actionId": "approval-1",
                       "result": {"questionId": "q-1"}})
    observe = {}
    with planner.bind_turn({}):
        changed, metadata, blocked = planner.maybe_compact_runtime_history(
            message="continue", params={"_contextCompactionLimit": 10_000}, observe=observe,
            history=[], loop_state=loop_state, context_usage={})
    assert metadata["applied"] is True
    assert blocked is False
    assert changed == [{"role": "agent", "text": "verified current-turn summary"}]
    assert 0 < metadata["compactedLoopStepCount"] < 30
    assert observe == {}
    assert loop_state[-1]["result"]["questionId"] == "q-1"


def test_legacy_compacts_returned_failed_results_but_preserves_unresolved_suffix():
    compactor = Compactor("Earlier work included a returned failure; current tool observations follow.")
    _state, planner, _model, _turn = fixture(compactor, prior=False, limit=10_000)
    loop_state = [
        {
            "tool": "unity_read",
            "status": "executed",
            "actionId": "read-before-failure",
            "outcome": {"status": "ok", "success": True},
            "modelInformation": {
                "retainedBy": "agent_tool_result_reader",
                "payload": {"text": "settled observation " + "x" * 5000},
            },
            "modelInformationRead": {"source": "owner_model", "page": {"ok": True, "hasMore": False}},
        },
        {
            "kind": "phase",
            "tool": "exposure_layer",
            "status": "entered_execution",
            "result": {"exposureLayer": "execution"},
        },
        {
            "tool": "unity_read",
            "status": "failed",
            "actionId": "returned-failure",
            "result": {"ok": False, "status": "failed", "error": "fixture read failed", "recovery": {"retryable": True}},
            "outcome": {
                "status": "failed", "success": False,
                "error": {"type": "tool", "code": "tool_failed", "retryable": True},
                "failureCause": {"message": "fixture read failed"},
                "rootCause": {"message": "fixture read failed"},
            },
            "error": "fixture read failed",
        },
        {
            "tool": "vrcforge_scan_blendshapes",
            "status": "failed",
            "actionId": "failed-readback",
            "outcome": {"status": "failed", "success": False, "summary": "Unity rejected the read."},
            "error": "Unity rejected the read.",
            "modelInformation": {
                "retainedBy": "agent_tool_result_reader",
                "payload": {"text": "actionId=failed-readback; outcomeStatus=failed; errorCode=tool_failed"},
            },
            "modelInformationRead": {
                "source": "owner_model",
                "page": {"ok": True, "hasMore": False, "items": [{"value": "errorCode=tool_failed"}]},
            },
        },
        *[
            {
                "tool": "unity_read",
                "status": "executed",
                "actionId": f"read-{index}",
                "outcome": {"status": "ok", "success": True},
                "modelInformation": {
                    "retainedBy": "agent_tool_result_reader",
                    "payload": {"text": f"large observation {index} " + "y" * 5000},
                },
                "modelInformationRead": {"source": "owner_model", "page": {"ok": True, "hasMore": False}},
            }
            for index in range(3)
        ],
        {
            "tool": "vrcforge_ask_user",
            "status": "executed",
            "actionId": "pending-question-after-dispatch",
            "result": {"questionId": "question-still-open"},
            "outcome": {"status": "needs_user_action", "success": False},
        },
        {
            "tool": "vrcforge_read_tool_result",
            "status": "pending",
            "actionId": "unreturned-read-page",
            "modelInformationRead": {"source": "owner_model", "nextRequest": {"tool": "vrcforge_read_tool_result", "arguments": {"offset": 20}}},
        },
    ]
    before = deepcopy(loop_state)
    history = [{"role": "user", "text": "earlier conversation " * 4000}]
    with planner.bind_turn({}):
        changed, metadata, blocked = planner.maybe_compact_runtime_history(
            message="Continue after the read.", params={"_contextCompactionLimit": 10_000},
            observe={}, history=history, loop_state=loop_state, context_usage={})

    assert metadata["applied"] is True
    assert blocked is False
    assert changed == [{"role": "agent", "text": compactor.summary}]
    assert loop_state == before
    passed = "\n".join(entry["text"] for entry in compactor.calls[0][0])
    assert "returned-failure" in passed
    assert "fixture read failed" in passed
    assert "retryable" in passed
    assert "failureCause" in passed and "rootCause" in passed
    assert "failed-readback" in passed
    assert "tool_failed" in passed
    assert "entered_execution" in passed
    assert "pending-question-after-dispatch" not in passed
    assert "unreturned-read-page" not in passed
    assert 0 < metadata["compactedLoopStepCount"] < len(before)
    assert "question-still-open" in json.dumps(loop_state[-2])
    next_prompt = planner._build_llm_plan_prompt(
        "Continue after the read.", changed,
        loop_state[metadata["compactedLoopStepCount"]:], observe={})
    assert compactor.summary in next_prompt
    assert "question-still-open" in next_prompt
    assert "unreturned-read-page" in next_prompt


@pytest.mark.parametrize(
    ("barrier", "should_compact"),
    [
        ({"status": "running", "outcome": {"status": "ok"}}, False),
        ({"status": "executed", "result": {"session": {"status": "running"}}, "outcome": {"status": "ok"}}, False),
        ({"status": "executed", "outcome": {"status": "pending"}}, False),
        ({"status": "failed", "result": {"status": "failed", "pending": True}, "outcome": {"status": "failed"}}, True),
    ],
)
def test_legacy_compaction_distinguishes_running_jobs_from_returned_failures(barrier, should_compact):
    compactor = Compactor("Prior settled observations are summarized.")
    _state, planner, _model, _turn = fixture(compactor, prior=False, limit=10_000)
    settled_failure = {
        "tool": "unity_read", "status": "failed", "actionId": "closed-failed-action",
        "result": {"status": "failed", "pending": True, "error": "asset remains dirty"},
        "outcome": {"status": "failed", "success": False, "summary": "Call returned with failure."},
        "error": "Call returned with failure.",
    }
    loop_state = [settled_failure] + [
        {
            "tool": "unity_read", "status": "executed", "actionId": f"settled-{index}",
            "outcome": {"status": "ok", "success": True},
            "modelInformation": {
                "retainedBy": "agent_tool_result_reader",
                "payload": {"text": f"large settled observation {index} " + "z" * 5000},
            },
            "modelInformationRead": {"source": "owner_model", "page": {"ok": True, "hasMore": False}},
        }
        for index in range(24)
    ]
    barrier_action_id = ""
    if barrier["status"] != "failed":
        barrier_action_id = "still-running-action"
        loop_state.append({"tool": "unity_read", "actionId": barrier_action_id, **barrier})
    before = deepcopy(loop_state)
    with planner.bind_turn({}):
        changed, metadata, blocked = planner.maybe_compact_runtime_history(
            message="Continue the task.", params={"_contextCompactionLimit": 10_000},
            observe={}, history=[{"role": "user", "text": "prior context " * 4000}],
            loop_state=loop_state, context_usage={})

    assert metadata["applied"] is True
    assert blocked is False
    assert loop_state == before
    summary_input = "\n".join(entry["text"] for entry in compactor.calls[0][0])
    assert "closed-failed-action" in summary_input
    if should_compact:
        assert not barrier_action_id
        assert "asset remains dirty" in summary_input
    else:
        assert barrier_action_id not in summary_input
        next_prompt = planner._build_llm_plan_prompt(
            "Continue the task.", changed,
            loop_state[metadata["compactedLoopStepCount"]:], observe={})
        assert barrier_action_id in next_prompt
