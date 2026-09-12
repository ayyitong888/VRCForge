from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/SKILL.md"
GUIDE = ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/references/workflow.md"
WORKFLOW = ROOT / "artifacts/skills/vrcforge-avatar-wardrobe/workflows/wardrobe-authoring.json"


def test_recognition_is_separate_bounded_read_only_loop() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    guide = GUIDE.read_text(encoding="utf-8")
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    loop = workflow["recognitionLoop"]
    first = workflow["steps"][0]

    assert "识别现有结构" in skill and "AnyState 只是扫描线索" in skill
    assert "现有结构识别循环（只读）" in guide
    assert loop["writesAllowed"] is False
    assert loop["assumeAnyState"] is False
    assert loop["assumeParameterNameOrType"] is False
    assert loop["localMissDoesNotEndGlobalScan"] is True
    assert loop["cyclePolicy"].startswith("mark_undetermined")
    assert loop["budgetExhaustion"]["capabilityGap"] is True
    assert loop["visitedKey"] == ["identityScope", "controller", "layer", "stateMachinePath", "resourceHash"]
    assert loop["pageCursor"] == "independent_per_read_source"
    assert [stage["name"] for stage in loop["stages"]] == ["enter", "read", "analyze", "judge", "exit", "next"]
    assert set(loop["stages"][3]["outputs"]) == {"confirmed", "rejected", "undetermined"}
    assert "prompts/get(identityLockUri, sessionContextUri)" in loop["identityReadiness"]["beforeDomainReads"]
    assert loop["identityReadiness"]["requiredContextStatus"] == "ready_for_planning"
    assert loop["identityReadiness"]["preserveFullExecutionTargetJson"] is True
    assert loop["confirmationRequiresFxAndClipEvidence"] is True
    assert loop["scanWardrobeIsOptionalClue"] is True
    assert "先沿允许的只读路径继续识别" in guide
    assert "Bool、Float、BlendTree" in skill
    assert "不得隐式迁移成 Int" in guide
    assert first["boundedRecognitionLoop"] is True
    assert first["continueOnLocalUndetermined"] is True
    assert first["writes"] is False


def test_recognition_tools_remain_within_signed_skill_allowlist() -> None:
    skill_lines = SKILL.read_text(encoding="utf-8").splitlines()
    allowed = {line.strip()[2:] for line in skill_lines if line.strip().startswith("- vrcforge_")}
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    first = workflow["steps"][0]
    assert {first["tool"], *first["tools"]} <= allowed
    assert first["tool"] == "vrcforge_read_avatar_descriptor"
    assert first["tools"].index("vrcforge_read_avatar_descriptor") < first["tools"].index("vrcforge_scan_fx_animator")
    assert {first["tool"], *first["tools"]}.isdisjoint({"vrcforge_manage_wardrobe", "vrcforge_write_animation_curve"})


def test_authoring_contract_is_scoped_and_does_not_migrate_existing_topology() -> None:
    contract = json.loads(WORKFLOW.read_text(encoding="utf-8"))["communityWardrobeContract"]
    scope = contract["applicability"]
    assert scope["appliesTo"] == "user_approved_new_or_repaired_fixed_value_int_wardrobe_authoring"
    assert scope["recognitionMode"] == "read_existing_any_supported_parameter_or_topology"
    assert scope["preserveExistingBoolFloatBlendTree"] is True
    assert scope["noImplicitMigrationToInt"] is True


def test_scanner_hit_alone_cannot_confirm_a_wardrobe():
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    loop = workflow["recognitionLoop"]
    assert loop["confirmationRequiresFxAndClipEvidence"] is True
    assert loop["scanWardrobeIsOptionalClue"] is True
    assert "entryExit" in loop["missingFxStructureFieldsRemainUndetermined"]
