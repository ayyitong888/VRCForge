from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

import dashboard_server
from dashboard_api_models import ParameterApplyOptimizationRequest


ROOT = Path(__file__).resolve().parents[1]


def _request() -> ParameterApplyOptimizationRequest:
    return ParameterApplyOptimizationRequest(
        projectPath="D:/Audit",
        avatarPath="Robot",
        dry_run=False,
        suggestions=[{"name": "Mood", "currentType": "Int", "suggestedType": "Bool"}],
    )


def _stub_dependencies(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda request: object())
    monkeypatch.setattr(dashboard_server.DASHBOARD_RUNTIME, "current_avatar_path", "Robot")
    monkeypatch.setattr(dashboard_server, "scan_avatar_parameters_direct", lambda settings, avatar: {"parameters": []})
    monkeypatch.setattr(
        dashboard_server,
        "save_parameter_snapshot_payload",
        lambda snapshot, avatar: {"snapshotPath": "probe", "snapshotUrl": "probe"},
    )
    monkeypatch.setattr(dashboard_server, "apply_parameter_optimization_direct", lambda settings, avatar, suggestions: payload)
    monkeypatch.setattr(dashboard_server, "emit_log", lambda *args, **kwargs: None)


def test_handler_returns_core_applied_count_after_a_real_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependencies(monkeypatch, {"ok": True, "appliedCount": 1, "applied": [{"name": "Mood"}]})

    result = dashboard_server.apply_parameter_optimization_sync(_request())

    assert result["ok"] is True
    assert result["appliedCount"] == 1
    assert result["result"]["appliedCount"] == 1


def test_handler_rejects_core_zero_applied_instead_of_claiming_requested_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependencies(monkeypatch, {"ok": True, "appliedCount": 0, "applied": []})

    with pytest.raises(Exception, match="appliedCount mismatch"):
        dashboard_server.apply_parameter_optimization_sync(_request())


def test_handler_rejects_nested_core_failure_and_invalid_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dependencies(monkeypatch, {"ok": True, "result": {"ok": False, "error": "nested rejection"}, "appliedCount": 1})
    with pytest.raises(Exception, match="nested rejection"):
        dashboard_server.apply_parameter_optimization_sync(_request())

    _stub_dependencies(monkeypatch, {"ok": True, "appliedCount": "1", "applied": []})
    with pytest.raises(Exception, match="valid appliedCount"):
        dashboard_server.apply_parameter_optimization_sync(_request())


def test_csharp_parameter_identity_is_exact_and_validated_before_mutation() -> None:
    source = (ROOT / "Assets/VRCForge/Editor/AvatarParameterWriter.cs").read_text(encoding="utf-8-sig")
    assert "name == requestedName" in source
    assert "StringComparer.Ordinal" in source
    assert "Exact parameter not found" in source
    assert "Parameter identity is ambiguous" in source
    assert "StringComparer.OrdinalIgnoreCase" not in source[source.index("var requestedNames"):source.index("var applied")]


def test_parameter_identity_harness_runs_mood_mood_missing_and_duplicate_cases(tmp_path: Path) -> None:
    """Compile/run the exact product identity method; Unity editor types are absent."""
    fixture = ROOT / "tests/fixtures/parameter_identity/ParameterIdentityHarness.cs"
    product = (ROOT / "Assets/VRCForge/Editor/AvatarParameterWriter.cs").read_text(encoding="utf-8-sig")
    marker = "internal static List<string> ValidateRequestedParameterNames"
    start = product.index(marker)
    brace = product.index("{", start)
    depth = 0
    end = brace
    while end < len(product):
        if product[end] == "{": depth += 1
        elif product[end] == "}":
            depth -= 1
            if depth == 0:
                end += 1
                break
        end += 1
    method = product[start:end].replace("internal static", "public static", 1)
    project = tmp_path / "harness.csproj"
    project.write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        '<TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>',
        encoding="utf-8",
    )
    (tmp_path / "Program.cs").write_text(
        fixture.read_text(encoding="utf-8").replace("/* PRODUCT_METHOD */", method),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["dotnet", "run", "--project", str(project), "--nologo"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
