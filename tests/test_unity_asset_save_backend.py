from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path

import dashboard_server
from agent_gateway import AgentGateway
from agent_approval_transactions import _domain_write_receipt
from agent_tool_result_contract import normalize_agent_tool_result
from external_mcp_tool_blocks import EXTERNAL_MCP_READ_TOOL_BLOCKS, EXTERNAL_MCP_WRITE_TOOL_BLOCKS
from unity_execution_plans_scene import build_save_asset_wrapper_arguments
from unity_mcp_tool_contract import EXPECTED_TOOL_COUNT, EXPECTED_TOOL_NAMES, PREVIOUS_CORE_TOOL_CONTRACT_VERSION, TOOL_CONTRACT_VERSION
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS


def test_single_asset_save_has_matching_inner_and_external_tool_surface() -> None:
    assert TOOL_CONTRACT_VERSION == "192"
    assert PREVIOUS_CORE_TOOL_CONTRACT_VERSION == "191"
    assert EXPECTED_TOOL_COUNT == 102
    assert "vrc_save_asset" in EXPECTED_TOOL_NAMES
    assert "vrc_save_asset" in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST
    assert "vrcforge_save_asset" in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
    assert "vrcforge_preview_save_asset" in EXTERNAL_MCP_READ_TOOL_BLOCKS["project"]
    assert "vrcforge_save_asset" in EXTERNAL_MCP_WRITE_TOOL_BLOCKS["project"]

    preview_schema = UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_save_asset"]
    write_schema = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_save_asset"]
    assert preview_schema["required"] == ["projectPath", "assetPath"]
    assert write_schema["required"] == ["projectPath", "assetPath"]
    assert "dirty assets" in write_schema["description"]

    wrapper = build_save_asset_wrapper_arguments({
        "projectPath": "D:/Unity/Avatar",
        "assetPath": "Assets/Menu.asset",
        "executionTarget": {"schema": "vrcforge.execution_target_ref.v1"},
    })
    assert wrapper == {
        "toolName": "vrc_save_asset",
        "arguments": {"assetPath": "Assets/Menu.asset", "preview": False},
        "projectPath": "D:/Unity/Avatar",
        "executionTarget": {"schema": "vrcforge.execution_target_ref.v1"},
    }
    assert dashboard_server.build_unity_mcp_write_execution_plan(wrapper) == [
        ("vrc_save_asset", {"assetPath": "Assets/Menu.asset", "preview": False})
    ]

    write_descriptor = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor(
        "vrcforge_save_asset", write=True, exposure_layer="execution",
    )
    preview_descriptor = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor(
        "vrcforge_preview_save_asset", write=False, exposure_layer="planning",
    )
    assert write_descriptor["inputSchema"]["required"] == [
        "projectPath", "assetPath", "executionTarget",
    ]
    assert "When NOT to use:" in dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_save_asset"].description
    assert preview_descriptor["name"] == "vrcforge_preview_save_asset"

    internal = dashboard_server._RuntimePlannerCatalog().read(
        "execution", project_context_active=True,
    )
    internal_names = {tool.runtime_name for tool in internal.routable_tools}
    external_names = {
        tool["name"] for tool in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
            "execution", tool_blocks=["project"],
        )
    }
    external_previews = {
        tool["name"] for tool in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
            "planning", tool_blocks=["project"],
        )
    }
    assert "vrcforge_save_asset" in internal_names
    assert "vrcforge_save_asset" in external_names
    assert "vrcforge_preview_save_asset" in internal_names
    assert "vrcforge_preview_save_asset" in external_previews


def test_asset_save_checkpoint_snapshots_only_the_approved_asset_without_core_preflight(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        dashboard_server, "save_asset_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("checkpoint must not repeat Core preview")),
    )
    monkeypatch.setattr(
        dashboard_server, "prepare_unity_checkpoint_sync",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not make a project-wide checkpoint")),
    )
    prepared = dashboard_server.prepare_authoritative_unity_checkpoint_sync(
        tmp_path,
        {
            "_checkpointTargetTool": "vrcforge_save_asset",
            "toolName": "vrc_save_asset",
            "arguments": {"assetPath": "Assets/Menu.asset", "preview": False},
        },
    )

    assert prepared["mode"] == "read_only_disk_snapshot"
    assert "canonicalRevalidated" not in prepared
    assert prepared["archiveAssetPaths"] == ["Assets/Menu.asset"]
    assert prepared["archiveScopeReason"] == "explicit_single_existing_asset_save"

    assets = tmp_path / "Assets"
    assets.mkdir()
    for name in ("Menu.asset", "Menu.asset.meta", "Other.asset", "Other.asset.meta"):
        (assets / name).write_text(name, encoding="utf-8")
    service = AgentGateway(tmp_path / "config.json", tmp_path / "audit").checkpoint_recovery
    checkpoint = service._create_archive_checkpoint(tmp_path, {
        "id": "asset_save_checkpoint", "projectRoot": str(tmp_path),
        "status": "ready", "archiveFiles": prepared["archiveAssetPaths"],
    })
    assert checkpoint["ok"] is True
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert set(archive.namelist()) == {"Assets/Menu.asset", "Assets/Menu.asset.meta"}


