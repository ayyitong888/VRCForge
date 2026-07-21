from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from background_goal_runtime import (
    BACKGROUND_CONCURRENCY_LIMIT,
    MAX_WAKE_STAGGER_SECONDS,
    PHASE_TIMEOUT_SECONDS,
    PROVIDER_PREFLIGHT_CACHE_SECONDS,
    PROVIDER_RETRY_BACKOFF_SECONDS,
    QUESTION_REMINDER_SECONDS,
    REPEATED_FAILURE_THRESHOLD,
    TOTAL_CONCURRENCY_LIMIT,
    TOTAL_PROVIDER_ATTEMPTS,
    ProviderFailureDecision,
    ProviderPreflightCache,
    RepeatedFailureGuard,
    RuntimeLaneBudget,
    aggregate_bounded_usage,
    classify_provider_failure,
    classify_runtime_plan_outcome,
    classify_runtime_step_failure,
    deterministic_wake_stagger_seconds,
    retry_backoff_seconds,
)


class FakeClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class StatusError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_policy_constants_are_bounded_and_phase_specific() -> None:
    assert TOTAL_CONCURRENCY_LIMIT == 5
    assert BACKGROUND_CONCURRENCY_LIMIT == 2
    assert PROVIDER_PREFLIGHT_CACHE_SECONDS == 300
    assert TOTAL_PROVIDER_ATTEMPTS == 3
    assert PROVIDER_RETRY_BACKOFF_SECONDS == (60, 120, 300)
    assert QUESTION_REMINDER_SECONDS == 1_800
    assert MAX_WAKE_STAGGER_SECONDS == 45
    assert REPEATED_FAILURE_THRESHOLD == 3
    assert dict(PHASE_TIMEOUT_SECONDS) == {
        "wake": 30,
        "project_lock": 120,
        "provider_call": 300,
        "apply": 900,
        "deliver": 60,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": False, "status": "blocked", "error": "Entrypoint tool is disallowed."},
        {"ok": False, "status": "blocked", "error": "Entrypoint tool is not allowed."},
        {"ok": False, "status": "blocked", "failureClass": "permission_denied"},
    ],
)
def test_runtime_step_permission_failures_are_structurally_denied(payload: dict[str, object]) -> None:
    assert classify_runtime_step_failure(payload) == "permission_denied"


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        ({"nextStep": "done"}, ("completed", "done")),
        ({"nextStep": "context_compaction_required"}, ("parked", "context_compaction_required")),
        ({"nextStep": "await_user_instruction"}, ("parked", "await_user_instruction")),
        ({"nextStep": "paused", "stepLimitReached": True}, ("parked", "step_limit_reached")),
        ({"nextStep": "loop_suppressed"}, ("failed", "loop_suppressed")),
        ({"nextStep": "cancelled"}, ("cancelled", "cancelled")),
    ],
)
def test_runtime_plan_outcome_matrix(plan: dict[str, object], expected: tuple[str, str]) -> None:
    assert classify_runtime_plan_outcome(plan) == expected


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [(1, 60), (2, 120), (3, 300), (4, 300), (500, 300)],
)
def test_retry_backoff_is_one_based_and_capped(attempt: int, expected: int) -> None:
    assert retry_backoff_seconds(attempt) == expected


@pytest.mark.parametrize("attempt", [0, -1, True, 1.0, "1"])
def test_retry_backoff_rejects_invalid_attempts(attempt: object) -> None:
    with pytest.raises(ValueError):
        retry_backoff_seconds(attempt)  # type: ignore[arg-type]


def test_wake_stagger_is_deterministic_and_bounded() -> None:
    scheduled = "2026-07-21T10:00:00Z"
    first = deterministic_wake_stagger_seconds("goal-a", scheduled)
    assert first == deterministic_wake_stagger_seconds("goal-a", scheduled)
    assert 0 <= first <= MAX_WAKE_STAGGER_SECONDS

    offsets = {
        deterministic_wake_stagger_seconds(f"goal-{index}", scheduled)
        for index in range(20)
    }
    assert len(offsets) > 1
    assert all(0 <= value <= MAX_WAKE_STAGGER_SECONDS for value in offsets)


