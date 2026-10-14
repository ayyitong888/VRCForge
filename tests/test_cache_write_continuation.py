"""Cache-write measurements remain distinct from missing usage across a wait."""
import pytest

from agent_task_loop import AgentTaskLoop, merge_provider_usage
from agent_runtime_event_projection import project_runtime_turn_event


def usage(write=0):
    return {"exact": True, "inputTokens": 10, "outputTokens": 1,
            "totalTokens": 11, "cacheWriteTokens": write,
            "cacheWriteUsageRequestCount": 1, "cacheWriteUsageComplete": True}


def test_cache_write_survives_durable_seed_and_projected_continuation():
    seed = AgentTaskLoop("qa", session_id="s", provider_usage=usage(7)).approval_seed()
    merged = merge_provider_usage(seed["providerUsage"], usage(0))
    assert merged["cacheWriteTokens"] == 7
    assert merged["cacheWriteUsageRequestCount"] == 2
    assert merged["cacheWriteUsageComplete"] is True
    event = project_runtime_turn_event({"continuationSource": "question_answered",
        "sessionId": "s", "turnId": "t", "contextUsage": merged, "plan": {"nextStep": "done"}})
    assert event["contextUsage"] == merged


@pytest.mark.parametrize("missing", [{}, {"exact": True, "inputTokens": 10, "outputTokens": 1, "totalTokens": 11}])
def test_missing_usage_preserves_known_partial_write_without_claiming_complete(missing):
    merged = merge_provider_usage(usage(7), missing)
    assert merged["cacheWriteTokens"] == 7
    assert merged["cacheWriteUsageRequestCount"] == 1
    assert merged["cacheWriteUsageComplete"] is False


@pytest.mark.parametrize("invalid", [True, -1, 1.5, "bad"])
def test_invalid_write_cannot_become_a_complete_measurement(invalid):
    merged = merge_provider_usage({}, usage(invalid))
    assert "cacheWriteTokens" not in merged
    assert merged["cacheWriteUsageComplete"] is False
