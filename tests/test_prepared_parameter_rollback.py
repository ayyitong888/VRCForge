from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server
from prepared_unity_execution import (
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
    prepared_evidence,
)


def _snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict | None = None) -> Path:
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    path = snapshot_root / "rollback.json"
    path.write_bytes(json.dumps(payload or {
        "avatarPath": "Assets/A.prefab",
        "parameterNames": [{
            "name": "A",
            "valueType": "Bool",
            "defaultValue": 0.0,
            "saved": True,
            "networkSynced": True,
        }],
        "parameterCount": 1,
    }).encode("utf-8"))
    monkeypatch.setattr(dashboard_server, "PARAMETER_SNAPSHOT_DIR", snapshot_root)
    return path


def _prepared(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[dict, Path]:
    path = _snapshot(monkeypatch, tmp_path)
    arguments, _ = dashboard_server.prepare_rollback_parameter_optimization_request(
        {"snapshot_path": str(path)}, None
    )
    return arguments, path


def test_rollback_preparer_freezes_exact_call_and_raw_snapshot_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    arguments, path = _prepared(monkeypatch, tmp_path)

    assert build_prepared_execution_plan(arguments) == [
        ("vrc_rollback_avatar_parameters", {"avatarPath": "Assets/A.prefab", "parameterNames": [{
            "name": "A",
            "valueType": "Bool",
            "defaultValue": 0.0,
            "saved": True,
            "networkSynced": True,
        }]})
    ]
    evidence = prepared_evidence(arguments)
    assert evidence["snapshotPath"] == str(path.resolve())
    assert evidence["snapshotSha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_rollback_executes_only_the_sealed_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    arguments, path = _prepared(monkeypatch, tmp_path)
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(
        dashboard_server,
        "invoke_unity_mcp",
        lambda _settings, tool, call_arguments: calls.append((tool, call_arguments))
        or dashboard_server.McpResult(0, "", "", {"data": {"restoredCount": 1}}),
    )

    result = dashboard_server.rollback_parameter_optimization_sync(arguments)

    assert calls == [("vrc_rollback_avatar_parameters", {"avatarPath": "Assets/A.prefab", "parameterNames": [{
        "name": "A",
        "valueType": "Bool",
        "defaultValue": 0.0,
        "saved": True,
        "networkSynced": True,
    }]})]
    assert result["snapshotPath"] == str(path.resolve())
    assert result["restoredCount"] == 1


@pytest.mark.parametrize("mutation", ["tamper", "delete"])
def test_rollback_snapshot_drift_or_deletion_blocks_core(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str) -> None:
    arguments, path = _prepared(monkeypatch, tmp_path)
    if mutation == "tamper":
        path.write_text('{"avatarPath":"Assets/A.prefab","parameterNames":[]}', encoding="utf-8")
    else:
        path.unlink()
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: SimpleNamespace())
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", lambda *_args: (_ for _ in ()).throw(AssertionError("Core must not be called")))

    with pytest.raises(Exception, match="snapshot"):
        dashboard_server.rollback_parameter_optimization_sync(arguments)


def test_rollback_preparer_rejects_non_object_parameter_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _snapshot(
        monkeypatch,
        tmp_path,
        {"avatarPath": "Assets/A.prefab", "parameterNames": ["A"], "parameterCount": 1},
    )
    with pytest.raises(RuntimeError, match="not an object"):
        dashboard_server.prepare_rollback_parameter_optimization_request(
            {"snapshot_path": str(path)}, None
        )


def test_rollback_preparer_rejects_caller_injected_internal_seal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _snapshot(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        dashboard_server.prepare_rollback_parameter_optimization_request(
            {"snapshot_path": str(path), PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, None
        )


def test_rollback_registration_uses_preparer_and_sealed_plan() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_rollback_parameters"]  # noqa: SLF001
    assert handler.request_preparer is dashboard_server.prepare_rollback_parameter_optimization_request
    assert handler.requires_approved_execution_context is True
    assert handler.approved_execution_plan_builder is build_prepared_execution_plan