def test_wake_stagger_includes_the_scheduled_occurrence() -> None:
    offsets = {
        deterministic_wake_stagger_seconds("recurring-goal", f"2026-07-{day:02d}T10:00:00Z")
        for day in range(1, 12)
    }
    assert len(offsets) > 1


def test_wake_stagger_normalizes_equivalent_datetimes() -> None:
    local_time = datetime(2026, 7, 21, 19, 0, tzinfo=timezone(timedelta(hours=9)))
    utc_time = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
    assert deterministic_wake_stagger_seconds("goal-a", local_time) == deterministic_wake_stagger_seconds(
        "goal-a", utc_time
    )
    assert deterministic_wake_stagger_seconds("goal-a", utc_time, max_seconds=0) == 0
    assert 0 <= deterministic_wake_stagger_seconds("goal-a", utc_time, max_seconds=7) <= 7


@pytest.mark.parametrize(
    ("goal_id", "scheduled_for", "max_seconds"),
    [("", "2026-07-21", 45), ("goal", "", 45), ("goal", "2026-07-21", -1), ("goal", "2026-07-21", 46)],
)
def test_wake_stagger_rejects_invalid_boundaries(goal_id: str, scheduled_for: str, max_seconds: int) -> None:
    with pytest.raises(ValueError):
        deterministic_wake_stagger_seconds(goal_id, scheduled_for, max_seconds=max_seconds)


def test_lane_budget_enforces_total_and_background_limits() -> None:
    clock = FakeClock()
    budget = RuntimeLaneBudget(clock=clock)

    assert budget.acquire("background", "b-1") is True
    assert budget.acquire("background", "b-2") is True
    assert budget.acquire("background", "b-3") is False
    assert budget.acquire("interactive", "i-1") is True
    assert budget.acquire("interactive", "i-2") is True
    assert budget.acquire("interactive", "i-3") is True
    assert budget.acquire("interactive", "i-4") is False

    assert budget.snapshot() == {
        "total": 5,
        "background": 2,
        "interactive": 3,
        "totalLimit": 5,
        "backgroundLimit": 2,
        "availableTotal": 0,
        "availableBackground": 0,
        "oldestLeaseAgeSeconds": 0.0,
    }

    assert budget.release("b-1") is True
    assert budget.acquire("background", "b-3") is True
    assert budget.release("missing") is False


def test_lane_budget_rejects_duplicate_and_cross_lane_token_reuse() -> None:
    budget = RuntimeLaneBudget()
    assert budget.acquire_result("interactive", "shared") == "acquired"
    assert budget.acquire_result("interactive", "shared") == "duplicate"
    assert budget.acquire_result("background", "shared") == "duplicate"
    assert budget.snapshot()["total"] == 1
    assert budget.release("shared") is True
    assert budget.release("shared") is False


def test_lane_budget_reports_capacity_separately_from_duplicate_ownership() -> None:
    budget = RuntimeLaneBudget(total_limit=1, background_limit=1)
    assert budget.acquire_result("background", "first") == "acquired"
    assert budget.acquire_result("background", "second") == "capacity"


def test_interactive_lane_can_use_idle_total_capacity() -> None:
    budget = RuntimeLaneBudget()
    assert all(budget.acquire("interactive", f"interactive-{index}") for index in range(5))
    assert budget.acquire("background", "background") is False
    assert budget.snapshot()["interactive"] == 5


def test_lane_snapshot_uses_injected_clock_without_exposing_tokens() -> None:
    clock = FakeClock(10.0)
    budget = RuntimeLaneBudget(clock=clock)
    assert budget.acquire("interactive", "private-token")
    clock.advance(12.5)
    snapshot = budget.snapshot()
    assert snapshot["oldestLeaseAgeSeconds"] == 12.5
    assert "private-token" not in str(snapshot)


def test_lane_budget_acquire_is_thread_safe_and_non_waiting() -> None:
    budget = RuntimeLaneBudget()
    tokens = [f"background-{index}" for index in range(50)]
    with ThreadPoolExecutor(max_workers=16) as executor:
        accepted = list(executor.map(lambda token: budget.acquire("background", token), tokens))
    assert sum(accepted) == BACKGROUND_CONCURRENCY_LIMIT
    assert budget.snapshot()["background"] == BACKGROUND_CONCURRENCY_LIMIT


