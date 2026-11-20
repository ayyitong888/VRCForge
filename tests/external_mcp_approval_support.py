"""Record an App decision for the original external MCP proposal in test fixtures."""

from agent_approval_transactions import _domain_write_receipt
from agent_gateway import AgentGateway
from agent_tool_result_contract import normalize_agent_tool_result


def review_external_proposal(
    gateway: AgentGateway, proposal: dict, decision: str = "approve",
) -> dict:
    assert proposal["status"] == "user_confirmation_required", proposal
    confirmation = proposal["confirmation"]
    operation_id = confirmation["operationId"]
    approval = next(
        item for item in gateway.list_external_mcp_pending_confirmations()
        if item["id"] == operation_id
    )
    project_root = approval["projectRoot"]
    reviewed = gateway.decide_external_mcp_confirmation(
        operation_id, decision, expected_project_root=project_root, global_only=not bool(project_root),
    )
    assert reviewed["ok"], reviewed
    return confirmation


def target_effect_verified(result: dict) -> bool:
    receipt = _domain_write_receipt(result)
    return normalize_agent_tool_result(receipt, fallback_summary="", write=True)["targetEffectVerified"]
