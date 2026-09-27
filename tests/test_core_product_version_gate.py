from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_gate():
    path = REPO_ROOT / "packaging" / "check_core_product_version.py"
    spec = importlib.util.spec_from_file_location("check_core_product_version_for_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fixture(root: Path, *, python_version: str, csharp_version: str) -> None:
    (root / "Assets" / "VRCForge" / "Editor" / "MCP").mkdir(parents=True)
    (root / "unity_mcp_tool_contract.py").write_text(
        f'PRODUCT_VERSION = "{python_version}"\n', encoding="utf-8"
    )
    (root / "Assets" / "VRCForge" / "Editor" / "MCP" / "VRCForgeMcpToolContract.cs").write_text(
        f'internal const string ProductVersion = "{csharp_version}";\n', encoding="utf-8"
    )


def test_core_product_version_gate_rejects_python_csharp_mismatch(tmp_path: Path) -> None:
    gate = _load_gate()
    _write_fixture(tmp_path, python_version="1.8.7", csharp_version="1.8.0")

    with pytest.raises(gate.VersionConsistencyError, match="C# Core ProductVersion"):
        gate.validate_core_product_version(tmp_path, "1.8.7")


def test_core_product_version_gate_accepts_requested_release_version(tmp_path: Path) -> None:
    gate = _load_gate()
    _write_fixture(tmp_path, python_version="1.8.7", csharp_version="1.8.7")

    result = gate.validate_core_product_version(tmp_path, "1.8.7")

    assert result == {
        "requestedVersion": "1.8.7",
        "pythonProductVersion": "1.8.7",
        "csharpProductVersion": "1.8.7",
    }


def test_release_build_invokes_core_product_version_gate_before_payload_build() -> None:
    source = (REPO_ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8")

    invocation = "check_core_product_version.py --repo-root $repoRoot --version $Version"
    assert invocation in source
    assert 'throw "Core product version consistency gate failed. No release payload was built."' in source
    assert source.index(invocation) < source.index("Build-TauriDesktopApp -DestinationExe")
