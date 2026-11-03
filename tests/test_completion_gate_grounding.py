"""Completion-claim grounding gate: verified completion comes only from an
explicit model-authored claim bound to executed action evidence.

Blind completion was observed in the audit: the runtime fabricated
satisfied=true claims for natural finals, so prose ("改好了") minted a
verified completion receipt. Codex CLI and Claude Code leave zero-tool
replies ungated (a plain answer stays an answer); VRCForge keeps that
property — natural finals end the turn — but fabricate no claim and mint no
taskCompletion. An explicit claim must cite evidence (executed action ids
visible in tool results) or follow a completed write/shell action.
"""
from __future__ import annotations

from agent_task_loop import AgentTaskLoop


def _record_read(loop: AgentTaskLoop) -> str:
    record = loop.record_action(
        kind="skill",
        tool="vrcforge_read_asset",
        arguments={"path": "Assets/Body.png"},
        raw_result={"ok": True},
        outcome={"status": "ok", "summary": "read"},
        native_read_observation=True,
    )
    return str(record["actionId"])


def _record_write(loop: AgentTaskLoop) -> str:
    record = loop.record_action(
        kind="write",
        tool="vrcforge_swap_body_texture",
        arguments={"target": "_MainTex"},
        raw_result={"ok": True},
        outcome={"status": "ok", "summary": "swapped"},
    )
    return str(record["actionId"])


def _plan(claim: dict | None) -> dict:
    plan = {"planner": "llm", "nextStep": "done"}
    if claim is not None:
        plan["completionClaim"] = claim
    return plan


def test_explicit_claim_with_zero_actions_lands_unverified() -> None:
    loop = AgentTaskLoop("swap the body texture")
    gated = loop.gate_terminal(_plan({"satisfied": True}))
    assert gated["nextStep"] == "completion_unverified"
    assert gated["completionGate"]["reason"] == "completion_evidence_missing"


def test_plain_reply_without_claim_completes_without_receipt() -> None:
    """Q&A shape: zero actions, no claim, done passes with no receipt."""
    loop = AgentTaskLoop("answer a question")
    gated = loop.gate_terminal(_plan(None))
    assert gated["nextStep"] == "done"
    assert "taskCompletion" not in gated


def test_explicit_claim_after_reads_only_without_evidence_lands_unverified() -> None:
    loop = AgentTaskLoop("inspect then claim done")
    _record_read(loop)
    gated = loop.gate_terminal(_plan({"satisfied": True}))
    assert gated["nextStep"] == "completion_unverified"
    assert gated["completionGate"]["reason"] == "completion_evidence_missing"


def test_explicit_claim_citing_read_evidence_passes() -> None:
    loop = AgentTaskLoop("inspect the project")
    action_id = _record_read(loop)
    gated = loop.gate_terminal(_plan({"satisfied": True, "evidenceActionIds": [action_id]}))
    assert gated["nextStep"] == "done"
    assert gated["taskCompletion"]["evidenceActionIds"] == [action_id]


def test_explicit_claim_after_completed_write_without_evidence_passes() -> None:
    loop = AgentTaskLoop("swap the body texture")
    _record_write(loop)
    gated = loop.gate_terminal(_plan({"satisfied": True}))
    assert gated["nextStep"] == "done"
    assert gated["taskCompletion"]["status"] == "completed"


def test_natural_final_after_reads_completes_without_receipt() -> None:
    """Prose mints no receipt: reads ran, the model replies with text only."""
    loop = AgentTaskLoop("inspect the project")
    _record_read(loop)
    gated = loop.gate_terminal(_plan(None))
    assert gated["nextStep"] == "done"
    assert "taskCompletion" not in gated


def test_empty_claim_is_not_a_completion_claim() -> None:
    """Reply action with the claim field omitted: natural-final semantics."""
    loop = AgentTaskLoop("inspect the project")
    _record_read(loop)
    gated = loop.gate_terminal(_plan({}))
    assert gated["nextStep"] == "done"
    assert "taskCompletion" not in gated
