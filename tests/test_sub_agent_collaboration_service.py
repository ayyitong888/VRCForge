from __future__ import annotations

import ast
from pathlib import Path

from background_goal_runtime import RuntimeLaneBudget
from sub_agent_collaboration_service import SubAgentCollaborationPorts, SubAgentCollaborationService
from sub_agent_delegate import build_sub_agent_role_handlers, build_sub_agent_roles
from sub_agent_tasks import SubAgentTaskRegistry


class _Gateway:
    def execute_runtime_skill(self, *_args, **_kwargs):  # pragma: no cover - handlers are not invoked here.
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
