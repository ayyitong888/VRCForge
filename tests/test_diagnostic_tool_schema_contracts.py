"""Exercise the real registered internal/external diagnostic schema projection."""
import pytest
from jsonschema import Draft202012Validator

EXPECTED = {'vrcforge_apply_parameter_optimization': ['avatarPath', 'dry_run', 'projectPath', 'suggestions'], 'vrcforge_avatar_encryption_plan': ['avatarPath', 'confirmCreatorOwnedAssets', 'includeCompatibility', 'inventory', 'materialIds', 'platform', 'profile', 'projectPath', 'protectionProfile', 'rendererPaths', 'targetPlatform', 'targetShaderFamilies', 'targets'], 'vrcforge_avatar_encryption_preview': ['avatarPath', 'confirmCreatorOwnedAssets', 'includeCompatibility', 'inventory', 'materialIds', 'plan', 'platform', 'profile', 'projectPath', 'protectionProfile', 'rendererPaths', 'targetPlatform', 'targetShaderFamilies', 'targets'], 'vrcforge_avatar_encryption_research_report': ['includeExternalReferences'], 'vrcforge_avatar_encryption_scan': ['avatarPath', 'includeCompatibility', 'inventory', 'projectPath'], 'vrcforge_inspect_primitive_basis_fixture': ['expectedRunIdDigest', 'projectPath'], 'vrcforge_optimization_aao_hidden_body_cut_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_aao_trace_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_baseline_scan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_dependency_doctor': ['projectPath'], 'vrcforge_optimization_lac_profile_plan': ['avatarPath', 'customProfile', 'includeQuest', 'maxErrors', 'projectPath', 'targetProfile'], 'vrcforge_optimization_ma2bt_convertibility_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_ma2bt_skipped_reasons': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_ma_responsive_layer_audit': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_material_slot_audit': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_mesh_triangle_audit': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_meshia_simplify_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_parameter_animator_usage': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_parameter_behavior_regression': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_parameter_budget_audit': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_parameter_compressibility_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_parameter_inventory': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_parameter_menu_map': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_parameter_path_to_skill': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_parameter_vrcfury_compressor_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_performance_tools_report': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_physbone_audit': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_physbone_reduce_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_plan': ['avatarPath', 'customProfile', 'includeQuest', 'maxErrors', 'projectPath', 'targetProfile'], 'vrcforge_optimization_profile_diff': ['afterValidation', 'avatarPath', 'beforeValidation', 'includeQuest', 'maxErrors', 'projectPath', 'rollbackValidation'], 'vrcforge_optimization_rollback_verify': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_shader_adapter_registry': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_target_profile': ['customProfile', 'targetProfile'], 'vrcforge_optimization_texture_vram_audit': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_ttt_atlas_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_upload_gate_audit': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_upload_gate_fix_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_validation_delta': ['afterValidation', 'approvalId', 'beforeValidation', 'checkpointId', 'optimizerTool', 'rollbackValidation'], 'vrcforge_optimization_visual_regression_plan': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_optimization_vrcfury_compatibility_report': ['avatarPath', 'includeQuest', 'maxErrors', 'projectPath'], 'vrcforge_preview_parameter_bit_packing': ['outputCloneName', 'projectPath', 'sourceAvatarPath', 'sourceScenePath'], 'vrcforge_run_validation_report': ['avatarPath', 'gateBuild', 'includeQuest', 'includeReadiness', 'includeSources', 'maxErrors', 'projectPath']}

@pytest.fixture(scope="module")
def descriptors():
    import dashboard_server as server
    gateway = server.AGENT_GATEWAY
    external = {item["name"]: item for item in gateway.build_external_mcp_tools("execution", tool_blocks=["*"])}
    return gateway, external

@pytest.mark.parametrize("name,fields", EXPECTED.items())
def test_registered_diagnostic_schema_exposes_handler_fields_and_matches_internal(descriptors, name, fields):
    gateway, external = descriptors
    schema = external[name]["inputSchema"]
    assert set(fields) <= schema.get("properties", {}).keys()
    internal = gateway.shared_agent_tool_descriptor(name, write=name == "vrcforge_apply_parameter_optimization")
    assert schema == internal["inputSchema"]
    assert not {"settings_path", "unity_host", "unity_port", "unity_instance", "api_key"} & schema["properties"].keys()
    Draft202012Validator.check_schema(schema)


def test_optional_diagnostics_and_legacy_aliases_remain_valid(descriptors):
    _, external = descriptors
    for name in EXPECTED:
        if name in {"vrcforge_inspect_primitive_basis_fixture", "vrcforge_preview_parameter_bit_packing", "vrcforge_apply_parameter_optimization"}:
            continue
        schema = external[name]["inputSchema"]
        Draft202012Validator(schema).validate({})
        Draft202012Validator(schema).validate({"project_path": "D:/Fixture", "avatar_path": "Avatar"})
    profile = external["vrcforge_optimization_target_profile"]["inputSchema"]
    Draft202012Validator(profile).validate({"targetProfile": "custom", "customProfile": {"label": "Mine", "weights": {"vram": 0.8}}})
    assert profile["properties"]["targetProfile"]["default"] == "pc_conservative"
    apply = external["vrcforge_apply_parameter_optimization"]["inputSchema"]
    assert apply["properties"]["dry_run"]["default"] is True
    assert "suggestions" in apply.get("required", [])  # handler rejects an empty request even for dry-run


def test_fixture_digest_alias_and_bit_packing_shapes(descriptors):
    _, external = descriptors
    fixture = external["vrcforge_inspect_primitive_basis_fixture"]["inputSchema"]
    Draft202012Validator(fixture).validate({"expected_run_id_digest": "a" * 64})
    assert list(Draft202012Validator(fixture).iter_errors({"expectedRunIdDigest": "bad"}))
    preview = external["vrcforge_preview_parameter_bit_packing"]["inputSchema"]
    target = {"schema": "vrcforge.execution_target.v1", "namespace": {}, "scope": {}, "project": {}, "editor": {}}
    identity = {"projectPath": "D:/Fixture", "sourceScenePath": "Assets/Scene.unity", "sourceAvatarPath": "Avatar", "outputCloneName": "Packed"}
    for arguments in [{**identity, "executionTarget": target}, {"arguments": identity, "executionTarget": target}, {"params": identity, "executionTarget": target}]:
        Draft202012Validator(preview).validate(arguments)
