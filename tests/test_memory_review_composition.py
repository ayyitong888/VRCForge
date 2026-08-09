from __future__ import annotations

import ast
import asyncio
import inspect
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_gateway import AgentGateway
from agent_memory_store import AgentMemoryStore
from background_goal_runtime import ProviderPreflightCache, RuntimeLaneBudget
from memory_review_composition import (
    MemoryReviewCompositionPorts,
    build_memory_review_composition,
)
from memory_review_dashboard_adapter import MemoryReviewDashboardAdapter
from memory_review_runtime import MemoryReviewIdleGate, MemoryReviewRuntimeCoordinator


def _adapter(root: Path) -> MemoryReviewDashboardAdapter:
    return MemoryReviewDashboardAdapter(
        project_snapshot=lambda: {"projects": []},
        selected_project_path=lambda: "",
        indexed_project_paths=lambda: [],
        requested_project_paths=lambda: [],
        resolve_project_root=lambda _candidate: None,
        chat_lock=threading.RLock(),
        chat_transcripts_path=lambda: root / "chats.json",
        project_chat_transcripts_path=lambda _project_root: None,
        chat_store_target=lambda *_args, **_kwargs: None,
        load_chat_transcript_file=lambda *_args, **_kwargs: (
            [],
            {"status": "missing"},
            None,
        ),
        list_tasks=lambda: {"tasks": []},
        audit_log_path=lambda: root / "gateway-audit.jsonl",
        load_provider_settings=lambda: object(),
        normalize_provider=lambda value: str(value or ""),
        provider_display_name=lambda value: str(value or ""),
        provider_requires_api_key=lambda _provider: False,
    )


def test_gateway_runtime_and_desktop_activity_use_constructor_bound_signal(tmp_path: Path) -> None:
    reasons: list[str] = []
    gateway = AgentGateway(
        tmp_path / "config.json",
        tmp_path / "audit",
        background_activity_started=reasons.append,
    )

    with pytest.raises(RuntimeError, match="runtime planner is not bound"):
        gateway.runtime_message({"message": "Trigger the interactive lane."})
    assert reasons == ["runtime_message"]

    gateway.request_desktop_action(
        {
            "action": "annotation",
            "prompt": "Record a local annotation.",
        }
    )
    assert reasons == ["runtime_message", "desktop_action"]


def test_memory_review_composition_has_no_reverse_root_or_host_proxy() -> None:
    source = inspect.getsource(__import__("memory_review_composition"))
    tree = ast.parse(source)
    imported_roots = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert "dashboard_server" not in imported_roots
    assert "agent_gateway" not in imported_roots
    assert "sys" not in imported_roots
    assert not any(
        isinstance(node, (ast.Attribute, ast.Name))
        and getattr(node, "attr", getattr(node, "id", "")) == "_host"
        for node in ast.walk(tree)
    )
    assert "__getattr__" not in source
    assert "_impl_" not in source
    assert "gateway:" not in source
    assert "gateway =" not in source


def test_memory_review_composition_owns_one_graph_with_narrow_ports(tmp_path: Path) -> None:
    shared_lock = threading.RLock()
    audit_lock = threading.Lock()
    chat_lock = threading.RLock()
    task_lock = threading.RLock()
    accepted_store = AgentMemoryStore(
        lambda: tmp_path / "agent-memory.jsonl",
        lambda: tmp_path / "accepted-memory-audit.jsonl",
        lock=shared_lock,
    )
    events: list[tuple[str, Any]] = []
    idle_gate = MemoryReviewIdleGate()

    async def broadcast(event_type: str, payload: Any) -> None:
        events.append((event_type, payload))

    async def no_wait(_seconds: float) -> None:
        return None

    lane_budget = RuntimeLaneBudget()
    ports = MemoryReviewCompositionPorts(
        accepted_memory_store=accepted_store,
        review_root=lambda: tmp_path / "memory-review",
        shared_state_lock=shared_lock,
        audit_append_lock=audit_lock,
        list_memory=lambda limit, project_root: {
            "limit": limit,
            "projectRoot": project_root,
        },
        acquire_background_project_read=lambda _token: True,
        release_background_project_read=lambda _token: True,
        idle_gate=idle_gate,
        lane_budget=lane_budget,
        preflight=ProviderPreflightCache(lambda _provider, _url: True),
        build_runtime=lambda budget, preflight, on_state: MemoryReviewRuntimeCoordinator(
            lane_budget=budget,
            preflight=preflight,
            on_state=on_state,
            sleep=no_wait,
        ),
        adapter=_adapter(tmp_path),
        broadcast=broadcast,
        emit_warning=lambda _failure_class: None,
        provider_call=lambda _settings, _payload, _token_cap: {},
        chat_lock=chat_lock,
        sub_agent_source_commit_lock=lambda: task_lock,
    )

    composition = build_memory_review_composition(ports)

    assert composition.service.accepted_store is accepted_store
    assert composition.service.transaction_lock is shared_lock
    assert composition.host.service is composition.service
    assert composition.host.runtime is composition.runtime
    assert composition.source_commit_lock._locks == (  # noqa: SLF001
        shared_lock,
        shared_lock,
        chat_lock,
        task_lock,
        audit_lock,
    )
    assert composition.idle_gate is idle_gate
    assert lane_budget._interactive_acquire_callback == idle_gate.signal_activity  # noqa: SLF001
    asyncio.run(composition.notify_review_changed())
    assert events == [("agentMemoryReview", {"changed": True})]
