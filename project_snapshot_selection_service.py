from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock, Thread
from typing import Any, Callable


PROJECT_SNAPSHOT_CACHE_SCHEMA = "vrcforge.project_snapshot_cache.v1"


@dataclass(frozen=True)
class ProjectSnapshotSelectionPorts:
    """Dashboard-provided operations; this service owns no Dashboard globals."""

    build_snapshot: Callable[[], dict[str, Any]]
    selected_project_path: Callable[[], str]
    unity_editor_path: Callable[[], str]
    normalize_path: Callable[[str], str]
    is_unity_project_path: Callable[[Path], bool]
    atomic_write_json: Callable[[Path, Any], None]
    utc_now_iso: Callable[[], str]
    broadcast_projects: Callable[[dict[str, Any]], None]


class ProjectSnapshotSelectionService:
    """Own cached project discovery and durable selected-project state.

    Cache and selection state are private to one backend instance.  The service
    owns its two locks and may create at most one daemon discovery thread at a
    time.  Its Dashboard ports are supplied at construction, so it does not
    import or proxy the Dashboard module, EventBus, or process/network owners.
    """

    __slots__ = (
        "_ports",
        "cache_path",
        "selection_path",
        "selection_schema",
        "cache_ttl_seconds",
        "_cache_lock",
        "_selection_lock",
        "_cache",
        "_refreshing",
        "_updated_at",
        "_started_at",
        "_last_error",
        "_last_duration_ms",
        "_last_changes",
        "_cache_monotonic",
        "_refresh_started_monotonic",
        "_cache_loaded",
        "_refresh_thread",
    )

    def __init__(
        self,
        ports: ProjectSnapshotSelectionPorts,
        *,
        cache_path: Path,
        selection_path: Path,
        selection_schema: str,
        cache_ttl_seconds: float = 20.0,
    ) -> None:
        self._ports = ports
        self.cache_path = cache_path
        self.selection_path = selection_path
        self.selection_schema = selection_schema
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache_lock = Lock()
        self._selection_lock = RLock()
        self._cache: dict[str, Any] | None = None
        self._refreshing = False
        self._updated_at = ""
        self._started_at = ""
        self._last_error = ""
        self._last_duration_ms = 0
        self._last_changes: dict[str, Any] = {}
        self._cache_monotonic = 0.0
        self._refresh_started_monotonic = 0.0
        self._cache_loaded = False
        self._refresh_thread: Thread | None = None

    def project_snapshot_list(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def project_snapshot_cache_document(self, payload: dict[str, Any], *, updated_at: str, duration_ms: int) -> dict[str, Any]:
        return {
            "schema": PROJECT_SNAPSHOT_CACHE_SCHEMA,
            "updatedAt": updated_at,
            "durationMs": duration_ms,
            "snapshot": {
                "selectedProjectPath": str(payload.get("selectedProjectPath") or ""),
                "unityEditorPath": str(payload.get("unityEditorPath") or ""),
                "projects": [project for project in self.project_snapshot_list(payload.get("projects")) if isinstance(project, dict)],
            },
        }

    def load_project_snapshot_cache(self) -> dict[str, Any] | None:
        with self._cache_lock:
            if self._cache_loaded:
                return copy.deepcopy(self._cache) if self._cache is not None else None
            self._cache_loaded = True
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("schema") != PROJECT_SNAPSHOT_CACHE_SCHEMA:
            return None
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else None
        if not isinstance(snapshot, dict):
            return None
        cached = {
            "selectedProjectPath": str(snapshot.get("selectedProjectPath") or ""),
            "unityEditorPath": str(snapshot.get("unityEditorPath") or ""),
            "projects": [project for project in self.project_snapshot_list(snapshot.get("projects")) if isinstance(project, dict)],
        }
        with self._cache_lock:
            self._cache = copy.deepcopy(cached)
            self._updated_at = str(payload.get("updatedAt") or "")
            self._started_at = ""
            self._last_error = ""
            self._last_duration_ms = int(payload.get("durationMs") or 0)
            self._last_changes = {"addedProjects": [], "removedProjects": [], "addedCount": 0, "removedCount": 0}
            self._cache_monotonic = 0.0
        return copy.deepcopy(cached)

    def project_snapshot_identity(self, project: dict[str, Any]) -> str:
        path = self._ports.normalize_path(str(project.get("path") or ""))
        if path:
            return path.casefold()
        name = str(project.get("name") or project.get("projectName") or "").strip().casefold()
        cli_instance = str(project.get("cliInstanceId") or project.get("sessionId") or "").strip().casefold()
        return f"name:{name}:{cli_instance}"

    def project_snapshot_label(self, project: dict[str, Any]) -> dict[str, str]:
        return {
            "name": str(project.get("name") or project.get("projectName") or "Active Unity Instance"),
            "path": self._ports.normalize_path(str(project.get("path") or "")),
            "source": ",".join(str(item) for item in self.project_snapshot_list(project.get("sources"))) or str(project.get("source") or ""),
        }

    def project_snapshot_changes(self, previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
        previous_projects = [project for project in self.project_snapshot_list((previous or {}).get("projects")) if isinstance(project, dict)]
        current_projects = [project for project in self.project_snapshot_list(current.get("projects")) if isinstance(project, dict)]
        previous_by_key = {self.project_snapshot_identity(project): project for project in previous_projects if self.project_snapshot_identity(project)}
        current_by_key = {self.project_snapshot_identity(project): project for project in current_projects if self.project_snapshot_identity(project)}
        added_keys = sorted(set(current_by_key) - set(previous_by_key))
        removed_keys = sorted(set(previous_by_key) - set(current_by_key))
        return {
            "addedProjects": [self.project_snapshot_label(current_by_key[key]) for key in added_keys[:20]],
            "removedProjects": [self.project_snapshot_label(previous_by_key[key]) for key in removed_keys[:20]],
            "addedCount": len(added_keys),
            "removedCount": len(removed_keys),
            "projectCount": len(current_projects),
        }

    def _scan_state(self, *, error: str = "") -> dict[str, Any]:
        return {
            "refreshing": self._refreshing,
            "updatedAt": self._updated_at,
            "startedAt": self._started_at,
            "durationMs": self._last_duration_ms,
            "error": error or self._last_error,
            **copy.deepcopy(self._last_changes),
        }

    def annotate_project_snapshot(self, payload: dict[str, Any], *, status: str, cached: bool, error: str = "") -> dict[str, Any]:
        annotated = copy.deepcopy(payload)
        with self._cache_lock:
            scan = self._scan_state(error=error)
        annotated["scan"] = {"status": status, "cached": cached, **scan}
        return annotated

    def empty_project_snapshot_payload(self, *, status: str = "pending") -> dict[str, Any]:
        with self._cache_lock:
            scan = self._scan_state()
        return {
            "selectedProjectPath": self._ports.selected_project_path(),
            "unityEditorPath": self._ports.unity_editor_path(),
            "projects": [],
            "scan": {"status": status, "cached": True, **scan},
        }

    def _store_project_snapshot_cache(self, payload: dict[str, Any], *, started_at: str, duration_ms: int) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        candidate = copy.deepcopy(payload)
        with self._cache_lock:
            previous = copy.deepcopy(self._cache) if self._cache is not None else None
            changes = self.project_snapshot_changes(previous, candidate)
            self._cache = candidate
            self._updated_at = completed_at
            self._started_at = started_at
            self._last_error = ""
            self._last_duration_ms = duration_ms
            self._last_changes = changes
            self._cache_monotonic = time.monotonic()
        try:
            self._ports.atomic_write_json(
                self.cache_path,
                self.project_snapshot_cache_document(candidate, updated_at=completed_at, duration_ms=duration_ms),
            )
        except OSError as exc:
            with self._cache_lock:
                self._last_error = f"Project cache write failed: {exc}"

    def refresh_project_snapshot_cache_sync(self) -> dict[str, Any]:
        started_monotonic = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        with self._cache_lock:
            self._refreshing = True
            self._started_at = started_at
            self._last_error = ""
            self._refresh_started_monotonic = started_monotonic
        try:
            payload = self._ports.build_snapshot()
            duration_ms = round((time.monotonic() - started_monotonic) * 1000)
            self._store_project_snapshot_cache(payload, started_at=started_at, duration_ms=int(duration_ms))
            result = self.annotate_project_snapshot(payload, status="ready", cached=False)
            result["scan"]["refreshing"] = False
            return result
        except Exception as exc:  # noqa: BLE001 - discovery must not take down app startup.
            with self._cache_lock:
                self._last_error = str(exc)
            raise
        finally:
            with self._cache_lock:
                self._refreshing = False

    def schedule_project_snapshot_refresh(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        with self._cache_lock:
            if self._refreshing:
                return False
            cache_is_fresh = self._cache is not None and (now - self._cache_monotonic) < self.cache_ttl_seconds
            if cache_is_fresh and not force:
                return False
            recently_started = (now - self._refresh_started_monotonic) < 1.0
            if recently_started and not force:
                return False
            self._refreshing = True
            self._started_at = started_at
            self._last_error = ""
            self._refresh_started_monotonic = now

        def run_refresh() -> None:
            result: dict[str, Any] | None = None
            try:
                payload = self._ports.build_snapshot()
                duration_ms = round((time.monotonic() - now) * 1000)
                self._store_project_snapshot_cache(payload, started_at=started_at, duration_ms=int(duration_ms))
                result = self.annotate_project_snapshot(payload, status="ready", cached=False)
            except Exception as exc:  # noqa: BLE001
                with self._cache_lock:
                    self._last_error = str(exc)
            finally:
                with self._cache_lock:
                    self._refreshing = False
                    self._refresh_thread = None
            if result is not None:
                result["scan"]["refreshing"] = False
                self._ports.broadcast_projects(result)

        thread = Thread(target=run_refresh, name="vrcforge-project-discovery", daemon=True)
        with self._cache_lock:
            self._refresh_thread = thread
        thread.start()
        return True

    def bootstrap_project_snapshot_payload(self) -> dict[str, Any]:
        return self.cached_project_snapshot_payload(refresh_async=True, force_refresh=True)

    def cached_project_snapshot_payload(self, *, refresh_async: bool = True, force_refresh: bool = False) -> dict[str, Any]:
        self.load_project_snapshot_cache()
        if refresh_async:
            self.schedule_project_snapshot_refresh(force=force_refresh)
        with self._cache_lock:
            cached = copy.deepcopy(self._cache) if self._cache is not None else None
            refreshing = self._refreshing
            error = self._last_error
        if cached is None:
            return self.empty_project_snapshot_payload(status="refreshing" if refreshing else "pending")
        status = "refreshing" if refreshing else ("error" if error else "ready")
        return self.annotate_project_snapshot(cached, status=status, cached=True, error=error)

    def project_snapshot_payload(self, *, use_cache: bool = False, refresh_async: bool = True) -> dict[str, Any]:
        if use_cache:
            return self.cached_project_snapshot_payload(refresh_async=refresh_async)
        started_monotonic = time.monotonic()
        started_at = datetime.now(timezone.utc).isoformat()
        payload = self._ports.build_snapshot()
        self._store_project_snapshot_cache(
            payload,
            started_at=started_at,
            duration_ms=int(round((time.monotonic() - started_monotonic) * 1000)),
        )
        return self.annotate_project_snapshot(payload, status="ready", cached=False)

    def canonical_selected_project_path(self, value: Any) -> str:
        """Return one existing Unity project root or an explicit empty selection."""
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            candidate = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("Selected Unity project does not exist.") from exc
        if not candidate.is_dir() or not self._ports.is_unity_project_path(candidate):
            raise ValueError("Selected path is not a Unity project root.")
        return self._ports.normalize_path(str(candidate))

    def load_persisted_selected_project_path(self) -> str:
        with self._selection_lock:
            try:
                payload = json.loads(self.selection_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                return ""
        if not isinstance(payload, dict) or payload.get("schema") != self.selection_schema:
            return ""
        try:
            return self.canonical_selected_project_path(payload.get("selectedProjectPath"))
        except ValueError:
            return ""

    def persist_selected_project_path(self, value: Any) -> str:
        selected = self.canonical_selected_project_path(value)
        payload = {
            "schema": self.selection_schema,
            "selectedProjectPath": selected,
            "updatedAt": self._ports.utc_now_iso(),
        }
        with self._selection_lock:
            self._ports.atomic_write_json(self.selection_path, payload)
            verified = json.loads(self.selection_path.read_text(encoding="utf-8"))
        if not isinstance(verified, dict) or verified.get("schema") != self.selection_schema:
            raise OSError("Selected Unity project persistence verification failed.")
        if str(verified.get("selectedProjectPath") or "") != selected:
            raise OSError("Selected Unity project persistence readback drifted.")
        return selected