@pytest.mark.parametrize(
    ("total_limit", "background_limit"),
    [(0, 0), (5, -1), (5, 6), (True, 1), (5, True)],
)
def test_lane_budget_rejects_invalid_limits(total_limit: object, background_limit: object) -> None:
    with pytest.raises(ValueError):
        RuntimeLaneBudget(total_limit=total_limit, background_limit=background_limit)  # type: ignore[arg-type]


def test_preflight_cache_normalizes_key_and_expires_at_ttl() -> None:
    clock = FakeClock()
    calls: list[tuple[str, str]] = []

    def probe(provider: str, base_url: str) -> bool:
        calls.append((provider, base_url))
        return True

    cache = ProviderPreflightCache(probe, clock=clock)
    first = cache.check(" Custom ", "HTTP://LOCALHOST:80/v1/")
    assert first.required is True
    assert first.reachable is True
    assert first.cached is False
    assert first.base_url == "http://localhost/v1"

    clock.advance(299)
    cached = cache.check("custom", "http://localhost/v1")
    assert cached.cached is True
    assert len(calls) == 1

    clock.advance(1)
    expired = cache.check("custom", "http://localhost/v1/")
    assert expired.cached is False
    assert len(calls) == 2


def test_preflight_failure_is_cached_and_never_consumes_retry() -> None:
    calls = 0

    def probe(_provider: str, _base_url: str) -> bool:
        nonlocal calls
        calls += 1
        return False

    cache = ProviderPreflightCache(probe)
    first = cache.check("custom", "http://127.0.0.1:9000/v1")
    second = cache.check("custom", "http://127.0.0.1:9000/v1")
    assert first.failure_reason == "provider_unreachable"
    assert first.consumes_retry is False
    assert second.cached is True
    assert second.failure_reason == "provider_unreachable"
    assert calls == 1
    assert first.as_dict()["consumesRetry"] is False


def test_preflight_probe_exception_becomes_bounded_unreachable_result() -> None:
    def probe(_provider: str, _base_url: str) -> bool:
        raise OSError("probe unavailable")

    result = ProviderPreflightCache(probe).check("custom", "https://[::1]:9443/v1")
    assert result.required is True
    assert result.reachable is False
    assert result.failure_reason == "provider_unreachable"
    assert result.base_url == "https://[::1]:9443/v1"


def test_local_provider_without_base_url_uses_loopback_default() -> None:
    calls: list[tuple[str, str]] = []
    cache = ProviderPreflightCache(lambda provider, url: calls.append((provider, url)) or True)
    result = cache.check("local", None)
    assert result.required is True
    assert result.reachable is True
    assert result.base_url == "http://127.0.0.1:11434"
    assert calls == [("local", "http://127.0.0.1:11434")]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://provider.example/v1",
        "ftp://127.0.0.1/data",
        "http://user:secret@127.0.0.1:9000/v1",
        "http://127.0.0.1:9000/v1?token=secret",
        "http://service.internal:9000/v1",
        "http://2130706433:9000/v1",
    ],
)
def test_preflight_never_probes_non_loopback_or_unsafe_urls(base_url: str) -> None:
    calls = 0

    def probe(_provider: str, _url: str) -> bool:
        nonlocal calls
        calls += 1
        return True

    result = ProviderPreflightCache(probe).check("local", base_url)
    assert result.required is False
    assert result.reachable is True
    assert result.base_url == ""
    assert calls == 0


def test_preflight_accepts_mapping_probe_contract_and_clear() -> None:
    calls = 0

    def probe(_provider: str, _url: str) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    cache = ProviderPreflightCache(probe)
    assert cache.check("custom", "http://localhost:8080").reachable is True
    assert cache.check("custom", "http://localhost:8080").cached is True
    cache.clear()
    assert cache.check("custom", "http://localhost:8080").cached is False
    assert calls == 2


