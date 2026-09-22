"""The gateway and planner share one trigger-contract renderer."""

import agent_gateway
import runtime_planner_service
from tool_usage_contract import tool_usage_description


def test_gateway_and_planner_use_the_shared_renderer() -> None:
    assert agent_gateway.tool_usage_description is tool_usage_description
    assert runtime_planner_service.tool_usage_description is tool_usage_description


def test_shared_renderer_preserves_all_trigger_sections_and_write_policy() -> None:
    read = tool_usage_description("inspect", "Inspect the current project", write=False)
    write = tool_usage_description("edit", "Edit the current project", write=True)
    for text in (read, write):
        assert "When to use:" in text
        assert "When NOT to use:" in text
        assert "Negative example:" in text
    assert "without an explicit project change request and approval" in write
    assert "when the user forbids inspection" in read


def test_existing_contract_is_not_rewrapped() -> None:
    contract = "When to use: A\nWhen NOT to use: B\nNegative example: C"
    assert tool_usage_description("inspect", contract, write=False) == contract
