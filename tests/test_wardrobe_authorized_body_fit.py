import json
from pathlib import Path

from agent_gateway import parse_skill_markdown, render_skill_markdown


ROOT = Path(__file__).resolve().parents[1] / "artifacts/skills/vrcforge-avatar-wardrobe"


def test_authorized_body_fit_survives_runtime_skill_projection(tmp_path):
    skill = parse_skill_markdown(ROOT / "SKILL.md")
    projected = tmp_path / "SKILL.md"
    projected.write_text(render_skill_markdown({**skill, "instructions": skill["body"]}), encoding="utf-8")
    loaded = parse_skill_markdown(projected)
    assert {"vrcforge_preview_blendshape_apply", "vrcforge_apply_blendshapes"} <= set(loaded["allowedTools"])
    for text in (loaded["body"], (ROOT / "references/workflow.md").read_text(encoding="utf-8")):
        assert "用户最新明确允许调整胸部大小" in text
        assert "默认保留用户身体体型" in text
        assert "不猜 Shape 名称、方向或固定权重" in text
        assert "先用问题部位的针对性视角验证" in text
        assert "不把常规试调交给 call_user" in text
        assert "全局体型调整" in text and "服装专属适配" in text


def test_body_fit_workflow_requires_authority_and_preserves_baseline():
    workflow = json.loads((ROOT / "workflows/wardrobe-authoring.json").read_text(encoding="utf-8"))
    fit = workflow["communityWardrobeContract"]["bodyShapeFitting"]
    assert fit["defaultPreserveBody"] is True
    assert fit["requiresExplicitBodySizeAuthorization"] is True
    assert fit["existingScannedShapesOnly"] is True
    assert fit["preserveUnrelatedBodyFacePhysicsAndManualEdits"] is True
    assert fit["outfitSpecificFitRequiresBaselineRestore"] is True
    assert fit["globalChangeOnlyWhenExplicitlyRequested"] is True
    step = next(s for s in workflow["steps"] if s["name"] == "apply_authorized_existing_body_shapes")
    assert step["tool"] == "vrcforge_preview_blendshape_apply"
    assert "vrcforge_apply_blendshapes" in step["tools"]
    assert step["optional"] and step["runtimeApprovalRequired"] and step["runtimePermissionGateRequired"]
    assert workflow["checkpoint"]["required"]
