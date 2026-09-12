from optimization_service import (
    build_material_slot_audit,
    build_optimization_tool_result,
    build_shader_adapter_registry,
)


def failed_material_validation() -> dict:
    return {
        "sources": {
            "materials": {
                "ok": True,
                "payload": {
                    "ok": True,
                    "inventory": {
                        "ok": False,
                        "code": "managed_write_required",
                        "error": "This tool requires a one-use managed write authorization.",
                    },
                    "materials": [],
                    "summary": {},
                },
            }
        }
    }


def test_material_slot_audit_fails_closed_on_nested_inventory_failure() -> None:
    result = build_material_slot_audit(failed_material_validation())
    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["failure"]["code"] == "managed_write_required"
    assert result["summary"]["rendererCount"] is None


def test_shader_registry_blocks_when_material_source_is_unavailable() -> None:
    result = build_shader_adapter_registry({}, failed_material_validation())
    assert result["ok"] is False
    assert result["summary"]["scannerCoverage"] == "unavailable"
    assert result["hardGate"]["status"] == "blocked"
    assert "source.materials" in result["hardGate"]["blockingIds"]


def test_material_tools_propagate_required_source_failure_to_outer_result() -> None:
    for name in ("optimization.material-slot-audit", "optimization.shader.adapter-registry"):
        result = build_optimization_tool_result(name, {}, failed_material_validation())
        assert result["ok"] is False
        assert result["status"] == "failed"
        assert result["error"]["code"] == "managed_write_required"


def test_shader_rows_project_real_scanner_rows_and_keep_same_named_materials_distinct() -> None:
    validation = {
        "sources": {
            "materials": {
                "payload": {
                    "materials": [
                        {
                            "renderer_path": "Avatar/Shirt",
                            "material_name": "Shared",
                            "material_asset_path": "Assets/A.mat",
                            "material_asset_guid": "guid-a",
                            "shader_name": "lilToon",
                            "renderer_component_id": "component-shirt",
                            "renderer_component_type": "SkinnedMeshRenderer",
                            "renderer_component_index": 0,
                            "slot_index": 0,
                            "scene_guid": "scene-a",
                        },
                        {
                            "renderer_path": "Avatar/Shirt",
                            "material_name": "Shared",
                            "material_asset_path": "Assets/B.mat",
                            "material_asset_guid": "guid-b",
                            "shader_name": "lilToon",
                            "renderer_component_id": "component-shirt",
                            "renderer_component_type": "SkinnedMeshRenderer",
                            "renderer_component_index": 1,
                            "slot_index": 1,
                            "scene_guid": "scene-a",
                        },
                    ]
                }
            }
        }
    }
    result = build_shader_adapter_registry({}, validation)
    assert result["summary"]["materialCount"] == 2
    assert {item["materialId"] for item in result["materialCoverage"]} == {"guid-a", "guid-b"}
    assert {item["slotIndex"] for item in result["materialCoverage"]} == {0, 1}
    assert {item["rendererComponentId"] for item in result["materialCoverage"]} == {"component-shirt"}
    assert {item["rendererComponentType"] for item in result["materialCoverage"]} == {"SkinnedMeshRenderer"}
    assert {item["sceneGuid"] for item in result["materialCoverage"]} == {"scene-a"}
