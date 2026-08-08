from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from background_goal_runtime import RuntimeLaneBudget
from sub_agent_tasks import (
    SUB_AGENT_LOG_SCHEMA,
    SUB_AGENT_RESULT_SCHEMA,
    SubAgentHandler,
    SubAgentRole,
    SubAgentTaskRegistry,
)


@dataclass(frozen=True)
class SubAgentCollaborationPorts:
    """Frozen dependencies for the durable sub-agent collaboration boundary."""

    artifact_dir: Path
    gateway: Any
    lane_budget: RuntimeLaneBudget
    build_roles: Callable[[], list[SubAgentRole]]
    build_handlers: Callable[[Any], dict[str, SubAgentHandler]]


@dataclass(frozen=True)
class SubAgentMaintenanceTargets:
    """Stable app-owned persistence paths for integrity and support consumers."""

    event_log_path: Path
    artifact_dir: Path
    result_dir: Path
    log_schema: str = SUB_AGENT_LOG_SCHEMA
    result_schema: str = SUB_AGENT_RESULT_SCHEMA


class SubAgentCollaborationService:
    """Own the existing durable sub-agent registry and its collaboration API.

    The registry continues to own its daemon workers, cancellation events,
    lifecycle JSONL, result sidecars and concurrency behavior. The Dashboard
    composition root keeps routes, authentication, EventBus broadcasts and the
    backend-owner decision; it supplies the shared runtime lane budget only.
    """

    __slots__ = ("_registry",)

    def __init__(self, ports: SubAgentCollaborationPorts) -> None:
        self._registry = SubAgentTaskRegistry(
            artifact_dir=ports.artifact_dir,
            roles=ports.build_roles(),
            handlers=ports.build_handlers(ports.gateway),
            max_concurrent=5,
            reconcile_on_init=False,
            lane_budget=ports.lane_budget,
        )

    @classmethod
    def from_registry_for_testing(cls, registry: SubAgentTaskRegistry) -> "SubAgentCollaborationService":
        """Inject one already-created registry without creating a second runtime."""

        service = cls.__new__(cls)
        service._registry = registry
        return service

    def reconcile_startup(self, *, refresh_from_disk: bool = False) -> bool:
        return self._registry.reconcile_startup(refresh_from_disk=refresh_from_disk)

    def list_tasks(self, *, include_events: bool = False, limit: int = 50) -> dict[str, Any]:
        return self._registry.list_tasks(include_events=include_events, limit=limit)

    def get_task(self, task_id: str, *, include_events: bool = True) -> dict[str, Any]:
        return self._registry.get_task(task_id, include_events=include_events)

    def create_task(
        self,
        *,
        role: str,
        task: str,
        display_name: str,
        parent_chat_id: str = "",
        parent_session_id: str = "",
        project_path: str = "",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._registry.create_task(
            role=role,
            task=task,
            display_name=display_name,
            parent_chat_id=parent_chat_id,
            parent_session_id=parent_session_id,
            project_path=project_path,
            params=params,
        )

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return self._registry.cancel_task(task_id)

    def retry_task(self, task_id: str) -> dict[str, Any]:
        return self._registry.retry_task(task_id)

    def merge_task(
        self,
        task_id: str,
        *,
        decision: str = "adopted",
        chat_id: str = "",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        return self._registry.merge_task(
            task_id,
            decision=decision,
            chat_id=chat_id,
            expected_revision=expected_revision,
        )

    def acknowledge_handoff(
        self,
        task_id: str,
        *,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        return self._registry.acknowledge_handoff(
            task_id,
            expected_revision=expected_revision,
        )

    def recent_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self._registry.recent_events(limit=limit)

    def maintenance_targets(self) -> SubAgentMaintenanceTargets:
        artifact_dir = self._registry.artifact_dir
        return SubAgentMaintenanceTargets(
            event_log_path=self._registry._event_log_path(),
            artifact_dir=artifact_dir,
            result_dir=artifact_dir / "results",
        )

    def source_commit_lock(self) -> Any:
        """Return the registry lock for the existing multi-store commit order."""

        return self._registry._lock

    @contextmanager
    def maintenance_lock(self) -> Iterator[None]:
        """Serialize app-owned session repair with registry lifecycle writes."""

        with self._registry._lock:
            yield
