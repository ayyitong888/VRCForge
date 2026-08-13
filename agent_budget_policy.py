"""Runtime budget policy with natural model-final termination.

Model turns are unlimited by default, matching established interactive agents.
An explicit model-turn limit remains available for automation. Tool calls are
tracked separately for telemetry and compatibility; they are not a turn
termination condition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_AGENT_BUDGET_POLICY = {
    "max_model_turns": None,
    "normal_tool_call_limit": None,
}


def _bounded_int(value: Any, default: int, *, maximum: int = 4096) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _optional_bounded_int(value: Any, *, maximum: int = 4096) -> int | None:
    if value in (None, ""):
        return None
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class AgentBudgetPolicy:
    max_model_turns: int | None = None
    normal_tool_call_limit: int | None = None

    def check(self, *, model_turns_used: int, tool_calls_used: int,
              remaining_action: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.max_model_turns is not None and model_turns_used >= self.max_model_turns:
            return {"paused": True, "nextStep": "paused",
                    "remainingAction": dict(remaining_action or {}),
                    "reason": "model_turn_budget_exhausted"}
        return {"paused": False, "remainingAction": dict(remaining_action or {})}


def freeze_agent_budget_policy(value: Mapping[str, Any] | None) -> AgentBudgetPolicy:
    """Normalize current and legacy config keys at turn start."""
    source = value if isinstance(value, Mapping) else {}
    turns = source.get(
        "max_model_turns",
        source.get(
            "maxAgenticTurns",
            source.get(
                "maxModelTurns",
                source.get(
                    "modelTurns",
                    source.get("maxSteps", DEFAULT_AGENT_BUDGET_POLICY["max_model_turns"]),
                ),
            ),
        ),
    )
    # Legacy tool-call configuration is intentionally ignored. Tool calls are
    # telemetry only; natural model-final termination and explicit model-turn
    # limits remain the runtime boundaries.
    return AgentBudgetPolicy(_optional_bounded_int(turns), None)
