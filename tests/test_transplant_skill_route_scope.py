import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEAD = ROOT / "examples/skill-packages/vrcforge-avatar-head-transplant"
PART = ROOT / "examples/skill-packages/vrcforge-avatar-part-transplant"
FACE = ROOT / "examples/skill-packages/vrcforge-face-tracking-installation"


def read_skill(package: Path) -> str:
    return (package / "SKILL.md").read_text(encoding="utf-8").casefold()


def read_workflow(package: Path) -> dict:
    filename = f"avatar-{package.name.removeprefix('vrcforge-avatar-')}.json"
    return json.loads((package / "workflows" / filename).read_text(encoding="utf-8"))


def test_transplant_packages_bump_minor_patch_without_author_change():
    for package in (HEAD, PART):
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["version"] == ("1.0.7" if package is HEAD else "1.0.4")
        assert manifest["author"] == "VRCForge"


def test_face_tracking_installation_is_a_short_docs_first_route():
    manifest = json.loads((FACE / "manifest.json").read_text(encoding="utf-8"))
    skill = read_skill(FACE)
    assert manifest["version"] == "1.0.1"
    assert manifest["entrypoints"] == {"skill": "SKILL.md"}
    assert "面捕安装与配置" in skill
    assert "patch" in skill
    assert "scene or variant" in skill
    assert "full head transplant" in skill
    assert "play mode" in skill
    assert "gesture manager" in skill


def test_head_lipsync_acceptance_is_mode_specific_and_missing_jaw_read_is_a_gap():
    workflow = read_workflow(HEAD)
    modes = workflow["headRendererConsumerClosureContract"]["lipSyncModeContract"]
    assert {"default", "viseme_blend_shape", "jaw_flap_blend_shape", "jaw_flap_bone", "viseme_parameter_only"} <= set(modes)
    assert modes["default"]["autoDetectByDefault"] is False
    assert "request_approval" in modes["default"]["ifStillDefault"]
    assert modes["default"]["onUnresolved"]["capabilityGap"] is True
    assert "visemeSkinnedMesh" in modes["viseme_blend_shape"]["requiredReadback"]
    assert {"jawFlapBlendShapeRenderer", "jawFlapBlendShapeName"} <= set(modes["jaw_flap_blend_shape"]["requiredReadback"])
    assert "visemeSkinnedMesh" not in modes["jaw_flap_bone"]["requiredReadback"]
    assert "jawBone" in modes["jaw_flap_bone"]["requiredReadback"]
    assert modes["jaw_flap_bone"]["onMissingReadback"]["capabilityGap"] is True
    assert "visemeSkinnedMesh" not in modes["viseme_parameter_only"]["requiredReadback"]
    assert "renderer" in modes["viseme_parameter_only"]["requiredReadback"]
    assert "mode-specific" in read_skill(HEAD)


def test_head_renderer_closure_allows_multiple_renderers_and_replace_object_is_conditional():
    workflow = read_workflow(HEAD)
    closure = workflow["headRendererConsumerClosureContract"]
    assert closure["bindingResolution"] == "each_binding_to_its_actual_active_renderer"
    assert closure["multipleActiveRenderersAllowed"] is True
    head_swap = workflow["communityAvatarModificationContract"]["headSwap"]
    replace_object = head_swap["maReplaceObject"]
    assert "kesera2" in replace_object["communityBasis"].casefold()
    assert replace_object["requiredWhen"]
    assert replace_object["notRequiredWhen"]
    assert "required" not in head_swap["steps"]
    assert "conditionally" in read_skill(HEAD)


def test_part_skeletal_fields_apply_only_to_weighted_smr_routes():
    workflow = read_workflow(PART)
    contract = workflow["skinningContract"]
    assert set(contract["requiredRoutes"]) == {"same_skeleton_smr", "foreign_skeleton_smr"}
    assert set(contract["notApplicableRoutes"]) >= {"rigid", "animator_menu"}
    rigid = next(mode for mode in workflow["transplantModes"] if mode["id"] == "rigid")
    assert "bone" in " ".join(rigid["steps"]).casefold()  # target-bone attachment is valid
    assert "rigid" in contract["notApplicableRoutes"]  # weighted-SMR closure is not a rigid-route gate


def test_strict_visual_acceptance_is_scoped_to_relevant_routes_and_is_project_specific():
    head = read_workflow(HEAD)
    part = read_workflow(PART)
    head_cat_ears = head["transplantModes"][3]
    part_cat_ears = part["transplantModes"][3]
    assert "same_framing_close_root_Rest_AFK_multiview" in " ".join(head_cat_ears["steps"])
    assert "same_framing_close_root_Rest_AFK_multiview" in " ".join(part_cat_ears["steps"])
    assert head["acceptanceViewContract"]["requiredAngles"][-1] == "bottom"
    cat_ears = part["catEarDeformationDiagnosticsContract"]
    assert "bottom" in cat_ears["views"]
    assert cat_ears["sameFramingRestAfkMultiViewPairs"] is True
    assert cat_ears["attachment"]["swayChainRoot"]["requireEveryDescendantBoneRestScaleOne"] is True
    assert "5.6-sol" not in read_skill(HEAD)
    assert "5.6-sol" not in read_skill(PART)
    assert "not a vrchat or unity standard" in read_skill(HEAD)
    assert "not a vrchat or unity standard" in read_skill(PART)
