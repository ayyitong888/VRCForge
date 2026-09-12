from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "skills"
    / "vrcforge-avatar-wardrobe"
    / "references"
    / "workflow.md"
)
SKILL = WORKFLOW.parents[1] / "SKILL.md"
WORKFLOW_JSON = WORKFLOW.parents[1] / "workflows" / "wardrobe-authoring.json"


def test_wardrobe_prompt_declares_identity_target_discovery_and_binding_tools():
    skill_text = SKILL.read_text(encoding="utf-8")
    workflow_text = WORKFLOW_JSON.read_text(encoding="utf-8")
    assert "vrcforge_list_execution_targets" in skill_text
    assert "vrcforge_bind_execution_target" in skill_text
    assert '"vrcforge_list_execution_targets"' in workflow_text
    assert '"vrcforge_bind_execution_target"' in workflow_text


def test_wardrobe_sequence_preserves_saved_clips_and_repairs_only_evidence_differences():
    text = WORKFLOW.read_text(encoding="utf-8")
    section = text.split("## 8. 最短安全调用序列", 1)[1]

    assert "全量重写所有已批准 clip 矩阵" not in section
    assert "220" not in text
    assert "按 scope、变更范围和证据 freshness 复用" in section
    assert "仅对陈旧或受影响范围补扫" in section
    assert "回读并验证全部已批准 clip 的完整矩阵" in section
    assert "220" not in section
    assert "保留已有正确稳定态与过渡时间线" in section
    assert "只对回读与已批准矩阵存在的证据差异做 preview/修补" in section
    assert "仅新建基础衣柜的瞬时稳定态写新建的完整互斥矩阵" in section
    assert "过渡和用户动画保留源时间线并验证" in section
    assert "当前套完整矩阵、静态/动态/穿模验收" in section
