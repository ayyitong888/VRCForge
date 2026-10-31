"""Pending job starters finish their transaction, not the background work."""
from copy import copy
from types import SimpleNamespace

import pytest

import dashboard_server
from agent_task_loop import AgentTaskLoop
from agent_tool_result_contract import normalize_agent_tool_result
from test_editor_menu_dispatch_lifecycle import _execute, dispatch_case


BUILD_TEST = "vrcforge_build_test_avatar"
GENERIC_JOB = "vrcforge_fixture_background_work"
JOB_ID = "5" * 32


@pytest.fixture
def pending_case(dispatch_case):
    gateway, service, menu_args, receipts, calls, checkpoints = dispatch_case
    gateway._write_handlers[BUILD_TEST] = copy(dashboard_server.AGENT_GATEWAY._write_handlers[BUILD_TEST])
    service.register_write_handler(
        GENERIC_JOB, "Start one background task and inspect the same job.", "medium", lambda _: receipts[GENERIC_JOB],
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _: [("vrc_refresh_asset_database", {})],
    )
    return gateway, service, menu_args, receipts, calls, checkpoints


def _startup(case):
    if case == "build_test":
        # The actual local-only Build & Test starter reported these facts. Its
        # job completed independently; only the outer starter was misclassified.
        return BUILD_TEST, "jobId", {
            "ok": True, "status": "initializing_sdk", "jobId": JOB_ID,
            "localOnly": True, "uploadAttempted": False, "published": False,
            "buildStarted": False, "mutationStarted": False, "commitState": "not_started",
        }
    return GENERIC_JOB, "job_id", {
        "ok": True, "status": case, "job_id": JOB_ID,
        "mutationStarted": True, "commitState": "unknown",
    }


@pytest.mark.parametrize("lane", ["external", "internal"])
@pytest.mark.parametrize("phase", ["build_test", "queued", "running"])
def test_pending_job_preserves_facts_releases_starter_and_polls_same_job(pending_case, monkeypatch, lane, phase):
    gateway, service, base, receipts, calls, checkpoints = pending_case
    name, job_key, raw = _startup(phase)
    receipts[name] = raw
    args = {"projectPath": base["projectPath"], "avatarPath": "Avatar"}
    result = _execute(pending_case, lane, name, args)
    assert result["ok"] is True, result
    assert result["status"] == "pending"
    assert result["result"] == raw
    outcome = result["outcome"]
    assert outcome["status"] == "pending"
    assert outcome.get("jobId", outcome.get("job_id")) == JOB_ID
    assert outcome["mutationStarted"] is raw["mutationStarted"]
    assert outcome["commitState"] == raw["commitState"]
    assert outcome["persistenceState"] == "pending"
    assert outcome["readbackState"] == "pending"
    assert outcome.get("verified") is not True
    assert result["checkpoint"]["id"] == checkpoints[0]["id"]
    assert service._ports.checkpoint.active_apply_recoveries() == []
    assert calls == [name]
    if lane == "external":
        public = gateway._external_mcp_write_result(name, result)
        assert public["ok"] is True, public
        assert public["status"] == "pending"
        assert public["outcome"]["status"] == "pending"
        assert public["commitState"] == raw["commitState"]
        assert public["mutationStarted"] is raw["mutationStarted"]
    else:
        assert result["taskCompletion"]["status"] == "running"
        assert result["taskCompletion"]["outcome"]["status"] == "pending"
        assert result["taskCompletion"]["outcome"].get("jobId", result["taskCompletion"]["outcome"].get("job_id")) == JOB_ID

    # Exercise the existing polling adapter, not a second starter invocation.
    polled = []
    terminal = {**raw, "status": "completed" if phase == "build_test" else "done", "ok": True}

    def invoke(_settings, tool, arguments, **kwargs):
        polled.append((tool, arguments, kwargs))
        return dashboard_server.McpResult(exit_code=0, stdout="", stderr="", payload={"data": terminal})

    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
    if phase == "build_test":
        assert dashboard_server.get_build_test_status_sync({"projectPath": args["projectPath"], "jobId": result["result"][job_key]}) == terminal
        assert polled == [("vrc_build_test_avatar", {"jobId": JOB_ID}, {"execution_context": {"lane": "app_build_test_poll"}})]
    else:
        assert dashboard_server.poll_unity_async_job_sync(SimpleNamespace(), result["result"], label="fixture job", timeout_seconds=1) == terminal
        assert polled == [("vrc_poll_job", {"job_id": JOB_ID}, {"preserve_tool_error": True})]
    assert calls == [name]

    if lane == "internal":
        resumed = AgentTaskLoop.from_approval_context(result["approval"]["taskContext"], result["taskCompletion"])
        # A status read for another job cannot finish the starter obligation.
        other = {**terminal, job_key: "b" * 32}
        resumed.record_action(
            kind="skill", tool="vrcforge_fixture_job_status", arguments={job_key: other[job_key]},
            raw_result=other, outcome=normalize_agent_tool_result(other, fallback_summary="Another job finished.", write=False),
        )
        starter_id = result["taskCompletion"]["actionId"]
        starter = next(action for action in resumed.planner_projection()["actions"] if action["actionId"] == starter_id)
        assert starter["status"] == "running"
        terminal_read = resumed.record_action(
            kind="skill", tool="vrcforge_get_build_test_status" if phase == "build_test" else "vrcforge_fixture_job_status",
            arguments={job_key: terminal[job_key]}, raw_result=terminal,
            outcome=normalize_agent_tool_result(terminal, fallback_summary="The original job finished.", write=False),
        )
        starter = next(action for action in resumed.planner_projection()["actions"] if action["actionId"] == starter_id)
        assert starter["status"] == "completed"
        completed = resumed.gate_terminal({
            "planner": "llm", "nextStep": "done", "reply": "The original job completed.",
            "completionClaim": {"satisfied": True, "evidenceActionIds": [starter_id, terminal_read["actionId"]]},
        })
        assert completed["nextStep"] == "done", completed

    following = "vrcforge_fixture_save_after_job_start"
    receipts[following] = {"ok": True, "verified": True, "commitState": "committed", "mutationStarted": True, "readback": {"saved": True}}
    service.register_write_handler(
        following, "Save the exact fixture asset.", "low", lambda _: receipts[following],
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _: [("vrc_save_asset", {"assetPath": "Assets/Fixture.asset"})],
    )
    assert _execute(pending_case, lane, following, {"projectPath": args["projectPath"]})["ok"] is True
    assert calls == [name, following]
    assert len(checkpoints) == 2


