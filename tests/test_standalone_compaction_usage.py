from types import SimpleNamespace

import pytest

import dashboard_server as dashboard
from dashboard_api_models import AgentCompactRequest
from runtime_planner_service import PlannerModelResult


def _request() -> AgentCompactRequest:
    return AgentCompactRequest(history=[{"role": "user", "text": "fixture history"}])


def test_standalone_compact_preserves_provider_usage_and_uses_metadata_route(monkeypatch):
    settings = SimpleNamespace(llm_provider="openai", llm_api_key="test", llm_model="model")
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _request: settings)
    monkeypatch.setattr(
        dashboard,
        "request_llm_plan",
        lambda *_args, **_kwargs: pytest.fail("standalone compaction must retain provider metadata"),
    )
    monkeypatch.setattr(
        dashboard,
        "request_llm_plan_with_metadata",
        lambda *_args, **_kwargs: PlannerModelResult(
            "summary", usage={"exact": True, "inputTokens": 120, "outputTokens": 8, "totalTokens": 128}
        ),
    )

    def compact(_history, *, summarizer, **_kwargs):
        assert summarizer("page") == "summary"
        return {"ok": True, "summary": "summary", "estimatedInputTokens": 999}

    monkeypatch.setattr(dashboard, "compact_context", compact)
    result = dashboard.app_agent_compact(_request())

    assert result["estimatedInputTokens"] == 999
    usage = result["providerUsage"]
    assert usage["schema"] == "vrcforge.provider_usage.v1"
    assert usage["provider"] == "openai" and usage["model"] == "model"
    assert usage["source"] == "compaction" and usage["exact"] is True
    assert usage["requestCount"] == 1
    assert usage["inputTokens"] == 120 and usage["outputTokens"] == 8 and usage["totalTokens"] == 128
    assert usage["partial"] is True
    assert usage["cacheCoverage"]["read"]["complete"] is False
    assert usage["cacheCoverage"]["write"]["complete"] is False


def test_standalone_compact_sums_each_attempt_once_and_keeps_missing_usage_unknown(monkeypatch):
    settings = SimpleNamespace(llm_provider="openai", llm_api_key="test", llm_model="model")
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _request: settings)
    responses = iter(
        [
            PlannerModelResult("page one", usage={"exact": True, "inputTokens": 10, "outputTokens": 2}),
            RuntimeError("temporary provider failure"),
            PlannerModelResult("page two", usage={"exact": True, "inputTokens": 11, "outputTokens": 3}),
        ]
    )

    def request(*_args, **_kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(dashboard, "request_llm_plan_with_metadata", request)

    def compact(_history, *, summarizer, **_kwargs):
        summaries = []
        for page in ("page-1", "page-1", "page-2"):
            try:
                summaries.append(summarizer(page))
            except RuntimeError:
                pass
        return {"ok": True, "summary": "\n".join(summaries), "estimatedInputTokens": 777}

    monkeypatch.setattr(dashboard, "compact_context", compact)
    result = dashboard.app_agent_compact(_request())

    usage = result["providerUsage"]
    assert usage["requestCount"] == 3
    assert usage["inputTokens"] == 21
    assert usage["outputTokens"] == 5
    assert usage["totalTokens"] == 26
    assert usage["exact"] is False
    assert usage["unavailableReason"] == "provider_error"
    assert usage["coverage"]["totalTokens"]["complete"] is False
    assert result["estimatedInputTokens"] == 777


def test_standalone_compact_missing_provider_usage_does_not_synthesize_zero(monkeypatch):
    settings = SimpleNamespace(llm_provider="openai", llm_api_key="test", llm_model="model")
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _request: settings)
    monkeypatch.setattr(
        dashboard,
        "request_llm_plan_with_metadata",
        lambda *_args, **_kwargs: PlannerModelResult("summary", usage={}),
    )
    monkeypatch.setattr(
        dashboard,
        "compact_context",
        lambda _history, *, summarizer, **_kwargs: (
            summarizer("page"), {"ok": True, "summary": "summary", "estimatedInputTokens": 12}
        )[1],
    )

    usage = dashboard.app_agent_compact(_request())["providerUsage"]

    assert usage["exact"] is False
    assert usage["requestCount"] == 1
    assert usage["unavailableReason"] == "provider_usage_missing"
    assert "inputTokens" not in usage
    assert "outputTokens" not in usage
    assert "totalTokens" not in usage


def test_standalone_compact_preserves_explicit_zero_cache_and_provider_total(monkeypatch):
    settings = SimpleNamespace(llm_provider="openai", llm_api_key="test", llm_model="model")
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _request: settings)
    monkeypatch.setattr(
        dashboard,
        "request_llm_plan_with_metadata",
        lambda *_args, **_kwargs: PlannerModelResult(
            "summary",
            usage={
                "exact": True,
                "inputTokens": 0,
                "outputTokens": 7,
                "totalTokens": 99,
                "cacheReadTokens": 0,
                "cacheWriteTokens": 4,
            },
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "compact_context",
        lambda _history, *, summarizer, **_kwargs: (
            summarizer("page"), {"ok": True, "summary": "summary", "estimatedInputTokens": 12}
        )[1],
    )

    usage = dashboard.app_agent_compact(_request())["providerUsage"]

    assert usage["inputTokens"] == 0
    assert usage["outputTokens"] == 7
    assert usage["totalTokens"] == 99
    assert usage["cacheReadTokens"] == 0 and usage["cacheWriteTokens"] == 4
    assert usage["cacheCoverage"]["read"]["complete"] is True
    assert usage["cacheCoverage"]["write"]["complete"] is True
