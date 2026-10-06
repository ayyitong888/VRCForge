import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "artifacts" / "skills"


def load_skill(package: str):
    folder = SKILLS / package
    return {
        "manifest": json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig")),
        "skill": (folder / "SKILL.md").read_text(encoding="utf-8-sig"),
        "guide": (folder / "references" / "workflow.md").read_text(encoding="utf-8-sig"),
        "workflow": json.loads((folder / "workflows" / ("hairstyle.json" if "hairstyle" in package else "accessory-switch.json")).read_text(encoding="utf-8-sig")),
    }


def test_hairstyle_existing_design_is_preserved_and_new_design_is_user_confirmed():
    package = load_skill("vrcforge-avatar-hairstyle")
    assert package["manifest"]["version"] == "1.0.2"
    text = "\n".join((package["skill"], package["guide"]))
    contract = package["workflow"]["communityContract"]
    assert "existingProjectDesign" in contract
    assert contract["existingProjectDesign"] == "preserve_observed_parameters_menu_paths_and_animation_timing"
    assert contract["newDesign"] == "use_user_confirmed_selector_menu_path_and_animation_timing"
    assert contract["automaticMigration"] is False
    assert "hair_int_migration" in package["workflow"]["scope"]
    assert contract["explicitlyAuthorizedMigrationPath"] == "hair_int_migration"
    assert "fewer_than_8_total_mutually_exclusive_options" in contract["newDesignSelectorConvention"]
    assert contract["selectorThresholdIsPlatformRequirement"] is False
    assert contract["thresholdRecommendationSource"] == "https://vrc.school/docs/Avatars/Multi-Toggles/"
    assert "不是平台强制规则" in text
    assert "OFF 空 Motion" in text and "确认行为有效时保留" in text
    assert "menuCategory" not in contract
    assert "thresholdRequiresSeparatelyAuthorizedMigration" not in contract
    assert "PhysBone" in text and "collider" in text and "Head Bone Proxy" in text
    assert all(step.get("conditional") for step in package["workflow"]["steps"] if step["writes"])


def test_accessory_existing_design_is_preserved_and_new_design_is_user_confirmed():
    package = load_skill("vrcforge-avatar-accessory-switch")
    assert package["manifest"]["version"] == "1.0.2"
    text = "\n".join((package["skill"], package["guide"]))
    contract = package["workflow"]["communityContract"]
    assert contract["existingProjectDesign"] == "preserve_observed_parameters_menu_paths_and_animation_timing"
    assert contract["newDesign"] == "use_user_confirmed_selector_menu_path_and_animation_timing"
    assert contract["automaticMigration"] is False
    assert "不得默认强制独立 Bool" in text
    assert "one_saved_synced_bool_per_accessory" not in str(contract)
    assert "menuCategory" not in contract
    assert "animationTiming" in contract
    assert "target bone" in text and "PhysBone" in text and "collider" in text
    assert "Bone Proxy" in text and "constraint" in text
    assert "OFF 空 Motion" in text and "证实其中有错误" in text
    assert all(step.get("conditional") for step in package["workflow"]["steps"] if step["writes"])
