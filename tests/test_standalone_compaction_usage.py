import json
from types import SimpleNamespace

import pytest

import dashboard_server as dashboard
from dashboard_api_models import AgentCompactRequest
from context_compaction import MODEL_HANDOFF_PREFIX
from runtime_planner_service import PlannerModelResult


def _request() -> AgentCompactRequest:
    return AgentCompactRequest(history=[{"role": "user", "text": "fixture history"}])


def test_standalone_compact_routes_prior_handoff_as_state_and_keeps_archive(monkeypatch):
    settings = SimpleNamespace(llm_provider="openai", llm_api_key="test", llm_model="model")
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _request: settings)
    prior = {"role": "agent", "text": MODEL_HANDOFF_PREFIX + "Previous handoff"}
    recent = {"role": "user", "text": "New information"}

    def compact(history, **kwargs):
        assert history == [recent]
        assert kwargs["prior_handoff"] == "Previous handoff"
        assert kwargs["recovery_history"] == [prior, recent]
        assert kwargs["handoff_archive_indices"] == [1]
        return {"ok": True, "summary": "fixture"}

    monkeypatch.setattr(dashboard, "compact_context", compact)
    assert dashboard.app_agent_compact(AgentCompactRequest(history=[prior, recent]))["ok"] is True


def test_standalone_residual_budget_folds_only_completed_detail_and_preserves_graph(monkeypatch):
    from agent_handoff_graph import HANDOFF_GRAPH_SCHEMA, parse_handoff_graph
    from context_compaction import estimate_compaction_prompt_tokens, normalize_compaction_recovery

    settings = SimpleNamespace(llm_provider="openai", llm_api_key="test", llm_model="model")
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _request: settings)
    prompts = []
    def summarize(_settings, prompt):
        prompts.append(prompt)
        return PlannerModelResult("{}")

    monkeypatch.setattr(dashboard, "request_llm_plan_with_metadata", summarize)
    source = {"type": "receipt", "id": "read-verified"}
    facts = "Long closed observation. " * 700
    prior = {"schema": HANDOFF_GRAPH_SCHEMA, "nodes": {
        "done": {"kind": "task", "status": "done", "text": "Verified item", "evidence": [source],
                 "packagePreflight": {"ready": True, "receiptId": source["id"]}},
        "item": {"kind": "issue", "status": "closed", "text": "Resolved item", "facts": facts, "evidence": [source]},
    }, "edges": [["done", "blocked_by", "item", {"evidence": [source], "reason": "Verified"}]]}
    result = dashboard.app_agent_compact(AgentCompactRequest(history=[
        {"role": "agent", "text": MODEL_HANDOFF_PREFIX + json.dumps(prior)},
        {"role": "user", "text": "Please continue reviewing the export."},
    ], target_tokens=1200, real_context_limit=32_000))
    assert result["ok"] and len(prompts) == 1
    assert estimate_compaction_prompt_tokens(result["summary"]) <= 1200
    graph = parse_handoff_graph(result["summary"])
    assert "item" not in graph["nodes"] and graph["nodes"]["done"]["packagePreflight"] == prior["nodes"]["done"]["packagePreflight"]
    recovery = normalize_compaction_recovery(result["recovery"])
    assert {"type": "message", "id": "handoff:" + recovery["summaryDigest"]} in graph["nodes"]["done"]["evidence"]
    assert parse_handoff_graph(recovery["summary"])["nodes"]["item"]["facts"] == facts


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
