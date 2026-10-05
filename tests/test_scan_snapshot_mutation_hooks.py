from dataclasses import replace
from pathlib import Path

import pytest

from agent_checkpoint_recovery import AgentCheckpointRecoveryService
from agent_approval_transactions import AgentApprovalTransactionService
from agent_gateway import AgentGateway, AgentGatewayError, freeze_approved_unity_execution_plan
from agent_scan_result_store import ScanResultStore
from approved_unity_execution import current_approved_unity_execution


@pytest.fixture
def fixture(tmp_path):
    project = tmp_path / "project"
    asset = project / "Assets" / "FX.controller"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"fixture controller")
    asset.with_name(asset.name + ".meta").write_text("guid: fixture\n", encoding="utf-8")
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    store = ScanResultStore(tmp_path / "scans")
    events = []
    owner = gateway.approval_transactions

    def invalidate(root, reason):
        events.append((root, reason))
        if reason.endswith(":starting"):
            store.begin_mutation(root, reason)
        else:
            store.end_mutation(root, reason)

    owner._ports = replace(owner._ports, invalidate_scan_results=invalidate)
    recovery = gateway.checkpoint_recovery
    recovery._ports = replace(recovery._ports, invalidate_scan_results=invalidate)
    return gateway, store, project, events


def capture(store, project, turn):
    return store.capture("session", turn, str(project), {
        "id": "scan", "tool": "vrc_scan_fx_animator", "status": "ok",
        "result": {"rows": [1, 2, 3]},
    }, {"controllerPath": "Assets/FX.controller"})


def invoke(owner, lane, name, project, *, frozen=None):
    arguments = {"projectPath": str(project)}
    handler = owner._ports.state.write_handlers[name]
    plan = frozen or freeze_approved_unity_execution_plan([("vrc_write_fixture", {})])
    if lane == "internal":
        return owner._call_write_handler(handler, name, "approval", None, arguments,
                                         "digest", plan, {}, {})
    arguments["executionTarget"] = {
        "schema": "vrcforge.execution_target.v1", "scope": "project",
        "project": {"root": str(project), "projectId": "fixture"},
        "editor": {"unityPid": 123, "processStartTime": "fixture", "coreInstanceId": "fixture"},
    }
    return owner._call_external_mcp_write_handler(handler, name, "operation", arguments,
                                                  "digest", plan)


@pytest.mark.parametrize("lane", ["internal", "external"])
@pytest.mark.parametrize("core", [False, True])
def test_handler_guard_expires_before_call_and_never_revives_concurrent_capture(fixture, lane, core):
    gateway, store, project, events = fixture
    owner = gateway.approval_transactions
    before = capture(store, project, "before")
    during = []

    def handler(_arguments):
        assert events == [(str(project), "fixture_write:starting")]
        assert store.lookup("session", "before", str(project), before["resultRef"])["expired"] is True
        events.append((str(project), "handler"))
        during.append(capture(store, project, "during"))
        assert during[0]["reason"] == "mutation_in_progress"
        if core:
            plan = current_approved_unity_execution()
            assert plan is not None
            plan.claim("vrc_write_fixture", {}, project).complete()
        return {"ok": True}

    owner.register_write_handler("fixture_write", "Write fixture", "low", handler,
        pre_write_checkpoint_required=False, requires_approved_execution_context=core,
        approved_execution_plan_builder=lambda _arguments: [("vrc_write_fixture", {})])
    assert invoke(owner, lane, "fixture_write", project)["ok"] is True
    assert events[-1] == (str(project), "fixture_write:finished")
    assert store.lookup("session", "during", str(project), during[0]["resultRef"])["expired"] is True
    assert "expired" not in capture(store, project, "after")


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_unapproved_requests_do_not_expire_scans(fixture, status):
    gateway, store, project, events = fixture
    owner = gateway.approval_transactions
    owner.register_write_handler("fixture_write", "Write fixture", "low",
        lambda _args: pytest.fail("unapproved handler executed"), pre_write_checkpoint_required=False)
    before = capture(store, project, "before")
    requested = owner.create_apply_request({"target_tool": "fixture_write",
                                          "arguments": {"projectPath": str(project)}})
    approval_id = requested["approval"]["id"]
    if status == "rejected":
        owner.reject(approval_id)
    assert owner.apply_approved({"approvalId": approval_id})["status"] == status
    assert events == []
    assert "expired" not in store.lookup("session", "before", str(project), before["resultRef"])


@pytest.mark.parametrize("lane", ["internal", "external"])
def test_plan_drift_stops_before_invalidation(fixture, lane):
    gateway, _store, project, events = fixture
    owner = gateway.approval_transactions
    owner.register_write_handler("fixture_write", "Write fixture", "low",
        lambda _args: pytest.fail("drifted handler executed"), pre_write_checkpoint_required=False,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _args: [("vrc_write_fixture", {"new": True})])
    with pytest.raises(AgentGatewayError, match="drifted"):
        invoke(owner, lane, "fixture_write", project)
    assert events == []