@pytest.mark.parametrize("lane", ["external", "internal"])
@pytest.mark.parametrize("phase", ["build_test", "running"])
def test_terminal_failed_job_is_not_reclassified_as_successful_pending_start(pending_case, lane, phase):
    _, service, base, receipts, _, _ = pending_case
    name, _, raw = _startup(phase)
    raw.update(ok=False, status="failed", error="The existing background job failed.")
    receipts[name] = raw
    result = _execute(pending_case, lane, name, {"projectPath": base["projectPath"], "avatarPath": "Avatar"})
    assert result["ok"] is False, result
    assert result["outcome"]["status"] == "failed"
    assert "The existing background job failed." in str(result)


@pytest.mark.parametrize("phase", ["build_test", "running"])
def test_failed_status_read_closes_only_the_same_job_as_failed(pending_case, phase):
    _, _, base, receipts, _, _ = pending_case
    name, job_key, raw = _startup(phase)
    receipts[name] = raw
    result = _execute(pending_case, "internal", name, {"projectPath": base["projectPath"], "avatarPath": "Avatar"})
    assert result["ok"] is True, result
    loop = AgentTaskLoop.from_approval_context(result["approval"]["taskContext"], result["taskCompletion"])
    failed = {**raw, "status": "failed", "ok": False, "error": "The existing job failed during execution."}
    loop.record_action(
        kind="skill", tool="vrcforge_fixture_job_status", arguments={job_key: failed[job_key]}, raw_result=failed,
        outcome=normalize_agent_tool_result(failed, fallback_summary="The original job failed.", write=False),
    )
    starter = next(action for action in loop.planner_projection()["actions"] if action["actionId"] == result["taskCompletion"]["actionId"])
    assert starter["status"] == "failed"
    assert loop.gate_terminal({"nextStep": "done", "reply": "Finished."})["nextStep"] != "done"


def test_unverified_asset_without_job_id_does_not_gain_pending_starter_semantics(pending_case):
    _, service, base, receipts, _, _ = pending_case
    name = "vrcforge_fixture_unverified_asset"
    raw = {"ok": True, "status": "running", "mutationStarted": True, "commitState": "unknown"}
    receipts[name] = raw
    outcome = normalize_agent_tool_result(raw, fallback_summary="Write the fixture asset.", write=True)
    assert outcome["status"] == "failed"
    assert outcome["commitState"] == "unknown"
    assert outcome["safeToRetry"] is False
    assert outcome["error"]["code"] == "running_write_without_job_handle"
    service.register_write_handler(
        name, "Write a fixture asset with no asynchronous job.", "low", lambda _: receipts[name],
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _: [("vrc_save_asset", {"assetPath": "Assets/Fixture.asset"})],
    )
    result = _execute(pending_case, "external", name, {"projectPath": base["projectPath"]})
    assert result["ok"] is False, result
    assert "no job handle" in result["error"]
    assert service._ports.checkpoint.active_apply_recoveries()


def test_job_id_does_not_bypass_an_assets_required_exact_fresh_readback(pending_case):
    _, service, base, receipts, _, _ = pending_case
    name = "vrcforge_fixture_verified_asset"
    _, _, receipts[name] = _startup("running")
    service.register_write_handler(
        name, "Write and read back the exact fixture asset.", "low", lambda _: receipts[name],
        fresh_readback_required=True, requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _: [("vrc_save_asset", {"assetPath": "Assets/Fixture.asset"})],
    )
    result = _execute(pending_case, "external", name, {"projectPath": base["projectPath"]})
    assert result["ok"] is False, result
    assert result["writeFailure"]["failureLayer"] == "fresh_readback"
    assert service._ports.checkpoint.active_apply_recoveries()
