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
    section = text.split("## 通用写入约束", 1)[1].split("## 运行观察的生命周期", 1)[0]

    assert "全量重写所有已批准 clip 矩阵" not in section
    assert "220" not in text
    assert "只修证据证明与批准目标不符的内容" in section
    assert "修改后回读真实结果，与批准差异比较" in section
    assert "验证范围包含实际受影响的依赖，而不是固定服装矩阵" in section
    assert "220" not in section
    assert "保留正确行为与源时间线" in section
    assert "执行权限与精确 preview 批准缺一不可" in section
    assert "引用发生变化时旧证据不能直接当作新证据" in text
    creation = text.split("## 从零新建默认方案", 1)[1]
    assert "检查、扩展、修复不进入此分支" in creation
    assert "不能把瞬时矩阵强写到过渡过程" in creation
    assert "再逐套独立回读、运行观察和视觉验收" in creation