@pytest.mark.parametrize("lane", ["internal", "external"])
def test_failed_start_does_not_execute_or_finish_nonexistent_marker(fixture, lane):
    gateway, _store, project, events = fixture
    owner = gateway.approval_transactions

    def invalidate(root, reason):
        events.append((root, reason))
        raise RuntimeError("invalidation unavailable")

    owner._ports = replace(owner._ports, invalidate_scan_results=invalidate)
    owner.register_write_handler("fixture_write", "Write fixture", "low",
        lambda _args: pytest.fail("handler executed after failed start"), pre_write_checkpoint_required=False)
    with pytest.raises(RuntimeError, match="invalidation unavailable"):
        invoke(owner, lane, "fixture_write", project)
    assert events == [(str(project), "fixture_write:starting")]


@pytest.mark.parametrize("lane", ["internal", "external"])
def test_handler_failure_always_finishes_invalidation(fixture, lane):
    gateway, store, project, events = fixture
    owner = gateway.approval_transactions

    def handler(_args):
        events.append((str(project), "handler"))
        raise RuntimeError("handler failed")

    owner.register_write_handler("fixture_write", "Write fixture", "low", handler,
                                 pre_write_checkpoint_required=False)
    with pytest.raises(RuntimeError, match="handler failed"):
        invoke(owner, lane, "fixture_write", project)
    assert events == [(str(project), "fixture_write:starting"), (str(project), "handler"),
                      (str(project), "fixture_write:finished")]
    assert "expired" not in capture(store, project, "after")


def test_failed_finish_propagates_and_keeps_future_captures_blocked(fixture):
    gateway, store, project, events = fixture
    owner = gateway.approval_transactions

    def invalidate(root, reason):
        events.append((root, reason))
        if reason.endswith(":starting"):
            store.begin_mutation(root, reason)
        else:
            raise RuntimeError("finish unavailable")

    owner._ports = replace(owner._ports, invalidate_scan_results=invalidate)
    owner.register_write_handler("fixture_write", "Write fixture", "low", lambda _args: {"ok": True},
                                 pre_write_checkpoint_required=False)
    with pytest.raises(RuntimeError, match="finish unavailable"):
        invoke(owner, "internal", "fixture_write", project)
    assert capture(store, project, "after")["reason"] == "mutation_in_progress"


def test_failed_checkpoint_gate_never_invalidates(fixture, monkeypatch):
    gateway, _store, project, events = fixture
    owner = gateway.approval_transactions
    owner.register_write_handler("fixture_write", "Write fixture", "low",
                                 lambda _args: pytest.fail("checkpoint-blocked handler executed"))
    requested = owner.create_apply_request({"target_tool": "fixture_write",
                                          "arguments": {"projectPath": str(project)}})
    approval_id = requested["approval"]["id"]
    owner.approve(approval_id)
    monkeypatch.setattr(AgentApprovalTransactionService, "_create_pre_write_checkpoint",
                        lambda *_args: {"ok": False, "error": "checkpoint unavailable"})
    assert owner.apply_approved({"approvalId": approval_id})["ok"] is False
    assert events == []


@pytest.mark.parametrize("dedicated", [False, True])
@pytest.mark.parametrize("failed_phase", ["starting", "finished"])
def test_checkpoint_prepare_cannot_fallback_past_invalidation_failure(fixture, dedicated, failed_phase):
    gateway, _store, project, events = fixture
    owner = gateway.approval_transactions

    def invalidate(root, reason):
        events.append((root, reason))
        if reason.endswith(":" + failed_phase):
            raise RuntimeError("prepare invalidation unavailable")

    def prepare(*_args):
        events.append((str(project), "prepare"))
        return {"ok": True}

    owner._ports = replace(owner._ports, invalidate_scan_results=invalidate,
        is_unity_project_root=lambda _root: True,
        run_git=lambda *_args: {"ok": False},
        checkpoint=replace(owner._ports.checkpoint,
            create_archive_checkpoint=lambda *_args: pytest.fail("checkpoint fallback executed")))
    owner.register_write_handler("fixture_write", "Write fixture", "low", lambda _args: {"ok": True},
                                 checkpoint_prepare_handler=prepare if dedicated else None)
    if not dedicated:
        owner.checkpoint_prepare_handler = prepare
    with pytest.raises(RuntimeError, match="prepare invalidation unavailable"):
        owner._create_pre_write_checkpoint({"id": "approval", "targetTool": "fixture_write"},
                                          {"projectPath": str(project)})
    expected = [(str(project), "fixture_write:starting")]
    if failed_phase == "finished":
        expected.extend([(str(project), "prepare"), (str(project), "fixture_write:finished")])
    assert events == expected


@pytest.mark.parametrize("name", ["vrcforge_shell_execute", "vrcforge_execute_roslyn_script",
                                  "vrcforge_rollback_parameters"])
def test_unknown_write_scope_uses_global_guard(fixture, name):
    gateway, store, project, events = fixture
    owner = gateway.approval_transactions

    def handler(_args):
        assert events == [("", name + ":starting")]
        assert capture(store, project, "during")["reason"] == "mutation_in_progress"
        return {"ok": True}

    owner.register_write_handler(name, "Write fixture", "low", handler, pre_write_checkpoint_required=False)
    registered = owner._ports.state.write_handlers[name]
    owner._call_write_handler(registered, name, "approval", None, {}, "digest", {}, {}, {})
    assert events[-1] == ("", name + ":finished")


