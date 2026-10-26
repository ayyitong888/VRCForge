from pathlib import Path

import pytest

from agent_gateway import parse_skill_markdown, render_skill_markdown


ROOT = Path(__file__).parents[1]
SOURCES = (
    "artifacts/skills/vrcforge-avatar-wardrobe",
    "examples/skill-packages/vrcforge-avatar-head-transplant",
    "examples/skill-packages/vrcforge-avatar-part-transplant",
)


@pytest.mark.parametrize("relative", SOURCES)
def test_adaptive_3d_observation_survives_skill_projection(relative, tmp_path):
    source = ROOT / relative
    skill = parse_skill_markdown(source / "SKILL.md")
    projected = tmp_path / "SKILL.md"
    projected.write_text(render_skill_markdown({**skill, "instructions": skill["body"]}), encoding="utf-8")
    for text in (
        parse_skill_markdown(projected)["body"],
        (source / "references/workflow.md").read_text(encoding="utf-8"),
    ):
        assert "三维资产不能仅凭正面或单张全身图判断" in text
        assert "主动补拍" in text and "斜角" in text and "局部近景" in text
        assert "观察不清不等于穿模或通过" in text
        assert "不把普通取景选择交给用户" in text
        assert "不能替代已有明确要求的验收视图" in text
        assert "用户要求全部制作完成后统一验收时" in text
        assert "不逐项要求用户确认" in text
        assert "逐笔审批、检查点、写后读回" in text
