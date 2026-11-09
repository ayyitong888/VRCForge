from __future__ import annotations

import ast
from pathlib import Path
import threading

from agent_gateway import AgentGateway
from background_goal_runtime import RuntimeLaneBudget
from sub_agent_collaboration_service import SubAgentCollaborationPorts, SubAgentCollaborationService, SubAgentExecutionPorts
from sub_agent_delegate import build_sub_agent_role_handlers, build_sub_agent_roles
from sub_agent_tasks import SubAgentRole, SubAgentTaskRegistry


class _Gateway:
    def execute(self, *_args, **_kwargs):  # pragma: no cover - handlers are not invoked here.
        return {"ok": True}


def test_service_owns_one_registry_and_the_existing_seven_role_handlers(tmp_path: Path) -> None:
    service = SubAgentCollaborationService(
        SubAgentCollaborationPorts(
            artifact_dir=tmp_path / "sub-agents",
            gateway=_Gateway(),
            lane_budget=RuntimeLaneBudget(),
            build_roles=build_sub_agent_roles,
            build_handlers=build_sub_agent_role_handlers,
        )
    )

    assert SubAgentCollaborationService.__slots__ == ("_registry",)
    assert set(service._registry.handlers) == {role.id for role in build_sub_agent_roles()}  # noqa: SLF001 - ownership proof.
    assert service.list_tasks()["roles"] == service._registry.list_roles()  # noqa: SLF001 - ownership proof.


def test_maintenance_targets_and_locks_are_exposed_without_root_registry_access(tmp_path: Path) -> None:
    registry = SubAgentTaskRegistry(tmp_path / "sub-agents", roles=[], handlers={})
    service = SubAgentCollaborationService.from_registry_for_testing(registry)

    targets = service.maintenance_targets()

    assert targets.event_log_path == tmp_path / "sub-agents" / "sub-agent-events.jsonl"
    assert targets.artifact_dir == tmp_path / "sub-agents"
    assert targets.result_dir == tmp_path / "sub-agents" / "results"
    assert service.source_commit_lock() is registry._lock  # noqa: SLF001 - typed maintenance API proof.
    with service.maintenance_lock():
        assert True


def test_service_forwards_bounded_registry_shutdown(tmp_path: Path) -> None:
    registry = SubAgentTaskRegistry(tmp_path / "sub-agents", roles=[], handlers={})
    service = SubAgentCollaborationService.from_registry_for_testing(registry)

    assert service.shutdown(timeout_seconds=0) == {
        "ok": True,
        "shutdown": True,
        "cancelRequestedTaskIds": [],
        "joinedTaskIds": [],
        "timedOutTaskIds": [],
        "timedOutContinuationTaskIds": [],
        "cancellationErrors": [],
    }


def test_parent_stop_while_report_is_pending_delivers_cancel_instead_of_report(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    wait_entered = threading.Event()
    deliveries = []
    role = SubAgentRole("fixture", "Fixture", "Read-only fixture.")

    def wait_parent_idle(session_id, *, cancelled):
        wait_entered.set()
        return gateway.wait_runtime_session_idle(session_id, cancelled=cancelled)

    service = SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=tmp_path / "sub-agents",
        gateway=SubAgentExecutionPorts(gateway.runtime_skills.execute, gateway.run_sub_agent_llm, wait_parent_idle),
        lane_budget=RuntimeLaneBudget(),
        build_roles=lambda: [role],
        build_handlers=lambda _: {"fixture": lambda _payload, _cancel: {"ok": True, "summaryText": "Finished report"}},
        task_finished=deliveries.append,
    ))
    gateway.bind_runtime_subagent_control(active_parent_task_ids=service.active_parent_task_ids, cancel_task=service.cancel_task)
    session = {"session_id": "parent", "turn_id": "turn", "client_turn_id": "client"}
    assert gateway.runtime_sessions.begin_turn(**session)
    task = service.create_task(
        role="fixture", task="Read receipt", display_name="Child", parent_session_id="parent",
        params={"_taskSeed": {"sessionId": "parent", "turnId": "turn", "clientTurnId": "client"}},
    )["task"]
    try:
        assert wait_entered.wait(3)
        assert service.get_task(task["id"])["task"]["parentContinuationStatus"] == "pending"
        stopped = gateway.request_runtime_cancel({"sessionId": "parent", "turnId": "turn", "clientTurnId": "client"})
        assert stopped["cancelledSubAgentTaskIds"] == [task["id"]]
        assert deliveries == []
    finally:
        with gateway._runtime_continuation_condition:
            gateway.runtime_sessions.finish_turn(**session)
            gateway.runtime_sessions.clear_finalizing_turn(session_id="parent", client_turn_id="client")
            gateway._runtime_continuation_condition.notify_all()
    worker = service._registry._threads[task["id"]]
    worker.join(3)
    assert not worker.is_alive()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "cancelled"
    assert deliveries[0]["result"] is None
    assert service.get_task(task["id"])["task"]["summary"] == "Finished report"


def test_service_has_no_dashboard_host_or_dynamic_lookup_seam() -> None:
    source = Path("sub_agent_collaboration_service.py").read_text(encoding="utf-8")
    dashboard_source = Path("dashboard_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "dashboard_server" not in source
    assert "sys.modules" not in source
    assert "__getattr__" not in source
    assert "SUB_AGENT_REGISTRY" not in dashboard_source
    assert "run_project_index_sub_agent" not in dashboard_source
    assert any(
        isinstance(node, ast.ClassDef) and node.name == "SubAgentCollaborationService"
        for node in tree.body
    )
