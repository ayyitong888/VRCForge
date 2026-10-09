"""Prompt-contract regressions from the real wardrobe inspection trace."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_wardrobe_inventory_requires_direct_menu_evidence_before_loading():
    text = (ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/SKILL.md").read_text(encoding="utf-8")
    assert "将 `vrcforge_scan_avatar_controls` 纳入首次具名加载" in text
    assert "不能用 `vrcforge_scan_wardrobe` 的候选条目替代完整菜单记录" in text
    assert "同一参数和值的多个菜单入口必须分别保留" in text


def test_wardrobe_binding_claims_preserve_missing_and_sampled_boundaries():
    text = (ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/SKILL.md").read_text(encoding="utf-8")
    assert "未绑定不等于写入 0" in text
    assert "按 clip 身份、绑定身份和分页覆盖核对" in text
    assert "不能仅凭总数或状态命名宣称全部链路完整" in text
