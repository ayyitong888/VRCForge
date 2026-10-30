"""Shared Unity preview/apply input contracts; no execution or registry ownership."""
from __future__ import annotations

from typing import Any, Literal, get_args


ShaderMaterialCategory = Literal["skin", "eyes", "hair", "clothes", "accessory", "unknown"]


def outfit_import_input_schema(*, require_project_path: bool) -> dict[str, Any]:
    """Build the shared plan/write contract for the supervised outfit import lane."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["packagePath", "projectPath"] if require_project_path else ["packagePath"],
        "properties": {
            "packagePath": {"type": "string", "minLength": 1, "description": "Exact existing filesystem path to a UnityPackage or loose outfit folder; this is not an Assets-relative path. Loose folders may include prefab, material, texture, model, and Editor assembly definition (.asmdef) files."},
            "projectPath": {"type": "string", "minLength": 1, "description": "Exact existing Unity project root."},
            "targetFolder": {"type": "string", "description": "Optional project-relative Assets/... destination; applies only when copying a loose folder, not when importing a UnityPackage. The plan shows existing destination paths that would be overwritten and requests confirmation before applying them."},
            "selectedUnityPackage": {"type": "string", "description": "Optional exact nested UnityPackage path selected from the preceding package inspection; omit when the source is already one package or a loose folder."},
            "dependencyMode": {
                "type": "string",
                "enum": ["auto", "selected_only"],
                "default": "auto",
                "description": "Dependency handling: auto discovers filename-matched companion support packages; selected_only imports only the selected package.",
            },
            "selectedPrefab": {"type": "string", "description": "Optional exact prefab path selected from the preceding import plan; omit when importing a texture-only or single-FBX source."},
            "baseAvatarName": {"type": "string", "description": "Optional exact base avatar name used by the import plan."},
            "maxEntries": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 5000},
        },
    }
SHADER_CATEGORY_OVERRIDES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Exact material_id to supported material category; unsupported category names are rejected.",
    "additionalProperties": {"type": "string", "enum": list(get_args(ShaderMaterialCategory))},
}
SHADER_CHANGE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["material_id", "semantic_property", "after"],
    "additionalProperties": True,
    "properties": {
        "material_id": {"type": "string", "minLength": 1, "description": "Exact material_id from a fresh material inventory."},
        "semantic_property": {"type": "string", "minLength": 1, "description": "Supported semantic property key from this material's supported_properties; raw shader property names are rejected."},
        "after": {"type": ["number", "string"], "description": "Requested finite numeric value or hex color; validated against the selected semantic property."},
        "before": {"description": "Optional prior value for display; preparation obtains and binds the current value from Unity."},
        "reason": {"type": "string", "description": "Reason for this requested change."},
    },
}


TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "projectPath",
        "textureAssetPath",
        "platform",
        "maxTextureSize",
        "format",
        "compression",
        "crunch",
        "quality",
    ],
    "properties": {
        "projectPath": {
            "type": "string",
            "description": "Exact existing Unity project root bound to this one-texture operation.",
        },
        "textureAssetPath": {
            "type": "string",
            "pattern": "^Assets/",
            "description": "Exact persistent project-relative texture path under Assets/.",
        },
        "platform": {
            "type": "string",
            "enum": ["default", "standalone", "android", "ios"],
            "description": "Exact TextureImporter platform settings to inspect or change.",
        },
        "maxTextureSize": {
            "type": "integer",
            "enum": [32, 64, 128, 256, 512, 1024, 2048, 4096, 8192],
        },
        "format": {
            "type": "string",
            "enum": [
                "automatic",
                "rgb24",
                "rgba32",
                "dxt1",
                "dxt5",
                "dxt1_crunched",
                "dxt5_crunched",
                "bc7",
                "etc_rgb4",
                "etc2_rgb4",
                "etc2_rgba8",
                "etc_rgb4_crunched",
                "etc2_rgba8_crunched",
                "astc_4x4",
                "astc_6x6",
                "astc_8x8",
                "pvrtc_rgb4",
                "pvrtc_rgba4",
            ],
        },
        "compression": {
            "type": "string",
            "enum": ["uncompressed", "normal", "high", "low"],
        },
        "crunch": {"type": "boolean"},
        "quality": {"type": "integer", "minimum": 0, "maximum": 100},
        "streamingMipmaps": {
            "type": "boolean",
            "description": "Optional mipmap-streaming state; omit to preserve the existing importer setting.",
        },
    },
}

_PROJECT_PATH_PROPERTY = {
    "type": "string",
    "description": "Exact existing Unity project root bound to this tool call.",
}
_AVATAR_PATH_PROPERTY = {
    "type": "string",
    "description": "Exact loaded-scene avatar hierarchy path.",
}

MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath"],
    "oneOf": [
        {"required": ["materialAssetPath", "propertyName", "textureAssetPath"], "not": {"required": ["assignments"]}},
        {"required": ["assignments"], "not": {"anyOf": [{"required": [key]} for key in ("materialAssetPath", "propertyName", "textureAssetPath", "textureScale", "textureOffset")] }},
    ],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "materialAssetPath": {
            "type": "string",
            "pattern": "^Assets/.*\\.mat$",
            "description": "Exact existing persistent Assets/... .mat material.",
        },
        "propertyName": {
            "type": "string",
            "minLength": 1,
            "description": "Exact Texture property exposed by the installed shader on this material. Unity validates the property type; float, color, vector and unknown properties are rejected.",
        },
        "textureAssetPath": {
            "type": "string",
            "pattern": "^Assets/.+",
            "description": "Exact existing project Texture2D asset to assign.",
        },
    },
}
MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA["properties"]["assignments"] = {
    "type": "array", "minItems": 1, "maxItems": 128,
    "items": {"type": "object", "additionalProperties": False,
        "required": ["materialAssetPath", "propertyName", "textureAssetPath"],
        "properties": {key: MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA["properties"][key] for key in ("materialAssetPath", "propertyName", "textureAssetPath")}},
}

RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath"],
    "oneOf": [
        {"required": ["rendererPath", "slotIndex", "newMaterialAssetPath"], "not": {"required": ["assignments"]}},
        {"required": ["assignments"], "not": {"anyOf": [{"required": [key]} for key in ("rendererPath", "rendererComponentId", "slotIndex", "newMaterialAssetPath")]}},
    ],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "rendererPath": {
            "type": "string",
            "minLength": 1,
            "description": "Exact loaded-scene Renderer hierarchy path. Single mode uses it with slotIndex and newMaterialAssetPath; batch rows also require the matching rendererComponentId. The path must resolve to one Renderer in one saved scene.",
        },
        "rendererComponentId": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
            "description": "Stable Renderer component identity from the preview. Optional only for a single-slot preview; required for single-slot apply and every batch row.",
        },
        "slotIndex": {"type": "integer", "minimum": 0, "maximum": 1024},
        "newMaterialAssetPath": {
            "type": "string",
            "pattern": "^Assets/.*\\.mat$",
            "description": "One existing persistent material asset to assign to this exact slot.",
        },
    },
}

RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA["properties"]["assignments"] = {
    "type": "array", "minItems": 1, "maxItems": 256,
    "description": "Batch mode: 1..256 exact slot assignments, all resolving to the same saved scene. Each row needs rendererPath, rendererComponentId from read/preview, slotIndex and newMaterialAssetPath. Do not send top-level rendererPath, slotIndex or newMaterialAssetPath, or scenePath/avatarPath selectors; the scene is resolved from the renderer paths. Bind executionTarget at scene or avatar scope (not component scope). All rows are preflighted before one approved scene save; unspecified slots remain unchanged. Maximum 512 KiB including sealed preview evidence.",
    "items": {"type": "object", "additionalProperties": False,
        "required": ["rendererPath", "rendererComponentId", "slotIndex", "newMaterialAssetPath"],
        "properties": {key: RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA["properties"][key] for key in ("rendererPath", "rendererComponentId", "slotIndex", "newMaterialAssetPath")}},
}

SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "sourceScenePath", "sourceObjectPath", "targetParentScenePath", "targetParentPath", "targetName"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "sourceScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$", "description": "Exact project-relative Assets/... .unity source scene; it must be saved."},
        "sourceObjectPath": {"type": "string", "description": "Exact hierarchy path of the object to duplicate, copied from a scene read."},
        "targetParentScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$", "description": "Exact project-relative Assets/... .unity scene containing the target parent; it must be saved."},
        "targetParentPath": {"type": "string", "description": "Exact hierarchy path of the parent that receives the new duplicate."},
        "targetName": {"type": "string", "description": "Name of the create-new duplicate; an existing sibling is never overwritten."},
        "preserveWorldTransform": {"type": "boolean", "default": False, "description": "Keep the copied object's world transform while parenting it under the target."},
        "overwrite": {"type": "boolean", "const": False},
    },
}

SCENE_ASSET_DUPLICATE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "sourceScenePath", "destinationScenePath"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "sourceScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$", "description": "Exact existing project-relative Assets/... .unity source scene."},
        "destinationScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$", "description": "Exact new project-relative Assets/... .unity path; neither scene file nor metadata may already exist."},
        "openAsOnlyActiveScene": {"type": "boolean", "default": False, "description": "After creating the copy, optionally open it as the only active scene."},
        "overwrite": {"type": "boolean", "const": False},
    },
}

AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "avatarPath"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "avatarPath": _AVATAR_PATH_PROPERTY,
        "viewPosition": {"type": "object", "additionalProperties": False, "description": "Viewpoint Vector3 in this tool as an object {x,y,z}; omitted coordinates keep their current values.", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}}},
        "lipSync": {"type": "string", "description": "Supported VRCAvatarDescriptor lip-sync enum name; copy a value from vrcforge_read_avatar_descriptor."},
        "visemeSkinnedMeshPath": {"type": "string", "description": "Exact loaded-scene hierarchy path of the SkinnedMeshRenderer used for visemes; copy it from a fresh scene read."},
        "visemeBlendShapes": {"type": "array", "description": "Viseme blendshape names in the order used by the avatar descriptor; use names from the selected renderer's blendshape scan.", "items": {"type": "string"}},
        "expressionParametersPath": {"type": "string", "pattern": "^Assets/", "description": "Exact project-relative Assets/... path to an existing VRCExpressionParameters asset."},
        "expressionsMenuPath": {"type": "string", "pattern": "^Assets/", "description": "Exact project-relative Assets/... path to an existing VRCExpressionsMenu asset."},
        "baseAnimationLayers": {"type": "array", "description": "Partial updates keyed by layer type; omitted layer types are preserved.", "items": {"type": "object", "description": "Each row supplies type (descriptor animation-layer enum name) and may set isDefault (boolean) and/or controllerPath (existing Assets/... RuntimeAnimatorController asset). Read the current descriptor first; rows update their matching layer type, not replace the whole layer array."}},
        "specialAnimationLayers": {"type": "array", "description": "Partial updates keyed by layer type; omitted layer types are preserved.", "items": {"type": "object", "description": "Each row supplies type (descriptor animation-layer enum name) and may set isDefault (boolean) and/or controllerPath (existing Assets/... RuntimeAnimatorController asset). Read the current descriptor first; rows update their matching layer type, not replace the whole layer array."}},
        "eyeLookSettingsSourceAvatarPath": {"type": "string", "description": "Exact loaded-scene hierarchy path of the source Avatar Descriptor whose complete Eye Look settings and scene references will be copied."},
        "eyeLookEnabled": {"type": "boolean", "description": "Explicitly enable or disable Eye Look; omitting it preserves the current state."},
    },
}

ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "For shader Color/Vector component animation, explicitly preserve every intended component with curves in each affected state; omitted components are not guaranteed to inherit static material values and can become zero. Use inspected original values, not assumed white/zero defaults. bindingTargetValidated only establishes binding resolution, and verified asset readback only establishes stored curves; neither proves the running Renderer MaterialPropertyBlock or transitions. A static Material read does not include PropertyBlock overrides.",
    "additionalProperties": False,
    "required": ["projectPath"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "action": {"type": "string", "enum": ["set_curve", "delete_curve", "retarget_curve"], "default": "set_curve", "description": "set_curve creates the .anim asset if absent and adds/replaces one binding; delete_curve and retarget_curve require an existing clip. Use preview before apply."},
        "clipPath": {"type": "string", "pattern": "^Assets/", "description": "Exact project-relative Assets/... .anim path. Only set_curve may create a missing clip; multi-clip batches accept existing clips only."},
        "animatorRootPath": {"type": "string", "minLength": 1, "pattern": "\\S", "description": "Required for scene/avatar edits: actual Animator root hierarchy path. bindingPath is relative to this root, excluding the root name. Do not use assetOnly for a scene effect."},
        "assetOnly": {"type": "boolean", "default": False, "description": "True only for explicitly asset-only reusable clip authoring with no scene target. Does not verify object/component/property existence or a scene effect. Mutually exclusive with animatorRootPath."},
        "bindingPath": {"type": "string", "description": "Animation binding path relative to animatorRootPath, excluding the Animator root name; use an empty string for the root itself. Do not pass a full hierarchy path here."},
        "objectPath": {"type": "string", "description": "Compatibility alias for bindingPath; supply one path field, not both."},
        "componentType": {"type": "string", "default": "GameObject", "description": "Exact bound Unity component type, normally copied from vrcforge_scan_animation_bindings."},
        "propertyName": {"type": "string", "description": "Exact serialized animation property from the binding scan; required for a single-binding operation. A shader color component such as material._Color.a does not promise preservation of .r/.g/.b; supply their inspected values as explicit curves when preserving the full color."},
        "sourceBindingPath": {"type": "string", "description": "Retarget source path relative to animatorRootPath; omitted uses bindingPath."},
        "sourceComponentType": {"type": "string", "description": "Retarget source component type; omitted uses componentType."},
        "sourcePropertyName": {"type": "string", "description": "Exact retarget source property name from the binding scan."},
        "deleteSource": {"type": "boolean", "default": True, "description": "retarget_curve only: true moves the curve and removes its source binding; false copies it."},
        "overwriteExisting": {"type": "boolean", "default": False, "description": "Allow replacing an existing destination curve; defaults false. Retargeting into an occupied destination is rejected unless true."},
        "keys": {"type": "array", "items": {"type": "object"}, "description": "Key objects with explicit time in seconds and value, e.g. [{time:0,value:0},{time:1,value:1}]. Times must be unique. Optional inTangent/outTangent, inWeight/outWeight and weightedMode (None/In/Out/Both) control interpolation. curveValue is a compatibility alias that takes precedence over value. Provide keys or constantFloat, not both."},
        "constantFloat": {"type": "number", "description": "Create a constant curve for one binding; provide this or keys, not both."},
        "curves": {
            "type": "array", "minItems": 1, "maxItems": 256,
            "description": "Set multiple bindings on this one clip atomically; at most 4096 total keys and 512 KiB. Mutually exclusive with top-level single-curve fields.",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["propertyName"],
                "properties": {
                    "bindingPath": {"type": "string"}, "objectPath": {"type": "string"},
                    "componentType": {"type": "string", "default": "GameObject"},
                    "propertyName": {"type": "string", "minLength": 1, "description": "Exact binding property. For shader Color/Vector components, include explicit curves for all components whose values must be preserved; an alpha-only curve can leave RGB zero at runtime."},
                    "overwriteExisting": {"type": "boolean", "default": False},
                    "keys": {"type": "array", "minItems": 1, "maxItems": 4096, "items": {"type": "object"}, "description": "Same key-object format as single mode: explicit time in seconds and value (or curveValue), unique times, optional tangents/weights/weightedMode. Not frame numbers or normalized motion time."},
                    "constantFloat": {"type": "number"},
                },
                "oneOf": [
                    {"required": ["keys"], "not": {"required": ["constantFloat"]}},
                    {"required": ["constantFloat"], "not": {"required": ["keys"]}},
                ],
            },
        },
    },
    "oneOf": [
        {"required": ["clipPath", "propertyName"], "not": {"anyOf": [{"required": ["curves"]}, {"required": ["clips"]}]}},
        {"required": ["clipPath", "curves"], "properties": {"action": {"const": "set_curve"}},
         "not": {"anyOf": [{"required": [field]} for field in (
             "clips", "bindingPath", "objectPath", "componentType", "propertyName", "sourceBindingPath",
             "sourceComponentType", "sourcePropertyName", "deleteSource", "overwriteExisting", "keys", "constantFloat",
         )]}},
    ],
    "examples": [{
        "projectPath": "D:/AvatarProject", "animatorRootPath": "Avatar",
        "clipPath": "Assets/Appearance.anim",
        "curves": [{
            "bindingPaths": ["Body"], "componentType": "SkinnedMeshRenderer",
            "properties": [
                {"propertyName": "material._Color.r", "constantFloat": 1},
                {"propertyName": "material._Color.g", "constantFloat": 1},
                {"propertyName": "material._Color.b", "constantFloat": 1},
                {"propertyName": "material._Color.a", "keys": [{"time": 0, "value": 1}, {"time": 1, "value": 0}]},
            ],
        }],
    }],
}

# A group is one explicit path/property product, never a nested template.
_ANIMATION_CURVE_ROW_SCHEMA = ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["properties"]["curves"]["items"]
ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["properties"]["curves"]["items"] = {
    "oneOf": [
        _ANIMATION_CURVE_ROW_SCHEMA,
        {
            "type": "object", "additionalProperties": False,
            "required": ["bindingPaths", "componentType", "properties"],
            "description": "When to use: apply the same property curves to exact bindingPaths in listed path-then-property order. Expanded limits remain 256 curves per clip, 4096 keys and 512 KiB. When NOT to use: wildcard discovery, nested groups or per-path overrides.",
            "properties": {
                "bindingPaths": {"type": "array", "minItems": 1, "maxItems": 256,
                                 "uniqueItems": True, "items": {"type": "string"}},
                "componentType": {"type": "string", "minLength": 1},
                "overwriteExisting": {"type": "boolean", "default": False},
                "properties": {
                    "type": "array", "minItems": 1, "maxItems": 256,
                    "items": {
                        "type": "object", "additionalProperties": False, "required": ["propertyName"],
                        "properties": {
                            "propertyName": _ANIMATION_CURVE_ROW_SCHEMA["properties"]["propertyName"],
                            "keys": _ANIMATION_CURVE_ROW_SCHEMA["properties"]["keys"],
                            "constantFloat": _ANIMATION_CURVE_ROW_SCHEMA["properties"]["constantFloat"],
                        },
                        "oneOf": _ANIMATION_CURVE_ROW_SCHEMA["oneOf"],
                    },
                },
            },
        },
    ],
}
MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA["properties"].update({
    key: {
        "type": "object", "additionalProperties": False, "required": ["x", "y"],
        "properties": {axis: {"type": "number", "minimum": -3.4028234663852886e38, "maximum": 3.4028234663852886e38} for axis in ("x", "y")},
        "description": "Optional finite float32 texture " + label + "; omitted preserves existing values. When to use: change this exact texture property's UV transform with the same sealed assignment transaction. When NOT to use: shader vectors, pixels or transforms of other properties. Single requests with a transform use a one-row batch receipt.",
    } for key, label in (("textureScale", "tiling"), ("textureOffset", "offset"))
})
MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA["properties"]["assignments"]["items"]["properties"].update({
    key: MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA["properties"][key] for key in ("textureScale", "textureOffset")
})

# Existing-clips and request-local sets reuse the same curve entry contract.
ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["properties"]["clips"] = {
    "type": "array", "minItems": 1, "maxItems": 32,
    "description": "Atomically edit existing clips; <=256 curves each, <=4096 total keys, <=512 KiB. No new clips; one checkpoint.",
    "items": {"type": "object", "additionalProperties": False, "required": ["clipPath"],
              "properties": {"clipPath": {"type": "string", "pattern": "^Assets/.*\\.anim$"},
                             "curves": ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["properties"]["curves"],
                             "curveSet": {"type": "integer", "minimum": 0, "maximum": 31,
                                          "description": "Zero-based index into this request's curveSets; mutually exclusive with curves."}},
              "oneOf": [{"required": ["curves"], "not": {"required": ["curveSet"]}},
                        {"required": ["curveSet"], "not": {"required": ["curves"]}}]},
}
ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["properties"]["curveSets"] = {
    "type": "array", "minItems": 1, "maxItems": 32,
    "items": ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["properties"]["curves"],
    "description": "When to use: reuse identical curve arrays across clips via curveSet indices. When NOT to use: persistent preview-plan references or per-clip overrides. The expanded batch retains all existing key/size limits, approval and atomic verification.",
}
ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["allOf"] = [{
    "if": {"required": ["curveSets"]},
    "then": {"required": ["clips"]},
    "else": {"properties": {"clips": {"items": {"not": {"required": ["curveSet"]}}}}},
}, {"oneOf": [
    {"required": ["animatorRootPath"], "properties": {"assetOnly": {"const": False}}},
    {"required": ["assetOnly"], "properties": {"assetOnly": {"const": True}}, "not": {"required": ["animatorRootPath"]}},
]}]
ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["oneOf"].append({
    "required": ["clips"], "properties": {"action": {"const": "set_curve"}},
    "not": {"anyOf": [{"required": [field]} for field in (
        "clipPath", "curves", "bindingPath", "objectPath", "componentType", "propertyName",
        "sourceBindingPath", "sourceComponentType", "sourcePropertyName", "deleteSource",
        "overwriteExisting", "keys", "constantFloat",
    )]},
})

EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "avatarPath", "action"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "avatarPath": _AVATAR_PATH_PROPERTY,
        "action": {"type": "string", "enum": ["update", "delete", "rename", "reorder"]},
        "parameterName": {"type": "string"},
        "newName": {"type": "string"},
        "orderNames": {"type": "array", "items": {"type": "string"}},
        "valueType": {"type": "string"},
        "defaultValue": {"type": "number"},
        "saved": {"type": "boolean"},
        "networkSynced": {"type": "boolean"},
    },
}

EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "avatarPath", "action"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "avatarPath": _AVATAR_PATH_PROPERTY,
        "action": {"type": "string", "enum": ["create", "update", "delete", "reorder"]},
        "assetDir": {"type": "string", "pattern": "^Assets/"},
        "menuPath": {"type": "string"},
        "controlName": {"type": "string"},
        "controlIndex": {"type": "integer", "minimum": 0},
        "newName": {"type": "string"},
        "controlType": {"type": "string"},
        "controlFloat": {"type": "number"},
        "value": {"type": "number"},
        "parameterName": {"type": "string"},
        "iconAssetPath": {"type": "string", "pattern": "^Assets/"},
        "subMenuAssetPath": {"type": "string", "pattern": "^Assets/"},
        "createSubMenu": {"type": "boolean"},
        "subParameters": {"type": "array", "items": {"type": "string"}},
        "orderNames": {"type": "array", "items": {"type": "string"}},
    },
}

MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath"],
    "properties": {
        "animatorRootPath": {
            **ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["properties"]["animatorRootPath"],
            "description": "For batch edits that attach motionClipPath only: actual scene Animator root hierarchy path used to validate the clip's bindings. Binding paths are relative to this root. Mutually exclusive with assetOnly=true; not a batch scene/avatar selector.",
        },
        "assetOnly": {
            **ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["properties"]["assetOnly"],
            "description": "For batch edits that attach motionClipPath only: true explicitly skips scene binding validation for reusable asset-only authoring. Does not verify a scene effect and cannot be combined with animatorRootPath.",
        },
        "projectPath": {"type": "string", "description": "Exact Unity project root."},
        "avatarPath": {"type": "string", "description": "Single-edit mode: exact loaded Avatar hierarchy path used to resolve its FX controller; omit only when the current Avatar is unambiguous. Not accepted with edits batch mode."},
        "controllerPath": {"type": "string", "pattern": "^Assets/", "description": "Single edit: exact existing AnimatorController to use instead of the Avatar's FX controller. Batch: required exact existing controller; batch mode never creates controllers or layers."},
        "fxControllerPath": {"type": "string", "pattern": "^Assets/", "description": "Compatibility alias for the single-edit controllerPath; use controllerPath for new calls. Batch mode requires controllerPath."},
        "action": {
            "type": "string",
            "enum": ["ensure_layer", "delete_layer", "ensure_state", "update_state", "delete_state", "ensure_transition", "delete_transition", "delete_parameter"],
            "description": "Single-edit mode: ensure_layer creates the named layer; ensure_state and update_state both use the current upsert path and may create a missing layer/state. Batch update_state instead requires an existing state, and every batch layer must already exist. The first state added to an empty layer becomes its default. Batch preview exposes this at plan.layers[].machine.defaultState. delete_parameter refuses parameters still referenced in the controller.",
        },
        "assetDir": {"type": "string", "pattern": "^Assets/"},
        "layerName": {"type": "string"},
        "stateName": {"type": "string", "description": "Exact state name. Creation in an empty layer makes the first added state its default; existing layer defaults are preserved by ensure_state."},
        "destinationStateName": {"type": "string"},
        "newName": {"type": "string"},
        "writeDefaults": {"type": "boolean"},
        "motionClipPath": {"type": "string", "pattern": "^Assets/"},
        "speed": {"type": "number"},
        "expectedSpeed": {"type": "number", "description": "Expected current speed for a single state edit; a mismatch is refused before mutation."},
        "expectedStateDigest": {"type": "string", "pattern": "^[0-9a-f]{64}$", "description": "Copy from the authoritative FX preview to reject controller changes before writing."},
        "timeParameterActive": {"type": "boolean", "description": "Optional: use a Float parameter as normalized motion time."},
        "timeParameter": {"type": "string", "minLength": 1, "description": "Optional existing Float Animator parameter used as normalized motion time."},
        "hasExitTime": {"type": "boolean"},
        "exitTime": {"type": "number"},
        "duration": {"type": "number", "minimum": 0},
        "canTransitionToSelf": {"type": "boolean"},
        "interruptionSource": {"type": "string", "enum": ["None", "Source", "Destination", "SourceThenDestination", "DestinationThenSource"]},
        "orderedInterruption": {"type": "boolean"},
        "transitionIndex": {"type": "integer", "minimum": 0},
        "conditions": {
            "type": "array",
            "maxItems": 64,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["parameterName", "mode", "threshold"],
                "properties": {
                    "parameterName": {"type": "string"},
                    "mode": {"type": "string"},
                    "threshold": {"type": "number"},
                },
            },
        },
        "parameterName": {"type": "string"},
        "conditionMode": {"type": "string"},
        "threshold": {"type": "number"},
    },
    "oneOf": [
        {
            "type": "object",
            "required": ["action", "parameterName"],
            "properties": {"action": {"type": "string", "const": "delete_parameter"}},
        },
        {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["ensure_layer", "delete_layer", "ensure_state", "update_state", "delete_state", "ensure_transition", "delete_transition"],
                }
            },
        },
    ],
}


MATERIAL_VARIANT_FLATTEN_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["projectPath", "assetPath"],
    "properties": {
        "projectPath": {"type": "string", "description": "Exact Unity project root."},
        "assetPath": {"type": "string", "pattern": "^Assets/.+\\.mat$", "description": "Exact persistent Material asset to flatten."},
        "expectedGuid": {"type": "string"}, "expectedDependencyHash": {"type": "string"},
        "expectedFileDigest": {"type": "string"}, "preview": {"type": "boolean", "default": True},
    },
}

# Batch mode extends the existing FX surface without changing single-action fields.
_FX_BATCH_ITEM_FIELDS = (
    "layerName", "stateName", "motionClipPath", "writeDefaults", "speed", "timeParameterActive", "timeParameter", "destinationStateName",
    "transitionIndex", "hasExitTime", "exitTime", "duration", "canTransitionToSelf", "interruptionSource", "orderedInterruption", "conditions",
)
_FX_BATCH_ITEM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["action", "layerName"],
    "properties": {
        **{name: MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA["properties"][name] for name in _FX_BATCH_ITEM_FIELDS},
        "sourceStateName": {"type": "string"},
        "action": {"type": "string", "enum": ["ensure_state", "update_state", "ensure_transition", "delete_transition", "delete_layer"]},
    },
    "oneOf": [
        {"properties": {"action": {"enum": ["ensure_state", "update_state"]}}, "required": ["stateName"]},
        {"properties": {"action": {"const": "ensure_transition"}}, "required": ["destinationStateName"]},
        {"properties": {"action": {"const": "delete_transition"}}, "required": ["destinationStateName", "transitionIndex"]},
        {"properties": {"action": {"const": "delete_layer"}},
         "not": {"anyOf": [{"required": [name]} for name in (*_FX_BATCH_ITEM_FIELDS, "sourceStateName") if name != "layerName"]}},
    ],
}
for _fx_single_branch in MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA["oneOf"]:
    _fx_single_branch["not"] = {"required": ["edits"]}
MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA["oneOf"].append({
    "required": ["controllerPath", "edits"],
    "not": {"anyOf": [{"required": [name]} for name in (
        *[field for field in MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA["properties"] if field not in {"projectPath", "controllerPath", "animatorRootPath", "assetOnly"}],
        "sourceStateName",
    )]},
})
MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA["properties"]["edits"] = {
    "type": "array", "minItems": 1, "maxItems": 128, "items": _FX_BATCH_ITEM_SCHEMA,
    "description": "Ordered atomic batch on one exact existing controllerPath; avatarPath and single-action fields are not accepted. Every layerName must select one existing unambiguous layer; batch mode never creates layers. ensure_state can add a missing state only inside an existing layer and then requires an existing motionClipPath. Batch mode never creates controllers or motion clips. For any motionClipPath, supply animatorRootPath for scene binding validation or assetOnly=true for reusable asset-only authoring. At most 128 edits and 512 KiB. Supports state/transition edits and delete_layer by exact unique layerName; delete synced dependents before their target. Layer deletion preserves subassets and clips.",
}
MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA["allOf"] = [{
    "if": {"required": ["edits"], "properties": {"edits": {"contains": {"required": ["motionClipPath"]}}}},
    "then": ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA["allOf"][1],
}]


# One existing assignment contract shared by its preview and approved write.
MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "oneOf": [
            {"required": ["materialAssetPath", "keywordChanges"], "not": {"anyOf": [{"required": [key]} for key in ("assignments", "shaderName", "shaderAssetPath", "rendererPath", "rendererComponentId", "slotIndex")]}},
            {"required": ["assignments"], "not": {"anyOf": [{"required": [key]} for key in ("materialAssetPath", "shaderName", "shaderAssetPath", "rendererPath", "rendererComponentId", "slotIndex", "keywordChanges")]}},
            {"required": ["rendererPath", "slotIndex", "shaderName"], "not": {"anyOf": [{"required": ["materialAssetPath"]}, {"required": ["assignments"]}, {"required": ["keywordChanges"]}]}},
            {"required": ["materialAssetPath", "shaderName"], "not": {"anyOf": [{"required": ["assignments"]}, {"required": ["rendererPath"]}, {"required": ["rendererComponentId"]}, {"required": ["slotIndex"]}, {"required": ["keywordChanges"]}]}},
        ],
        "properties": {
            "keywordChanges": {"type": "array", "minItems": 1, "maxItems": 128, "description": "Keyword-only mode for one independent, saved material. No shaderName required; cannot combine with shader assignment or renderer selectors. Names must exist in the current shader keyword space.", "items": {"type": "object", "additionalProperties": False, "required": ["keyword", "enabled"], "properties": {"keyword": {"type": "string", "minLength": 1, "maxLength": 256}, "enabled": {"type": "boolean"}}}},
            "assignments": {"type": "array", "minItems": 1, "maxItems": 128, "description": "Pure existing material asset shader assignments, one checkpoint; maximum 512 KiB including sealed evidence. No renderer selectors.", "items": {
                "type": "object", "additionalProperties": False, "required": ["materialAssetPath", "shaderName"], "properties": {
                    "materialAssetPath": {"type": "string", "pattern": "^Assets/.+\\.mat$"}, "shaderName": {"type": "string", "minLength": 1}, "shaderAssetPath": {"type": "string"}}}},
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this material write."},
            "rendererPath": {"type": "string", "description": "Exact loaded-scene renderer hierarchy path used to resolve the material slot."},
            "rendererComponentId": {"type": "string", "description": "Optional exact renderer identity returned by the preview."},
            "materialAssetPath": {"type": "string", "description": "Exact persistent Assets/... .mat target; use instead of rendererPath/slotIndex/rendererComponentId."},
            "slotIndex": {"type": "integer", "minimum": 0, "description": "Zero-based material slot index on the selected renderer."},
            "shaderName": {"type": "string", "description": "Exact installed Unity shader name to assign."},
            "shaderAssetPath": {"type": "string", "description": "Optional exact Assets/... or Packages/... shader asset path used to bind the preview."},
        },
    }

# Scalar edits share the existing preview/approval surface and cannot mix modes.
for _material_branch in MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA["oneOf"]:
    _material_branch["not"]["anyOf"].extend({"required": [key]} for key in ("propertyChanges", "renderQueue"))
MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA["oneOf"].append({
    "required": ["materialAssetPath", "propertyChanges"],
    "not": {"anyOf": [{"required": [key]} for key in ("assignments", "keywordChanges", "shaderName", "shaderAssetPath", "rendererPath", "rendererComponentId", "slotIndex")]},
})
MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA["properties"].update({
    "propertyChanges": {"type": "array", "minItems": 1, "maxItems": 32,
        "description": "When-to-use: edit exact declared scalar properties on one saved independent material. Float/Range require finite float32 values within declared Range; Int requires integral int32. When-NOT-to-use: texture/color/vector edits, unknown properties, or mixed shader/keyword/renderer modes. No rendering mode is inferred.",
        "items": {"type": "object", "additionalProperties": False, "required": ["propertyName", "value"], "properties": {
            "propertyName": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,127}$"},
            "value": {"type": "number", "minimum": -3.4028234663852886e38, "maximum": 3.4028234663852886e38}}}},
    "renderQueue": {"type": "integer", "minimum": -1, "maximum": 5000,
        "description": "Optional explicit material render queue with propertyChanges only; -1 uses the shader default. Omit to preserve both raw and effective queue."},
})