@pytest.mark.parametrize(
    ("error", "status_code", "expected"),
    [
        (RuntimeError("invalid api key"), None, ProviderFailureDecision("auth", False)),
        (RuntimeError("credits exhausted"), None, ProviderFailureDecision("credit", False)),
        (RuntimeError("quota was exhausted"), None, ProviderFailureDecision("credit", False)),
        (RuntimeError("response schema mismatch"), None, ProviderFailureDecision("schema", False)),
        (RuntimeError("invalid request"), None, ProviderFailureDecision("invalid_request", False)),
        (ConnectionError("connection reset"), None, ProviderFailureDecision("network", True)),
        (RuntimeError("too many requests"), 429, ProviderFailureDecision("rate_limit", True)),
        (TimeoutError("deadline"), None, ProviderFailureDecision("timeout", True)),
        (RuntimeError("gateway timeout"), 504, ProviderFailureDecision("timeout", True)),
        (RuntimeError("service unavailable"), 503, ProviderFailureDecision("server_error", True)),
        (RuntimeError("HTTP 429."), None, ProviderFailureDecision("rate_limit", True)),
        (RuntimeError("HTTP 503."), None, ProviderFailureDecision("server_error", True)),
        (RuntimeError("unrecognized provider failure"), None, ProviderFailureDecision("unknown", False)),
        (asyncio.CancelledError(), None, ProviderFailureDecision("cancelled", False)),
    ],
)
def test_provider_failure_classification_matrix(
    error: BaseException,
    status_code: int | None,
    expected: ProviderFailureDecision,
) -> None:
    assert classify_provider_failure(error, status_code=status_code) == expected


def test_provider_failure_reads_status_from_exception_and_prefers_schema_detail() -> None:
    assert classify_provider_failure(StatusError("request rejected", 401)) == ProviderFailureDecision("auth", False)
    assert classify_provider_failure(StatusError("schema validation failed", 422)) == ProviderFailureDecision(
        "schema", False
    )
    assert classify_provider_failure(StatusError("bad payload", 400)) == ProviderFailureDecision(
        "invalid_request", False
    )


@pytest.mark.parametrize(
    "message",
    [
        "model family-500 returned invalid request",
        "token budget 500 exceeded context length",
    ],
)
def test_provider_failure_does_not_treat_unrelated_three_digit_values_as_http_status(message: str) -> None:
    assert classify_provider_failure(RuntimeError(message)) == ProviderFailureDecision("invalid_request", False)


@pytest.mark.parametrize("message", ["HTTP 503.", "status_code=503", "status code: 503"])
def test_provider_failure_accepts_only_contextual_http_codes(message: str) -> None:
    assert classify_provider_failure(RuntimeError(message)) == ProviderFailureDecision("server_error", True)


def test_repeated_failure_guard_requires_three_identical_consecutive_failures() -> None:
    guard = RepeatedFailureGuard()
    assert guard.record_failure("vrc_read", {"path": "Assets/A", "limit": 2}, "network") is False
    assert guard.record_failure("VRC_READ", {"limit": 2, "path": "Assets/A"}, "NETWORK") is False
    assert guard.record_failure("vrc_read", '{"path":"Assets/A","limit":2}', "network") is True
    assert guard.snapshot()["consecutive"] == 3
    assert guard.snapshot()["suppressed"] is True


def test_repeated_failure_guard_resets_on_arguments_class_or_success() -> None:
    guard = RepeatedFailureGuard()
    assert guard.record_failure("vrc_read", {"path": "Assets/A"}, "network") is False
    assert guard.record_failure("vrc_read", {"path": "Assets/A"}, "network") is False
    assert guard.record_failure("vrc_read", {"path": "Assets/B"}, "network") is False
    assert guard.snapshot()["consecutive"] == 1

    assert guard.record_failure("vrc_read", {"path": "Assets/B"}, "timeout") is False
    assert guard.snapshot()["consecutive"] == 1
    guard.record_success()
    assert guard.snapshot()["consecutive"] == 0
    assert guard.record_failure("vrc_read", {"path": "Assets/B"}, "timeout") is False


def test_repeated_failure_guard_keeps_only_a_digest_of_arguments() -> None:
    guard = RepeatedFailureGuard()
    guard.record_failure("vrc_read", {"secret": "do-not-retain"}, "network")
    snapshot = guard.snapshot()
    assert len(snapshot["argumentDigest"]) == 64
    assert "do-not-retain" not in str(snapshot)


def test_repeated_failure_guard_is_thread_safe_and_caps_counter() -> None:
    guard = RepeatedFailureGuard()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _index: guard.record_failure("vrc_read", {"path": "Assets/A"}, "network"),
                range(20),
            )
        )
    assert sum(results) == 18
    assert guard.snapshot()["consecutive"] == 3


