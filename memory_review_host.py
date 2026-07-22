"""FastAPI-facing orchestration for bounded Memory Review.

This domain host owns API presentation, provider-run coordination, candidate
mutations, and the single background task. Project discovery and transcript
loading stay injected so this module cannot reach Unity or arbitrary files.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Mapping, Sequence

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from background_goal_runtime import TOTAL_PROVIDER_ATTEMPTS
from memory_consolidation import (
    CandidateStateError,
    MemoryConsolidationError,
    MemoryConsolidationService,
    RevisionConflictError,
    StoreCorruptionError,
)
from memory_consolidation_sources import (
    MemoryScope,
    ScopeResolutionError,
    SourceProjection,
    project_scope_key,
)
from memory_review_provider import MemoryReviewProviderError
from memory_review_runtime import (
    MemoryReviewCommitDeferred,
    MemoryReviewIdleGate,
    MemoryReviewRuntimeCoordinator,
)


@dataclass(frozen=True)
class MemoryReviewProviderContext:
    settings: Any
    provider: str
    provider_label: str
    model: str
    base_url: str
    credential_ready: bool


@dataclass(frozen=True)
class MemoryReviewSourceInventory:
    sources: tuple[SourceProjection, ...]
    complete_source_types: frozenset[str]
    reason_counts: Mapping[str, int]


class MemoryReviewConfigRequest(BaseModel):
    mode: Literal["off", "shadow", "suggest_only", "bounded_background", "auto_safe"] = "off"
    cadence_minutes: int = Field(default=1440, alias="cadenceMinutes", ge=30, le=10080)
    input_char_cap: int = Field(default=12000, alias="inputCharCap", ge=1000, le=50000)
    token_cap: int = Field(default=2048, alias="tokenCap", ge=128, le=8192)
    cost_cap_usd: float = Field(default=0.0, alias="costCapUsd", ge=0.0, le=100.0)
    input_cost_per_million_usd: float = Field(
        default=0.0,
        alias="inputCostPerMillionUsd",
        ge=0.0,
        le=1_000_000.0,
    )
    output_cost_per_million_usd: float = Field(
        default=0.0,
        alias="outputCostPerMillionUsd",
        ge=0.0,
        le=1_000_000.0,
    )
    retention_days: int = Field(default=30, alias="retentionDays", ge=1, le=365)
    scope: Literal["user", "project"] = "user"
    provider: str = Field(default="", max_length=120)
    model: str = Field(default="", max_length=160)
    project_root: str | None = Field(default=None, alias="projectRoot")
    expected_revision: int = Field(default=0, alias="expectedRevision", ge=0)

    model_config = {"populate_by_name": True}


class MemoryReviewRunRequest(BaseModel):
    scope: Literal["user", "project"] = "user"
    project_root: str | None = Field(default=None, alias="projectRoot")
    expected_revision: int = Field(default=0, alias="expectedRevision", ge=0)

    model_config = {"populate_by_name": True}


class MemoryReviewCancelRequest(BaseModel):
    run_id: str = Field(alias="runId", min_length=1, max_length=200)

    model_config = {"populate_by_name": True}


class MemoryReviewCandidateRequest(BaseModel):
    expected_revision: int = Field(default=0, alias="expectedRevision", ge=0)
    edited_text: str | None = Field(default=None, alias="editedText", max_length=2000)
    project_root: str | None = Field(default=None, alias="projectRoot")

    model_config = {"populate_by_name": True}


ResolveScope = Callable[[str, str], tuple[MemoryScope, str]]
CollectSources = Callable[
    [MemoryScope, str],
    MemoryReviewSourceInventory | Sequence[SourceProjection],
]
LoadProviderContext = Callable[[], MemoryReviewProviderContext]
ProviderCall = Callable[[Any, Mapping[str, Any], int], Mapping[str, Any]]
ChangedCallback = Callable[[], Any]
MemoryChangedCallback = Callable[[str], Any]
ScopeRootLookup = Callable[[str], str]
BackgroundBlocker = Callable[[], str]
BoundedWarning = Callable[[str], Any]
BackgroundLeaseAcquire = Callable[[str], bool]
BackgroundLeaseRelease = Callable[[str], bool]


class MemoryReviewHost:
    def __init__(
        self,
        *,
        service: MemoryConsolidationService,
        runtime: MemoryReviewRuntimeCoordinator,
        resolve_scope: ResolveScope,
        collect_sources: CollectSources,
        load_provider_context: LoadProviderContext,
        provider_call: ProviderCall,
        on_changed: ChangedCallback,
        on_memory_changed: MemoryChangedCallback,
        root_for_scope_key: ScopeRootLookup,
        acquire_background_lease: BackgroundLeaseAcquire,
        release_background_lease: BackgroundLeaseRelease,
        on_bounded_warning: BoundedWarning | None = None,
        source_commit_lock: Any | None = None,
        idle_gate: MemoryReviewIdleGate | None = None,
    ) -> None:
        self.service = service
        self.runtime = runtime
        self._resolve_scope = resolve_scope
        self._collect_sources = collect_sources
        self._load_provider_context = load_provider_context
        self._provider_call = provider_call
        self._on_changed = on_changed
        self._on_memory_changed = on_memory_changed
        self._root_for_scope_key = root_for_scope_key
        self._acquire_background_lease = acquire_background_lease
        self._release_background_lease = release_background_lease
        self._on_bounded_warning = on_bounded_warning
        self._source_commit_lock = source_commit_lock or threading.RLock()
        self._idle_gate = idle_gate or MemoryReviewIdleGate()
        self._background_task: asyncio.Task[dict[str, Any]] | None = None
        self._active_run_tasks: dict[str, asyncio.Task[Any]] = {}
        self._active_run_tasks_lock = threading.RLock()

    @property
    def background_active(self) -> bool:
        return self._background_task is not None and not self._background_task.done()

    def _available_root_for_scope_key(self, scope_key: str) -> str:
        try:
            return str(self._root_for_scope_key(str(scope_key or "")) or "")
        except (OSError, RuntimeError, ValueError):
            return ""

    @staticmethod
    async def _await_callback(callback: Callable[..., Any], *args: Any) -> None:
        value = callback(*args)
        if inspect.isawaitable(value):
            await value

    @staticmethod
    async def _thread_transaction(
        callback: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, bool]:
        """Drain one synchronous transaction before honoring cancellation."""

        worker = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))
        cancelled = False
        while True:
            try:
                return await asyncio.shield(worker), cancelled
            except asyncio.CancelledError:
                cancelled = True

    @staticmethod
    def _usage(value: Any) -> dict[str, Any]:
        usage = dict(value) if isinstance(value, dict) else {}
        if "costUsd" not in usage and isinstance(usage.get("cost"), (int, float)):
            usage["costUsd"] = float(usage["cost"])
        return usage

    @staticmethod
    def _cost_upper_bound_usd(
        *,
        input_char_cap: int,
        token_cap: int,
        input_cost_per_million_usd: float,
        output_cost_per_million_usd: float,
    ) -> float:
        # A UTF-8 code point occupies at most four bytes. Treating each byte as
        # a token is deliberately conservative and independent of tokenizer.
        input_token_upper = max(0, int(input_char_cap)) * 4
        return (
            input_token_upper * max(0.0, float(input_cost_per_million_usd))
            + max(0, int(token_cap)) * max(0.0, float(output_cost_per_million_usd))
        ) / 1_000_000.0

    def snapshot(self, *, requested_project_root: str = "") -> dict[str, Any]:
        internal = self.service.review_store.snapshot(include_internal=True)
        internal_config = internal.get("config") if isinstance(internal.get("config"), dict) else {}
        configured_scope_key = str(internal_config.get("projectScopeKey") or "")
        configured_project_available = bool(
            configured_scope_key and self._available_root_for_scope_key(configured_scope_key)
        )
        allow_unavailable_project_erase = bool(
            not str(requested_project_root or "").strip()
            and configured_scope_key
            and not configured_project_available
        )
        raw = self.service.snapshot(
            requested_project_root,
            allow_unavailable_project_erase=allow_unavailable_project_erase,
        )
        config = dict(raw.pop("config", {}) if isinstance(raw.get("config"), dict) else {})
        mode = str(raw.get("mode") or config.get("mode") or "off")
        try:
            provider_context = self._load_provider_context()
        except Exception:  # noqa: BLE001 - storage remains readable without provider config.
            provider_context = MemoryReviewProviderContext(None, "", "", "", "", False)

        configured_scope = str(
            config.get("scopeKind")
            or config.get("scope")
            or raw.get("configuredScope")
            or raw.get("scope")
            or "user"
        ).strip().casefold()
        if configured_scope not in {"user", "project"}:
            configured_scope = "user"
        candidates: list[dict[str, Any]] = []
        for item in raw.get("candidates", []):
            if not isinstance(item, dict):
                continue
            card = dict(item)
            card["scope"] = str(card.pop("scopeKind", card.get("scope") or "user"))
            if not isinstance(card.get("unread"), bool):
                card["unread"] = bool(
                    not card.get("eraseOnly")
                    and card.get("state") in {"proposed", "conflicting"}
                    and not card.get("readAt")
                )
            conflict_ids = [str(value) for value in (card.get("conflicts") or []) if str(value)]
            card["conflictCount"] = len(conflict_ids)
            conflict_kinds = {
                "candidate" if value.startswith("memcand_") else "accepted_memory"
                for value in conflict_ids
            }
            card["conflictExplanation"] = (
                "mixed"
                if len(conflict_kinds) > 1
                else next(iter(conflict_kinds), "none")
            )
            if isinstance(card.get("usage"), dict):
                card["usage"] = self._usage(card["usage"])
            for field in (
                "sourceReferences",
                "readAt",
                "acceptedText",
                "promotionId",
                "scopeKey",
                "projectRoot",
                "lastUndoneMemoryId",
                "priorMemoryIds",
                "confidenceFactors",
                "conflicts",
                "supersedes",
            ):
                card.pop(field, None)
            candidates.append(card)
        candidates.sort(
            key=lambda card: (
                card.get("state") == "conflicting",
                -int(card.get("confidenceScore") or 0),
                -int(card.get("evidenceCount") or 0),
                str(card.get("candidateId") or ""),
            )
        )

        raw_run_status = raw.get("runStatus")
        if isinstance(raw_run_status, dict):
            run_status = dict(raw_run_status)
        else:
            run_state = str(raw_run_status or "idle")
            run_status = {"state": run_state, "phase": run_state}
        last_run = dict(raw.get("lastRun") or {}) if isinstance(raw.get("lastRun"), dict) else None
        if last_run is not None:
            last_run["usage"] = self._usage(last_run.get("usage"))
            last_run.pop("scopeKey", None)
        usage = self._usage(raw.get("usage"))
        cadence = int(raw.get("cadenceMinutes") or config.get("cadenceMinutes") or 1440)
        input_cap = int(raw.get("inputCharCap") or config.get("inputCharCap") or 12000)
        token_cap = int(raw.get("tokenCap") or config.get("tokenCap") or 2048)
        cost_cap = float(raw.get("costCapUsd") or config.get("costCapUsd") or 0.0)
        input_price = float(
            raw.get("inputCostPerMillionUsd")
            or config.get("inputCostPerMillionUsd")
            or 0.0
        )
        output_price = float(
            raw.get("outputCostPerMillionUsd")
            or config.get("outputCostPerMillionUsd")
            or 0.0
        )
        retention_days = int(raw.get("retentionDays") or config.get("retentionDays") or 30)
        configured_provider = str(config.get("provider") or raw.get("provider") or "")
        configured_model = str(config.get("model") or raw.get("model") or "")
        paid_run = mode in {"suggest_only", "bounded_background"}
        provider_matches = not paid_run or (
            configured_provider == provider_context.provider
            and configured_model == provider_context.model
        )
        provider_label = (
            provider_context.provider_label
            if configured_provider == provider_context.provider
            else configured_provider
        )
        configured_project_matches = (
            configured_scope != "project"
            or bool(str(raw.get("projectRoot") or ""))
        )
        return {
            "ok": True,
            "schema": "vrcforge.memory_review_snapshot.v1",
            "mode": mode,
            "policyVersion": str(raw.get("policyVersion") or ""),
            "revision": int(raw.get("revision") or 0),
            "scope": configured_scope,
            "projectRoot": str(raw.get("projectRoot") or ""),
            "requestedProjectRoot": requested_project_root or "",
            "configuredProjectMatches": configured_project_matches,
            "cadenceMinutes": cadence,
            "inputCharCap": input_cap,
            "tokenCap": token_cap,
            "costCapUsd": cost_cap,
            "inputCostPerMillionUsd": input_price,
            "outputCostPerMillionUsd": output_price,
            "retentionDays": retention_days,
            "provider": configured_provider,
            "model": configured_model,
            "runStatus": run_status,
            "unreadCount": sum(1 for candidate in candidates if candidate.get("unread")),
            "candidates": candidates,
            "providerDisclosure": {
                "paidRun": paid_run,
                "provider": configured_provider,
                "providerLabel": provider_label,
                "model": configured_model,
                "activeConfigMatches": provider_matches,
                "cadenceMinutes": cadence,
                "inputCharCap": input_cap,
                "tokenCap": token_cap,
                "costCapUsd": cost_cap,
                "inputCostPerMillionUsd": input_price,
                "outputCostPerMillionUsd": output_price,
                "privacyScope": configured_scope,
            },
            "usage": usage,
            "nextRunAt": str(raw.get("nextRunAt") or ""),
            "lastRun": last_run,
            "shadowSummary": raw.get("shadowSummary"),
        }

    @staticmethod
    def _source_inventory(
        value: MemoryReviewSourceInventory | Sequence[SourceProjection],
    ) -> MemoryReviewSourceInventory:
        if isinstance(value, MemoryReviewSourceInventory):
            return value
        sources = tuple(source for source in value if isinstance(source, SourceProjection))
        return MemoryReviewSourceInventory(
            sources=sources,
            complete_source_types=frozenset(source.source_type for source in sources),
            reason_counts={},
        )

    def _collect_sources_with_project_lease(
        self,
        scope: MemoryScope,
        canonical_project: str,
        token: str,
    ) -> MemoryReviewSourceInventory:
        if scope.kind != "project":
            return self._source_inventory(self._collect_sources(scope, canonical_project))
        acquired = self._acquire_background_lease(token)
        if not acquired:
            raise MemoryReviewCommitDeferred("Project state is changing; retry the review scan.")
        try:
            return self._source_inventory(self._collect_sources(scope, canonical_project))
        finally:
            if not self._release_background_lease(token):
                raise MemoryConsolidationError("Memory Review project-read lease was lost.")

    async def update_config(self, request: MemoryReviewConfigRequest) -> dict[str, Any]:
        scope, canonical_project = self._resolve_scope(request.scope, request.project_root or "")
        provider = ""
        model = ""
        if request.mode in {"suggest_only", "bounded_background"}:
            provider_context = await asyncio.to_thread(self._load_provider_context)
            provider = provider_context.provider
            model = provider_context.model
            if not provider or not model:
                raise MemoryConsolidationError(
                    "Paid Memory Review requires an explicit provider and model."
                )
            if request.mode == "bounded_background" and not provider_context.credential_ready:
                raise MemoryConsolidationError(
                    "Background Memory Review requires a ready provider credential."
                )
        if request.mode in {"off", "shadow"} and (
            request.cost_cap_usd > 0
            or request.input_cost_per_million_usd > 0
            or request.output_cost_per_million_usd > 0
        ):
            raise MemoryConsolidationError("Provider pricing applies only to paid Memory Review modes.")
        if request.mode in {"suggest_only", "bounded_background"} and (
            (request.input_cost_per_million_usd > 0)
            != (request.output_cost_per_million_usd > 0)
        ):
            raise MemoryConsolidationError(
                "Provider pricing requires both input and output token prices."
            )
        if request.cost_cap_usd > 0:
            if (
                request.input_cost_per_million_usd <= 0
                or request.output_cost_per_million_usd <= 0
            ):
                raise MemoryConsolidationError(
                    "A monetary cap requires explicit input and output token prices."
                )
            worst_case_cost = self._cost_upper_bound_usd(
                input_char_cap=request.input_char_cap,
                token_cap=request.token_cap,
                input_cost_per_million_usd=request.input_cost_per_million_usd,
                output_cost_per_million_usd=request.output_cost_per_million_usd,
            )
            if worst_case_cost * TOTAL_PROVIDER_ATTEMPTS > request.cost_cap_usd:
                raise MemoryConsolidationError(
                    "The monetary cap is below the configured worst-case run cost."
                )
        await asyncio.to_thread(
            self.service.update_config,
            {
                "mode": request.mode,
                "cadenceMinutes": request.cadence_minutes,
                "inputCharCap": request.input_char_cap,
                "tokenCap": request.token_cap,
                "costCapUsd": request.cost_cap_usd,
                "inputCostPerMillionUsd": request.input_cost_per_million_usd,
                "outputCostPerMillionUsd": request.output_cost_per_million_usd,
                "retentionDays": request.retention_days,
                "provider": provider,
                "model": model,
                "scope": scope.kind,
                "scopeKind": scope.kind,
                "projectRoot": canonical_project,
                "projectScopeKey": scope.scope_key if scope.kind == "project" else "",
            },
            request.expected_revision,
        )
        await self._cancel_active_runs()
        await self._await_callback(self._on_changed)
        return self.snapshot(requested_project_root=canonical_project)

    def _register_active_run(self, run_id: str, task: asyncio.Task[Any]) -> None:
        with self._active_run_tasks_lock:
            self._active_run_tasks[run_id] = task

    def _unregister_active_run(self, run_id: str, task: asyncio.Task[Any]) -> None:
        with self._active_run_tasks_lock:
            if self._active_run_tasks.get(run_id) is task:
                self._active_run_tasks.pop(run_id, None)

    async def _cancel_active_runs(self, run_id: str = "") -> int:
        current = asyncio.current_task()
        with self._active_run_tasks_lock:
            selected = [
                (candidate_id, task)
                for candidate_id, task in self._active_run_tasks.items()
                if (not run_id or candidate_id == run_id)
                and task is not current
                and not task.done()
            ]
        same_loop: list[asyncio.Task[Any]] = []
        loop = asyncio.get_running_loop()
        for _candidate_id, task in selected:
            if task.get_loop() is loop:
                task.cancel()
                same_loop.append(task)
            else:
                task.get_loop().call_soon_threadsafe(task.cancel)
        if same_loop:
            await asyncio.gather(*same_loop, return_exceptions=True)
        return len(selected)

    async def cancel(self, request: MemoryReviewCancelRequest) -> dict[str, Any]:
        durable = await asyncio.to_thread(
            self.service.review_store.snapshot,
            include_internal=True,
        )
        run = next(
            (
                item
                for item in durable.get("runs", [])
                if isinstance(item, dict)
                and str(item.get("runId") or "") == request.run_id
            ),
            None,
        )
        if run is None:
            raise CandidateStateError("Memory Review run was not found.")
        cancelled = await self._cancel_active_runs(request.run_id)
        if cancelled == 0 and str(run.get("status") or "") == "running":
            await asyncio.to_thread(
                self._finish_run_safely,
                request.run_id,
                "cancelled",
            )
        await self._await_callback(self._on_changed)
        project_root = ""
        if str(run.get("scopeKind") or "") == "project":
            project_root = self._available_root_for_scope_key(str(run.get("scopeKey") or ""))
        return self.snapshot(requested_project_root=project_root)

    async def execute(
        self,
        *,
        scope_name: str,
        project_root: str,
        expected_revision: int,
        lane: str,
        background_generation: int = 0,
    ) -> dict[str, Any]:
        if lane == "interactive":
            self._idle_gate.signal_activity("manual_review")
        elif lane == "background" and not self._idle_gate.is_current(background_generation):
            raise MemoryReviewCommitDeferred("Background Memory Review lost its idle generation.")
        scope, canonical_project = self._resolve_scope(scope_name, project_root)
        snapshot = self.snapshot(requested_project_root=canonical_project)
        if int(snapshot["revision"]) != expected_revision:
            raise RevisionConflictError(
                f"Memory Review revision changed from {expected_revision} to {snapshot['revision']}."
            )
        mode = str(snapshot.get("mode") or "off")
        if mode == "off":
            raise MemoryConsolidationError("Memory Review is off.")
        durable = await asyncio.to_thread(self.service.review_store.snapshot, include_internal=True)
        config = durable.get("config") if isinstance(durable.get("config"), dict) else {}
        configured_scope = str(config.get("scopeKind") or config.get("scope") or "user")
        if configured_scope != scope.kind:
            raise MemoryConsolidationError("Memory Review run scope does not match its saved configuration.")
        if scope.kind == "project" and str(config.get("projectScopeKey") or "") != scope.scope_key:
            raise MemoryConsolidationError("Memory Review run project does not match its saved configuration.")
        inventory = await asyncio.to_thread(
            self._collect_sources_with_project_lease,
            scope,
            canonical_project,
            f"memory-review-scan:{scope.scope_key[-16:]}:{expected_revision}",
        )
        sources = list(inventory.sources)
        eligible_for_run = len(sources)
        if mode == "shadow":
            shadow_result = await asyncio.to_thread(
                self.service.shadow_scan,
                sources,
                expected_revision=expected_revision,
                scope=scope,
                reason_counts=inventory.reason_counts,
            )
            await self._await_callback(self._on_changed)
            response = self.snapshot(requested_project_root=canonical_project)
            if isinstance(shadow_result.get("shadowSummary"), dict):
                response["shadowSummary"] = dict(shadow_result["shadowSummary"])
            return response
        if mode not in {"suggest_only", "bounded_background"}:
            raise MemoryConsolidationError("Memory Review mode is not available.")
        configured_provider = str(config.get("provider") or "")
        configured_model = str(config.get("model") or "")
        input_cap = int(snapshot.get("inputCharCap") or 12000)
        token_cap = int(snapshot.get("tokenCap") or 2048)
        cost_cap = float(snapshot.get("costCapUsd") or 0.0)
        input_price = float(snapshot.get("inputCostPerMillionUsd") or 0.0)
        output_price = float(snapshot.get("outputCostPerMillionUsd") or 0.0)

        async def persist_preflight_deferral(
            reason: str,
            failure_class: str,
            *,
            selection: Mapping[str, Any] | None = None,
        ) -> dict[str, Any]:
            def persist_transaction() -> None:
                started_deferral = self.service.begin_provider_run(
                    scope=scope,
                    expected_revision=expected_revision,
                    provider=configured_provider,
                    model=configured_model,
                    budget={
                        "inputCharCap": input_cap,
                        "tokenCap": token_cap,
                        "costCapUsd": cost_cap,
                        "inputCostPerMillionUsd": input_price,
                        "outputCostPerMillionUsd": output_price,
                    },
                )
                deferred_run = (
                    started_deferral.get("run")
                    if isinstance(started_deferral, dict)
                    else {}
                )
                deferred_run_id = str((deferred_run or {}).get("runId") or "")
                deferred_revision = int(started_deferral.get("revision") or 0)
                if not deferred_run_id:
                    raise MemoryConsolidationError("Memory Review deferral did not start.")
                if selection is not None:
                    cursor_result = self.service.review_store.record_source_cursor(
                        scope_key=scope.scope_key,
                        cursor=str(selection.get("cursor") or ""),
                        skipped_oversized_count=int(
                            selection.get("skippedOversizedCount") or 0
                        ),
                        expected_revision=deferred_revision,
                    )
                    deferred_revision = int(
                        cursor_result.get("revision") or deferred_revision
                    )
                self.service.update_run_state(
                    deferred_run_id,
                    phase="failed",
                    failure_class=failure_class,
                    attempt=0,
                )
                self.service.finish_run(
                    deferred_run_id,
                    status="skipped",
                    non_consuming=True,
                    deferred_reason=reason,
                    eligible_count=eligible_for_run,
                    candidate_count=0,
                )

            _unused, cancelled = await self._thread_transaction(persist_transaction)
            await self._await_callback(self._on_changed)
            if cancelled:
                raise asyncio.CancelledError
            return self.snapshot(requested_project_root=canonical_project)

        try:
            provider_context = await asyncio.to_thread(self._load_provider_context)
        except Exception:  # noqa: BLE001 - persist a bounded schema deferral.
            return await persist_preflight_deferral("config_changed", "schema")
        if (
            configured_provider != provider_context.provider
            or configured_model != provider_context.model
        ):
            return await persist_preflight_deferral("config_changed", "schema")
        request_payload: dict[str, Any] | None = None
        request_cost_upper = 0.0
        selected_sources: list[SourceProjection] = []
        selected_source_identity: set[tuple[str, str, str]] = set()
        if sources:
            request_payload, selected_sources, selection = self.service.build_provider_request_with_selection(
                sources,
                scope,
                input_cap,
            )
            if not selected_sources:
                return await persist_preflight_deferral(
                    "input_oversized",
                    "schema",
                    selection=selection,
                )
            selected_source_identity = {
                (source.source_type, source.source_id, source.source_digest)
                for source in selected_sources
            }
            eligible_for_run = len(selected_sources)
            if input_price > 0 and output_price > 0:
                serialized = json.dumps(
                    request_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                request_cost_upper = (
                    len(serialized.encode("utf-8")) * input_price
                    + token_cap * output_price
                ) / 1_000_000.0
                if cost_cap > 0 and request_cost_upper * TOTAL_PROVIDER_ATTEMPTS > cost_cap:
                    raise MemoryConsolidationError(
                        "Memory Review run exceeds its monetary cap."
                    )
        if request_payload is not None and not provider_context.credential_ready:
            return await persist_preflight_deferral("auth", "auth")

        started, cancelled_during_begin = await self._thread_transaction(
            self.service.begin_provider_run,
            scope=scope,
            expected_revision=expected_revision,
            provider=provider_context.provider,
            model=provider_context.model,
            budget={
                "inputCharCap": input_cap,
                "tokenCap": token_cap,
                "costCapUsd": float(snapshot.get("costCapUsd") or 0.0),
                "inputCostPerMillionUsd": input_price,
                "outputCostPerMillionUsd": output_price,
            },
        )
        run = started.get("run") if isinstance(started, dict) else {}
        run_id = str((run or {}).get("runId") or "")
        working_revision = int(started.get("revision") or 0)
        if not run_id:
            raise MemoryConsolidationError("Memory Review run did not start.")
        if cancelled_during_begin:
            await self._thread_transaction(
                self._finish_run_safely,
                run_id,
                "cancelled",
                True,
                "interactive_activity",
                {},
                eligible_for_run,
                0,
            )
            await self._await_callback(self._on_changed)
            raise asyncio.CancelledError

        async def persist_run_state(signal: Mapping[str, Any]) -> None:
            nonlocal working_revision
            try:
                updated = await asyncio.to_thread(
                    self.service.update_run_state,
                    run_id,
                    phase=str(signal.get("phase") or "failed"),
                    failure_class=str(signal.get("failureClass") or ""),
                    attempt=int(signal.get("attempt") or 0),
                )
            except MemoryConsolidationError:
                durable_state = await asyncio.to_thread(
                    self.service.review_store.snapshot,
                    include_internal=True,
                )
                durable_run = next(
                    (
                        item
                        for item in durable_state.get("runs", [])
                        if isinstance(item, dict) and str(item.get("runId") or "") == run_id
                    ),
                    None,
                )
                if durable_run is not None and durable_run.get("status") != "running":
                    return
                raise
            working_revision = int(updated.get("revision") or working_revision)

        if request_payload is None:
            def finish_empty_run() -> None:
                def commit() -> None:
                    self.service.finish_provider_run(
                        run_id,
                        sources=sources,
                        provider_result={"candidates": [], "usage": {}},
                        expected_revision=working_revision,
                        input_char_cap=input_cap,
                        complete_source_types=inventory.complete_source_types,
                    )

                if lane == "background":
                    if not self._idle_gate.run_if_current(
                        background_generation,
                        commit,
                    ):
                        raise MemoryReviewCommitDeferred(
                            "Background Memory Review was revoked before empty commit."
                        )
                else:
                    commit()

            _unused, cancelled_during_commit = await self._thread_transaction(
                finish_empty_run
            )
            await self._await_callback(self._on_changed)
            if cancelled_during_commit:
                raise asyncio.CancelledError
            return self.snapshot(requested_project_root=canonical_project)

        provider_attempts = 0
        validated_usage: dict[str, Any] = {}

        def attempt_usage_evidence(attempts: int | None = None) -> dict[str, Any]:
            count = max(0, int(provider_attempts if attempts is None else attempts))
            if count <= 0:
                return {}
            usage: dict[str, Any] = {"attempts": count}
            if request_cost_upper > 0:
                usage["costUpperBoundUsd"] = request_cost_upper * count
                usage["costAccounting"] = "bounded_retry"
            else:
                usage["costAccounting"] = "retry_usage_unavailable"
            return usage

        def terminal_usage_evidence(attempts: int | None = None) -> dict[str, Any]:
            evidence = dict(validated_usage)
            evidence.update(attempt_usage_evidence(attempts))
            return evidence

        def provider_call() -> Mapping[str, Any]:
            nonlocal provider_attempts, validated_usage
            self.service.assert_provider_run_current(run_id)
            if lane == "background" and not self._idle_gate.is_current(background_generation):
                raise MemoryReviewCommitDeferred("Background Memory Review was revoked before provider work.")
            provider_attempts += 1
            raw_result = self._provider_call(
                provider_context.settings,
                request_payload or {},
                token_cap,
            )
            if not isinstance(raw_result, Mapping):
                raise MemoryConsolidationError("Provider candidate schema is invalid.")
            pricing = (
                {
                    "inputPerMillion": input_price,
                    "outputPerMillion": output_price,
                    "currency": "USD",
                }
                if input_price > 0 and output_price > 0
                else None
            )
            validated = self.service.validate_provider_result(
                raw_result,
                sources=selected_sources,
                scope=scope,
                pricing=pricing,
                attempts=provider_attempts,
                cost_upper_bound_usd=(
                    request_cost_upper * provider_attempts
                    if request_cost_upper > 0 and provider_attempts > 1
                    else None
                ),
            )
            validated_usage = self._usage(validated.get("usage"))
            actual_cost = validated.get("usage", {}).get("costUsd")
            if cost_cap > 0 and (
                not isinstance(actual_cost, (int, float))
                or isinstance(actual_cost, bool)
                or float(actual_cost) > cost_cap
            ):
                raise MemoryConsolidationError(
                    "Memory Review provider usage could not satisfy its monetary cap."
                )
            return validated

        def commit_provider_result(validated_result: Any) -> None:
            if not isinstance(validated_result, Mapping):
                raise MemoryConsolidationError("Memory Review provider result is invalid.")
            def commit_current_result() -> None:
                commit_token = f"memory-review-commit:{run_id}"
                commit_lease = False
                if scope.kind == "project":
                    commit_lease = self._acquire_background_lease(commit_token)
                    if not commit_lease:
                        raise MemoryReviewCommitDeferred(
                            "Project state is changing; retry the review commit."
                        )
                try:
                    with self._source_commit_lock:
                        fresh_inventory = self._source_inventory(
                            self._collect_sources(scope, canonical_project)
                        )
                        fresh_sources = list(fresh_inventory.sources)
                        fresh_identity = {
                            (source.source_type, source.source_id, source.source_digest)
                            for source in fresh_sources
                        }
                        if not selected_source_identity.issubset(fresh_identity):
                            raise MemoryConsolidationError(
                                "Memory Review evidence changed while the provider run was in flight."
                            )
                        self.service.finish_provider_run(
                            run_id,
                            sources=fresh_sources,
                            validated_result=validated_result,
                            expected_revision=working_revision,
                            input_char_cap=input_cap,
                            complete_source_types=fresh_inventory.complete_source_types,
                        )
                finally:
                    if commit_lease and not self._release_background_lease(commit_token):
                        raise MemoryConsolidationError("Memory Review project-read lease was lost.")

            if lane == "background":
                if not self._idle_gate.run_if_current(
                    background_generation,
                    commit_current_result,
                ):
                    raise MemoryReviewCommitDeferred(
                        "Background Memory Review was revoked before commit."
                    )
            else:
                commit_current_result()

        active_task = asyncio.current_task()
        if active_task is None:
            raise MemoryConsolidationError("Memory Review run has no active task.")
        self._register_active_run(run_id, active_task)
        try:
            runtime_result = await self.runtime.run(
                lane=lane,
                token=f"memory-review:{run_id}",
                provider=provider_context.provider,
                base_url=provider_context.base_url,
                call=provider_call,
                commit=commit_provider_result,
                on_run_state=persist_run_state,
                continue_guard=(
                    lambda: self._idle_gate.is_current(background_generation)
                    if lane == "background"
                    else True
                ),
            )
        except asyncio.CancelledError:
            non_consuming_cancel = lane == "background" and provider_attempts == 0
            await asyncio.to_thread(
                self._finish_run_safely,
                run_id,
                "cancelled",
                non_consuming_cancel,
                "interactive_activity" if non_consuming_cancel else "",
                terminal_usage_evidence(),
                eligible_for_run,
                0,
            )
            await self._await_callback(self._on_changed)
            raise
        except Exception:
            await asyncio.to_thread(
                self._finish_run_safely,
                run_id,
                "failed",
                False,
                "",
                terminal_usage_evidence(),
                eligible_for_run,
                0,
            )
            await self._await_callback(self._on_changed)
            raise
        finally:
            self._unregister_active_run(run_id, active_task)
        if not runtime_result.ok:
            if runtime_result.status == "cancelled":
                non_consuming = runtime_result.attempts == 0
                await asyncio.to_thread(
                    self._finish_run_safely,
                    run_id,
                    "cancelled",
                    non_consuming,
                    "interactive_activity" if non_consuming else "",
                    terminal_usage_evidence(runtime_result.attempts),
                    eligible_for_run,
                    0,
                )
                await self._await_callback(self._on_changed)
                return self.snapshot(requested_project_root=canonical_project)
            deferred_reason = (
                runtime_result.status
                if runtime_result.status in {"capacity", "duplicate", "provider_unreachable"}
                else runtime_result.failure_class
            )
            non_consuming = deferred_reason in {
                "auth",
                "capacity",
                "credit",
                "duplicate",
                "provider_unreachable",
                "schema",
            }
            if deferred_reason == "capacity" and runtime_result.attempts > 0:
                non_consuming = False
            terminal = "skipped" if non_consuming else "failed"
            await asyncio.to_thread(
                self._finish_run_safely,
                run_id,
                terminal,
                non_consuming,
                deferred_reason if non_consuming else "",
                terminal_usage_evidence(runtime_result.attempts),
                eligible_for_run,
                0,
            )
            await self._await_callback(self._on_changed)
            status_code = 409 if (
                runtime_result.status in {"capacity", "duplicate"}
                or runtime_result.failure_class == "capacity"
            ) else 503
            if runtime_result.failure_class in {"auth", "credit", "invalid_request", "schema"}:
                status_code = 502
            raise HTTPException(status_code=status_code, detail="Memory Review provider run did not complete.")
        await self._await_callback(self._on_changed)
        return self.snapshot(requested_project_root=canonical_project)

    def _finish_run_safely(
        self,
        run_id: str,
        status: str,
        non_consuming: bool = False,
        deferred_reason: str = "",
        usage: Mapping[str, Any] | None = None,
        eligible_count: int = 0,
        candidate_count: int = 0,
    ) -> None:
        try:
            self.service.finish_run(
                run_id,
                status=status,
                non_consuming=non_consuming,
                deferred_reason=deferred_reason,
                usage=usage,
                eligible_count=eligible_count,
                candidate_count=candidate_count,
            )
        except MemoryConsolidationError:
            pass

    async def mutate(
        self,
        candidate_id: str,
        action: str,
        request: MemoryReviewCandidateRequest,
    ) -> dict[str, Any]:
        if action not in {"accept", "reject", "defer", "erase", "undo", "read"}:
            raise CandidateStateError("Candidate action is not supported.")
        candidate = await asyncio.to_thread(self.service.review_store.get, candidate_id)
        if candidate is None:
            if action != "erase":
                raise CandidateStateError("Candidate was not found.")
            intent = await asyncio.to_thread(
                self.service.review_store.get_erase_intent,
                candidate_id,
            )
            if intent is not None:
                candidate = {
                    "scopeKind": str(intent.get("scopeKind") or ""),
                    "scopeKey": str(intent.get("scopeKey") or ""),
                }
            else:
                durable = await asyncio.to_thread(
                    self.service.review_store.snapshot,
                    include_internal=True,
                )
                if candidate_id not in set(durable.get("retiredCandidateIds") or []):
                    raise CandidateStateError("Candidate was not found.")
                retired_scope = await asyncio.to_thread(
                    self.service.review_store.get_retired_scope,
                    candidate_id,
                )
                if retired_scope is None:
                    raise CandidateStateError("Candidate erase receipt is unavailable.")
                candidate = retired_scope
        candidate_scope = str(candidate.get("scopeKind") or "")
        canonical_project = ""
        if candidate_scope == "project":
            if str(request.project_root or "").strip():
                try:
                    resolved, canonical_project = self._resolve_scope(
                        "project",
                        request.project_root or "",
                    )
                except ScopeResolutionError:
                    supplied_scope_key = project_scope_key(
                        request.project_root or "",
                        require_existing=False,
                    )
                    if (
                        action != "erase"
                        or supplied_scope_key != str(candidate.get("scopeKey") or "")
                        or self._available_root_for_scope_key(supplied_scope_key)
                    ):
                        raise
                    canonical_project = ""
                else:
                    if resolved.scope_key != str(candidate.get("scopeKey") or ""):
                        raise CandidateStateError("Project candidate does not match the current project scope.")
            elif action != "erase" or self._available_root_for_scope_key(str(candidate.get("scopeKey") or "")):
                raise CandidateStateError("Project candidate action requires the exact project root.")
        elif candidate_scope == "user" and str(request.project_root or "").strip():
            raise CandidateStateError("User candidate action cannot carry a project root.")
        mutation_kwargs: dict[str, Any] = {
            "expected_revision": request.expected_revision,
            "project_root": canonical_project,
            "edited_text": request.edited_text,
        }
        try:
            if action == "accept":
                scope = MemoryScope(
                    kind=candidate_scope,
                    scope_key=str(candidate.get("scopeKey") or ""),
                    project_root=canonical_project,
                )

                def accept_with_fresh_sources() -> None:
                    with self._source_commit_lock:
                        inventory = self._source_inventory(
                            self._collect_sources(scope, canonical_project)
                        )
                        self.service.mutate_candidate(
                            candidate_id,
                            action,
                            **mutation_kwargs,
                            current_sources=inventory.sources,
                            complete_source_types=inventory.complete_source_types,
                        )

                await asyncio.to_thread(accept_with_fresh_sources)
            else:
                await asyncio.to_thread(
                    self.service.mutate_candidate,
                    candidate_id,
                    action,
                    **mutation_kwargs,
                )
        except CandidateStateError:
            await self._await_callback(self._on_changed)
            raise
        await self._await_callback(self._on_changed)
        if action in {"accept", "undo", "erase"}:
            await self._await_callback(self._on_memory_changed, canonical_project)
        return self.snapshot(requested_project_root=canonical_project)

    def configured_background_scope(self) -> tuple[str, str, int] | None:
        state = self.service.review_store.snapshot(include_internal=True)
        config = state.get("config") if isinstance(state.get("config"), dict) else {}
        if str(config.get("mode") or "") != "bounded_background":
            return None
        scope_kind = str(config.get("scopeKind") or config.get("scope") or "user")
        if scope_kind == "user":
            return "user", "", int(state.get("revision") or 0)
        if scope_kind != "project":
            return None
        project_root = self._available_root_for_scope_key(str(config.get("projectScopeKey") or ""))
        if not project_root:
            return None
        return "project", project_root, int(state.get("revision") or 0)

    async def schedule_due_background(self, blocker: BackgroundBlocker) -> bool:
        if self.background_active:
            return False
        configured = self.configured_background_scope()
        if configured is None:
            return False
        scope_name, project_root, expected_revision = configured
        authorized_roots = [project_root] if scope_name == "project" else []
        due = await asyncio.to_thread(
            self.service.due_background,
            authorized_project_roots=authorized_roots,
        )
        if not due.get("due"):
            return False
        generation = self._idle_gate.try_acquire(
            blocker,
            self._cancel_background_from_idle_gate,
        )
        if generation is None:
            return False
        try:
            task = asyncio.create_task(
                self.execute(
                    scope_name=scope_name,
                    project_root=project_root,
                    expected_revision=expected_revision,
                    lane="background",
                    background_generation=generation,
                )
            )
        except BaseException:
            self._idle_gate.release(generation)
            raise
        self._background_task = task
        task.add_done_callback(
            lambda completed, active_generation=generation: self._background_done(
                completed,
                active_generation,
            )
        )
        return True

    def _cancel_background_from_idle_gate(self) -> None:
        task = self._background_task
        if task is None or task.done():
            return
        task.get_loop().call_soon_threadsafe(task.cancel)

    def _background_done(
        self,
        task: asyncio.Task[dict[str, Any]],
        generation: int,
    ) -> None:
        self._idle_gate.release(generation)
        if self._background_task is task:
            self._background_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except (HTTPException, MemoryConsolidationError, ScopeResolutionError, Exception):  # noqa: BLE001
            failure_class = "runtime"
            try:
                state = self.service.review_store.snapshot(include_internal=True)
                runs = [run for run in state.get("runs", []) if isinstance(run, dict)]
                if runs:
                    latest = max(runs, key=lambda run: str(run.get("updatedAt") or ""))
                    failure_class = str(
                        latest.get("failureClass")
                        or latest.get("deferredReason")
                        or "runtime"
                    )
            except Exception:  # noqa: BLE001 - diagnostics stay bounded.
                failure_class = "runtime"
            if self._on_bounded_warning is not None:
                self._on_bounded_warning(failure_class)
            try:
                changed = self._on_changed(None)
                if inspect.isawaitable(changed):
                    asyncio.create_task(changed)
            except Exception:  # noqa: BLE001 - warning delivery cannot affect durable state.
                pass

    async def shutdown(self) -> None:
        await self._cancel_active_runs()
        task = self._background_task
        self._background_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def reconcile_startup(self, project_roots: Sequence[str]) -> dict[str, Any]:
        return await asyncio.to_thread(self.service.reconcile_startup, project_roots)


def raise_memory_review_http_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, RevisionConflictError):
        raise HTTPException(status_code=409, detail="Memory Review state changed; refresh and retry.") from exc
    if isinstance(exc, MemoryReviewCommitDeferred):
        raise HTTPException(status_code=409, detail="Memory Review project state is busy; retry shortly.") from exc
    if isinstance(exc, CandidateStateError):
        status = 404 if "not found" in str(exc).casefold() else 400
        raise HTTPException(status_code=status, detail="Memory Review candidate is unavailable for that action.") from exc
    if isinstance(exc, ScopeResolutionError):
        raise HTTPException(status_code=400, detail="Memory Review scope is unavailable.") from exc
    if isinstance(exc, StoreCorruptionError):
        raise HTTPException(status_code=503, detail="Memory Review storage needs repair.") from exc
    if isinstance(exc, (MemoryConsolidationError, MemoryReviewProviderError, ValueError)):
        raise HTTPException(status_code=400, detail="Memory Review request failed validation.") from exc
    raise HTTPException(status_code=500, detail="Memory Review could not complete the durable operation.") from exc


def build_memory_review_router(host: MemoryReviewHost) -> APIRouter:
    router = APIRouter()

    @router.get("/api/app/agent/memory/review")
    def read_review(scope: str = "", projectRoot: str = "") -> dict[str, Any]:
        try:
            _resolved, canonical_project = host._resolve_scope(scope, projectRoot)
            return host.snapshot(requested_project_root=canonical_project)
        except ScopeResolutionError as exc:
            normalized_scope = str(scope or "").strip().casefold().replace("-", "_")
            if normalized_scope == "project" or (
                not normalized_scope and str(projectRoot or "").strip()
            ):
                # A removed or moved project cannot be re-authorized. Return
                # only metadata-only erase handles; the service never exposes
                # candidate prose without an exact live-root match.
                requested = str(projectRoot or "").strip()
                configured_scope_key = str(
                    host.service.review_store.snapshot(include_internal=True)
                    .get("config", {})
                    .get("projectScopeKey", "")
                )
                supplied_matches = not requested
                if requested:
                    try:
                        supplied_matches = (
                            project_scope_key(requested, require_existing=False)
                            == configured_scope_key
                        )
                    except (OSError, ValueError):
                        supplied_matches = False
                if supplied_matches:
                    fallback = host.snapshot(requested_project_root="")
                    erase_handles = [
                        candidate
                        for candidate in fallback.get("candidates", [])
                        if isinstance(candidate, dict) and candidate.get("eraseOnly") is True
                    ]
                    if fallback.get("scope") == "project" and erase_handles:
                        return fallback
            raise_memory_review_http_error(exc)
            raise AssertionError("unreachable")
        except Exception as exc:  # noqa: BLE001
            raise_memory_review_http_error(exc)
            raise AssertionError("unreachable")

    @router.post("/api/app/agent/memory/review/config")
    async def update_review(request: MemoryReviewConfigRequest) -> dict[str, Any]:
        try:
            return await host.update_config(request)
        except Exception as exc:  # noqa: BLE001
            raise_memory_review_http_error(exc)
            raise AssertionError("unreachable")

    @router.post("/api/app/agent/memory/review/run")
    async def run_review(request: MemoryReviewRunRequest) -> dict[str, Any]:
        try:
            return await host.execute(
                scope_name=request.scope,
                project_root=request.project_root or "",
                expected_revision=request.expected_revision,
                lane="interactive",
            )
        except Exception as exc:  # noqa: BLE001
            raise_memory_review_http_error(exc)
            raise AssertionError("unreachable")

    @router.post("/api/app/agent/memory/review/cancel")
    async def cancel_review(request: MemoryReviewCancelRequest) -> dict[str, Any]:
        try:
            return await host.cancel(request)
        except Exception as exc:  # noqa: BLE001
            raise_memory_review_http_error(exc)
            raise AssertionError("unreachable")

    @router.post("/api/app/agent/memory/review/candidates/{candidate_id}/{action}")
    async def mutate_candidate(
        candidate_id: str,
        action: str,
        request: MemoryReviewCandidateRequest,
    ) -> dict[str, Any]:
        try:
            return await host.mutate(candidate_id, action, request)
        except Exception as exc:  # noqa: BLE001
            raise_memory_review_http_error(exc)
            raise AssertionError("unreachable")

    return router


__all__ = [
    "MemoryReviewCandidateRequest",
    "MemoryReviewCancelRequest",
    "MemoryReviewConfigRequest",
    "MemoryReviewHost",
    "MemoryReviewProviderContext",
    "MemoryReviewSourceInventory",
    "MemoryReviewRunRequest",
    "build_memory_review_router",
    "raise_memory_review_http_error",
]
