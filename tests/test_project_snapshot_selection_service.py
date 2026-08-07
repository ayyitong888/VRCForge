from __future__ import annotations

import ast
import json
import threading
from pathlib import Path
from typing import Any

import dashboard_server
from project_snapshot_selection_service import ProjectSnapshotSelectionPorts, ProjectSnapshotSelectionService


ROOT = Path(__file__).parents[1]
METHODS = {
    "project_snapshot_list",
    "project_snapshot_cache_document",
    "load_project_snapshot_cache",
    "project_snapshot_identity",
    "project_snapshot_label",
    "project_snapshot_changes",
    "annotate_project_snapshot",
    "empty_project_snapshot_payload",
    "_store_project_snapshot_cache",
    "refresh_project_snapshot_cache_sync",
    "schedule_project_snapshot_refresh",
    "bootstrap_project_snapshot_payload",
    "cached_project_snapshot_payload",
    "project_snapshot_payload",
    "canonical_selected_project_path",
    "load_persisted_selected_project_path",
    "persist_selected_project_path",
}


def make_service(tmp_path: Path, *, build_snapshot=None, atomic_write=None, broadcasts=None) -> ProjectSnapshotSelectionService:
    state = {"selected": "", "editor": ""}
    write = atomic_write or (lambda path, payload: path.parent.mkdir(parents=True, exist_ok=True) or path.write_text(json.dumps(payload), encoding="utf-8"))
    ports = ProjectSnapshotSelectionPorts(
        build_snapshot=build_snapshot or (lambda: {"selectedProjectPath": "", "unityEditorPath": "", "projects": []}),
        selected_project_path=lambda: state["selected"],
        unity_editor_path=lambda: state["editor"],
        normalize_path=lambda value: str(Path(value)).replace("\\", "/") if value else "",
        is_unity_project_path=lambda path: all((path / name).exists() for name in ("Assets", "Packages", "ProjectSettings")),
        atomic_write_json=write,
        utc_now_iso=lambda: "2026-08-08T00:00:00+00:00",
        broadcast_projects=broadcasts.append if broadcasts is not None else lambda _payload: None,
    )
    return ProjectSnapshotSelectionService(
        ports,
        cache_path=tmp_path / "project-cache.json",
        selection_path=tmp_path / "config" / "selected-project.json",
        selection_schema="vrcforge.selected_project.v1",
    )


def test_snapshot_selection_has_explicit_ports_owner_and_narrow_root_facades() -> None:
    service_source = (ROOT / "project_snapshot_selection_service.py").read_text(encoding="utf-8")
    dashboard_source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    service = next(node for node in ast.parse(service_source).body if isinstance(node, ast.ClassDef) and node.name == "ProjectSnapshotSelectionService")
    implementation_names = {node.name for node in service.body if isinstance(node, ast.FunctionDef) and node.name != "__init__" and node.name != "_scan_state"}
    assert implementation_names == METHODS
    assert "dashboard_server import" not in service_source
    assert "sys.modules" not in service_source
    assert "__getattr__" not in service_source
    assert "DashboardEventBus" not in service_source
    assert "Thread(target=run_refresh, name=\"vrcforge-project-discovery\", daemon=True)" in service_source
    assert ProjectSnapshotSelectionService.__slots__[0] == "_ports"
    assert "PROJECT_SNAPSHOT_CACHE =" not in dashboard_source
    assert "PROJECT_SELECTION_PATH =" not in dashboard_source

    facades = {
        node.name: node
        for node in ast.parse(dashboard_source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in METHODS
    }
    assert set(facades) == METHODS
    for facade in facades.values():
        assert len(facade.body) == 1
        assert isinstance(facade.body[0], ast.Return)
        assert "_PROJECT_SNAPSHOT_SELECTION" in ast.unparse(facade.body[0])


def test_snapshot_cache_fails_closed_and_returns_deep_copies(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    service.cache_path.write_text('{"schema":"wrong","snapshot":[]}', encoding="utf-8")
    assert service.load_project_snapshot_cache() is None
    assert service.cached_project_snapshot_payload(refresh_async=False)["projects"] == []

    payload = {"selectedProjectPath": "", "unityEditorPath": "", "projects": [{"name": "Original", "path": "P"}]}
    service._store_project_snapshot_cache(payload, started_at="start", duration_ms=1)
    first = service.cached_project_snapshot_payload(refresh_async=False)
    first["projects"][0]["name"] = "Mutated response"
    second = service.cached_project_snapshot_payload(refresh_async=False)
    assert second["projects"][0]["name"] == "Original"


def test_cache_write_failure_keeps_new_memory_snapshot_and_exposes_error(tmp_path: Path) -> None:
    def fail_write(_path: Path, _payload: Any) -> None:
        raise OSError("disk full")

    service = make_service(tmp_path, atomic_write=fail_write)
    result = service.project_snapshot_payload(use_cache=False)
    cached = service.cached_project_snapshot_payload(refresh_async=False)
    assert result["scan"]["error"] == "Project cache write failed: disk full"
    assert cached["scan"]["error"] == "Project cache write failed: disk full"
    assert cached["projects"] == []


def test_selection_write_verifies_atomic_readback_and_never_guesses(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True, exist_ok=True)
    service = make_service(tmp_path)
    expected = service.persist_selected_project_path(project)
    assert service.load_persisted_selected_project_path() == expected

    def wrong_readback(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**payload, "selectedProjectPath": "wrong"}), encoding="utf-8")

    drift = make_service(tmp_path / "drift", atomic_write=wrong_readback)
    try:
        drift.persist_selected_project_path(project)
    except OSError as exc:
        assert "readback drifted" in str(exc)
    else:  # pragma: no cover - guards the persistence contract
        raise AssertionError("selection persistence accepted a mismatched readback")
    assert drift.load_persisted_selected_project_path() == ""


def test_async_refresh_is_single_flight_and_broadcasts_one_deep_copy(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    broadcasts: list[dict[str, Any]] = []

    def build_snapshot() -> dict[str, Any]:
        started.set()
        assert release.wait(timeout=5)
        return {"selectedProjectPath": "", "unityEditorPath": "", "projects": [{"name": "Only", "path": "P"}]}

    service = make_service(tmp_path, build_snapshot=build_snapshot, broadcasts=broadcasts)
    assert service.schedule_project_snapshot_refresh(force=True) is True
    assert started.wait(timeout=5)
    thread = service._refresh_thread
    assert thread is not None
    assert service.schedule_project_snapshot_refresh(force=True) is False
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert service._refresh_thread is None
    assert len(broadcasts) == 1
    broadcasts[0]["projects"][0]["name"] = "Mutated broadcast"
    assert service.cached_project_snapshot_payload(refresh_async=False)["projects"][0]["name"] == "Only"
