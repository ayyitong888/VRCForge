"""Run the actual App callback with its transport replaced, never Unity or a provider."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def callback():
    source = Path(os.environ.get("VRCFORGE_CALLBACK_SOURCE", Path(__file__).parents[1] / "dashboard_server.py"))
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "prepare_unity_checkpoint_restore_sync")
    calls = []
    settings = SimpleNamespace(unity_mcp_timeout_seconds=30, unity_mcp_retries=9)
    def invoke(configuration, tool, arguments, **kwargs):
        calls.append((configuration, tool, arguments, kwargs))
        return {"ok": True, "recoveryEvidence": {"complete": True}}
    scope = {"Path": Path, "Any": Any, "CHECKPOINT_RELOAD_CALL_TIMEOUT_SECONDS": 20,
             "PrimitiveBasisLiveUnityConnection": type("FixtureLiveConnection", (), {}),
             "build_agent_connection_request": lambda value: value,
             "load_dashboard_settings": lambda value: settings,
             "invoke_unity_mcp": invoke,
             "normalize_unity_checkpoint_result": lambda value, root: value}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), scope)
    return scope[function.name], calls, settings


def test_failure_witness_uses_readonly_tool_exact_scope_no_transport_retry():
    run, calls, settings = callback()
    result = run(Path("E:/Fixture"), checkpoint_files=["Assets/Recovery.controller"], capture_evidence_only=True)
    assert result["recoveryEvidence"]["complete"]
    assert len(calls) == 1
    _, tool, args, options = calls[0]
    assert tool == "vrc_prepare_checkpoint"
    assert args == {"projectPath": str(Path("E:/Fixture")), "checkpointFiles": ["Assets/Recovery.controller"], "recoveryEvidenceOnly": True}
    assert options == {"execution_context": {"lane": "app_safety_control"}, "preserve_tool_error": True}
    assert settings.unity_mcp_retries == 0


def test_failure_witness_missing_scope_fails_without_calling_core():
    run, calls, _ = callback()
    result = run(Path("E:/Fixture"), checkpoint_files=[], capture_evidence_only=True)
    assert not result["ok"] and result["code"] == "recovery_evidence_scope_missing"
    assert not calls


def test_existing_restore_prepare_still_uses_original_tool_and_phase():
    run, calls, _ = callback()
    assert run(Path("E:/Fixture"), checkpoint_files=["Assets/Recovery.controller"])["ok"]
    assert calls[0][1] == "vrc_reload_after_checkpoint_restore"
    assert calls[0][2]["phase"] == "prepare_restore"
    assert "recoveryEvidenceOnly" not in calls[0][2]
