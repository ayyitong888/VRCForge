"""Package regression: inspection/authoring must not prescribe activation results.

This checks the distributed contract, not model capability or live acceptance.
"""
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / "artifacts/skills/vrcforge-avatar-wardrobe"


@pytest.mark.parametrize("operation", ["inspect_existing", "extend_existing", "repair_existing", "delete_existing"])
def test_new_build_defaults_are_excluded_from_existing_operations(operation):
    workflow = json.loads((ROOT / "workflows/wardrobe-authoring.json").read_text(encoding="utf-8"))
    branch = workflow["newBuildDefaults"]
    assert operation in branch["excludedOperations"]
    assert branch["requiresExplicitFromScratchRequest"] is True
    assert branch["requiresVerifiedNoExistingWardrobeInScope"] is True
    assert branch["ambiguousScopeAction"] == "ask_before_selecting_branch"
    assert branch["userRequirementsOverrideDefaults"] is True
    assert branch["approvalBeforeWrites"] is True


def test_from_scratch_branch_retains_default_authoring_design():
    workflow = json.loads((ROOT / "workflows/wardrobe-authoring.json").read_text(encoding="utf-8"))
    design = workflow["newBuildDefaults"]["design"]
    assert design["selector"] == {"name": "衣柜", "type": "Int", "saved": True, "synced": True, "default": 0}
    assert design["animation"]["fullMutualExclusionMatrixRequired"] is True
    assert design["fx"]["durationSeconds"] == 0
    assert design["perOutfitReadbackAndRuntimeAcceptance"] is True


def test_animation_contract_does_not_supply_an_activation_answer():
    workflow = json.loads((ROOT / "workflows/wardrobe-authoring.json").read_text(encoding="utf-8"))
    animation = workflow["communityWardrobeContract"]["animation"]
    assert "fullMutualExclusionMatrixRequired" not in animation
    assert "outfitRootRule" not in animation
    assert "stableStateMutualExclusionOnly" not in animation["transitionClipPolicy"]
    assert animation["expectedEffectsSource"] == "observed_evidence_and_explicit_user_approved_changes"
    assert animation["repairStableValueOnlyWhenEvidenceDiffers"] is True


def test_distributed_instructions_do_not_require_selected_on_others_off():
    for relative in ("SKILL.md", "references/workflow.md"):
        content = (ROOT / relative).read_text(encoding="utf-8")
        for prescribed in (
            "稳定态必须具备并验证完整互斥矩阵",
            "当前套装根开启、其他所有套装根关闭",
            "当前套装根 `m_IsActive=1`，其他所有套装根 `m_IsActive=0`",
            "当前根必须 ON、所有其他根必须 OFF",
        ):
            assert prescribed not in content, (relative, prescribed)
