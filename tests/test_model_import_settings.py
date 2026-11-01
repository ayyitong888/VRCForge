from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

import dashboard_server
from authoritative_unity_writes import (
    AUTHORITATIVE_UNITY_WRITE_TOOLS,
    authoritative_unity_write_has_strict_result,
    prepare_authoritative_unity_write,
)
from agent_gateway import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS, UNITY_READ_TOOL_INPUT_SCHEMAS
from model_import_settings import (
    APPROVAL_PREVIEW_SCHEMA,
    RESULT_SCHEMA,
    TOOL_NAME,
    ModelImportSettingsError,
    bind_authoritative_preview,
    build_preview_arguments,
    build_wrapper_arguments,
)


PROJECT_PATH = str(Path.cwd().resolve())
ASSET_PATH = "Assets/Avatar/Models/Body.fbx"


def request_wrapper() -> dict:
    return build_wrapper_arguments({
        "projectPath": PROJECT_PATH,
        "modelAssetPath": ASSET_PATH,
        "isReadable": True,
    })


def preview_payload(*, before: bool = False, target: bool = True) -> dict:
    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "preview": True,
        "verified": True,
        "saved": False,
        "reimported": False,
        "projectPath": PROJECT_PATH,
        "modelAssetPath": ASSET_PATH,
        "importerType": "UnityEditor.ModelImporter",
        "beforeSettings": {"isReadable": before},
        "targetSettings": {"isReadable": target},
        "readbackSettings": {"isReadable": before},
        "wouldChange": before != target,
    }


def test_model_import_preview_binds_exact_target_and_approval_facts() -> None:
    wrapper = request_wrapper()
    preview_args = build_preview_arguments(wrapper["arguments"])
    assert preview_args == {
        "modelAssetPath": ASSET_PATH,
        "isReadable": True,
        "preview": True,
        "saveAndReimport": False,
    }

    canonical, approval = bind_authoritative_preview(wrapper, preview_payload())

    assert canonical["arguments"]["preview"] is False
    assert canonical["arguments"]["saveAndReimport"] is True
    assert canonical["arguments"] == {
        "modelAssetPath": ASSET_PATH,
        "isReadable": True,
        "preview": False,
        "saveAndReimport": True,
        "expectedProjectPath": PROJECT_PATH,
    }
    assert approval["schema"] == APPROVAL_PREVIEW_SCHEMA
    assert approval["change"] == {
        "before": {"isReadable": False},
        "after": {"isReadable": True},
        "wouldChange": True,
    }


@pytest.mark.parametrize("bad_path", ["Assets/../outside.fbx", "Assets//Body.fbx", "Assets\\Body.fbx"])
def test_model_import_preview_rejects_non_exact_asset_paths(bad_path: str) -> None:
    wrapper = request_wrapper()
    wrapper["arguments"]["modelAssetPath"] = bad_path
    with pytest.raises(ModelImportSettingsError):
        bind_authoritative_preview(wrapper, preview_payload())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("modelAssetPath", "Assets/Avatar/Models/Other.fbx"),
        ("importerType", "UnityEditor.TextureImporter"),
    ],
)
def test_model_import_preview_rejects_mismatched_authoritative_facts(field: str, value: object) -> None:
    payload = preview_payload()
    payload[field] = value
    with pytest.raises(ModelImportSettingsError):
        bind_authoritative_preview(request_wrapper(), payload)


def test_model_import_preview_projects_core_would_change_fact_without_recomputing_it() -> None:
    payload = preview_payload(before=False, target=True)
    payload["wouldChange"] = False
    _canonical, approval = bind_authoritative_preview(request_wrapper(), payload)
    assert approval["change"]["wouldChange"] is False


def test_authoritative_workflow_invokes_preview_then_returns_approved_apply() -> None:
    wrapper = request_wrapper()
    calls = []

    def invoke(tool_name: str, arguments: dict) -> dict:
        calls.append((tool_name, deepcopy(arguments)))
        return preview_payload()

    canonical, approval = prepare_authoritative_unity_write(wrapper, None, invoke)

    assert calls == [(TOOL_NAME, {**build_preview_arguments(wrapper["arguments"]), "expectedProjectPath": PROJECT_PATH})]
    assert canonical["arguments"]["modelAssetPath"] == ASSET_PATH
    assert approval["change"]["wouldChange"] is True
    assert TOOL_NAME in AUTHORITATIVE_UNITY_WRITE_TOOLS
    assert authoritative_unity_write_has_strict_result({"toolName": TOOL_NAME, "arguments": canonical["arguments"]}) is False


def test_approved_checkpoint_archives_only_the_exact_model_and_meta() -> None:
    prepared = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
        Path(PROJECT_PATH),
        {"_checkpointTargetTool": "vrcforge_set_model_import_settings",
         "arguments": {"modelAssetPath": ASSET_PATH}},
    )
    assert prepared["archiveAssetPaths"] == [ASSET_PATH]
    assert prepared["archiveScopeReason"] == "single_model_importer_asset_and_meta_only"


def test_model_import_settings_has_shared_preview_apply_schema_and_supervised_registration() -> None:
    preview_schema = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_model_import_settings"]
    apply_schema = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_set_model_import_settings"]
    assert preview_schema is apply_schema
    assert preview_schema["required"] == ["projectPath", "modelAssetPath", "isReadable"]
    assert dashboard_server.MODEL_IMPORT_SETTINGS_TOOL in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST
    assert "vrcforge_set_model_import_settings" in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_set_model_import_settings"]
    assert handler.request_preparer is dashboard_server.prepare_model_import_settings_request
    assert handler.requires_approved_execution_context is True


def test_core_uses_model_importer_save_reimport_and_fresh_readback_without_hash_gates() -> None:
    source = Path("Assets/VRCForge/Editor/ModelImportSettingsTool.cs").read_text(encoding="utf-8-sig")
    assert 'toolId: "vrc_set_model_import_settings"' in source
    assert "as ModelImporter" in source
    assert "importer.SaveAndReimport();" in source
    assert "AssetImporter.GetAtPath(assetPath) as ModelImporter" in source
    assert "readbackImporter != null && readbackImporter.isReadable == targetReadable" in source
    assert "? AssetImporter.GetAtPath(assetPath) as ModelImporter" in source
    assert "mutationStarted = wouldChange" in source
    assert 'commitState = wouldChange ? "complete" : "no_change"' in source
    assert 'persistenceState = wouldChange ? "persisted" : "no_change"' in source
    assert "persistedReadback = wouldChange" in source
    assert 'readbackState = "verified"' in source
    assert "Model asset '" in source
    assert "expectedModelAssetPath" not in source
    assert "expectedImporterType" not in source
    assert "expectedIsReadable" not in source
    assert "FileDigest" not in source
    assert "AssetDatabase.WriteImportSettingsIfDirty" not in source
    assert "AssetDatabase.ImportAsset" not in source
