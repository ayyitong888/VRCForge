from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from memory_consolidation import MemoryConsolidationError
from memory_consolidation_sources import (
    MemoryScope,
    ScopeResolutionError,
    admit_memory_sources,
    project_scope_key,
    redact_memory_text,
    resolve_memory_scope,
)
from memory_review_host import MemoryReviewProviderContext, MemoryReviewSourceInventory
from memory_review_inputs import (
    collect_adopted_task_records,
    collect_user_chat_records,
    collect_validated_project_records,
)


MEMORY_REVIEW_AUDIT_SCAN_MAX_BYTES = 2 * 1024 * 1024
MEMORY_REVIEW_AUDIT_SCAN_MAX_ROWS = 500


class MemoryReviewSourceCommitLock:
    """Hold all source writers in one documented order through commit."""

    def __init__(self, *locks: Any) -> None:
        self._locks = tuple(locks)

    def __enter__(self) -> "MemoryReviewSourceCommitLock":
        acquired: list[Any] = []
        try:
            for lock in self._locks:
                lock.acquire()
                acquired.append(lock)
        except BaseException:
            for lock in reversed(acquired):
                lock.release()
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        for lock in reversed(self._locks):
            lock.release()


@dataclass(frozen=True)
class MemoryReviewDashboardAdapter:
    """Bind Memory Review domain policy to the existing dashboard stores."""

    project_snapshot: Callable[[], dict[str, Any]]
    selected_project_path: Callable[[], str]
    indexed_project_paths: Callable[[], Iterable[str]]
    requested_project_paths: Callable[[], Iterable[str]]
    resolve_project_root: Callable[[str], Path | None]
    chat_lock: Any
    chat_transcripts_path: Callable[[], Path]
    project_chat_transcripts_path: Callable[[str], Path | None]
    chat_store_target: Callable[..., Any]
    load_chat_transcript_file: Callable[..., tuple[Any, Any, Any]]
    list_tasks: Callable[[], dict[str, Any]]
    audit_log_path: Callable[[], Path]
    load_provider_settings: Callable[[], Any]
    normalize_provider: Callable[[str], str]
    provider_display_name: Callable[[str], str]
    provider_requires_api_key: Callable[[str], bool]

    def authorized_project_roots(self) -> list[str]:
        candidates: list[str] = []
        try:
            snapshot = self.project_snapshot()
            candidates.extend(
                str(item.get("path") or "")
                for item in snapshot.get("projects", [])
                if isinstance(item, dict)
            )
        except Exception:  # noqa: BLE001 - an unavailable cache must fail closed.
            pass
        candidates.append(str(self.selected_project_path() or ""))
        candidates.extend(str(path or "") for path in self.indexed_project_paths())
        with self.chat_lock:
            candidates.extend(str(path or "") for path in self.requested_project_paths())

        roots: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            root = self.resolve_project_root(candidate)
            if root is None:
                continue
            resolved = str(root.resolve())
            normalized = os.path.normcase(resolved)
            if normalized in seen:
                continue
            seen.add(normalized)
            roots.append(resolved)
        return roots

    def resolve_scope(
        self,
        scope: str = "",
        project_root: str = "",
        *,
        authorized_project_roots: Iterable[str] | None = None,
    ) -> tuple[MemoryScope, str]:
        normalized_scope = str(scope or "").strip().casefold().replace("-", "_")
        if not normalized_scope:
            normalized_scope = "project" if str(project_root or "").strip() else "user"
        authorized = list(
            self.authorized_project_roots()
            if authorized_project_roots is None
            else authorized_project_roots
        )
        resolved = resolve_memory_scope(
            normalized_scope,
            project_root,
            authorized_project_roots=authorized if normalized_scope == "project" else None,
        )
        canonical_project = ""
        if resolved.kind == "project":
            canonical_project = next(
                (root for root in authorized if project_scope_key(root) == resolved.scope_key),
                "",
            )
            if not canonical_project:
                raise ScopeResolutionError("Project root is not in the authorized scope set.")
        return resolved, canonical_project

    def read_audit_inventory(self) -> tuple[list[dict[str, Any]], bool, str]:
        """Read one bounded audit snapshot without taking the gateway state lock."""

        audit_path = self.audit_log_path()
        try:
            if not audit_path.exists():
                return [], True, ""
            if not audit_path.is_file() or audit_path.is_symlink():
                return [], False, "audit_inventory_unsafe"
            with audit_path.open("rb") as handle:
                initial_size = os.fstat(handle.fileno()).st_size
                start = max(0, initial_size - MEMORY_REVIEW_AUDIT_SCAN_MAX_BYTES)
                handle.seek(start)
                raw = handle.read(
                    min(MEMORY_REVIEW_AUDIT_SCAN_MAX_BYTES, initial_size - start)
                )
                stable_size = os.fstat(handle.fileno()).st_size
        except OSError:
            return [], False, "audit_inventory_incomplete"

        if start:
            boundary = raw.find(b"\n")
            raw = raw[boundary + 1 :] if boundary >= 0 else b""
        raw_lines = [line for line in raw.splitlines() if line.strip()]
        truncated_rows = len(raw_lines) > MEMORY_REVIEW_AUDIT_SCAN_MAX_ROWS
        selected = raw_lines[-MEMORY_REVIEW_AUDIT_SCAN_MAX_ROWS :]
        events: list[dict[str, Any]] = []
        invalid = False
        for line in selected:
            try:
                parsed = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                invalid = True
                continue
            if not isinstance(parsed, dict):
                invalid = True
                continue
            events.append(parsed)
        complete = (
            start == 0
            and not truncated_rows
            and not invalid
            and stable_size == initial_size
        )
        return events, complete, "" if complete else "audit_inventory_incomplete"

    def collect_sources(
        self,
        scope: MemoryScope,
        canonical_project: str = "",
    ) -> MemoryReviewSourceInventory:
        """Read only exact-scope, allowlisted semantic source projections."""

        records: list[dict[str, Any]] = []
        complete_source_types: set[str] = set()
        reason_counts: dict[str, int] = {}

        def count_reason(reason: str, amount: int = 1) -> None:
            reason_counts[reason] = reason_counts.get(reason, 0) + max(0, int(amount))

        with self.chat_lock:
            if scope.kind == "user":
                path = self.chat_transcripts_path()
                chats, chat_source, _recovery = self.load_chat_transcript_file(
                    self.chat_store_target(path, scope="app"),
                    scope="app",
                    self_heal_app_owned=False,
                )
                records.extend(collect_user_chat_records(chats, scope="user"))
                if str(chat_source.get("status") or "") in {"ok", "missing"}:
                    complete_source_types.add("user_chat")
                else:
                    count_reason("chat_inventory_unavailable")
            else:
                path = self.project_chat_transcripts_path(canonical_project)
                if path is not None:
                    chats, chat_source, _recovery = self.load_chat_transcript_file(
                        self.chat_store_target(
                            path,
                            scope="project",
                            project_path=canonical_project,
                        ),
                        scope="project",
                        self_heal_app_owned=False,
                    )
                    records.extend(
                        collect_user_chat_records(
                            chats,
                            scope="project",
                            project_root=canonical_project,
                        )
                    )
                    if str(chat_source.get("status") or "") in {"ok", "missing"}:
                        complete_source_types.add("user_chat")
                    else:
                        count_reason("chat_inventory_unavailable")
                else:
                    count_reason("chat_inventory_unavailable")

        if scope.kind == "project":
            tasks_payload = self.list_tasks()
            tasks = tasks_payload.get("tasks") if isinstance(tasks_payload, dict) else []
            task_items = tasks if isinstance(tasks, list) else []
            records.extend(
                collect_adopted_task_records(
                    task_items,
                    project_root=canonical_project,
                )
            )
            if isinstance(tasks, list) and len(tasks) < 200:
                complete_source_types.add("adopted_task")
            else:
                count_reason("task_inventory_truncated")

            audit_events, audit_inventory_complete, audit_reason = self.read_audit_inventory()
            if audit_inventory_complete:
                complete_source_types.add("validated_project_result")
            else:
                count_reason(audit_reason or "audit_inventory_incomplete")
            records.extend(
                collect_validated_project_records(
                    audit_events,
                    project_root=canonical_project,
                )
            )

        admitted, admission_counts = admit_memory_sources(records, scope=scope)
        for key in ("admitted", "excluded", "invalid"):
            count_reason(key, int(admission_counts.get(key) or 0))
        return MemoryReviewSourceInventory(
            sources=tuple(admitted),
            complete_source_types=frozenset(complete_source_types),
            reason_counts=reason_counts,
        )

    def load_provider_context(self) -> MemoryReviewProviderContext:
        settings = self.load_provider_settings()
        provider = self.normalize_provider(settings.llm_provider)
        model = str(settings.llm_model or "").strip()
        for field, value, limit in (
            ("provider", provider, 120),
            ("model", model, 160),
        ):
            rescanned, report = redact_memory_text(value, limit=limit)
            if int(report.get("total", 0)) or rescanned != value:
                raise MemoryConsolidationError(
                    f"{field} metadata failed the privacy boundary."
                )
        return MemoryReviewProviderContext(
            settings=settings,
            provider=provider,
            provider_label=self.provider_display_name(provider),
            model=model,
            base_url=str(settings.llm_base_url or ""),
            credential_ready=(
                not self.provider_requires_api_key(provider)
                or bool(str(settings.llm_api_key or "").strip())
            ),
        )

    def project_root_for_scope_key(
        self,
        scope_key: str,
        *,
        authorized_project_roots: Iterable[str] | None = None,
    ) -> str:
        expected = str(scope_key or "")
        if not expected:
            return ""
        roots = (
            self.authorized_project_roots()
            if authorized_project_roots is None
            else authorized_project_roots
        )
        for project_root in roots:
            try:
                if project_scope_key(project_root) == expected:
                    return project_root
            except ScopeResolutionError:
                continue
        return ""
