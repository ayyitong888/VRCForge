"""Immutable application-lifetime composition for the existing Memory Review lane.

This module creates no process, listener, file handle, or external endpoint.  It
only connects the already-owned Gateway store/lock, dashboard source adapter,
runtime coordinator, and FastAPI-facing host into one explicit graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

from agent_memory_store import AgentMemoryStore
from background_goal_runtime import ProviderPreflightCache, RuntimeLaneBudget
from memory_consolidation import MemoryConsolidationService
from memory_consolidation_sources import MemoryScope
from memory_review_dashboard_adapter import MemoryReviewDashboardAdapter, MemoryReviewSourceCommitLock
from memory_review_host import MemoryReviewHost, MemoryReviewProviderContext, MemoryReviewSourceInventory
from memory_review_runtime import MemoryReviewIdleGate, MemoryReviewRuntimeCoordinator


Broadcast = Callable[[str, Any], Awaitable[None]]
ProviderCall = Callable[[Any, Mapping[str, Any], int], Mapping[str, Any]]


class LockPort(Protocol):
    """The narrow lock capability shared by the existing durable stores."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...

    def release(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MemoryReviewCompositionPorts:
    """Typed dependencies supplied by the dashboard composition root."""

    accepted_memory_store: AgentMemoryStore
    review_root: Callable[[], Path]
    shared_state_lock: LockPort
    audit_append_lock: LockPort
    list_memory: Callable[[int, str], dict[str, Any]]
    acquire_background_project_read: Callable[[str], bool]
    release_background_project_read: Callable[[str], bool]
    bind_background_activity: Callable[[Callable[[str], Any]], None]
    lane_budget: RuntimeLaneBudget
    preflight: ProviderPreflightCache
    build_runtime: Callable[[RuntimeLaneBudget, ProviderPreflightCache, Callable[..., Any]], MemoryReviewRuntimeCoordinator]
    adapter: MemoryReviewDashboardAdapter
    broadcast: Broadcast
    emit_warning: Callable[[str], None]
    provider_call: ProviderCall
    chat_lock: LockPort
    sub_agent_source_commit_lock: Callable[[], LockPort]


@dataclass(frozen=True, slots=True)
class MemoryReviewComposition:
    """One immutable graph for the already-existing Memory Review owners."""

    service: MemoryConsolidationService
    adapter: MemoryReviewDashboardAdapter
    runtime: MemoryReviewRuntimeCoordinator
    idle_gate: MemoryReviewIdleGate
    source_commit_lock: MemoryReviewSourceCommitLock
    host: MemoryReviewHost
    broadcast: Broadcast

    def authorized_project_roots(self) -> list[str]:
        return self.adapter.authorized_project_roots()

    def resolve_scope(self, scope: str = "", project_root: str = "") -> tuple[MemoryScope, str]:
        return self.adapter.resolve_scope(
            scope,
            project_root,
            authorized_project_roots=self.authorized_project_roots(),
        )

    def runtime_summary(self, project_root: str = "") -> dict[str, Any]:
        """Project the bounded Memory Review fields used by the runtime sidebar."""

        summary: dict[str, Any] = {
            "revision": 0,
            "unreadCount": 0,
            "runStatus": "idle",
            "needsAttention": False,
            "failureClass": "",
        }
        try:
            review_project = ""
            if str(project_root or "").strip():
                _scope, review_project = self.resolve_scope("project", project_root)
            review = self.host.snapshot(requested_project_root=review_project)
            review_status = review.get("runStatus") if isinstance(review.get("runStatus"), dict) else {}
            last_run = review.get("lastRun") if isinstance(review.get("lastRun"), dict) else {}
            last_status = str((last_run or {}).get("status") or "").strip().casefold()
            failure_class = str(
                (last_run or {}).get("failureClass")
                or (last_run or {}).get("deferredReason")
                or ""
            ).strip().casefold()
            return {
                "revision": int(review.get("revision") or 0),
                "unreadCount": int(review.get("unreadCount") or 0),
                "runStatus": str((review_status or {}).get("state") or "idle"),
                "needsAttention": bool(last_status in {"failed", "timed_out", "skipped"}),
                "failureClass": failure_class,
            }
        except Exception:  # noqa: BLE001 - runtime sidebar summary fails closed.
            return summary

    async def notify_review_changed(self) -> None:
        await self.broadcast("agentMemoryReview", {"changed": True})


def build_memory_review_composition(ports: MemoryReviewCompositionPorts) -> MemoryReviewComposition:
    """Build the single graph without introducing a second Gateway store or lock."""

    service = MemoryConsolidationService(
        ports.review_root,
        accepted_memory_store=ports.accepted_memory_store,
        lock=ports.shared_state_lock,
    )
    adapter = ports.adapter
    idle_gate = MemoryReviewIdleGate()

    async def on_changed(_state: dict[str, Any] | None = None) -> None:
        await ports.broadcast("agentMemoryReview", {"changed": True})

    async def on_memory_changed(project_root: str) -> None:
        await ports.broadcast(
            "agentMemory",
            ports.list_memory(30, project_root),
        )

    runtime = ports.build_runtime(ports.lane_budget, ports.preflight, on_changed)
    source_commit_lock = MemoryReviewSourceCommitLock(
        service.transaction_lock,
        ports.shared_state_lock,
        ports.chat_lock,
        ports.sub_agent_source_commit_lock(),
        ports.audit_append_lock,
    )

    def resolve_scope(scope: str = "", project_root: str = "") -> tuple[MemoryScope, str]:
        return adapter.resolve_scope(
            scope,
            project_root,
            authorized_project_roots=adapter.authorized_project_roots(),
        )

    def root_for_scope_key(scope_key: str) -> str:
        return adapter.project_root_for_scope_key(
            scope_key,
            authorized_project_roots=adapter.authorized_project_roots(),
        )

    def bounded_warning(failure_class: str) -> None:
        ports.emit_warning(str(failure_class or "runtime"))

    host = MemoryReviewHost(
        service=service,
        runtime=runtime,
        resolve_scope=resolve_scope,
        collect_sources=adapter.collect_sources,
        load_provider_context=adapter.load_provider_context,
        provider_call=ports.provider_call,
        on_changed=on_changed,
        on_memory_changed=on_memory_changed,
        root_for_scope_key=root_for_scope_key,
        acquire_background_lease=ports.acquire_background_project_read,
        release_background_lease=ports.release_background_project_read,
        on_bounded_warning=bounded_warning,
        source_commit_lock=source_commit_lock,
        idle_gate=idle_gate,
    )
    ports.lane_budget.set_interactive_acquire_callback(idle_gate.signal_activity)
    ports.bind_background_activity(idle_gate.signal_activity)
    return MemoryReviewComposition(
        service=service,
        adapter=adapter,
        runtime=runtime,
        idle_gate=idle_gate,
        source_commit_lock=source_commit_lock,
        host=host,
        broadcast=ports.broadcast,
    )