def test_usage_aggregation_preserves_explicit_fields_and_aliases() -> None:
    result = aggregate_bounded_usage(
        [
            {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120, "cachedTokens": 10},
            {"prompt_tokens": "50", "completion_tokens": 5.0, "total_tokens": 55, "cacheReadTokens": 5},
        ]
    )
    assert result == {
        "inputTokens": 150,
        "outputTokens": 25,
        "totalTokens": 175,
        "cachedTokens": 15,
        "costUnavailableReason": "pricing_not_configured",
    }


def test_usage_aggregation_never_infers_missing_total() -> None:
    result = aggregate_bounded_usage({"inputTokens": 100, "outputTokens": 20})
    assert result["inputTokens"] == 100
    assert result["outputTokens"] == 20
    assert "totalTokens" not in result
    assert "cost" not in result


def test_usage_cost_requires_explicit_pricing_and_uses_reported_cache_tokens() -> None:
    result = aggregate_bounded_usage(
        [
            {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120, "cachedTokens": 10},
            {"inputTokens": 50, "outputTokens": 5, "totalTokens": 55, "cachedTokens": 5},
        ],
        pricing={
            "inputPerMillion": 2,
            "outputPerMillion": 10,
            "cachedInputPerMillion": 0.5,
            "currency": "USD",
        },
    )
    assert result["cost"] == pytest.approx(0.0005275)
    assert result["currency"] == "USD"
    assert "costUnavailableReason" not in result


@pytest.mark.parametrize(
    ("usage", "pricing", "reason"),
    [
        ({"inputTokens": 10}, {"inputPerMillion": 1, "outputPerMillion": 2}, "usage_incomplete"),
        ({"inputTokens": 10, "outputTokens": 1}, {"inputPerMillion": 1}, "pricing_incomplete"),
        (
            {"inputTokens": 10, "outputTokens": 1, "cachedTokens": 2},
            {"inputPerMillion": 1, "outputPerMillion": 2},
            "pricing_incomplete",
        ),
        (
            {"inputTokens": 10, "outputTokens": 1},
            {"inputPerMillion": -1, "outputPerMillion": 2},
            "pricing_invalid",
        ),
        (
            {"inputTokens": 2, "outputTokens": 1, "cachedTokens": 3},
            {"inputPerMillion": 1, "outputPerMillion": 2, "cachedInputPerMillion": 0.5},
            "usage_inconsistent",
        ),
    ],
)
def test_usage_cost_fails_closed_without_estimation(
    usage: dict[str, int],
    pricing: dict[str, float],
    reason: str,
) -> None:
    result = aggregate_bounded_usage(usage, pricing=pricing)
    assert "cost" not in result
    assert result["costUnavailableReason"] == reason


def test_usage_aggregation_ignores_invalid_counts() -> None:
    result = aggregate_bounded_usage(
        [
            {"inputTokens": -1, "outputTokens": True, "totalTokens": 1.5, "cachedTokens": "bad"},
            {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6, "cachedTokens": 0},
        ]
    )
    assert result["inputTokens"] == 4
    assert result["outputTokens"] == 2
    assert result["totalTokens"] == 6
    assert result["cachedTokens"] == 0


def test_usage_aggregation_bounds_records_and_suppresses_partial_cost() -> None:
    records = (
        {"inputTokens": index + 1, "outputTokens": 1, "totalTokens": index + 2}
        for index in range(10)
    )
    result = aggregate_bounded_usage(
        records,
        pricing={"inputPerMillion": 1, "outputPerMillion": 2},
        max_records=2,
    )
    assert result["inputTokens"] == 3
    assert result["outputTokens"] == 2
    assert result["totalTokens"] == 5
    assert result["bounded"] is True
    assert result["costUnavailableReason"] == "usage_bounded"
    assert "cost" not in result


def test_usage_aggregation_saturates_token_values() -> None:
    result = aggregate_bounded_usage(
        {"inputTokens": 900, "outputTokens": 200, "totalTokens": 1_100},
        max_tokens=1_000,
    )
    assert result["inputTokens"] == 900
    assert result["outputTokens"] == 200
    assert result["totalTokens"] == 1_000
    assert result["bounded"] is True
