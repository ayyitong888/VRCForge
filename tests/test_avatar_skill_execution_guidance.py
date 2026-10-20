"""Contract checks for avatar-skill execution boundaries."""

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SKILLS = ROOT / "artifacts" / "skills"


def load(skill: str, relative: str):
    path = SKILLS / skill / relative
    return json.loads(path.read_text(encoding="utf-8"))


def test_authoring_workflows_require_complete_unpack_and_rebind() -> None:
    for skill, workflow, contract_key in (
        ("vrcforge-avatar-wardrobe", "workflows/wardrobe-authoring.json", "communityWardrobeContract"),
        ("vrcforge-avatar-accessory-switch", "workflows/accessory-switch.json", "communityContract"),
    ):
        data = load(skill, workflow)
        unpack = data[contract_key]["prefabInstanceAuthoring"]
        assert unpack["tool"] == "vrcforge_unpack_prefab"
        assert unpack["mode"] == "completely"
        assert unpack["previewRequired"] is True
        assert unpack["approvalAndCheckpointRequired"] is True
        assert unpack["rebindAfterUnpack"] is True
        assert unpack["persistedReadbackRequired"] is True
        assert unpack["preserveSourcePrefabAsset"] is True
        assert any(step["tool"] == "vrcforge_unpack_prefab" for step in data["steps"])


def test_face_tracking_configuration_and_play_acceptance_are_separate() -> None:
    for skill, workflow, contract_key in (
        ("vrcforge-avatar-wardrobe", "workflows/wardrobe-authoring.json", "communityWardrobeContract"),
        ("vrcforge-avatar-accessory-switch", "workflows/accessory-switch.json", "communityContract"),
    ):
        split = load(skill, workflow)[contract_key]["faceTrackingAcceptance"]
        assert split["serializedInstallAndConfiguration"] == "independent_edit_mode_readback"
        assert split["playRuntimeAndVisualAcceptance"] == "separate_explicitly_approved_bounded_observation"
        assert split["configurationDoesNotProveRuntime"] is True


def test_breast_physics_audit_only_hands_off_explicit_serialized_writes() -> None:
    handoff = load("vrcforge-avatar-breast-physics-audit", "workflows/breast-physics-audit.json")["postAuditHandoff"]
    assert handoff["auditRemainsReadOnly"] is True
    assert handoff["requiresSeparateExplicitUserAuthorization"] is True
    assert handoff["allowedWriteSurface"] == "confirmed_serialized_fields_only"
    assert handoff["toolSurface"] == "existing_generic_core176_component_or_property_tools"
    assert "dynamic_csharp" in handoff["forbidden"]
