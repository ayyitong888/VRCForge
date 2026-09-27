from __future__ import annotations

import json

import pytest

from tests.native_planner_fixture import NativePlannerFixture
from runtime_planner_service import PlannerModelResult
from tests.test_runtime_planner_service import FakeModel, NativeModel, native_call, service


USAGE = {
    "exact": True,
    "inputTokens": 100,
    "outputTokens": 10,
    "totalTokens": 110,
    "cacheReadTokens": 80,
}


def _seeded_context() -> dict[str, object]:
    context: dict[str, object] = {}
    planner = service(model=FakeModel(PlannerModelResult(json.dumps({"action": "reply", "reply": "ok"}, ensure_ascii=False))))
    planner.record_context_usage(context, "seed", [], USAGE)
    return context


def test_legacy_provider_exception_marks_usage_incomplete_without_inventing_tokens():
    context = _seeded_context()
    planner = service(model=FakeModel(PlannerModelResult("unused"), error=RuntimeError("provider timeout")))

    result = planner.plan_agent_turn("continue", {}, {}, context_usage=context)

    assert result["plannerFailure"]["code"] == "provider_timeout"
    assert context["requestCount"] == 2
    assert context["inputTokens"] == 100
    assert context["outputTokens"] == 10
    assert context["totalTokens"] == 110
    assert context["cacheUsageComplete"] is False
    assert context["cacheUsageRequestCount"] == 1


def test_first_provider_exception_creates_inexact_zero_token_accounting():
    context: dict[str, object] = {}
    planner = service(model=FakeModel(PlannerModelResult("unused"), error=RuntimeError("provider timeout")))

    result = planner.plan_agent_turn("continue", {}, {}, context_usage=context)

    assert result["plannerFailure"]["code"] == "provider_timeout"
    assert context["requestCount"] == 1
    assert context["exact"] is False
    assert context["cacheUsageComplete"] is False
    assert context["inputTokens"] == 0
    assert context["outputTokens"] == 0
    assert context["totalTokens"] == 0


def test_native_provider_exception_marks_usage_before_propagating():
    context = _seeded_context()
    class RaisingNativeModel(NativeModel):
        def plan_native(self, request):
            raise RuntimeError("provider timeout")

    planner = service(model=RaisingNativeModel(native_call("read_file")))

    with pytest.raises(RuntimeError, match="provider timeout"):
        planner.plan_agent_turn(
            "continue", {"_backgroundGoalRun": True}, {}, context_usage=context,
            native_turn=NativePlannerFixture([{"role": "user", "content": "continue"}]),
        )

    assert context["requestCount"] == 2
    assert context["cacheUsageComplete"] is False
    assert context["inputTokens"] == 100


def test_parse_failure_records_one_successful_receipt_without_duplicate_unknown_call():
    context: dict[str, object] = {}
    model = FakeModel(PlannerModelResult("not-json", usage=USAGE))
    result = service(model=model).plan_agent_turn("continue", {}, {}, context_usage=context)

    assert result["plannerFailure"]["code"] == "planner_invalid_response"
    assert context["requestCount"] == 1
    assert context["cacheUsageComplete"] is True


def test_queued_native_receipt_does_not_count_as_provider_attempt():
    context: dict[str, object] = {}
    message = native_call("read_file")
    message["tool_calls"].append(native_call("read_file", call_id="call-two")["tool_calls"][0])
    model = NativeModel(message)
    native = NativePlannerFixture([{"role": "user", "content": "continue"}])
    planner = service(model=model)
    first = planner.plan_agent_turn("continue", {}, {}, context_usage=context, native_turn=native)
    assert first["nativeCallIds"] == ["call-read"]
    before = dict(context)

    second = planner.plan_agent_turn("continue", {}, {}, context_usage=context, native_turn=native)

    assert second["nativeCallIds"] == ["call-two"]
    assert len(model.requests) == 1
    assert context == before
