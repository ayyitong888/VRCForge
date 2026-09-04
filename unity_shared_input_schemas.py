"""Shared Unity preview/apply input contracts; no execution or registry ownership."""
from __future__ import annotations

from typing import Any


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
    "required": ["projectPath", "materialAssetPath", "propertyName", "textureAssetPath"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "materialAssetPath": {
            "type": "string",
            "pattern": "^Assets/.*\\.mat$",
            "description": "Exact existing persistent Assets/... .mat material.",
        },
        "propertyName": {
            "type": "string",
            "enum": ["_MainTex", "_Main2ndTex", "_Main3rdTex", "_ShadowColorTex"],
        },
        "textureAssetPath": {
            "type": "string",
            "pattern": "^Assets/.+",
            "description": "Exact existing project Texture2D asset to assign.",
        },
    },
}

RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "rendererPath", "slotIndex", "newMaterialAssetPath"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "rendererPath": {
            "type": "string",
            "minLength": 1,
            "description": "Exact loaded-scene renderer hierarchy path used only to locate the stable component identity.",
        },
        "rendererComponentId": {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
            "description": "Optional for preview; required by the approved apply and copied from authoritative preview evidence.",
        },
        "slotIndex": {"type": "integer", "minimum": 0, "maximum": 1024},
        "newMaterialAssetPath": {
            "type": "string",
            "pattern": "^Assets/.*\\.mat$",
            "description": "One existing persistent material asset to assign to this exact slot.",
        },
    },
}

SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "sourceScenePath", "sourceObjectPath"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "sourceScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$"},
        "sourceObjectPath": {"type": "string"},
        "targetParentScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$"},
        "targetParentPath": {"type": "string"},
        "targetName": {"type": "string"},
        "preserveWorldTransform": {"type": "boolean", "default": False},
        "overwrite": {"type": "boolean", "const": False},
    },
}

SCENE_ASSET_DUPLICATE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "sourceScenePath", "destinationScenePath"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "sourceScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$"},
        "destinationScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$"},
        "openAsOnlyActiveScene": {"type": "boolean", "default": False},
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
        "viewPosition": {"type": "object", "additionalProperties": False, "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}}},
        "lipSync": {"type": "string"},
        "visemeSkinnedMeshPath": {"type": "string"},
        "visemeBlendShapes": {"type": "array", "items": {"type": "string"}},
        "expressionParametersPath": {"type": "string", "pattern": "^Assets/"},
        "expressionsMenuPath": {"type": "string", "pattern": "^Assets/"},
        "baseAnimationLayers": {"type": "array", "items": {"type": "object"}},
        "specialAnimationLayers": {"type": "array", "items": {"type": "object"}},
        "eyeLookSettingsSourceAvatarPath": {"type": "string"},
        "eyeLookEnabled": {"type": "boolean"},
    },
}

ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["projectPath", "clipPath", "propertyName"],
    "properties": {
        "projectPath": _PROJECT_PATH_PROPERTY,
        "action": {"type": "string", "enum": ["set_curve", "delete_curve", "retarget_curve"], "default": "set_curve"},
        "clipPath": {"type": "string", "pattern": "^Assets/"},
        "bindingPath": {"type": "string"},
        "objectPath": {"type": "string"},
        "componentType": {"type": "string", "default": "GameObject"},
        "propertyName": {"type": "string"},
        "sourceBindingPath": {"type": "string"},
        "sourceComponentType": {"type": "string"},
        "sourcePropertyName": {"type": "string"},
        "deleteSource": {"type": "boolean", "default": True},
        "overwriteExisting": {"type": "boolean", "default": False},
        "keys": {"type": "array", "items": {"type": "object"}},
        "constantFloat": {"type": "number"},
    },
}

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
    "required": ["projectPath", "action"],
    "properties": {
        "projectPath": {"type": "string", "description": "Exact Unity project root."},
        "avatarPath": {"type": "string", "description": "Avatar hierarchy path used to resolve its FX controller."},
        "controllerPath": {"type": "string", "pattern": "^Assets/", "description": "Exact AnimatorController asset path; overrides avatar FX resolution."},
        "fxControllerPath": {"type": "string", "pattern": "^Assets/", "description": "Compatibility alias for controllerPath."},
        "action": {
            "type": "string",
            "enum": ["ensure_layer", "delete_layer", "ensure_state", "update_state", "delete_state", "ensure_transition", "delete_transition", "delete_parameter"],
            "description": "One exact FX mutation. delete_parameter refuses parameters still referenced anywhere in the controller.",
        },
        "assetDir": {"type": "string", "pattern": "^Assets/"},
        "layerName": {"type": "string"},
        "stateName": {"type": "string"},
        "destinationStateName": {"type": "string"},
        "newName": {"type": "string"},
        "writeDefaults": {"type": "boolean"},
        "motionClipPath": {"type": "string", "pattern": "^Assets/"},
        "speed": {"type": "number"},
        "hasExitTime": {"type": "boolean"},
        "exitTime": {"type": "number"},
        "duration": {"type": "number", "minimum": 0},
        "canTransitionToSelf": {"type": "boolean"},
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
