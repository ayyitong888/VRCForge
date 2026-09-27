from runtime_planner_service import RuntimePlannerService


def test_native_guard_metadata_does_not_prevent_first_usage_initialization():
    # Native planning records this guard before the first provider response.
    guard = {
        "schema": "vrcforge.runtime_context_compaction.v1",
        "applied": False,
        "blocked": False,
        "trigger": "native_guard",
        "target": "native_history",
        "phase": "mid_turn",
        "beforeTokens": 48918,
        "contextLimit": 256000,
        "triggerTokens": 217600,
        "hardLimitTokens": 243200,
        "targetAfterTokens": 128000,
        "usageExact": False,
        "providerOverheadTokens": 0,
        "requestTokens": 48918,
        "measurement": "native_serialized_request_estimate",
    }
    current = {"nativeContextGuard": dict(guard)}
    provider_usage = {
        "exact": True,
        "inputTokens": 100,
        "outputTokens": 10,
        "totalTokens": 110,
        "cacheReadTokens": 80,
    }

    for _ in range(9):
        RuntimePlannerService.record_context_usage(None, current, "request", [], provider_usage)

    assert current["requestCount"] == current["cacheUsageRequestCount"] == 9
    assert current["inputTokens"] == current["cumulativeInputTokens"] == 900
    assert current["outputTokens"] == 90
    assert current["cacheReadTokens"] == 720
    assert current["cacheUsageComplete"] is True
    assert current["schema"] == "vrcforge.context_usage.v1"
    assert current["source"] == "provider_usage"
    assert current["exact"] is True
    assert current["nativeContextGuard"] == guard
