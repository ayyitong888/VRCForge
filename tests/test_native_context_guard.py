from __future__ import annotations

from contextlib import contextmanager
from tests.native_planner_fixture import NativePlannerFixture
from dataclasses import dataclass, field
import json

import pytest

from runtime_planner_service import (
    PlannerCatalogSnapshot,
    PlannerModelResult,
    PlannerTurnMetadata,
    RuntimePlannerService,
)


@dataclass
class _Catalog:
    def read(self, _layer: str, *, project_context_active: bool = True) -> PlannerCatalogSnapshot:
        return PlannerCatalogSnapshot()


@dataclass
class _Desktop:
    def summarize_action_result(self, result: object) -> str:
        return str(result)


@dataclass
class _NativeModel:
    requests: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    def plan(self, _prompt: str) -> PlannerModelResult:
        raise AssertionError("native context guard must use native transport")

    def plan_native(self, request: dict) -> PlannerModelResult:
        self.requests.append(request)
        return PlannerModelResult(
            text="done",
            usage=self.usage,
            assistant_message={"role": "assistant", "content": "done"},
            finish_reason="stop",
        )


@dataclass
class _Turn:
    metadata: PlannerTurnMetadata

    @contextmanager
    def bind(self, _request: dict):
        yield self.metadata


def test_native_guard_blocks_serialized_request_before_provider_call() -> None:
    model = _NativeModel()
    planner = RuntimePlannerService(
        catalog=_Catalog(),
        desktop=_Desktop(),
        model=model,
        turn=_Turn(PlannerTurnMetadata(verified_context_limit=100)),
    )
    messages = [{"role": "user", "content": "x" * 5000}]
    usage: dict[str, object] = {}

    with planner.bind_turn({"provider": "fixture", "model": "native"}):
        plan = planner.plan_agent_turn(
            "continue",
            {},
            {},
            context_usage=usage,
            native_turn=NativePlannerFixture(messages),
        )

    assert model.requests == []
    assert plan["nextStep"] == "context_compaction_required"
    assert plan["contextCompaction"]["applied"] is False
    assert plan["contextCompaction"]["blocked"] is True
    assert plan["contextCompaction"]["measurement"] == "native_serialized_request_estimate"
    assert usage["nativeContextGuard"]["blocked"] is True


def test_client_context_limit_is_ignored_without_host_binding() -> None:
    model = _NativeModel()
    planner = RuntimePlannerService(catalog=_Catalog(), desktop=_Desktop(), model=model)

    plan = planner.plan_agent_turn(
        "continue",
        {"_contextCompactionLimit": 1},
        {},
        native_turn=NativePlannerFixture([{"role": "user", "content": "small"}]),
    )

    assert model.requests
    assert plan["nextStep"] == "done"


@pytest.mark.parametrize("reported_input", [319_721, None])
def test_native_guard_records_only_input_from_its_own_provider_receipt(reported_input: int | None) -> None:
    provider_usage = {"exact": True, "outputTokens": 4}
    if reported_input is not None:
        provider_usage["inputTokens"] = reported_input
    model = _NativeModel(usage=provider_usage)
    metadata = PlannerTurnMetadata(verified_context_limit=500_000, provider="chatgpt", model="gpt-6.1-sol")
    planner = RuntimePlannerService(catalog=_Catalog(), desktop=_Desktop(), model=model, turn=_Turn(metadata))
    context_usage = {"lastInputTokens": 400_000, "requestCount": 1}

    with planner.bind_turn({}):
        plan = planner.plan_agent_turn(
            "continue", {}, {}, context_usage=context_usage,
            native_turn=NativePlannerFixture([{"role": "user", "content": "small"}]),
        )

    assert plan["nextStep"] == "done"
    guard = context_usage["nativeContextGuard"]
    assert guard["requestTokens"] == context_usage["lastPromptEstimatedTokens"]
    assert guard.get("observedInputTokens") == reported_input


@pytest.mark.parametrize(("request_tokens", "compaction_attempted"), [(440_411, False), (475_000, True)])
def test_native_soft_trigger_uses_exact_prior_input_without_relaxing_hard_gate(
    request_tokens: int, compaction_attempted: bool,
) -> None:
    # Live run: provider reported 319,721 for the prior request, estimated at
    # about 422,000 locally. The next local estimate was 440,411.
    metadata = PlannerTurnMetadata(verified_context_limit=500_000, provider="chatgpt", model="gpt-6.1-sol")
    planner = RuntimePlannerService(catalog=_Catalog(), desktop=_Desktop(), model=_NativeModel(), turn=_Turn(metadata))
    previous_estimate = 422_000
    previous_input = 319_721
    previous_request = {"instructions": "fixture", "messages": [{"role": "user", "content": ""}], "tools": []}
    previous_request["messages"][0]["content"] = "x" * (
        previous_estimate * 4 - len(json.dumps(previous_request, separators=(",", ":")))
    )
    request = {**previous_request, "messages": [
        *previous_request["messages"], {"role": "assistant", "content": ""},
    ]}
    request["messages"][-1]["content"] = "x" * (
        request_tokens * 4 - len(json.dumps(request, separators=(",", ":")))
    )
    native_turn = NativePlannerFixture(request["messages"])

    with planner.bind_turn({}):
        prior_guard = planner.native_context_guard(previous_request)
        assert prior_guard["requestTokens"] == previous_estimate
        usage = {
            "nativeContextGuard": {
                **prior_guard,
                "observedInputTokens": previous_input,
                "observedProvider": metadata.provider,
                "observedModel": metadata.model,
            },
            "lastInputTokens": previous_input,
            "lastPromptEstimatedTokens": previous_estimate,
            "lastUsageExact": True,
        }
        _, guard = planner.maybe_compact_native_context(request, native_turn, context_usage=usage)

    assert guard["beforeTokens"] == request_tokens
    assert guard["softProjectedTokens"] == previous_input + request_tokens - previous_estimate
    assert guard["blocked"] is compaction_attempted
    assert (native_turn.compaction is not None) is compaction_attempted
