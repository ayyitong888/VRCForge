"""Prompt-contract regressions from the real wardrobe inspection trace."""
import json
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


def test_default_outfit_uses_confirmed_pre_inspection_worn_state():
    skill = (ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/SKILL.md").read_text(encoding="utf-8")
    guide = (ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/references/workflow.md").read_text(encoding="utf-8")
    assert "原装／默认穿搭" in skill and "原装／默认穿搭" in guide
    assert "不能只凭当前 `activeSelf`" in skill
    assert "不能仅凭当下显示状态" in guide
    assert "先询问" in skill and "应先询问" in guide


def test_prefab_unpack_uses_read_preview_tool_before_approved_write():
    skill = (ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/SKILL.md").read_text(encoding="utf-8")
    workflow = json.loads(
        (ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/workflows/wardrobe-authoring.json").read_text(
            encoding="utf-8"
        )
    )
    allowed = {line.strip()[2:] for line in skill.splitlines() if line.strip().startswith("- vrcforge_")}
    steps = workflow["steps"]
    preview = next(step for step in steps if step["tool"] == "vrcforge_preview_unpack_prefab")
    unpack = next(step for step in steps if step["tool"] == "vrcforge_unpack_prefab")
    contract = workflow["communityWardrobeContract"]["prefabInstanceAuthoring"]
    assert contract["previewTool"] in allowed
    assert preview["writes"] is False and steps.index(preview) < steps.index(unpack)
    assert contract["previewResultFields"] == [
        "gameObjectPath", "unpackMode", "globalObjectId", "prefabGuid", "dependencyHash", "scenePath"
    ]
    assert "vrcforge_preview_unpack_prefab" in skill
    assert "Do not pass preview=true to this write tool" in unpack["guidance"]


def test_imported_outfit_scene_instance_is_unpacked_before_setup():
    workflow = json.loads(
        (ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/workflows/wardrobe-authoring.json").read_text(
            encoding="utf-8"
        )
    )
    steps = workflow["steps"]
    names = [step["name"] for step in steps]
    placement = names.index("place_one_imported_outfit_instance_without_setup")
    preview = names.index("read_prefab_unpack_preview_before_authoring_write")
    unpack = names.index("completely_unpack_prefab_instance_before_authoring")
    setup = names.index("setup_installed_outfit_after_prefab_unpack")
    assert placement < preview < unpack < setup
    guidance = steps[placement]["guidance"]
    assert "manageWardrobe=false" in guidance
    assert "setupOutfit=false" in guidance
    assert "unpackPrefab=false" in guidance
    contract = workflow["communityWardrobeContract"]["prefabInstanceAuthoring"]
    assert "imported_asset_package_file" in contract["neverUnpack"]
    assert contract["existingPlacedOutfitsMustBeCheckedIndividually"] is True