def test_shell_readonly_classification_cannot_bypass_global_guard(fixture):
    gateway, store, project, events = fixture
    owner = gateway.approval_transactions
    name = "vrcforge_shell_execute"

    def handler(_args):
        assert events == [("", name + ":starting")]
        assert capture(store, project, "during")["reason"] == "mutation_in_progress"
        return {"ok": True}

    owner.register_write_handler(name, "Shell fixture", "low", handler, pre_write_checkpoint_required=False)
    registered = owner._ports.state.write_handlers[name]
    owner._call_write_handler(registered, name, "approval", None,
                             {"classification_snapshot": {"readOnly": True}}, "digest", {}, {}, {})
    assert events[-1] == ("", name + ":finished")


def restore_fixture(fixture, monkeypatch, strategy="git", *, unknown=False):
    gateway, _store, project, _events = fixture
    owner = gateway.checkpoint_recovery
    checkpoint = {"id": "checkpoint", "strategy": strategy, "projectRoot": "" if unknown else str(project),
                  "gitRoot": str(project), "checkpointRef": "fixture-ref", "pathspecs": ["Assets"]}
    monkeypatch.setattr(AgentCheckpointRecoveryService, "_load_checkpoint", lambda _self, _id: checkpoint)
    monkeypatch.setattr(AgentCheckpointRecoveryService, "_checkpoint_available", lambda _self, _cp: {"ok": True})
    monkeypatch.setattr(AgentCheckpointRecoveryService, "_build_checkpoint_rollback_coverage_audit",
                        lambda *_args, **_kwargs: {})
    return owner


@pytest.mark.parametrize("strategy", ["git", "archive", "local_state_archive", "project_chat_archive"])
def test_restore_guard_covers_prepare_restore_cleanup_and_reload(fixture, monkeypatch, strategy):
    gateway, store, project, events = fixture
    owner = restore_fixture(fixture, monkeypatch, strategy)

    def mutation(_self=None, *_args, **_kwargs):
        assert events[0] == (str(project), "vrcforge_restore_checkpoint:starting")
        assert capture(store, project, "during")["reason"] == "mutation_in_progress"
        events.append((str(project), "restore"))
        return {"ok": True, "stdout": "", "stderr": "", "error": ""}

    owner._ports = replace(owner._ports, run_git=mutation)
    for name in ("_restore_archive_checkpoint", "_restore_local_state_checkpoint", "_restore_project_chat_checkpoint"):
        monkeypatch.setattr(AgentCheckpointRecoveryService, name, mutation)
    if strategy in {"git", "archive"}:
        owner.checkpoint_restore_prepare_handler = lambda _root: mutation()
        owner.checkpoint_restore_handler = lambda _root, _ctx: mutation()
    assert owner.restore_checkpoint({"checkpointId": "checkpoint", "confirmRestore": True})["ok"] is True
    assert events[-1] == (str(project), "vrcforge_restore_checkpoint:finished")
    assert len([event for _root, event in events if event.endswith(":starting")]) == 1


def test_unconfirmed_restore_never_invalidates(fixture, monkeypatch):
    owner = restore_fixture(fixture, monkeypatch)
    assert owner.restore_checkpoint({"checkpointId": "checkpoint"})["ok"] is False
    assert fixture[3] == []


def test_unknown_git_restore_scope_invalidates_all_projects(fixture, monkeypatch):
    _gateway, store, project, events = fixture
    owner = restore_fixture(fixture, monkeypatch, unknown=True)

    def run_git(*_args, **_kwargs):
        assert events[0] == ("", "vrcforge_restore_checkpoint:starting")
        assert capture(store, project, "during")["reason"] == "mutation_in_progress"
        return {"ok": False, "stdout": "", "stderr": "", "error": "restore failed"}

    owner._ports = replace(owner._ports, run_git=run_git)
    assert owner.restore_checkpoint({"checkpointId": "checkpoint", "confirmRestore": True})["ok"] is False
    assert events[-1] == ("", "vrcforge_restore_checkpoint:finished")


def test_restore_invalidation_failure_is_not_swallowed_by_prepare(fixture, monkeypatch):
    owner = restore_fixture(fixture, monkeypatch)
    events = fixture[3]

    def invalidate(root, reason):
        events.append((root, reason))
        raise RuntimeError("restore invalidation unavailable")

    owner._ports = replace(owner._ports, invalidate_scan_results=invalidate)
    owner.checkpoint_restore_prepare_handler = lambda _root: pytest.fail("prepare executed")
    owner.checkpoint_restore_handler = lambda _root, _ctx: pytest.fail("reload executed")
    with pytest.raises(RuntimeError, match="restore invalidation unavailable"):
        owner.restore_checkpoint({"checkpointId": "checkpoint", "confirmRestore": True})
    assert events == [(str(fixture[2]), "vrcforge_restore_checkpoint:starting")]
