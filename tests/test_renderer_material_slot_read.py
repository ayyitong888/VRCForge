from unittest.mock import Mock

import dashboard_server as server


def test_exact_renderer_material_slots_include_nulls_and_reuse_shader_read(monkeypatch):
    monkeypatch.setattr(server, "get_gameobject_sync", lambda params: {
        "ok": True,
        "gameObjectPath": params["gameObjectPath"],
        "componentIdentities": [{"type": "UnityEngine.SkinnedMeshRenderer", "componentIndex": 0}],
    })
    read = Mock(return_value={
        "ok": True,
        "propertyValue": [{"name": "Face", "assetPath": "Assets/Face.mat"}, None],
        "valueItemCount": 2,
    })
    shader = Mock(return_value={"shaderName": "Hidden/lilToonOutline"})
    monkeypatch.setattr(server, "read_component_property_sync", read)
    monkeypatch.setattr(server, "inspect_material_properties_sync", shader)

    result = server.scan_shader_materials_sync(server.ShaderMaterialScanRequest(
        projectPath="project", avatarPath="Avatar", rendererPaths=["Avatar/Face", "Avatar/Body"]))
    assert result["ok"]
    assert result["nullSlotCount"] == 2
    assert result["slotsRead"] == 4
    assert result["renderers"][0]["slots"] == [
        {"slotIndex": 0, "materialName": "Face", "materialAssetPath": "Assets/Face.mat", "shaderName": "Hidden/lilToonOutline"},
        {"slotIndex": 1, "materialName": None},
    ]
    assert read.call_count == 2
    assert all(call.args[0]["propertyPath"] == "sharedMaterials" for call in read.call_args_list)
    shader.assert_called_once_with({"projectPath": "project", "materialAssetPath": "Assets/Face.mat", "limit": 1})
