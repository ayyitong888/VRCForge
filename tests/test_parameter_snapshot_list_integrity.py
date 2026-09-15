"""A broken snapshot must never prepare a destructive empty restoration."""
import json

import pytest

import dashboard_server as dashboard
from prepared_unity_execution import build_prepared_execution_plan


@pytest.mark.parametrize("fields", [
    {}, {"parameterNames": None}, {"parameterNames": False},
    {"parameterNames": ""}, {"parameters": None},
])
def test_snapshot_requires_an_explicit_list(monkeypatch, tmp_path, fields):
    monkeypatch.setattr(dashboard, "PARAMETER_SNAPSHOT_DIR", tmp_path)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"avatarPath": "Avatar", **fields}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="parameter"):
        dashboard.prepare_rollback_parameter_optimization_request({"snapshot_path": str(snapshot)}, None)


@pytest.mark.parametrize("field", ["parameterNames", "parameters"])
def test_explicit_empty_snapshot_remains_restorable(monkeypatch, tmp_path, field):
    monkeypatch.setattr(dashboard, "PARAMETER_SNAPSHOT_DIR", tmp_path)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"avatarPath": "Avatar", field: []}), encoding="utf-8")
    prepared, _ = dashboard.prepare_rollback_parameter_optimization_request({"snapshot_path": str(snapshot)}, None)
    assert build_prepared_execution_plan(prepared)[0][1]["parameterNames"] == []
