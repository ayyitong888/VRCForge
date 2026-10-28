from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from agent_approval_transactions import AgentApprovalTransactionService
from agent_gateway import AgentGateway, AgentGatewayError


TOOL = "vrcforge_checkpoint_choice_fixture"
QUOTE = "这次不用 checkpoints"


def fixture_gateway(tmp_path: Path, *, mode: str = "auto", fail: bool = False, checkpoint: bool = True):
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = config.allow_write_requests = True
    config.execution_mode = mode
    gateway.save_config(config)
    project = tmp_path / "Project"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    target = project / "Assets" / "value.txt"
    target.write_text("before", encoding="utf-8")
    calls = []

    def write(arguments):
        calls.append(dict(arguments))
        staging = target.with_suffix(".pending")
        try:
            staging.write_text(str(arguments["value"]), encoding="utf-8")
            if fail:
                raise ValueError("fixture failure before atomic replace")
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)
        return {"ok": True, "status": "applied", "schema": "vrcforge.fixture_write.v1",
                "mutationStarted": True, "mutationApplied": True, "committed": True,
                "verified": True, "readback": {"value": target.read_text(encoding="utf-8")}}

    gateway.approval_transactions.register_write_handler(
        TOOL, "Write one isolated fixture.", "medium", write,
        external_mcp_capability="fixture", pre_write_checkpoint_required=checkpoint,
    )
    gateway.register_external_mcp_unity_tool(TOOL, "avatar")
    return gateway, project, target, calls


def arguments(project, **extra):
    return {"projectPath": str(project), "value": "after", **extra}


def call(gateway, args, session="external:first"):
    with gateway.resource_context(session):
        return gateway.call_external_mcp_tool(TOOL, args)


def events(gateway):
    return [json.loads(line) for line in gateway.audit_log_path.read_text(encoding="utf-8").splitlines()]


def test_external_explicit_choice_writes_without_checkpoint_and_records_quote(tmp_path, monkeypatch):
    gateway, project, target, calls = fixture_gateway(tmp_path)
    monkeypatch.setattr(AgentApprovalTransactionService, "_create_pre_write_checkpoint",
                        lambda *_: pytest.fail("explicit user choice must omit checkpoint"))
    args = arguments(project, checkpointChoice={"mode": "skip", "quote": QUOTE})
    original = deepcopy(args)
    result = call(gateway, args)
    assert result["ok"] is True, result
    assert target.read_text(encoding="utf-8") == "after"
    assert calls == [arguments(project)]
    assert args == original
    assert result["checkpointSkipped"] is True
    assert result["sourceQuote"] == QUOTE
    assert result["rollbackAvailable"] is False
    assert "checkpoint" not in result
    skipped = [row for row in events(gateway) if row.get("event") == "external_mcp_checkpoint_skipped_by_user"]
    assert len(skipped) == 1
    assert skipped[0]["checkpointSkipped"] is True
    assert skipped[0]["sourceQuote"] == QUOTE
    assert skipped[0]["source"] == "external_user_instruction"
    assert skipped[0]["sessionId"] == "external:first"


@pytest.mark.parametrize("choice", [None, {}, True, {"mode": "skip"},
    {"mode": "skip", "quote": " "}, {"mode": "skip", "quote": QUOTE, "trusted": True}])
def test_external_choice_requires_explicit_nonempty_user_quote(tmp_path, choice):
    gateway, project, target, calls = fixture_gateway(tmp_path)
    result = call(gateway, arguments(project, checkpointChoice=choice))
    assert result["ok"] is False
    assert calls == []
    assert target.read_text(encoding="utf-8") == "before"


@pytest.mark.parametrize("field", ["skipCheckpoint", "skip_checkpoint", "checkpointUserPolicy", "checkpointSkipAuthority"])
def test_external_private_skip_flags_are_rejected(tmp_path, field):
    gateway, project, _, calls = fixture_gateway(tmp_path)
    result = call(gateway, arguments(project, **{field: True}))
    assert result["ok"] is False
    assert calls == []


def test_external_omitted_choice_still_requires_checkpoint(tmp_path, monkeypatch):
    gateway, project, target, calls = fixture_gateway(tmp_path)
    checkpoint_calls = []
    def unavailable(*_):
        checkpoint_calls.append(True)
        return {"ok": False, "error": "fixture checkpoint unavailable"}
    monkeypatch.setattr(AgentApprovalTransactionService, "_create_pre_write_checkpoint", unavailable)
    result = call(gateway, arguments(project))
    assert result["ok"] is False
    assert checkpoint_calls == [True]
    assert calls == []
    assert target.read_text(encoding="utf-8") == "before"
    assert "checkpointSkipped" not in result


def test_noncheckpointed_write_does_not_claim_checkpoint_was_skipped(tmp_path):
    gateway, project, _, _ = fixture_gateway(tmp_path, checkpoint=False)
    result = call(gateway, arguments(project, checkpointChoice={"mode": "skip", "quote": QUOTE}))
    assert result["ok"] is True, result
    assert "checkpointSkipped" not in result
    assert not any(row.get("event") == "external_mcp_checkpoint_skipped_by_user" for row in events(gateway))


