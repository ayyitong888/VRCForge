import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
from types import SimpleNamespace
from typing import Any
import zipfile
import tempfile


def functions(scope, *names):
    path = Path(__file__).parents[1] / "dashboard_server.py"
    nodes = [n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), scope)


def doctor_scope():
    data = {"ok": True, "schema": "vrcforge.restore_failure_doctor.v1", "recoveries": [{
        "id": "incident-fixture", "diagnostics": [{"phase": "prepare_restore", "exceptionType": "ValueError", "message": "redacted failure", "requestId": "restore-request-1"}]}]}
    scope = {"Any": Any, "DOCTOR_READINESS_REPORT": SimpleNamespace(build_app_doctor_report=lambda: {"ok": True}),
             "data_root_status_payload": lambda: {},
             "AGENT_GATEWAY": SimpleNamespace(checkpoint_recovery=SimpleNamespace(doctor_restore_diagnostics=lambda params: data, list_checkpoints=lambda params: []), approval_transactions=SimpleNamespace(recent_audit_logs=lambda **kwargs: []))}
    functions(scope, "read_agentic_app_doctor")
    return scope, data


def test_actual_doctor_route_includes_redacted_restore_diagnostics():
    scope, data = doctor_scope()
    assert scope["read_agentic_app_doctor"]()["restoreFailureDiagnostics"] == data


def test_actual_support_bundle_exports_doctor_failure_phase_type_request_id(tmp_path):
    scope, data = doctor_scope()
    scope.update({"SupportBundleRequest": SimpleNamespace, "SUPPORT_BUNDLE_DIR": tmp_path,
        "datetime": datetime, "timezone": timezone, "secrets": secrets, "os": os,
        "tempfile": tempfile, "zipfile": zipfile, "Path": Path,
        "app": SimpleNamespace(version="fixture"), "runtime_paths": SimpleNamespace(PORTABLE_MODE=True),
        "DIAGNOSTIC_LOGGER": SimpleNamespace(log_level="info", tail_lines=lambda limit: []),
        "debug_logging_enabled": lambda: False,
        "build_agentic_app_bootstrap_payload": lambda **kwargs: {},
        "diagnostics_state": lambda: {}, "safety_posture_state": lambda: {},
        "SUB_AGENT_COLLABORATION": SimpleNamespace(recent_events=lambda **kwargs: [], list_tasks=lambda **kwargs: []),
        "write_support_bundle_member": lambda bundle, name, value, *args: bundle.writestr(name, json.dumps(value)),
        "write_support_bundle_text_member": lambda bundle, name, value: bundle.writestr(name, ""),
        "emit_log": lambda *args: None, "to_artifact_url": lambda value: value})
    functions(scope, "build_support_bundle")
    result = scope["build_support_bundle"](SimpleNamespace(log_limit=10, include_full_paths=False))
    with zipfile.ZipFile(result["bundlePath"]) as archive:
        doctor = json.loads(archive.read("doctor.json"))
    assert doctor["restoreFailureDiagnostics"] == data
    diagnostic = doctor["restoreFailureDiagnostics"]["recoveries"][0]["diagnostics"][0]
    assert diagnostic["phase"] == "prepare_restore"
    assert diagnostic["exceptionType"] == "ValueError"
    assert diagnostic["requestId"] == "restore-request-1"
