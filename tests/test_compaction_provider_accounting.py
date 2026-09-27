from copy import deepcopy
from types import SimpleNamespace
import json
import time

import pytest

import dashboard_server as dashboard
from agent_gateway import AgentGateway
from runtime_planner_service import RuntimePlannerService, PlannerModelResult
from test_runtime_planner_dashboard_wiring import fixture_config
from test_dashboard_server import bind_test_runtime_planner


USAGE = {"exact": True, "inputTokens": 100, "outputTokens": 10, "cacheReadTokens": 40}


def test_compaction_usage_accumulates_without_changing_main_request_window():
    from test_native_context_compaction import fixture
    usage = {}
    RuntimePlannerService.record_context_usage(None, usage, "main request", [{"text": "history"}], USAGE)
    before = deepcopy(usage)
    _, planner, _, _ = fixture()
    with planner.bind_turn({}):
        guard_before = planner.native_context_guard({"messages": []}, context_usage=usage)
    RuntimePlannerService.record_context_usage(None, usage, "summary page", [], USAGE, update_window=False)
    RuntimePlannerService.record_context_usage(None, usage, "failed page", [], {}, update_window=False)
    assert usage["requestCount"] == 3 and usage["inputTokens"] == 200
    assert usage["cacheReadTokens"] == 80 and usage["cacheUsageComplete"] is False
    assert usage["exact"] is False and usage["lastUsageExact"] is True
    for key in before:
        if key.startswith(("last", "peak", "sentHistory")):
            assert usage[key] == before[key]
    with planner.bind_turn({}):
        assert planner.native_context_guard({"messages": []}, context_usage=usage) == guard_before


def model_fixture(monkeypatch, request):
    model = dashboard._RuntimePlannerModel(SimpleNamespace(current_config=fixture_config))
    monkeypatch.setattr(dashboard.PROVIDER_TEXT_PROBE, "probe_settings", lambda _: SimpleNamespace())
    monkeypatch.setattr(dashboard, "request_llm_plan_with_metadata", request)
    monkeypatch.setattr(model, "_runtime_cancel_requested", lambda _: False)
    return model


def test_compactor_records_each_page_and_failed_attempt_without_forwarding_private_callback(monkeypatch):
    calls, receipts = [], []
    def request(_settings, prompt, **kwargs):
        calls.append(prompt)
        assert "_usage_callback" not in kwargs and "_deadline" not in kwargs
        if len(calls) == 2:
            raise RuntimeError("HTTP 503 temporary")
        return PlannerModelResult("Safe summary", usage=USAGE)
    model = model_fixture(monkeypatch, request)
    compactor = dashboard._RuntimePlannerCompactor(SimpleNamespace(current_config=fixture_config), model)
    result = compactor.compact(({"role": "user", "text": "字" * 3000},), {
        "targetTokens": 2000, "_recordUsage": lambda prompt, usage: receipts.append((prompt, usage))})
    assert len(receipts) == len(calls) == result["providerAttempts"] == 3
    assert receipts[0][1] == USAGE and receipts[2][1] == USAGE
    assert receipts[1][1].get("exact") is not True
    assert result["completeness"]["summarizerInputComplete"] is True


def test_compaction_absolute_deadline_stops_active_worker_and_later_pages(monkeypatch):
    calls, receipts = [], []
    def request(_settings, prompt, *, cancel_event, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return PlannerModelResult("Safe first page", usage=USAGE)
        assert cancel_event.wait(1), "whole-compaction deadline must cancel the worker"
        raise RuntimeError("transport closed")
    model = model_fixture(monkeypatch, request)
    compactor = dashboard._RuntimePlannerCompactor(SimpleNamespace(current_config=fixture_config), model)
    compactor._TOTAL_TIMEOUT_SECONDS = 0.05
    result = compactor.compact(({"role": "user", "text": "字" * 3000},), {
        "targetTokens": 2000, "_recordUsage": lambda p, u: receipts.append(u)})
    assert len(calls) == len(receipts) == 2
    assert result["fallbackReason"] == "compaction_deadline"
    assert result["completeness"]["summarizerInputComplete"] is False
    assert model.active_call_count() == 0


def test_cancel_after_receipt_preserves_known_usage(monkeypatch):
    finished, receipts = [], []
    def request(*args, **kwargs):
        finished.append(True)
        return PlannerModelResult("Safe summary", usage=USAGE)
    model = model_fixture(monkeypatch, request)
    monkeypatch.setattr(model, "_runtime_cancel_requested", lambda _: bool(finished))
    with pytest.raises(dashboard.RuntimePlannerProviderCancelledError):
        model.plan("fixture", _deadline=time.monotonic() + 1, _usage_callback=receipts.append)
    assert receipts == [USAGE]


def test_expired_deadline_does_not_admit_or_charge_a_provider_attempt(monkeypatch):
    receipts = []
    model = model_fixture(monkeypatch, lambda *_a, **_k: pytest.fail("expired call was admitted"))
    with pytest.raises(dashboard.RuntimePlannerProviderTimeoutError) as raised:
        model.plan("fixture", _deadline=time.monotonic() - 1, _usage_callback=receipts.append)
    assert raised.value.phase == "compaction"
    assert receipts == [] and model.active_call_count() == 0


def test_gateway_cancel_restores_window_but_keeps_compaction_cost(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.register_tool("vrcforge_test_read", "Read fixture", "read/debug", lambda _: {"ok": True})
    def plan(_):
        return {"text": json.dumps({"action": "skill", "skill_tool": "vrcforge_test_read", "skill_params": {}, "summary": "read"}),
                "usage": {"exact": True, "inputTokens": 18000, "outputTokens": 10, "cacheReadTokens": 0}}
    def compact(history, metadata):
        metadata["_recordUsage"]("page", USAGE)
        gateway.request_runtime_cancel({"sessionId": "billing-cancel", "clientTurnId": "billing-ui", "reason": "test"})
        return {"summary": "Safe compact summary", "providerAttempts": 1,
                "completeness": {"summarizerInputComplete": True}, "summaryDigest": "test-digest"}
    bind_test_runtime_planner(gateway, plan, compact=compact)
    response = gateway.runtime_message({"message": "continue", "sessionId": "billing-cancel", "clientTurnId": "billing-ui",
        "history": [{"role": "user", "text": "original"}], "_contextCompactionLimit": 20000})
    assert response["plan"]["nextStep"] == "cancelled"
    assert response["contextUsage"]["inputTokens"] == 18100
    assert response["contextUsage"]["requestCount"] == 2
    assert response["contextUsage"]["peakInputTokens"] == 18000