def test_registered_asset_save_handler_calls_core_once_and_normalizes_saved_and_noop(monkeypatch) -> None:
    from vrchat_blendshape_agent import McpResult

    handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_save_asset"]
    samples = (
        ({
            "schema": "vrcforge.asset_save.v1", "ok": True, "preview": False,
            "assetPath": "Assets/Menu.asset", "wasDirty": True, "saved": True,
            "noChange": False, "changed": True, "mutationStarted": True,
            "mutationApplied": True, "committed": True, "commitState": "committed",
            "commitStateKnown": True, "verified": True,
        }, True, "complete"),
        ({
            "schema": "vrcforge.asset_save.v1", "ok": True, "preview": False,
            "assetPath": "Assets/Menu.asset", "wasDirty": False, "saved": False,
            "noChange": True, "changed": False, "mutationStarted": False,
            "mutationApplied": False, "committed": True, "commitState": "no_change",
            "commitStateKnown": True, "verified": True,
        }, False, "no_change"),
    )
    for raw, changed, commit_state in samples:
        calls = []

        def invoke(_settings, tool_name, arguments, **_kwargs):
            calls.append((tool_name, dict(arguments)))
            return McpResult(0, "", "", {"structuredContent": raw})

        monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)
        prepared_arguments, preview = handler.request_preparer(
            {"projectPath": "D:/Unity/Avatar", "assetPath": "Assets/Menu.asset"}, None,
        )
        assert preview is None
        assert calls == []
        plan = handler.approved_execution_plan_builder(prepared_arguments)
        assert plan == [("vrc_save_asset", {"assetPath": "Assets/Menu.asset", "preview": False})]

        transport = handler.handler(prepared_arguments)
        assert calls == [("vrc_save_asset", {"assetPath": "Assets/Menu.asset", "preview": False})]
        receipt = _domain_write_receipt(transport)
        outcome = normalize_agent_tool_result(receipt, fallback_summary=handler.description, write=True)
        assert outcome["status"] == "ok"
        assert outcome["mutationStarted"] is changed
        assert outcome["committed"] is True
        if changed:
            assert outcome["commitState"] == "complete"
        else:
            assert outcome.get("commitState", "no_change") != "unknown"
            assert receipt["commitState"] == "no_change"


def test_prewrite_checkpoint_archives_only_asset_and_rejects_missing_asset_before_core(monkeypatch, tmp_path) -> None:
    from test_agent_checkpoint_recovery_service import _gateway
    from vrchat_blendshape_agent import McpResult

    project = tmp_path / "project"
    for directory in ("Assets", "Packages", "ProjectSettings"):
        (project / directory).mkdir(parents=True)
    for relative, content in (
        ("Assets/Menu.asset", "old asset"),
        ("Assets/Menu.asset.meta", "asset meta"),
        ("Assets/Other.asset", "unrelated asset"),
        ("Assets/Other.asset.meta", "unrelated meta"),
    ):
        (project / relative).write_text(content, encoding="utf-8")

    registered = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_save_asset"]
    gateway = _gateway(tmp_path / "app")
    service = gateway.approval_transactions
    service.register_write_handler(
        registered.name, registered.description, registered.risk_level, registered.handler,
        request_preparer=registered.request_preparer,
        checkpoint_prepare_handler=registered.checkpoint_prepare_handler,
        approved_execution_plan_builder=registered.approved_execution_plan_builder,
        requires_approved_execution_context=registered.requires_approved_execution_context,
    )
    service._ports = replace(
        service._ports,
        run_git=lambda *_args, **_kwargs: {"ok": False, "returncode": 1, "stdout": "", "error": "no repository"},
    )

    calls = []
    def invoke(_settings, tool_name, arguments, **_kwargs):
        calls.append((tool_name, dict(arguments)))
        return McpResult(0, "", "", {"structuredContent": {
            "schema": "vrcforge.asset_save.v1", "ok": True, "preview": False,
            "assetPath": "Assets/Menu.asset", "wasDirty": True, "saved": True,
            "noChange": False, "changed": True, "mutationStarted": True,
            "mutationApplied": True, "committed": True, "commitState": "committed",
            "commitStateKnown": True, "verified": True,
        }})
    monkeypatch.setattr(dashboard_server, "invoke_unity_mcp", invoke)

    prepared, _preview = registered.request_preparer(
        {"projectPath": str(project), "assetPath": "Assets/Menu.asset"}, None,
    )
    checkpoint = service._create_pre_write_checkpoint(
        {"id": "save-menu", "targetTool": "vrcforge_save_asset"},
        {**prepared, "arguments": {"assetPath": "Assets/Menu.asset", "preview": False}},
    )
    assert checkpoint["ok"] is True, checkpoint
    assert checkpoint["archiveFiles"] == ["Assets/Menu.asset", "Assets/Menu.asset.meta"]
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert set(archive.namelist()) == {"Assets/Menu.asset", "Assets/Menu.asset.meta"}
    assert calls == []

    registered.handler(prepared)
    assert calls == [("vrc_save_asset", {"assetPath": "Assets/Menu.asset", "preview": False})]

    missing = service._create_pre_write_checkpoint(
        {"id": "save-missing", "targetTool": "vrcforge_save_asset"},
        {**prepared, "arguments": {"assetPath": "Assets/Missing.asset", "preview": False}},
    )
    assert missing["ok"] is False
    assert "Missing.asset" in missing["error"]
    assert calls == [("vrc_save_asset", {"assetPath": "Assets/Menu.asset", "preview": False})]
