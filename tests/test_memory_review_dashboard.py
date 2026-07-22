from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

import dashboard_server
from background_goal_runtime import ProviderPreflightCache, RuntimeLaneBudget
from memory_consolidation_sources import MemoryScope, admit_memory_source, project_scope_key
from memory_review_host import (
    MemoryReviewCancelRequest,
    MemoryReviewConfigRequest,
    MemoryReviewSourceInventory,
)
from memory_review_runtime import MemoryReviewRuntimeCoordinator
from vrchat_blendshape_agent import Settings


REAL_COLLECT_MEMORY_REVIEW_SOURCES = dashboard_server.collect_memory_review_sources


@dataclass
class DashboardMemoryReviewHarness:
    root: Path
    project: Path
    other_project: Path
    unauthorized_project: Path
    settings: dict[str, Settings]
    sources: list[Any]
    provider: dict[str, Callable[..., dict[str, Any]]]
    provider_calls: list[dict[str, Any]]
    events: list[tuple[str, Any]]
    client: TestClient
    host: Any | None = None

    def configure(
        self,
        mode: str,
        *,
        revision: int = 0,
        scope: str = "user",
        project_root: str = "",
        cost_cap_usd: float = 0.0,
        input_cost_per_million_usd: float = 0.0,
        output_cost_per_million_usd: float = 0.0,
    ) -> dict[str, Any]:
        response = self.client.post(
            "/api/app/agent/memory/review/config",
            json={
                "mode": mode,
                "scope": scope,
                "projectRoot": project_root,
                "costCapUsd": cost_cap_usd,
                "inputCostPerMillionUsd": input_cost_per_million_usd,
                "outputCostPerMillionUsd": output_cost_per_million_usd,
                "expectedRevision": revision,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def run(
        self,
        *,
        revision: int,
        scope: str = "user",
        project_root: str = "",
    ) -> Any:
        return self.client.post(
            "/api/app/agent/memory/review/run",
            json={
                "scope": scope,
                "projectRoot": project_root,
                "expectedRevision": revision,
            },
        )

    def review_files_text(self) -> str:
        audit_root = self.root / "gateway-audit"
        chunks: list[str] = []
        if audit_root.exists():
            for path in audit_root.rglob("*"):
                if path.is_file():
                    chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(chunks)


def _settings(
    *,
    provider: str = "openai",
    model: str = "review-model",
    api_key: str = "credential-must-not-persist",
    base_url: str = "https://provider.invalid/v1",
) -> Settings:
    return Settings(
        llm_provider=provider,
        llm_api_key=api_key,
        llm_base_url=base_url,
        llm_model=model,
        llm_api_key_env="",
        gemini_thinking_level="",
        unity_mcp_command=[],
        unity_mcp_host="127.0.0.1",
        unity_mcp_port=8080,
        unity_mcp_instance="",
        unity_mcp_retries=0,
        unity_mcp_retry_backoff_seconds=0,
        unity_mcp_timeout_seconds=1,
        export_tool_name="",
        execute_tool_name="",
        export_path=Path("unused.json"),
        min_confidence=0,
    )


def _source(
    scope: MemoryScope,
    *,
    source_id: str = "chat-1",
    revision: str = "1",
    text: str = "Please remember that I prefer concise answers.",
) -> Any:
    payload: dict[str, Any] = {
        "sourceType": "user_chat",
        "sourceId": source_id,
        "sourceRevision": revision,
        "role": "user",
        "status": "completed",
        "signalKind": "preference",
        "text": text,
    }
    if scope.kind == "project":
        payload["projectRoot"] = scope.project_root
    else:
        payload["memoryScope"] = "user"
    admitted = admit_memory_source(payload, scope=scope)
    assert admitted is not None
    return admitted


def _candidate_result(payload: dict[str, Any], *, text: str = "I prefer concise answers.") -> dict[str, Any]:
    source_ids = [str(item["sourceId"]) for item in payload.get("sources", [])]
    return {
        "candidates": [
            {
                "kind": "preference",
                "text": text,
                "sourceIds": source_ids,
                "confidenceFactors": ["explicit_user_intent"],
            }
        ],
        "usage": {"inputTokens": 17, "outputTokens": 5, "totalTokens": 22},
    }


def test_source_inventory_marks_only_authoritative_source_types_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_scope = MemoryScope(kind="user", scope_key="user")
    monkeypatch.setattr(dashboard_server, "chat_transcripts_path", lambda: tmp_path / "chats.json")
    monkeypatch.setattr(
        dashboard_server,
        "load_chat_transcript_file",
        lambda *_args, **_kwargs: ([], {"status": "missing"}, None),
    )
    user_inventory = REAL_COLLECT_MEMORY_REVIEW_SOURCES(user_scope)
    assert user_inventory.complete_source_types == frozenset({"user_chat"})

    monkeypatch.setattr(
        dashboard_server,
        "load_chat_transcript_file",
        lambda *_args, **_kwargs: ([], {"status": "needs_repair"}, {"reason": "invalid"}),
    )
    unavailable_inventory = REAL_COLLECT_MEMORY_REVIEW_SOURCES(user_scope)
    assert unavailable_inventory.complete_source_types == frozenset()
    assert unavailable_inventory.reason_counts["chat_inventory_unavailable"] == 1


def test_project_source_inventory_never_treats_truncation_or_damage_as_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    project_scope = MemoryScope(
        kind="project",
        scope_key=project_scope_key(str(project)),
        project_root=str(project.resolve()),
    )
    monkeypatch.setattr(
        dashboard_server,
        "project_chat_transcripts_path",
        lambda _root: tmp_path / "project-chats.json",
    )
    monkeypatch.setattr(
        dashboard_server,
        "load_chat_transcript_file",
        lambda *_args, **_kwargs: ([], {"status": "missing"}, None),
    )
    monkeypatch.setattr(
        dashboard_server.SUB_AGENT_REGISTRY,
        "list_tasks",
        lambda **_kwargs: {"tasks": [{} for _ in range(200)]},
    )
    original_paths = (
        dashboard_server.AGENT_GATEWAY.config_path,
        dashboard_server.AGENT_GATEWAY.audit_dir,
    )
    dashboard_server.AGENT_GATEWAY.configure_paths(
        tmp_path / "gateway.json",
        tmp_path / "gateway-audit",
    )
    dashboard_server.AGENT_GATEWAY.audit_dir.mkdir(parents=True, exist_ok=True)
    dashboard_server.AGENT_GATEWAY.audit_log_path.write_text("{malformed\n", encoding="utf-8")
    try:
        inventory = REAL_COLLECT_MEMORY_REVIEW_SOURCES(project_scope, str(project.resolve()))
    finally:
        dashboard_server.AGENT_GATEWAY.configure_paths(*original_paths)
    assert inventory.complete_source_types == frozenset({"user_chat"})
    assert inventory.reason_counts["task_inventory_truncated"] == 1
    assert inventory.reason_counts["audit_inventory_incomplete"] == 1


def test_large_audit_tail_scan_is_bounded_and_does_not_take_gateway_state_lock(
    tmp_path: Path,
) -> None:
    gateway = dashboard_server.AGENT_GATEWAY
    original_paths = (gateway.config_path, gateway.audit_dir)
    gateway.configure_paths(tmp_path / "gateway.json", tmp_path / "gateway-audit")
    gateway.audit_dir.mkdir(parents=True, exist_ok=True)
    gateway.audit_log_path.write_bytes(
        b'{"oversized":"' + (b"x" * (dashboard_server.MEMORY_REVIEW_AUDIT_SCAN_MAX_BYTES + 1024)) + b'"}\n'
    )
    completed = threading.Event()
    result: dict[str, Any] = {}

    def scan() -> None:
        result["value"] = dashboard_server.read_memory_review_audit_inventory()
        completed.set()

    try:
        with gateway._lock:
            worker = threading.Thread(target=scan, daemon=True)
            worker.start()
            assert completed.wait(timeout=0.5)
        worker.join(timeout=1)
    finally:
        gateway.configure_paths(*original_paths)
    events, complete, reason = result["value"]
    assert events == []
    assert complete is False
    assert reason == "audit_inventory_incomplete"


@pytest.fixture
def memory_review_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    gateway = dashboard_server.AGENT_GATEWAY
    original_paths = (gateway.config_path, gateway.audit_dir)
    project = tmp_path / "AuthorizedProject"
    other_project = tmp_path / "OtherProject"
    unauthorized_project = tmp_path / "UnauthorizedProject"
    project.mkdir()
    other_project.mkdir()
    unauthorized_project.mkdir()
    gateway.configure_paths(tmp_path / "gateway.json", tmp_path / "gateway-audit")
    gateway.ensure_config()

    settings = {"value": _settings()}
    sources: list[Any] = []
    provider_calls: list[dict[str, Any]] = []
    events: list[tuple[str, Any]] = []
    provider: dict[str, Callable[..., dict[str, Any]]] = {
        "call": lambda _settings, payload, **_kwargs: _candidate_result(payload)
    }

    monkeypatch.setattr(
        dashboard_server,
        "memory_review_authorized_project_roots",
        lambda: [str(project), str(other_project)],
    )
    monkeypatch.setattr(
        dashboard_server,
        "load_dashboard_settings",
        lambda _request: settings["value"],
    )
    def source_inventory(scope: MemoryScope, _project_root: str = "") -> MemoryReviewSourceInventory:
        complete_types = {"user_chat"}
        if scope.kind == "project":
            complete_types.update({"adopted_task", "validated_project_result"})
        return MemoryReviewSourceInventory(
            sources=tuple(sources),
            complete_source_types=frozenset(complete_types),
            reason_counts={"admitted": len(sources)},
        )

    monkeypatch.setattr(dashboard_server, "collect_memory_review_sources", source_inventory)

    def call_provider(
        current_settings: Settings,
        payload: dict[str, Any],
        token_cap: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        provider_calls.append(
            {
                "provider": current_settings.llm_provider,
                "model": current_settings.llm_model,
                "payload": payload,
                "tokenCap": token_cap if token_cap is not None else kwargs.get("token_cap"),
            }
        )
        return provider["call"](
            current_settings,
            payload,
            token_cap=token_cap if token_cap is not None else kwargs.get("token_cap"),
        )

    monkeypatch.setattr(dashboard_server, "invoke_memory_review_provider", call_provider)

    async def capture_event(event_type: str, payload: Any) -> None:
        events.append((event_type, payload))

    monkeypatch.setattr(dashboard_server.EVENT_BUS, "broadcast", capture_event)

    async def no_wait(_seconds: float) -> None:
        return None

    lane_budget = RuntimeLaneBudget()
    monkeypatch.setattr(dashboard_server, "RUNTIME_LANE_BUDGET", lane_budget)
    runtime = MemoryReviewRuntimeCoordinator(
        lane_budget=lane_budget,
        preflight=ProviderPreflightCache(lambda _provider, _url: True),
        on_state=dashboard_server.broadcast_memory_review_state,
        sleep=no_wait,
        provider_timeout_seconds=0.25,
    )
    monkeypatch.setattr(dashboard_server, "MEMORY_REVIEW_RUNTIME", runtime)
    host = getattr(dashboard_server, "MEMORY_REVIEW_HOST", None)
    if host is not None:
        monkeypatch.setattr(host, "_resolve_scope", dashboard_server.resolve_memory_review_request_scope)
        monkeypatch.setattr(host, "_collect_sources", source_inventory)
        monkeypatch.setattr(host, "_provider_call", call_provider)
        monkeypatch.setattr(
            host,
            "_root_for_scope_key",
            lambda scope_key: next(
                (
                    str(candidate.resolve())
                    for candidate in (project, other_project)
                    if project_scope_key(str(candidate)) == scope_key
                ),
                "",
            ),
        )
        monkeypatch.setattr(host, "runtime", runtime)
        monkeypatch.setattr(host, "_background_task", None)
        lane_budget.set_interactive_acquire_callback(host._idle_gate.signal_activity)  # noqa: SLF001
    client = TestClient(dashboard_server.app)
    harness = DashboardMemoryReviewHarness(
        root=tmp_path,
        project=project,
        other_project=other_project,
        unauthorized_project=unauthorized_project,
        settings=settings,
        sources=sources,
        provider=provider,
        provider_calls=provider_calls,
        events=events,
        client=client,
        host=host,
    )
    try:
        yield harness
    finally:
        client.close()
        gateway.configure_paths(*original_paths)


def test_get_is_authoritative_flat_schema_without_candidate_internals(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    response = env.client.get("/api/app/agent/memory/review", params={"scope": "user"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == "vrcforge.memory_review_snapshot.v1"
    assert payload["mode"] == "off"
    assert payload["revision"] == 0
    assert payload["scope"] == "user"
    assert payload["projectRoot"] == ""
    assert "config" not in payload
    serialized = json.dumps(payload, ensure_ascii=False)
    for internal in (
        "sourceReferences",
        "scopeKey",
        "acceptedText",
        "promotionId",
        "confidenceFactors",
        "conflicts",
        "supersedes",
    ):
        assert internal not in serialized


def test_unauthorized_project_fails_before_provider_or_store(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    response = env.client.post(
        "/api/app/agent/memory/review/run",
        json={
            "scope": "project",
            "projectRoot": str(env.unauthorized_project),
            "expectedRevision": 0,
        },
    )
    assert response.status_code == 400
    assert env.provider_calls == []
    assert not (env.root / "gateway-audit" / "memory-review" / "memory-review.json").exists()


def test_config_revision_cas_and_auto_safe_gate(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    configured = env.configure("shadow")
    assert configured["revision"] == 1

    stale = env.client.post(
        "/api/app/agent/memory/review/config",
        json={"mode": "suggest_only", "scope": "user", "expectedRevision": 0},
    )
    assert stale.status_code == 409

    planned = env.client.post(
        "/api/app/agent/memory/review/config",
        json={"mode": "auto_safe", "scope": "user", "expectedRevision": 1},
    )
    assert planned.status_code == 400
    current = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert current["mode"] == "shadow"
    assert current["revision"] == 1


def test_saved_scope_and_provider_must_match_before_a_paid_run(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    configured = env.configure("suggest_only")

    scope_drift = env.run(
        revision=configured["revision"],
        scope="project",
        project_root=str(env.project),
    )
    assert scope_drift.status_code == 400
    assert env.provider_calls == []
    unchanged = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert unchanged["revision"] == configured["revision"]

    env.settings["value"] = _settings(model="different-active-model")
    drifted = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert drifted["providerDisclosure"]["activeConfigMatches"] is False
    assert drifted["model"] == "review-model"
    provider_drift = env.run(revision=configured["revision"])
    assert provider_drift.status_code == 200
    assert env.provider_calls == []
    deferred = provider_drift.json()["lastRun"]
    assert deferred["status"] == "skipped"
    assert deferred["nonConsuming"] is True
    assert deferred["deferredReason"] == "config_changed"
    assert deferred["eligibleCount"] == 1
    assert deferred["candidateCount"] == 0
    final = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert final["revision"] == provider_drift.json()["revision"]
    assert final["candidates"] == []


def test_switching_projects_does_not_masquerade_as_the_saved_binding(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    configured = env.configure(
        "suggest_only",
        scope="project",
        project_root=str(env.project),
    )
    assert Path(configured["projectRoot"]) == env.project.resolve()
    assert configured["configuredProjectMatches"] is True
    scope, _root = dashboard_server.resolve_memory_review_request_scope(
        "project",
        str(env.project),
    )
    env.sources[:] = [_source(scope, text="Project A candidate must not cross scopes.")]
    generated = env.run(
        revision=configured["revision"],
        scope="project",
        project_root=str(env.project),
    )
    assert generated.status_code == 200, generated.text
    candidate_id = generated.json()["candidates"][0]["candidateId"]

    switched = env.client.get(
        "/api/app/agent/memory/review",
        params={"scope": "project", "projectRoot": str(env.other_project)},
    ).json()
    assert switched["scope"] == "project"
    assert switched["projectRoot"] == ""
    assert Path(switched["requestedProjectRoot"]) == env.other_project.resolve()
    assert switched["configuredProjectMatches"] is False
    assert switched["revision"] == generated.json()["revision"]
    assert switched["candidates"] == []
    returned = env.client.get(
        "/api/app/agent/memory/review",
        params={"scope": "project", "projectRoot": str(env.project)},
    ).json()
    assert [candidate["candidateId"] for candidate in returned["candidates"]] == [candidate_id]


def test_off_and_shadow_make_no_provider_call_or_candidate_prose_persistence(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    off = env.run(revision=0)
    assert off.status_code == 400
    assert env.provider_calls == []

    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    secret_source_text = "Please remember concise replies with credential-never-persist at C:\\Users\\Private\\notes.txt"
    env.sources.append(_source(scope, text=secret_source_text))
    configured = env.configure("shadow")
    shadow = env.run(revision=configured["revision"])
    assert shadow.status_code == 200, shadow.text
    assert env.provider_calls == []
    assert shadow.json()["candidates"] == []
    persisted = env.review_files_text()
    assert secret_source_text not in persisted
    assert "credential-never-persist" not in persisted
    assert "C:\\Users\\Private\\notes.txt" not in persisted


def test_off_and_shadow_configuration_do_not_depend_on_provider_metadata(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    env.settings["value"] = _settings(
        model="C:\\Users\\Private\\unsafe-model",
        api_key="",
    )
    off = env.configure("off")
    assert off["mode"] == "off"
    assert off["provider"] == ""
    assert off["model"] == ""
    shadow = env.configure("shadow", revision=off["revision"])
    assert shadow["mode"] == "shadow"
    run = env.run(revision=shadow["revision"])
    assert run.status_code == 200, run.text
    assert env.provider_calls == []


def test_background_mode_requires_ready_provider_credential(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    env.settings["value"] = _settings(api_key="")
    response = env.client.post(
        "/api/app/agent/memory/review/config",
        json={"mode": "bounded_background", "scope": "user", "expectedRevision": 0},
    )
    assert response.status_code == 400
    readback = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert readback["mode"] == "off"
    assert readback["lastRun"] is None


def test_provider_pricing_is_paired_and_persisted_with_actual_usage(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))

    unpaired = env.client.post(
        "/api/app/agent/memory/review/config",
        json={
            "mode": "suggest_only",
            "scope": "user",
            "inputCostPerMillionUsd": 2.0,
            "outputCostPerMillionUsd": 0.0,
            "expectedRevision": 0,
        },
    )
    assert unpaired.status_code == 400
    assert env.provider_calls == []

    below_worst_case = env.client.post(
        "/api/app/agent/memory/review/config",
        json={
            "mode": "suggest_only",
            "scope": "user",
            "costCapUsd": 0.1,
            "inputCostPerMillionUsd": 2.0,
            "outputCostPerMillionUsd": 8.0,
            "expectedRevision": 0,
        },
    )
    assert below_worst_case.status_code == 400
    assert env.provider_calls == []

    configured = env.configure(
        "suggest_only",
        cost_cap_usd=0.4,
        input_cost_per_million_usd=2.0,
        output_cost_per_million_usd=8.0,
    )
    assert configured["inputCostPerMillionUsd"] == 2.0
    assert configured["outputCostPerMillionUsd"] == 8.0
    result = env.run(revision=configured["revision"])
    assert result.status_code == 200, result.text
    snapshot = result.json()
    assert snapshot["lastRun"]["budget"]["inputCostPerMillionUsd"] == 2.0
    assert snapshot["lastRun"]["budget"]["outputCostPerMillionUsd"] == 8.0
    assert snapshot["lastRun"]["usage"]["costUsd"] == pytest.approx(0.000074)
    assert snapshot["candidates"][0]["usage"]["costUsd"] == pytest.approx(0.000074)


def test_retry_usage_is_bounded_by_the_run_cap_and_records_attempt_evidence(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    calls = {"count": 0}

    class RetryableProviderError(RuntimeError):
        status_code = 500

    def retry_then_succeed(
        _settings: Settings,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls["count"] += 1
        if calls["count"] < 3:
            raise RetryableProviderError("bounded provider failure")
        return _candidate_result(payload)

    env.provider["call"] = retry_then_succeed
    configured = env.configure(
        "suggest_only",
        cost_cap_usd=0.4,
        input_cost_per_million_usd=2.0,
        output_cost_per_million_usd=8.0,
    )
    result = env.run(revision=configured["revision"])
    assert result.status_code == 200, result.text
    usage = result.json()["lastRun"]["usage"]
    assert calls["count"] == 3
    assert usage["attempts"] == 3
    assert usage["costAccounting"] == "bounded_retry"
    assert usage["costUpperBoundUsd"] <= 0.4
    assert result.json()["lastRun"]["attempt"] == 3


def test_monetary_cap_fails_closed_when_provider_usage_is_incomplete(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    env.provider["call"] = lambda _settings, payload, **_kwargs: {
        **_candidate_result(payload),
        "usage": {},
    }
    configured = env.configure(
        "suggest_only",
        cost_cap_usd=0.5,
        input_cost_per_million_usd=2.0,
        output_cost_per_million_usd=8.0,
    )
    result = env.run(revision=configured["revision"])
    assert result.status_code in {502, 503}
    assert len(env.provider_calls) == 1
    readback = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert readback["candidates"] == []
    assert readback["lastRun"]["status"] == "failed"


def test_suggest_accept_is_the_only_path_into_memory_and_runtime_prompt(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    configured = env.configure("suggest_only")
    result = env.run(revision=configured["revision"])
    assert result.status_code == 200, result.text
    snapshot = result.json()
    assert len(env.provider_calls) == 1
    assert len(snapshot["candidates"]) == 1
    candidate = snapshot["candidates"][0]
    assert "sourceReferences" not in candidate
    assert "scopeKey" not in candidate

    before = env.client.get("/api/app/agent/memory", params={"scope": "user"}).json()
    before_observe = dashboard_server.AGENT_GATEWAY.runtime_observe()
    before_prompt = dashboard_server.AGENT_GATEWAY._message_with_runtime_context("continue", before_observe)  # noqa: SLF001
    assert before["count"] == 0
    assert candidate["proposedText"] not in before_prompt

    accepted = env.client.post(
        f"/api/app/agent/memory/review/candidates/{candidate['candidateId']}/accept",
        json={"expectedRevision": snapshot["revision"]},
    )
    assert accepted.status_code == 200, accepted.text
    listed = env.client.get("/api/app/agent/memory", params={"scope": "user"}).json()
    assert listed["count"] == 1
    assert listed["memories"][0]["source"] == "consolidation_review"
    assert listed["memories"][0]["text"] == candidate["proposedText"]
    observe = dashboard_server.AGENT_GATEWAY.runtime_observe()
    prompt = dashboard_server.AGENT_GATEWAY._message_with_runtime_context("continue", observe)  # noqa: SLF001
    assert candidate["proposedText"] in prompt


@pytest.mark.parametrize("legacy_action", ["delete", "clear"])
def test_legacy_memory_removal_immediately_reconciles_review_state(
    legacy_action: str,
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources[:] = [_source(scope)]
    configured = env.configure("suggest_only")
    proposed = env.run(revision=configured["revision"]).json()
    candidate = proposed["candidates"][0]
    accepted_response = env.client.post(
        f"/api/app/agent/memory/review/candidates/{candidate['candidateId']}/accept",
        json={"expectedRevision": proposed["revision"]},
    )
    assert accepted_response.status_code == 200, accepted_response.text
    accepted = accepted_response.json()
    assert accepted["candidates"][0]["state"] == "accepted"
    memories = env.client.get("/api/app/agent/memory", params={"scope": "user"}).json()["memories"]
    assert len(memories) == 1

    if legacy_action == "delete":
        response = env.client.request(
            "DELETE",
            f"/api/app/agent/memory/{memories[0]['memoryId']}",
            json={"reason": "explicit user removal"},
        )
    else:
        response = env.client.post(
            "/api/app/agent/memory/clear",
            json={"scope": "user", "reason": "explicit user clear"},
        )
    assert response.status_code == 200, response.text
    readback = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert readback["candidates"][0]["state"] != "accepted"
    assert any(event_type == "agentMemoryReview" for event_type, _payload in env.events)


@pytest.mark.parametrize("action", ["reject", "defer"])
def test_reject_and_defer_never_enter_memory(
    action: str,
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    configured = env.configure("suggest_only")
    snapshot = env.run(revision=configured["revision"]).json()
    candidate = snapshot["candidates"][0]
    response = env.client.post(
        f"/api/app/agent/memory/review/candidates/{candidate['candidateId']}/{action}",
        json={"expectedRevision": snapshot["revision"]},
    )
    assert response.status_code == 200, response.text
    assert env.client.get("/api/app/agent/memory", params={"scope": "user"}).json()["count"] == 0
    prompt = dashboard_server.AGENT_GATEWAY._message_with_runtime_context(  # noqa: SLF001
        "continue",
        dashboard_server.AGENT_GATEWAY.runtime_observe(),
    )
    assert candidate["proposedText"] not in prompt


def _make_project_candidate(env: DashboardMemoryReviewHarness) -> tuple[dict[str, Any], dict[str, Any]]:
    scope, canonical = dashboard_server.resolve_memory_review_request_scope("project", str(env.project))
    env.sources[:] = [_source(scope, source_id="project-chat")]
    configured = env.configure(
        "suggest_only",
        scope="project",
        project_root=canonical,
    )
    result = env.run(
        revision=configured["revision"],
        scope="project",
        project_root=canonical,
    )
    assert result.status_code == 200, result.text
    snapshot = result.json()
    return snapshot, snapshot["candidates"][0]


@pytest.mark.parametrize("action", ["accept", "reject", "defer", "erase"])
def test_every_project_candidate_action_requires_exact_root(
    action: str,
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    snapshot, candidate = _make_project_candidate(env)
    route = f"/api/app/agent/memory/review/candidates/{candidate['candidateId']}/{action}"

    missing = env.client.post(route, json={"expectedRevision": snapshot["revision"]})
    assert missing.status_code == 400
    wrong = env.client.post(
        route,
        json={
            "expectedRevision": snapshot["revision"],
            "projectRoot": str(env.other_project),
        },
    )
    assert wrong.status_code == 400
    unchanged = env.client.get(
        "/api/app/agent/memory/review",
        params={"scope": "project", "projectRoot": str(env.project)},
    ).json()
    assert unchanged["revision"] == snapshot["revision"]

    accepted = env.client.post(
        route,
        json={
            "expectedRevision": snapshot["revision"],
            "projectRoot": str(env.project),
        },
    )
    assert accepted.status_code == 200, accepted.text


def test_project_erase_retry_after_candidate_rewrite_keeps_exact_scope(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    proposed, candidate = _make_project_candidate(env)
    route = f"/api/app/agent/memory/review/candidates/{candidate['candidateId']}"
    accepted_response = env.client.post(
        route + "/accept",
        json={
            "expectedRevision": proposed["revision"],
            "projectRoot": str(env.project),
        },
    )
    assert accepted_response.status_code == 200, accepted_response.text
    accepted = accepted_response.json()
    original_finish = env.host.service.review_store.finish_erase
    failed_once = False

    def fail_after_candidate_rewrite(candidate_id: str) -> dict[str, Any]:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise OSError("simulated finish marker failure")
        return original_finish(candidate_id)

    monkeypatch.setattr(
        env.host.service.review_store,
        "finish_erase",
        fail_after_candidate_rewrite,
    )
    first = env.client.post(
        route + "/erase",
        json={
            "expectedRevision": accepted["revision"],
            "projectRoot": str(env.project),
        },
    )
    assert first.status_code == 500
    assert env.host.service.review_store.get(candidate["candidateId"]) is None
    assert env.host.service.review_store.get_erase_intent(candidate["candidateId"]) is not None

    wrong_scope = env.client.post(
        route + "/erase",
        json={
            "expectedRevision": accepted["revision"],
            "projectRoot": str(env.other_project),
        },
    )
    assert wrong_scope.status_code == 400
    retried = env.client.post(
        route + "/erase",
        json={
            "expectedRevision": accepted["revision"],
            "projectRoot": str(env.project),
        },
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["candidates"] == []
    wrong_after_completion = env.client.post(
        route + "/erase",
        json={
            "expectedRevision": retried.json()["revision"],
            "projectRoot": str(env.other_project),
        },
    )
    assert wrong_after_completion.status_code == 400
    idempotent_retry = env.client.post(
        route + "/erase",
        json={
            "expectedRevision": retried.json()["revision"],
            "projectRoot": str(env.project),
        },
    )
    assert idempotent_retry.status_code == 200, idempotent_retry.text
    assert idempotent_retry.json()["revision"] == retried.json()["revision"]
    memories = env.client.get(
        "/api/app/agent/memory",
        params={"projectRoot": str(env.project)},
    ).json()
    assert memories["count"] == 0


def test_project_undo_also_requires_exact_root(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    snapshot, candidate = _make_project_candidate(env)
    route = f"/api/app/agent/memory/review/candidates/{candidate['candidateId']}"
    accepted = env.client.post(
        route + "/accept",
        json={"expectedRevision": snapshot["revision"], "projectRoot": str(env.project)},
    )
    assert accepted.status_code == 200, accepted.text
    accepted_snapshot = accepted.json()

    missing = env.client.post(
        route + "/undo",
        json={"expectedRevision": accepted_snapshot["revision"]},
    )
    assert missing.status_code == 400
    wrong = env.client.post(
        route + "/undo",
        json={
            "expectedRevision": accepted_snapshot["revision"],
            "projectRoot": str(env.other_project),
        },
    )
    assert wrong.status_code == 400
    undone = env.client.post(
        route + "/undo",
        json={
            "expectedRevision": accepted_snapshot["revision"],
            "projectRoot": str(env.project),
        },
    )
    assert undone.status_code == 200, undone.text


def test_memory_review_events_are_signal_only(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    configured = env.configure("suggest_only")
    env.run(revision=configured["revision"])
    review_events = [payload for event_type, payload in env.events if event_type == "agentMemoryReview"]
    assert review_events
    assert all(payload == {"changed": True} for payload in review_events)
    serialized = json.dumps(review_events, ensure_ascii=False)
    for private_key in ("candidate", "source", "revision", "proposedText"):
        assert private_key not in serialized


def test_provider_secret_and_machine_path_never_reach_snapshot_or_audit(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    unsafe_path = "C:\\Users\\Private\\hidden-model"
    env.settings["value"] = _settings(model=unsafe_path, api_key="credential-must-not-persist")
    rejected = env.client.post(
        "/api/app/agent/memory/review/config",
        json={"mode": "suggest_only", "scope": "user", "expectedRevision": 0},
    )
    assert rejected.status_code == 400
    snapshot = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    serialized = json.dumps(snapshot, ensure_ascii=False) + env.review_files_text()
    assert "credential-must-not-persist" not in serialized
    assert unsafe_path not in serialized
    assert snapshot["model"] == ""
    assert snapshot["provider"] == ""

    env.settings["value"] = _settings(api_key="credential-must-not-persist")
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    raw_token = "token=dashboard-private-token-114514"
    raw_source_path = "C:\\Users\\Private\\source.txt"
    env.sources.append(
        _source(
            scope,
            text=f"Please remember concise answers; {raw_token} is stored at {raw_source_path}",
        )
    )
    configured = env.configure("suggest_only")
    completed = env.run(revision=configured["revision"])
    assert completed.status_code == 200, completed.text
    provider_payload = json.dumps(env.provider_calls[0]["payload"], ensure_ascii=False)
    durable_and_api = (
        json.dumps(completed.json(), ensure_ascii=False)
        + env.review_files_text()
    )
    for private_value in ("dashboard-private-token-114514", raw_source_path, "credential-must-not-persist"):
        assert private_value not in provider_payload
        assert private_value not in durable_and_api


def test_unreachable_preflight_and_timeout_never_commit_candidates(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))

    async def no_wait(_seconds: float) -> None:
        return None

    env.settings["value"] = _settings(
        provider="ollama",
        model="local-review",
        api_key="",
        base_url="http://127.0.0.1:11434/v1",
    )
    budget = RuntimeLaneBudget()
    unreachable_runtime = MemoryReviewRuntimeCoordinator(
        lane_budget=budget,
        preflight=ProviderPreflightCache(lambda _provider, _url: False),
        sleep=no_wait,
        provider_timeout_seconds=0.1,
    )
    monkeypatch.setattr(dashboard_server, "MEMORY_REVIEW_RUNTIME", unreachable_runtime)
    if env.host is not None:
        monkeypatch.setattr(env.host, "runtime", unreachable_runtime)
    configured = env.configure("suggest_only")
    unreachable = env.run(revision=configured["revision"])
    assert unreachable.status_code == 503
    assert env.provider_calls == []
    snapshot = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert snapshot["candidates"] == []

    # Reset the isolated store and exercise the watchdog/late-result boundary.
    dashboard_server.AGENT_GATEWAY.configure_paths(
        env.root / "gateway-timeout.json",
        env.root / "gateway-timeout-audit",
    )
    env.settings["value"] = _settings()
    slow_budget = RuntimeLaneBudget()
    timeout_runtime = MemoryReviewRuntimeCoordinator(
        lane_budget=slow_budget,
        preflight=ProviderPreflightCache(lambda _provider, _url: True),
        sleep=no_wait,
        provider_timeout_seconds=0.01,
    )
    monkeypatch.setattr(dashboard_server, "MEMORY_REVIEW_RUNTIME", timeout_runtime)
    if env.host is not None:
        monkeypatch.setattr(env.host, "runtime", timeout_runtime)

    def slow_provider(_settings: Settings, payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        time.sleep(0.05)
        return _candidate_result(payload, text="A late candidate must not commit.")

    env.provider["call"] = slow_provider
    configured = env.configure("suggest_only")
    timed_out = env.run(revision=configured["revision"])
    assert timed_out.status_code == 503
    time.sleep(0.08)
    snapshot = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert snapshot["candidates"] == []


def test_schema_failure_is_non_consuming_and_cannot_hot_loop(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))

    def invalid_schema(
        _settings: Settings,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = _candidate_result(payload)
        result["candidates"][0]["providerOwnedConflict"] = "not-authoritative"
        return result

    env.provider["call"] = invalid_schema
    configured = env.configure("bounded_background")
    response = env.run(revision=configured["revision"])
    assert response.status_code == 502
    snapshot = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert snapshot["candidates"] == []
    assert snapshot["lastRun"]["status"] == "skipped"
    assert snapshot["lastRun"]["nonConsuming"] is True
    assert snapshot["lastRun"]["deferredReason"] == "schema"
    assert snapshot["lastRun"]["nextRetryAt"]
    due = env.host.service.due_background() if env.host is not None else {}
    assert due["due"] is False
    assert due["reason"] == "deferred_pending"


def test_source_edit_during_provider_call_rejects_stale_output(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources[:] = [_source(scope, revision="1", text="Please remember concise replies.")]

    def edit_source_before_return(
        _settings: Settings,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        result = _candidate_result(payload, text="I prefer concise replies.")
        env.sources[:] = [_source(scope, revision="2", text="Please remember detailed replies.")]
        return result

    env.provider["call"] = edit_source_before_return
    configured = env.configure("suggest_only")
    response = env.run(revision=configured["revision"])
    assert response.status_code == 503
    snapshot = env.client.get("/api/app/agent/memory/review", params={"scope": "user"}).json()
    assert snapshot["candidates"] == []
    assert snapshot["runStatus"]["state"] == "failed"


def test_source_commit_critical_section_blocks_every_source_writer_until_commit(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources[:] = [_source(scope)]
    configured = env.configure("suggest_only")
    commit_entered = threading.Event()
    release_commit = threading.Event()
    original_finish = env.host.service.finish_provider_run
    acquired: set[str] = set()
    writer_threads: list[threading.Thread] = []

    def blocked_finish(*args: Any, **kwargs: Any) -> dict[str, Any]:
        commit_entered.set()
        release_commit.wait(timeout=2)
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(env.host.service, "finish_provider_run", blocked_finish)

    def source_writer(name: str, lock: Any) -> None:
        with lock:
            acquired.add(name)

    async def scenario() -> None:
        task = asyncio.create_task(
            env.host.execute(
                scope_name="user",
                project_root="",
                expected_revision=configured["revision"],
                lane="interactive",
            )
        )
        assert await asyncio.to_thread(commit_entered.wait, 1)
        for name, lock in (
            ("gateway", dashboard_server.AGENT_GATEWAY._lock),
            ("chat", dashboard_server.CHAT_TRANSCRIPTS_LOCK),
            ("task", dashboard_server.SUB_AGENT_REGISTRY._lock),
            ("audit", dashboard_server.AGENT_GATEWAY._audit_append_lock),
        ):
            writer = threading.Thread(target=source_writer, args=(name, lock), daemon=True)
            writer_threads.append(writer)
            writer.start()
        await asyncio.sleep(0.05)
        assert acquired == set()
        release_commit.set()
        result = await asyncio.wait_for(task, timeout=2)
        assert result["runStatus"]["state"] == "completed"
        for writer in writer_threads:
            writer.join(timeout=1)
        assert acquired == {"gateway", "chat", "task", "audit"}

    asyncio.run(scenario())


def test_cancelled_provider_run_is_immediately_terminal(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources[:] = [_source(scope)]
    configured = env.configure("suggest_only")
    started = threading.Event()
    release = threading.Event()

    def blocked_provider(
        _settings: Settings,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        started.set()
        release.wait(timeout=2)
        return _candidate_result(payload)

    env.provider["call"] = blocked_provider

    async def scenario() -> None:
        task = asyncio.create_task(
            env.host.execute(
                scope_name="user",
                project_root="",
                expected_revision=configured["revision"],
                lane="interactive",
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        snapshot = env.host.snapshot(requested_project_root="")
        assert snapshot["runStatus"]["state"] == "cancelled"
        assert snapshot["candidates"] == []
        release.set()
        for _ in range(50):
            if env.host.runtime.snapshot()["drainingWorkers"] == 0:
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())


def test_cancellation_after_durable_commit_starts_cannot_race_terminal_state(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources[:] = [_source(scope)]
    configured = env.configure("suggest_only")
    commit_started = threading.Event()
    release_commit = threading.Event()
    original_finish = env.host.service.finish_provider_run

    def blocked_finish(*args: Any, **kwargs: Any) -> dict[str, Any]:
        commit_started.set()
        release_commit.wait(timeout=2)
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(env.host.service, "finish_provider_run", blocked_finish)

    async def scenario() -> None:
        task = asyncio.create_task(
            env.host.execute(
                scope_name="user",
                project_root="",
                expected_revision=configured["revision"],
                lane="interactive",
            )
        )
        assert await asyncio.to_thread(commit_started.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert task.done() is False
        release_commit.set()
        result = await task
        assert result["runStatus"]["state"] == "completed"
        assert len(result["candidates"]) == 1

    asyncio.run(scenario())


def test_explicit_cancel_drains_late_provider_without_candidate_commit(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources[:] = [_source(scope)]
    configured = env.configure("suggest_only")
    started = threading.Event()
    release = threading.Event()

    def blocked_provider(
        _settings: Settings,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        started.set()
        release.wait(timeout=2)
        return _candidate_result(payload)

    env.provider["call"] = blocked_provider

    async def scenario() -> None:
        task = asyncio.create_task(
            env.host.execute(
                scope_name="user",
                project_root="",
                expected_revision=configured["revision"],
                lane="interactive",
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        running = env.host.snapshot(requested_project_root="")
        run_id = running["lastRun"]["runId"]
        cancelled = await env.host.cancel(MemoryReviewCancelRequest(runId=run_id))
        assert cancelled["lastRun"]["status"] == "cancelled"
        assert cancelled["candidates"] == []
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        await asyncio.sleep(0.05)
        final = env.host.snapshot(requested_project_root="")
        assert final["lastRun"]["status"] == "cancelled"
        assert final["candidates"] == []

    asyncio.run(scenario())


def test_config_change_while_provider_is_in_flight_cannot_commit_candidates(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources[:] = [_source(scope)]
    configured = env.configure("suggest_only")
    started = threading.Event()
    release = threading.Event()

    def blocked_provider(
        _settings: Settings,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        started.set()
        release.wait(timeout=2)
        return _candidate_result(payload)

    env.provider["call"] = blocked_provider

    async def scenario() -> None:
        task = asyncio.create_task(
            env.host.execute(
                scope_name="user",
                project_root="",
                expected_revision=configured["revision"],
                lane="interactive",
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        current = env.host.snapshot(requested_project_root="")
        disabled = await env.host.update_config(
            MemoryReviewConfigRequest(
                mode="off",
                scope="user",
                expectedRevision=current["revision"],
            )
        )
        assert disabled["mode"] == "off"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        final = env.host.snapshot(requested_project_root="")
        assert final["mode"] == "off"
        assert final["candidates"] == []
        assert final["lastRun"]["status"] == "cancelled"

    asyncio.run(scenario())


def test_switching_to_off_during_retry_prevents_every_later_provider_call(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources[:] = [_source(scope)]
    configured = env.configure("suggest_only")
    retry_waiting = threading.Event()
    release_retry = threading.Event()
    calls = {"count": 0}

    class RetryableProviderError(RuntimeError):
        status_code = 500

    def fail_first_attempt(
        _settings: Settings,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        calls["count"] += 1
        raise RetryableProviderError("bounded provider failure")

    async def blocked_retry(_seconds: float) -> None:
        retry_waiting.set()
        await asyncio.to_thread(release_retry.wait, 2)

    env.provider["call"] = fail_first_attempt
    env.host.runtime._sleep = blocked_retry  # noqa: SLF001

    async def scenario() -> None:
        task = asyncio.create_task(
            env.host.execute(
                scope_name="user",
                project_root="",
                expected_revision=configured["revision"],
                lane="interactive",
            )
        )
        assert await asyncio.to_thread(retry_waiting.wait, 1)
        current = env.host.snapshot(requested_project_root="")
        disabled = await env.host.update_config(
            MemoryReviewConfigRequest(
                mode="off",
                scope="user",
                expectedRevision=current["revision"],
            )
        )
        assert disabled["mode"] == "off"
        release_retry.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls["count"] == 1
        final = env.host.snapshot(requested_project_root="")
        assert final["mode"] == "off"
        assert final["candidates"] == []

    asyncio.run(scenario())


def test_project_read_lease_wraps_only_scan_and_commit_not_provider_wait(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope(
        "project",
        str(env.project),
    )
    env.sources[:] = [_source(scope)]
    configured = env.configure(
        "bounded_background",
        scope="project",
        project_root=str(env.project),
    )
    provider_started = threading.Event()
    provider_release = threading.Event()
    events: list[tuple[str, str]] = []
    acquire = dashboard_server.AGENT_GATEWAY.try_acquire_background_project_read
    release = dashboard_server.AGENT_GATEWAY.release_background_project_read

    def tracked_acquire(token: str) -> bool:
        events.append(("acquire", token))
        return acquire(token)

    def tracked_release(token: str) -> bool:
        events.append(("release", token))
        return release(token)

    monkeypatch.setattr(env.host, "_acquire_background_lease", tracked_acquire)
    monkeypatch.setattr(env.host, "_release_background_lease", tracked_release)

    def blocked_provider(
        _settings: Settings,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        provider_started.set()
        provider_release.wait(timeout=2)
        return _candidate_result(payload)

    env.provider["call"] = blocked_provider

    async def scenario() -> None:
        generation = env.host._idle_gate.try_acquire(lambda: "", lambda: None)  # noqa: SLF001
        assert generation is not None
        try:
            task = asyncio.create_task(
                env.host.execute(
                    scope_name="project",
                    project_root=str(env.project),
                    expected_revision=configured["revision"],
                    lane="background",
                    background_generation=generation,
                )
            )
            assert await asyncio.to_thread(provider_started.wait, 1)
            assert [event for event, _token in events] == ["acquire", "release"]
            assert dashboard_server.AGENT_GATEWAY._background_project_read_leases == set()
            provider_release.set()
            result = await task
            assert result["runStatus"]["state"] == "completed"
            assert [event for event, _token in events] == ["acquire", "release", "acquire", "release"]
            assert events[0][1].startswith("memory-review-scan:")
            assert events[2][1].startswith("memory-review-commit:")
            assert dashboard_server.AGENT_GATEWAY._background_project_read_leases == set()
        finally:
            env.host._idle_gate.release(generation)  # noqa: SLF001

    asyncio.run(scenario())


def test_paid_project_commit_lease_failure_preserves_usage_and_persists_no_candidate(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope(
        "project",
        str(env.project),
    )
    env.sources[:] = [_source(scope)]
    configured = env.configure(
        "bounded_background",
        scope="project",
        project_root=str(env.project),
    )
    calls = {"acquire": 0}

    def acquire_once(_token: str) -> bool:
        calls["acquire"] += 1
        return calls["acquire"] == 1

    monkeypatch.setattr(env.host, "_acquire_background_lease", acquire_once)
    monkeypatch.setattr(env.host, "_release_background_lease", lambda _token: True)

    async def scenario() -> None:
        generation = env.host._idle_gate.try_acquire(lambda: "", lambda: None)  # noqa: SLF001
        assert generation is not None
        try:
            with pytest.raises(Exception) as failure:
                await env.host.execute(
                    scope_name="project",
                    project_root=str(env.project),
                    expected_revision=configured["revision"],
                    lane="background",
                    background_generation=generation,
                )
            assert getattr(failure.value, "status_code", 0) == 409
            final = env.host.snapshot(requested_project_root=str(env.project))
            assert final["candidates"] == []
            assert final["lastRun"]["status"] == "failed"
            assert final["lastRun"]["nonConsuming"] is False
            assert final["lastRun"]["deferredReason"] == ""
            assert final["lastRun"]["failureClass"] == "capacity"
            assert final["lastRun"]["usage"]["inputTokens"] == 17
            assert final["lastRun"]["usage"]["outputTokens"] == 5
            assert final["lastRun"]["usage"]["attempts"] == 1
            audit_rows = [
                json.loads(line)
                for line in (
                    env.root / "gateway-audit" / "memory-review" / "memory-review-audit.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]
            finished = [row for row in audit_rows if row.get("event") == "review_run_finished"][-1]
            assert finished["failureClass"] == "capacity"
            assert finished["usage"]["inputTokens"] == 17
            assert finished["usage"]["outputTokens"] == 5
        finally:
            env.host._idle_gate.release(generation)  # noqa: SLF001

    asyncio.run(scenario())


def test_missing_project_exposes_only_erase_handle_and_permanent_erase_still_works(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope(
        "project",
        str(env.project),
    )
    env.sources[:] = [_source(scope, text="Project-only candidate prose must stay hidden.")]
    configured = env.configure(
        "suggest_only",
        scope="project",
        project_root=str(env.project),
    )
    generated = env.run(
        revision=configured["revision"],
        scope="project",
        project_root=str(env.project),
    )
    assert generated.status_code == 200, generated.text
    candidate_id = generated.json()["candidates"][0]["candidateId"]
    missing_live_root = env.client.get(
        "/api/app/agent/memory/review",
        params={"scope": "project"},
    )
    assert missing_live_root.status_code == 400
    live_erase_without_root = env.client.post(
        f"/api/app/agent/memory/review/candidates/{candidate_id}/erase",
        json={"expectedRevision": generated.json()["revision"]},
    )
    assert live_erase_without_root.status_code == 400
    env.project.rmdir()

    wrong_missing = env.client.get(
        "/api/app/agent/memory/review",
        params={
            "scope": "project",
            "projectRoot": str(env.unauthorized_project / "different-missing-root"),
        },
    )
    assert wrong_missing.status_code == 400
    wrong_live = env.client.get(
        "/api/app/agent/memory/review",
        params={"scope": "project", "projectRoot": str(env.unauthorized_project)},
    )
    assert wrong_live.status_code == 400

    unavailable = env.client.get(
        "/api/app/agent/memory/review",
        params={"scope": "project", "projectRoot": str(env.project)},
    )
    assert unavailable.status_code == 200, unavailable.text
    snapshot = unavailable.json()
    assert snapshot["configuredProjectMatches"] is False
    assert snapshot["unreadCount"] == 0
    assert snapshot["candidates"] == [
        {
            "candidateId": candidate_id,
            "scope": "project",
            "kind": "unavailable",
            "proposedText": "",
            "state": "proposed",
            "policyVersion": snapshot["policyVersion"],
            "evidenceCount": 0,
            "unread": False,
            "eraseOnly": True,
            "conflictCount": 0,
            "conflictExplanation": "none",
        }
    ]
    assert "Project-only candidate prose" not in unavailable.text
    implicit_recovery = env.client.get(
        "/api/app/agent/memory/review",
        params={"scope": "project"},
    )
    assert implicit_recovery.status_code == 200
    assert implicit_recovery.json()["candidates"] == snapshot["candidates"]

    reject = env.client.post(
        f"/api/app/agent/memory/review/candidates/{candidate_id}/reject",
        json={"expectedRevision": snapshot["revision"]},
    )
    assert reject.status_code == 400
    erased = env.client.post(
        f"/api/app/agent/memory/review/candidates/{candidate_id}/erase",
        json={
            "expectedRevision": snapshot["revision"],
            "projectRoot": str(env.project),
        },
    )
    assert erased.status_code == 200, erased.text
    assert erased.json()["candidates"] == []
    assert "Project-only candidate prose" not in env.review_files_text()


def test_background_due_scope_and_blocker_are_backend_authoritative(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    host = env.host
    configured = env.configure(
        "bounded_background",
        scope="project",
        project_root=str(env.project),
    )
    due = host.service.due_background(
        authorized_project_roots=[str(env.project)]
    )
    assert due["due"] is True
    assert due["scope"] == "project"
    assert Path(due["projectRoot"]) == env.project.resolve()
    persisted_scope = host.configured_background_scope()
    assert persisted_scope == ("project", str(env.project.resolve()), configured["revision"])

    assert asyncio.run(host.schedule_due_background(lambda: "pending_approval")) is False

    calls: list[dict[str, Any]] = []

    async def fake_execute(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(host, "execute", fake_execute)

    async def schedule() -> bool:
        scheduled = await host.schedule_due_background(lambda: "")
        await asyncio.sleep(0)
        if host._background_task is not None:  # noqa: SLF001 - focused scheduler contract.
            await host._background_task  # noqa: SLF001
        return scheduled

    assert asyncio.run(schedule()) is True
    assert len(calls) == 1
    assert calls[0]["scope_name"] == "project"
    assert calls[0]["project_root"] == str(env.project.resolve())
    assert calls[0]["expected_revision"] == configured["revision"]
    assert calls[0]["lane"] == "background"
    assert isinstance(calls[0]["background_generation"], int)
    assert calls[0]["background_generation"] > 0
    assert dashboard_server.AGENT_GATEWAY._background_project_read_leases == set()


def test_background_schedule_epoch_closes_blocker_to_task_race(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    env.configure("bounded_background", scope="user")
    execute_calls = 0

    async def fake_execute(**_kwargs: Any) -> dict[str, Any]:
        nonlocal execute_calls
        execute_calls += 1
        return {"ok": True}

    monkeypatch.setattr(env.host, "execute", fake_execute)
    blocker_entered = threading.Event()
    activity_signalled = threading.Event()

    def enter_interactive() -> None:
        assert blocker_entered.wait(timeout=5)
        assert env.host is not None
        budget = env.host.runtime._lane_budget  # noqa: SLF001 - exact shared-lane race contract.
        assert budget.acquire("interactive", "scheduler-race") is True
        activity_signalled.set()

    worker = threading.Thread(target=enter_interactive)
    worker.start()

    def blocker() -> str:
        blocker_entered.set()
        assert activity_signalled.wait(timeout=5)
        return ""

    try:
        assert asyncio.run(env.host.schedule_due_background(blocker)) is False
    finally:
        worker.join(timeout=5)
        env.host.runtime._lane_budget.release("scheduler-race")  # noqa: SLF001
    assert not worker.is_alive()
    assert execute_calls == 0
    assert env.provider_calls == []


def test_interactive_start_revokes_paid_background_before_late_commit(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    configured = env.configure("bounded_background", scope="user")
    before = env.host.service.review_store.snapshot(include_internal=True)
    provider_started = threading.Event()
    provider_release = threading.Event()
    provider_calls = 0

    def late_provider(
        _settings: Settings,
        payload: dict[str, Any],
        _token_cap: int,
    ) -> dict[str, Any]:
        nonlocal provider_calls
        provider_calls += 1
        provider_started.set()
        assert provider_release.wait(timeout=10)
        source = payload["sources"][0]
        return {
            "candidates": [
                {
                    "kind": "preference",
                    "text": "Prefer blue accents.",
                    "sourceIds": [source["sourceId"]],
                    "confidenceFactors": ["explicit_user_intent"],
                }
            ],
            "usage": {"inputTokens": 7, "outputTokens": 3, "costUsd": 0.001},
        }

    monkeypatch.setattr(env.host, "_provider_call", late_provider)

    async def scenario() -> None:
        scheduled = await env.host.schedule_due_background(lambda: "")
        assert scheduled is True
        task = env.host._background_task  # noqa: SLF001
        assert task is not None
        assert await asyncio.to_thread(provider_started.wait, 5)
        budget = env.host.runtime._lane_budget  # noqa: SLF001
        assert budget.acquire("interactive", "foreground-turn") is True
        for _ in range(100):
            if env.host.runtime.snapshot()["drainingWorkers"] == 1:
                break
            await asyncio.sleep(0.01)
        assert env.host.runtime.snapshot()["drainingWorkers"] == 1
        provider_release.set()
        await asyncio.gather(task, return_exceptions=True)
        for _ in range(100):
            if env.host.runtime.snapshot()["drainingWorkers"] == 0:
                break
            await asyncio.sleep(0.01)
        assert env.host.runtime.snapshot()["drainingWorkers"] == 0
        assert budget.release("foreground-turn") is True

    try:
        asyncio.run(scenario())
    finally:
        provider_release.set()

    after = env.host.service.review_store.snapshot(include_internal=True)
    assert provider_calls == 1
    assert after["candidates"] == []
    assert after.get("sourceCursors", {}) == before.get("sourceCursors", {})
    run = after["runs"][-1]
    assert run["status"] == "cancelled"
    assert run["candidateCount"] == 0
    assert run["attempt"] == 1
    assert int(after["revision"]) > int(configured["revision"])


def test_activity_during_durable_run_begin_leaves_no_stuck_running_record(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    env.configure("bounded_background", scope="user")
    begin_committed = threading.Event()
    release_begin = threading.Event()
    original_begin = env.host.service.begin_provider_run

    def blocked_begin(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_begin(*args, **kwargs)
        begin_committed.set()
        assert release_begin.wait(timeout=10)
        return result

    monkeypatch.setattr(env.host.service, "begin_provider_run", blocked_begin)

    async def cancel_during_begin() -> None:
        assert await env.host.schedule_due_background(lambda: "") is True
        task = env.host._background_task  # noqa: SLF001
        assert task is not None
        assert await asyncio.to_thread(begin_committed.wait, 5)
        budget = env.host.runtime._lane_budget  # noqa: SLF001
        assert budget.acquire("interactive", "begin-race") is True
        release_begin.set()
        await asyncio.gather(task, return_exceptions=True)
        assert budget.release("begin-race") is True

    try:
        asyncio.run(cancel_during_begin())
    finally:
        release_begin.set()

    state = env.host.service.review_store.snapshot(include_internal=True)
    assert state["runs"]
    assert all(run["status"] != "running" for run in state["runs"])
    assert state["runs"][-1]["status"] == "cancelled"
    assert state["runs"][-1]["nonConsuming"] is True
    assert state["runs"][-1]["deferredReason"] == "interactive_activity"
    assert env.provider_calls == []

    monkeypatch.setattr(
        env.host.service,
        "due_background",
        lambda **_kwargs: {"due": True},
    )
    rescheduled = threading.Event()

    async def fake_execute(**_kwargs: Any) -> dict[str, Any]:
        rescheduled.set()
        return {"ok": True}

    monkeypatch.setattr(env.host, "execute", fake_execute)

    async def schedule_again() -> None:
        assert await env.host.schedule_due_background(lambda: "") is True
        task = env.host._background_task  # noqa: SLF001
        assert task is not None
        await task

    asyncio.run(schedule_again())
    assert rescheduled.is_set()


def test_background_schedule_is_blocked_while_project_write_is_applying(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    assert env.host is not None
    configured = env.configure("bounded_background", scope="user")
    assert configured["mode"] == "bounded_background"

    class OwnedLease:
        owned = True

    monkeypatch.setattr(dashboard_server, "BACKEND_OWNER_LEASE", OwnedLease())
    monkeypatch.setattr(
        dashboard_server.AGENT_GATEWAY,
        "has_in_flight_project_write",
        lambda: True,
    )
    assert dashboard_server.memory_review_background_blocker() == "active_project_write"
    assert asyncio.run(
        env.host.schedule_due_background(dashboard_server.memory_review_background_blocker)
    ) is False


def test_runtime_snapshot_exposes_only_review_unread_summary(
    memory_review_dashboard: DashboardMemoryReviewHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    configured = env.configure("suggest_only")
    candidate_snapshot = env.run(revision=configured["revision"]).json()
    assert candidate_snapshot["unreadCount"] == 1
    candidate_text = candidate_snapshot["candidates"][0]["proposedText"]

    monkeypatch.setattr(
        dashboard_server,
        "build_workspace_diff_summary",
        lambda *_args, **_kwargs: {"ok": True, "status": "clean", "files": []},
    )
    runtime = env.client.get("/api/app/runtime/snapshot").json()
    assert runtime["memoryReview"] == {
        "revision": candidate_snapshot["revision"],
        "unreadCount": 1,
        "runStatus": "completed",
        "needsAttention": False,
        "failureClass": "",
    }
    serialized = json.dumps(runtime["memoryReview"], ensure_ascii=False)
    assert candidate_text not in serialized
    assert "candidates" not in runtime["memoryReview"]
    assert "sourceReferences" not in serialized


def test_open_inbox_read_mutation_clears_unread_without_changing_candidate_state(
    memory_review_dashboard: DashboardMemoryReviewHarness,
) -> None:
    env = memory_review_dashboard
    scope, _root = dashboard_server.resolve_memory_review_request_scope("user", "")
    env.sources.append(_source(scope))
    configured = env.configure("suggest_only")
    proposed = env.run(revision=configured["revision"]).json()
    candidate = proposed["candidates"][0]
    assert candidate["unread"] is True

    read = env.client.post(
        f"/api/app/agent/memory/review/candidates/{candidate['candidateId']}/read",
        json={"expectedRevision": proposed["revision"]},
    )
    assert read.status_code == 200, read.text
    snapshot = read.json()
    assert snapshot["unreadCount"] == 0
    assert snapshot["candidates"][0]["state"] == "proposed"
    assert snapshot["candidates"][0]["unread"] is False

    runtime = env.client.get("/api/app/runtime/snapshot").json()
    assert runtime["memoryReview"]["unreadCount"] == 0
