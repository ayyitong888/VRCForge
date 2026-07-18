from pathlib import Path

import agent_gateway
import dashboard_server


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "Assets" / "VRCForge" / "Editor" / "VrmExporter.cs").read_text(
    encoding="utf-8-sig"
)


def test_vrm_exporter_uses_the_current_vrm10_static_api_without_a_fake_fallback():
    assert '[McpForUnityTool(' in SOURCE
    assert 'name: "vrc_export_vrm"' in SOURCE
    assert '"UniVRM10.Vrm10Exporter"' in SOURCE
    assert '"UniGLTF.GltfExportSettings"' in SOURCE
    assert '"UniVRM10.VRM10ObjectMeta"' in SOURCE
    assert "parameters.Length == 5" in SOURCE
    assert "method.ReturnType == typeof(byte[])" in SOURCE
    assert '"VRM.VRMExporter"' not in SOURCE
    assert "Export(GameObject)" not in SOURCE
    assert "UniVRM VRM 1.0 dependency unavailable" in SOURCE
    assert "return new ErrorResponse" in SOURCE


def test_vrm_export_requires_rights_author_and_a_valid_humanoid():
    assert "confirmRights=true is required" in SOURCE
    assert 'RequireText(parameters.author, "author", 256)' in SOURCE
    assert "animator.avatar.isValid" in SOURCE
    assert "animator.avatar.isHuman" in SOURCE
    assert "renderer is MeshRenderer || renderer is SkinnedMeshRenderer" in SOURCE
    assert "Resources.FindObjectsOfTypeAll<Animator>()" in SOURCE
    assert "More than one valid Humanoid model is loaded" in SOURCE


def test_vrm_export_restricts_output_and_validates_vrm1_content_before_replace():
    assert '"Assets/VRCForge/Exports"' in SOURCE
    assert '"VRM export path must use the .vrm extension."' in SOURCE
    assert "VRCForgeOutputPathGuard.ResolveManagedProjectPath" in SOURCE
    assert "File.WriteAllBytes(temporaryPath, bytes)" in SOURCE
    assert "ValidateVrm10Glb(temporaryPath)" in SOURCE
    assert "CommitValidatedOutput(temporaryPath, outputPath, replacementBackupPath)" in SOURCE
    assert "missing glTF header" in SOURCE
    assert "GLB version 2" in SOURCE
    assert 'document["extensions"]?["VRMC_vrm"]' in SOURCE
    assert 'licenseProfile = "only_author_personal_non_profit_credit_required_no_redistribution_no_modification"' in SOURCE
    assert 'validation = "glb_v2_vrm1_extension_valid"' in SOURCE


def test_vrm_export_is_registered_as_a_medium_risk_approved_static_unity_write():
    assert "vrc_export_vrm" in dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS
    assert "vrc_export_vrm" in dashboard_server.VRCFORGE_UNITY_MCP_WRITE_ALLOWLIST
    dashboard_server.register_agent_gateway_tools()
    write_handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_export_vrm"]
    assert write_handler.risk_level == "medium"
    assert write_handler.risk_level_resolver is not None
    assert write_handler.risk_level_resolver({"overwrite": False}) == "medium"
    assert write_handler.risk_level_resolver({"overwrite": True}) == "high"
    assert "checkpoint" in write_handler.description.lower()
    override = agent_gateway.BUILTIN_SKILL_OVERRIDES["vrcforge_export_vrm"]
    assert override["title"] == "Unity Avatar to VRM 1.0"
    assert "current" in override["whenToUse"].lower()
    assert "confirmRights=true" in override["inputs"][0]
    assert "does not replace" in override["backupRestore"]

    config = dashboard_server.AGENT_GATEWAY.ensure_config()
    skills = {
        skill["name"]: skill
        for skill in dashboard_server.AGENT_GATEWAY._builtin_skill_definitions(config)
    }
    skill = skills["vrcforge_export_vrm"]
    assert skill["title"] == "Unity Avatar to VRM 1.0"
    assert skill["source"] == "builtin"
    assert skill["skillType"] == "tool"
    assert skill["permissionMode"] == "approval_required"
    assert skill["riskLevel"] == "medium"
    assert skill["entrypointTool"] == "vrcforge_export_vrm"
    assert skill["allowedTools"] == [
        "vrcforge_request_apply",
        "vrcforge_apply_approved",
        "vrcforge_export_vrm",
    ]


def test_vrm_write_handler_only_delegates_to_the_static_allowlisted_unity_tool(monkeypatch):
    dashboard_server.register_agent_gateway_tools()
    calls = []
    monkeypatch.setattr(
        dashboard_server,
        "unity_mcp_write_sync",
        lambda payload: calls.append(payload) or {"ok": True, "toolName": payload["toolName"]},
    )

    arguments = {
        "avatarPath": "Avatar",
        "author": "Test Author",
        "confirmRights": True,
        "outputPath": "Assets/VRCForge/Exports/avatar.vrm",
    }
    result = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_export_vrm"].handler(
        arguments
    )

    assert result == {"ok": True, "toolName": "vrc_export_vrm"}
    assert calls == [{"toolName": "vrc_export_vrm", "arguments": arguments}]


def test_vrm_overwrite_escalates_to_high_risk_and_blocks_auto_approval(tmp_path):
    source_handler = dashboard_server.AGENT_GATEWAY._write_handlers["vrcforge_export_vrm"]
    gateway = agent_gateway.AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    gateway.register_write_handler(
        "vrcforge_export_vrm",
        source_handler.description,
        source_handler.risk_level,
        lambda arguments: {"ok": True, "arguments": arguments},
        risk_level_resolver=source_handler.risk_level_resolver,
    )

    config = gateway.ensure_config()
    config.execution_mode = "auto"
    gateway.save_config(config)
    normal = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_export_vrm",
            "arguments": {"author": "Test Author", "confirmRights": True, "overwrite": False},
        }
    )
    assert normal["status"] == "pending"
    assert normal["approval"]["riskLevel"] == "medium"
    assert normal["approval"]["requiresExplicitApproval"] is True
    assert "content rights" in normal["approval"]["explicitApprovalReason"]

    overwrite = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_export_vrm",
            "arguments": {"author": "Test Author", "confirmRights": True, "overwrite": True},
        }
    )
    assert overwrite["status"] == "pending"
    assert overwrite["approval"]["riskLevel"] == "high"
    assert overwrite["approval"]["requiresExplicitApproval"] is True
    assert "medium to high risk" in overwrite["approval"]["explicitApprovalReason"]
