from __future__ import annotations

from contextlib import contextmanager
from tests.native_planner_fixture import NativePlannerFixture
from dataclasses import dataclass, field

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

    def plan(self, _prompt: str) -> PlannerModelResult:
        raise AssertionError("native context guard must use native transport")

    def plan_native(self, request: dict) -> PlannerModelResult:
        self.requests.append(request)
        return PlannerModelResult(
            text="done",
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
