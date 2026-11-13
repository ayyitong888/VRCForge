"""Read exact mesh renderer material slots through existing Unity read tools."""

from collections.abc import Callable


def read_renderer_material_slots(project_path, avatar_path, renderer_paths, *,
                                 read_object: Callable, read_property: Callable, inspect_material: Callable):
    if len(set(renderer_paths)) != len(renderer_paths) or any(
        not path or path != path.strip() for path in renderer_paths
    ):
        return {"ok": False, "error": "rendererPaths must contain unique exact hierarchy paths."}
    project = {"projectPath": project_path}
    renderers = []
    shaders = {}
    for path in renderer_paths:
        if avatar_path and path != avatar_path and not path.startswith(avatar_path + "/"):
            return {"ok": False, "error": f"{path} is outside the requested avatar hierarchy."}
        target = {"gameObjectPath": path, **project}
        obj = read_object(target)
        if obj.get("ok") is False:
            return obj
        components = [row for row in obj.get("componentIdentities", []) if row.get("type") in (
            "UnityEngine.MeshRenderer", "UnityEngine.SkinnedMeshRenderer",
        )]
        if obj.get("gameObjectPath") != path or not components:
            return {"ok": False, "error": f"Find the exact hierarchy path of a MeshRenderer or SkinnedMeshRenderer for {path}."}
        for component in components:
            value = read_property({
                **target, "componentType": component["type"], "componentIndex": component["componentIndex"],
                "propertyPath": "sharedMaterials", "maxItems": 2000,
            })
            if value.get("ok") is False:
                return value
            materials = value.get("propertyValue")
            if (not isinstance(materials, list) or value.get("valueTruncated") or value.get("valueIncomplete")
                    or value.get("valueItemCount") != len(materials)):
                return {"ok": False, "error": f"Unity did not return all material slots for {path}."}
            slots = []
            for index, material in enumerate(materials):
                row = {"slotIndex": index, "materialName": material.get("name", "") if material is not None else None}
                if material is not None:
                    asset = material.get("assetPath") or ""
                    row["materialAssetPath"] = asset
                    if asset.startswith("Assets/") and asset.lower().endswith(".mat"):
                        if asset not in shaders:
                            shaders[asset] = inspect_material({**project, "materialAssetPath": asset, "limit": 1})
                        shader = shaders[asset]
                        if "shaderName" in shader:
                            row["shaderName"] = shader["shaderName"]
                        else:
                            row["shaderReadStatus"] = "unavailable"
                    else:
                        row["shaderReadStatus"] = "unsupported_asset"
                slots.append(row)
            renderers.append({
                "rendererPath": path, "componentType": component["type"],
                "componentIndex": component["componentIndex"], "slots": slots,
            })
    return {
        "ok": True, "renderers": renderers,
        "slotsRead": sum(len(renderer["slots"]) for renderer in renderers),
        "nullSlotCount": sum(slot["materialName"] is None for renderer in renderers for slot in renderer["slots"]),
    }
