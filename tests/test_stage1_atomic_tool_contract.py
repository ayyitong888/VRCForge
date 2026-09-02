from __future__ import annotations

from pathlib import Path

import agent_gateway
import dashboard_server
import unity_mcp_tool_contract
from stage1_atomic_writes import (
    SCENE_SAVE_TOOL,
    SCENE_TRANSITION_TOOL,
    TEXTURE_PATCH_TOOL,
    USER_ADJUSTMENT_HANDOFF_TOOL,
    bind_authoritative_preview,
    build_wrapper_arguments,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (ROOT / "Assets/VRCForge/Editor/MCP/VRCForgeMcpToolContract.cs").read_text(
    encoding="utf-8-sig"
)


STAGE1_PUBLIC_TOOLS = {
    "vrcforge_scene_save": "avatar",
    "vrcforge_scene_transition": "project",
    "vrcforge_texture_patch": "materials",
    "vrcforge_user_adjustment_handoff": "avatar",
}

STAGE1_CORE_TOOLS = {
    "vrc_scene_save": "VRCForge.Editor.SceneSaveTool",
    "vrc_scene_transition": "VRCForge.Editor.SceneTransitionTool",
    "vrc_texture_patch": "VRCForge.Editor.TexturePatchTool",
    "vrc_user_adjustment_handoff": "VRCForge.Editor.UserAdjustmentHandoffTool",
}


def test_stage1_tools_are_registered_once_in_the_existing_lazy_tree() -> None:
    for name, block in STAGE1_PUBLIC_TOOLS.items():
        assert name in agent_gateway.EXTERNAL_MCP_WRITE_TOOL_BLOCKS[block]
        assert name in dashboard_server.VRCFORGE_UNITY_MCP_BACKED_WRITE_TARGETS
        assert name in dashboard_server.AGENT_GATEWAY._write_handlers

    assert "vrcforge_save_current_scene" not in dashboard_server.AGENT_GATEWAY._write_handlers
    assert dashboard_server.AGENT_GATEWAY.resolve_external_mcp_tool_name(
        "vrcforge_save_current_scene"
    ) == "vrcforge_scene_save"


def test_stage1_core_contract_contains_real_handlers() -> None:
    assert STAGE1_CORE_TOOLS.keys() <= unity_mcp_tool_contract.EXPECTED_TOOL_NAMES
    for tool_name, type_name in STAGE1_CORE_TOOLS.items():
        assert f'{{ "{tool_name}", "{type_name}" }}' in CONTRACT

    for relative in (
        "Assets/VRCForge/Editor/Stage1SceneTools.cs",
        "Assets/VRCForge/Editor/TexturePatchTool.cs",
        "Assets/VRCForge/Editor/UserAdjustmentHandoffTool.cs",
    ):
        assert (ROOT / relative).is_file()


def test_stage1_descriptors_tell_an_agent_when_to_use_and_when_not_to_use() -> None:
    for name in STAGE1_PUBLIC_TOOLS:
        descriptor = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor(
            name,
            write=True,
            exposure_layer="execution",
        )
        assert descriptor["name"] == name
        assert descriptor["canonicalName"].startswith("vrcforge.")
        assert descriptor["whenToUse"]
        assert descriptor["whenNotToUse"]
        assert descriptor["negativeExamples"]
        assert descriptor["permission"] == "RequiresApproval"
        assert descriptor["freshReadback"]["required"] is True
        assert descriptor["inputSchema"]["additionalProperties"] is False

    canonical = dashboard_server.AGENT_GATEWAY.shared_agent_tool_descriptor(
        "vrcforge_scene_save",
        write=True,
    )
    assert canonical["legacyAliases"] == ["vrcforge_save_current_scene"]


def test_stage1_write_tools_are_hidden_from_planning_exposure() -> None:
    planning = dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
        exposure_layer="planning",
        tool_blocks=set(agent_gateway.EXTERNAL_MCP_TOOL_BLOCKS),
    )
    names = {tool["name"] for tool in planning}
    assert names.isdisjoint(STAGE1_PUBLIC_TOOLS)
    assert "vrcforge_save_current_scene" not in names


def test_stage1_preview_receipt_binds_only_expected_fields(tmp_path: Path) -> None:
    (tmp_path / "Assets").mkdir()
    for tool_name, schema in (
        (SCENE_SAVE_TOOL, "vrcforge.scene_save.v1"),
        (SCENE_TRANSITION_TOOL, "vrcforge.scene_transition.v1"),
        (TEXTURE_PATCH_TOOL, "vrcforge.texture_patch.v1"),
        (USER_ADJUSTMENT_HANDOFF_TOOL, "vrcforge.user_adjustment_handoff.v1"),
    ):
        wrapper = build_wrapper_arguments(
            {"projectPath": str(tmp_path), "action": "fixture"},
            tool_name,
        )
        prepared, approval = bind_authoritative_preview(
            wrapper,
            {
                "schema": schema,
                "operation": "fixture",
                "ok": True,
                "preview": True,
                "verified": True,
                "mutationStarted": False,
                "commitState": "not_started",
                "projectPath": str(tmp_path),
                "previewDigest": "a" * 64,
                "applyBinding": {"expectedFixtureIdentity": "exact"},
                "target": {"handle": "exact"},
                "effect": "fixture",
            },
        )
        assert prepared["arguments"]["expectedFixtureIdentity"] == "exact"
        assert prepared["arguments"]["expectedPreviewDigest"] == "a" * 64
        assert prepared["arguments"]["preview"] is False
        assert approval["requiresExplicitUserApproval"] is True


def test_stage1_core_sources_keep_preview_nonmutating_and_identity_fail_closed() -> None:
    scene = (ROOT / "Assets/VRCForge/Editor/Stage1SceneTools.cs").read_text(encoding="utf-8-sig")
    texture = (ROOT / "Assets/VRCForge/Editor/TexturePatchTool.cs").read_text(encoding="utf-8-sig")
    handoff = (ROOT / "Assets/VRCForge/Editor/UserAdjustmentHandoffTool.cs").read_text(encoding="utf-8-sig")

    assert scene.index('if (preview)') < scene.index('EditorSceneManager.SaveScene(scene')
    assert "The transition would drop dirty scene changes" in scene
    assert "overwrite is unsupported" in scene
    assert "expectedSceneSetupDigest" in scene and "expectedOpenSceneStateDigest" in scene

    assert texture.index("if (preview)") < texture.index("File.WriteAllBytes")
    assert "expectedSourceHash" in texture and "expectedPreviewDigest" in texture
    assert "outsideAndProtectedPixelsUnchanged = true" in texture
    assert "alphaUnchanged = true" in texture
    assert "inpaint" not in texture.casefold()

    assert handoff.index("if (p.preview ?? false)") < handoff.index("new GameObject(proxyName)")
    assert "target.SetParent(proxy.transform, true)" in handoff
    assert "GlobalObjectIdentifierToObjectSlow" in handoff
    assert "was deleted, replaced, or recreated." in handoff
    assert "hierarchy path" not in handoff.casefold() or "never resolve by hierarchy path" in handoff.casefold()