def test_external_confirmation_shows_quote_rejects_other_session_and_consumes_once(tmp_path, monkeypatch):
    gateway, project, target, calls = fixture_gateway(tmp_path, mode="approval")
    monkeypatch.setattr(AgentApprovalTransactionService, "_create_pre_write_checkpoint",
                        lambda *_: pytest.fail("explicit user choice must omit checkpoint"))
    args = arguments(project, checkpointChoice={"mode": "skip", "quote": QUOTE})
    proposal = call(gateway, args)
    assert proposal["status"] == "user_confirmation_required", proposal
    assert "No restore point" in proposal["message"] and QUOTE in proposal["message"]
    assert proposal["taskContext"]["checkpointUserPolicy"]["sourceQuote"] == QUOTE
    assert proposal["rollbackAvailable"] is False
    confirmation = {**proposal["confirmation"], "decision": "approve"}
    confirmed_args = {**args, "confirmation": confirmation}
    assert call(gateway, confirmed_args, "external:other")["ok"] is False
    assert calls == []
    assert call(gateway, confirmed_args)["ok"] is True
    assert target.read_text(encoding="utf-8") == "after"
    assert call(gateway, confirmed_args)["ok"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["session", "project", "arguments", "plan"])
def test_external_prepared_skip_stays_bound_to_exact_write(tmp_path, change):
    gateway, project, _, calls = fixture_gateway(tmp_path)
    service = gateway.approval_transactions
    prepared = service.prepare_external_mcp_write(
        TOOL, arguments(project, checkpointChoice={"mode": "skip", "quote": QUOTE}),
        external_session_id="external:first")
    session = "external:first"
    if change == "session":
        session = "external:other"
    else:
        policy_key = {"project": "projectRoot", "arguments": "approvedArgumentsSha256", "plan": "approvedPlanDigest"}[change]
        prepared["checkpointUserPolicy"][policy_key] = "different"
    with pytest.raises(AgentGatewayError, match="does not match"):
        service.execute_prepared_external_mcp_write(prepared, external_session_id=session)
    assert calls == []


def test_skipping_retained_checkpoint_preserves_handler_atomic_failure(tmp_path, monkeypatch):
    gateway, project, target, calls = fixture_gateway(tmp_path, fail=True)
    monkeypatch.setattr(AgentApprovalTransactionService, "_create_pre_write_checkpoint",
                        lambda *_: pytest.fail("explicit user choice must omit checkpoint"))
    result = call(gateway, arguments(project, checkpointChoice={"mode": "skip", "quote": QUOTE}))
    assert result["ok"] is False
    assert len(calls) == 1
    assert target.read_text(encoding="utf-8") == "before"
    assert not target.with_suffix(".pending").exists()
    assert result["checkpointSkipped"] is True
    assert result["sourceQuote"] == QUOTE
    assert any(row.get("event") == "external_mcp_write_failed" and row.get("checkpointSkipped") for row in events(gateway))


def test_session_switch_persists_until_explicit_require_and_new_session_defaults_on(tmp_path):
    gateway, project, target, calls = fixture_gateway(tmp_path)
    first = call(gateway, arguments(project, checkpointChoice={"mode": "skip", "quote": "Turn off checkpoints"}))
    assert first["checkpointSkipped"] is True
    second = call(gateway, arguments(project, value="second"))
    assert second["checkpointSkipped"] is True
    assert second["sourceQuote"] == "Turn off checkpoints"
    fresh = call(gateway, arguments(project, value="fresh"), "external:fresh")
    assert fresh["ok"] is True and fresh.get("checkpoint"), fresh
    assert "checkpointSkipped" not in fresh
    enabled = call(gateway, arguments(project, checkpointChoice={"mode": "require", "quote": "Turn checkpoints back on"}))
    assert enabled["ok"] is True and enabled.get("checkpoint"), enabled
    assert "checkpointSkipped" not in enabled
    later = call(gateway, arguments(project, value="later"))
    assert later["ok"] is True and later.get("checkpoint"), later
    assert "checkpointSkipped" not in later
    assert target.read_text(encoding="utf-8") == "later"
    assert len(calls) == 5


def test_reenable_invalidates_pending_skip_confirmation(tmp_path):
    gateway, project, _, calls = fixture_gateway(tmp_path, mode="approval")
    off_args = arguments(project, checkpointChoice={"mode": "skip", "quote": "Disable checkpoints"})
    off = call(gateway, off_args)
    on = call(gateway, arguments(project, checkpointChoice={"mode": "require", "quote": "Enable checkpoints"}))
    assert on["status"] == "user_confirmation_required"
    result = call(gateway, {**off_args, "confirmation": {**off["confirmation"], "decision": "approve"}})
    assert result["ok"] is False
    assert "re-enabled" in str(result)
    assert calls == []
