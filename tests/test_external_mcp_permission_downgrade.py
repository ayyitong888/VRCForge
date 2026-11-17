from pathlib import Path

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
from approval_auto_review import review_auto_approval
from tests.external_mcp_approval_support import review_external_proposal


@pytest.mark.parametrize(("mode", "risk"), [
    ("approval", "low"), ("approval", "medium"),
    ("approval", "high"), ("approval", "critical"),
    ("auto", "low"), ("auto", "medium"), ("auto", "high"), ("auto", "critical"),
])
def test_prepared_automatic_write_cannot_keep_full_permission_after_downgrade(
    tmp_path: Path, mode: str, risk: str,
) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.execution_mode = "roslyn_full_auto"
    gateway.save_config(config)
    executed = []
    gateway.approval_transactions.register_write_handler(
        "vrcforge_permission_probe", "Check permission before applying.", risk,
        lambda arguments: executed.append(arguments) or {"ok": True},
    )
    prepared = gateway.approval_transactions.prepare_external_mcp_write(
        "vrcforge_permission_probe", {"value": 1},
    )
    assert prepared["requiresUserConfirmation"] is False
    gateway.approval_transactions.update_permission_state(mode)
    reloaded = AgentGateway(tmp_path / "config.json", tmp_path / "reload-audit")
    assert reloaded.approval_transactions.permission_state()["executionMode"] == mode

    with pytest.raises(AgentGatewayError, match="[Pp]ermission"):
        gateway.approval_transactions.execute_prepared_external_mcp_write(prepared)
    assert executed == []


@pytest.mark.parametrize("risk", ["low", "medium"])
@pytest.mark.parametrize("mode", ["approval", "auto"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_external_write_obeys_per_action_approval_mode(tmp_path: Path, risk: str, mode: str, decision: str) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.execution_mode = mode
    gateway.save_config(config)
    executed = []
    reviews = []
    gateway.approval_transactions.auto_approval_reviewer = lambda request: reviews.append(request) or "manual"
    gateway.approval_transactions.register_write_handler(
        "vrcforge_permission_probe", "Check per-action approval.", risk,
        lambda arguments: executed.append(arguments) or {"ok": True},
    )
    gateway.register_external_mcp_unity_tool("vrcforge_permission_probe", "avatar")
    assert gateway.approval_transactions.permission_state()["executionMode"] == mode
    if mode == "approval":
        assert gateway.approval_transactions.permission_state()["perActionApproval"] is True
    else:
        assert gateway.approval_transactions.permission_state()["autoApprove"] is True
    result = gateway.call_external_mcp_tool("vrcforge_permission_probe", {"value": 1})
    assert result["status"] == "user_confirmation_required"
    assert executed == []
    if mode == "auto":
        assert "independent approval reviewer" in result["reason"]
    confirmation = review_external_proposal(gateway, result, decision)
    resolved = gateway.call_external_mcp_tool("vrcforge_permission_probe", {"value": 1, "confirmation": confirmation})
    assert resolved["status"] == ("executed" if decision == "approve" else "rejected")
    assert executed == ([{"value": 1}] if decision == "approve" else [])
    assert len(reviews) == (1 if mode == "auto" else 0)


@pytest.mark.parametrize(("risk", "decision", "manual_reason", "mode_after_review", "expected"), [
    ("medium", "allow_auto", "", None, "executed"),
    ("high", "allow_auto", "", None, "user_confirmation_required"),
    ("medium", "allow_auto", "Requires a manual decision for this exact tool.", None, "user_confirmation_required"),
    ("low", "unavailable", "", None, "user_confirmation_required"),
    ("medium", "allow_auto", "", "approval", "failed"),
])
def test_external_auto_uses_only_the_app_review_for_eligible_writes(
    tmp_path, risk, decision, manual_reason, mode_after_review, expected,
):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.execution_mode = "auto"
    gateway.save_config(config)
    name = "vrcforge_permission_probe"
    executed, reviewed = [], []
    gateway.approval_transactions.register_write_handler(
        name, "Check the App's approval mode.", risk,
        lambda args: executed.append(args) or {"ok": True},
        manual_approval_resolver=lambda _args, _preview: manual_reason,
    )
    gateway.register_external_mcp_unity_tool(name, "avatar")

    def app_review(request):
        assert executed == []
        reviewed.append(request)
        if mode_after_review:
            gateway.approval_transactions.update_permission_state(mode_after_review)
        return decision

    gateway.approval_transactions.auto_approval_reviewer = app_review
    result = gateway.call_external_mcp_tool(name, {"value": 1})

    assert result["status"] == expected
    assert executed == ([{"value": 1}] if expected == "executed" else [])
    assert [record["arguments"] for record in reviewed] == ([] if risk == "high" or manual_reason else [{"value": 1}])
    if mode_after_review:
        assert "Permission changed after write preparation" in result["error"]


def test_external_auto_without_app_task_context_waits_for_manual_review(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.execution_mode = "auto"
    gateway.save_config(config)
    name = "vrcforge_permission_probe"
    gateway.approval_transactions.register_write_handler(
        name, "Check the App's approval mode.", "low",
        lambda _args: pytest.fail("Pending request reached execution"),
    )
    gateway.register_external_mcp_unity_tool(name, "avatar")
    gateway.approval_transactions.auto_approval_reviewer = lambda record: review_auto_approval(
        record, lambda _prompt: pytest.fail("No App task context to review"),
    )

    result = gateway.call_external_mcp_tool(name, {"value": 1})
    assert result["status"] == "user_confirmation_required"
