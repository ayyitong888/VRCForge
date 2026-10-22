import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_skill(name):
    base = ROOT / "artifacts" / "skills" / name
    return {
        "manifest": json.loads((base / "manifest.json").read_text(encoding="utf-8")),
        "skill": (base / "SKILL.md").read_text(encoding="utf-8"),
        "reference": (base / "references" / "workflow.md").read_text(encoding="utf-8"),
        "workflow": json.loads((base / "workflows" / ("animation.json" if name.endswith("animation") else "expression-menu.json")).read_text(encoding="utf-8")),
    }


def test_animation_keeps_timeline_and_only_uses_time_zero_for_approved_static_design():
    skill = load_skill("vrcforge-avatar-animation")
    assert skill["manifest"]["version"] == "1.0.6"
    assert skill["manifest"]["author"] == "VRCForge"
    assert skill["manifest"]["description"] == next(line.removeprefix("description: ") for line in skill["skill"].splitlines() if line.startswith("description: "))
    assert "timeline" in skill["reference"].lower()
    assert "Write Defaults" in skill["reference"]
    contract = skill["workflow"]["communityContract"]
    assert "write_defaults_recovery_curves_and_evidence" in contract["offMotionDecision"]
    assert "keyframeTimeSeconds" not in contract
    assert contract["preserveExistingTimelineUnlessApprovedOtherwise"] is True
    assert contract["timeZeroOnlyForApprovedStaticDesign"] is True
    assert contract["offMotionDecision"] == "inspect_write_defaults_recovery_curves_and_evidence; modify_only_if_explicitly_approved"
    assert "preserveExistingEmptyOffMotion" not in contract
    assert "createMissingOffMotion" not in contract
    assert "AnimationClip" in skill["skill"] and "OFF motion" in skill["skill"]
    allowed = skill["skill"].split("allowed-tools:", 1)[1].split("support-files:", 1)[0]
    assert all(
        f"- {tool}" in allowed
        for step in skill["workflow"]["steps"]
        for tool in [step["tool"], *step.get("tools", [])]
    )


def test_expression_menu_is_scoped_to_user_request_and_has_parameter_readback():
    skill = load_skill("vrcforge-avatar-expression-menu")
    assert skill["manifest"]["version"] == "1.0.2"
    assert skill["manifest"]["author"] == "VRCForge"
    assert skill["manifest"]["description"] == next(line.removeprefix("description: ") for line in skill["skill"].splitlines() if line.startswith("description: "))
    assert "rootOrder" not in skill["workflow"]["communityContract"]
    assert "wardrobeCategoryOrder" not in skill["workflow"]["communityContract"]
    assert "FacialTracking" not in skill["reference"]
    assert "R18" not in skill["reference"]
    assert "includeText=true" in skill["reference"]
    steps = skill["workflow"]["steps"]
    allowed = skill["skill"].split("allowed-tools:", 1)[1].split("support-files:", 1)[0]
    assert all(
        f"- {tool}" in allowed
        for step in steps
        for tool in [step["tool"], *step.get("tools", [])]
    )
    parameter_step = next(step for step in steps if step["name"] == "ensure_expression_parameter_only_when_required_by_requested_control")
    assert parameter_step["tool"] == "vrcforge_preview_ensure_expression_parameter"
    assert parameter_step["tools"] == ["vrcforge_ensure_expression_parameter"]
    assert "needs a missing or explicitly changed parameter" in parameter_step["when"]
    assert "derive its exact name, type and behavior from project evidence and user intent" in parameter_step["when"]
    assert "present them in preview" in parameter_step["when"]
    assert "only after that design is approved" in parameter_step["when"]
    readback = next(step for step in steps if step["name"] == "read_back_exact_requested_controls_and_parameters")
    assert "vrcforge_get_asset_info" in readback["tools"]
    assert "control_name_type_parameter_value_icon_submenu_reference" in readback["verify"]
    assert "parameter_type_default_saved_synced" in readback["verify"]
    assert readback["scope"] == "requested_menu_paths_and_associated_parameters_only"
    assert "vrcforge_scan_avatar_controls" in skill["reference"]
    schema = (ROOT / "unity_read_input_schemas.py").read_text(encoding="utf-8")
    server = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    reader = (ROOT / "asset_text_resources.py").read_text(encoding="utf-8")
    assert '"includeText": {"type": "boolean"' in schema
    assert 'if params.get("includeText") is True' in server
    assert 'payload["text"] = publish_asset_text' in server
    assert '"sourceComplete": True' in reader
