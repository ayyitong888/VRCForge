from __future__ import annotations

import jsonschema
import pytest

import dashboard_server
from authoritative_unity_writes import prepare_authoritative_unity_write, validate_authoritative_unity_write_result, AuthoritativeUnityWriteError
from material_variant_flatten import TOOL_NAME, build_wrapper_arguments
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS


def test_flatten_preview_and_write_share_exact_core_arguments():
    args = {"projectPath": "D:/Project", "assetPath": "Assets/Clothes.mat", "preview": True}
    jsonschema.validate(args, UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_material_variant_flatten"])
    jsonschema.validate(args, EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_flatten_material_variant"])
    wrapper = build_wrapper_arguments({**args, "executionTarget": {"scope": "project"}, "expectedGuid": "a" * 32, "expectedDependencyHash": "hash", "expectedFileDigest": "b" * 64})
    assert wrapper == {"toolName": TOOL_NAME, "projectPath": "D:/Project", "preview": True, "executionTarget": {"scope": "project"}, "arguments": {
        "assetPath": "Assets/Clothes.mat", "preview": True, "expectedGuid": "a" * 32,
        "expectedDependencyHash": "hash", "expectedFileDigest": "b" * 64,
    }}


def test_request_preparer_preserves_preview_bound_identity(monkeypatch):
    observed = {}
    def mocked_prepare(wrapper, caller_preview):
        observed.update(wrapper)
        assert caller_preview == {"previewDigest": "digest"}
        return wrapper, {"previewDigest": "digest", "guid": "a" * 32, "dependencyHash": "hash", "fileDigest": "b" * 64}
    monkeypatch.setattr(dashboard_server, "prepare_unity_mcp_write_request", mocked_prepare)
    wrapper, preview = dashboard_server.prepare_material_variant_flatten_request({
        "projectPath": "D:/Project", "assetPath": "Assets/Clothes.mat", "executionTarget": {"scope": "project"},
    }, {"previewDigest": "digest"})
    assert observed["executionTarget"] == {"scope": "project"}
    assert wrapper["arguments"]["assetPath"] == "Assets/Clothes.mat"
    assert preview["guid"] == "a" * 32


def test_approved_plan_carries_exact_preview_identity_and_false_preview():
    wrapper = build_wrapper_arguments({
        "projectPath": "D:/Project", "assetPath": "Assets/Clothes.mat", "preview": False,
        "executionTarget": {"scope": "project"}, "expectedGuid": "a" * 32,
        "expectedDependencyHash": "hash", "expectedFileDigest": "b" * 64,
    })
    plan = dashboard_server.build_unity_mcp_write_execution_plan(wrapper)
    assert plan == [(TOOL_NAME, {"assetPath": "Assets/Clothes.mat", "preview": False,
                                  "expectedGuid": "a" * 32, "expectedDependencyHash": "hash",
                                  "expectedFileDigest": "b" * 64})]


def test_flatten_is_explicitly_registered_in_preview_and_approved_write_lanes():
    assert "vrcforge_preview_material_variant_flatten" in dashboard_server.AGENT_GATEWAY._tools
    assert "vrcforge_flatten_material_variant" in dashboard_server.AGENT_GATEWAY._write_handlers


def test_authoritative_prepare_calls_core_preview_and_binds_asset_receipt(tmp_path):
    (tmp_path / "Assets").mkdir()
    calls = []
    guid = "a" * 32
    digest = "b" * 64
    dependency = "c" * 32
    payload = {
        "schema": "vrcforge.material_variant_flatten.v1", "ok": True, "preview": True,
        "verified": False, "persistedReadback": False, "readback": None,
        "committed": False, "mutationStarted": False, "pending": False,
        "assetPath": "Assets/Clothes.mat", "guid": guid, "dependencyHash": dependency,
        "fileDigest": digest, "state": "preview", "isVariant": True,
        "commitState": "not_started", "persistenceState": "not_applicable", "readbackState": "not_required",
        "before": {"shader": "Standard", "properties": {}},
    }
    request = {"tool_name": "vrc_flatten_material_variant", "projectPath": str(tmp_path), "arguments": {"assetPath": "Assets/Clothes.mat"}}
    canonical, preview = prepare_authoritative_unity_write(request, None, lambda name, args: calls.append((name, args)) or payload)
    assert calls == [("vrc_flatten_material_variant", {"assetPath": "Assets/Clothes.mat", "preview": True, "expectedProjectPath": str(tmp_path)})]
    assert canonical["arguments"]["expectedGuid"] == guid
    assert preview["effectiveSnapshot"] == payload["before"]


def test_authoritative_apply_rejects_unverified_or_variant_readback(tmp_path):
    args = {"assetPath": "Assets/Clothes.mat", "expectedGuid": "a" * 32, "expectedDependencyHash": "c" * 32, "expectedFileDigest": "b" * 64}
    bad = {"schema": "vrcforge.material_variant_flatten.v1", "ok": True, "preview": False, "verified": True, "persistedReadback": True, "committed": True, "pending": False, "persistenceState": "persisted", "readbackState": "verified", "commitState": "committed", "assetPath": args["assetPath"], "guid": args["expectedGuid"], "dependencyHash": args["expectedDependencyHash"], "fileDigest": "a" * 64, "before": {"shader": "Standard"}, "after": {"shader": "Changed"}, "parent": "", "isVariant": False, "readback": {"verified": True, "parent": "", "isVariant": False, "effectiveState": {"shader": "Changed"}}}
    with pytest.raises(AuthoritativeUnityWriteError):
        validate_authoritative_unity_write_result({"tool_name": "vrc_flatten_material_variant", "arguments": args}, bad)
