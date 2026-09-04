"""Canonical read and preview schemas used by every Agent tool projection."""
from __future__ import annotations

from typing import Any

from external_mcp_tool_blocks import EXTERNAL_MCP_TOOL_BLOCKS
from path_to_skill_controller import PATH_TO_SKILL_PREVIEW_INPUT_SCHEMA
from unity_shared_input_schemas import (
    ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA,
    AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA,
    EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA,
    EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA,
    MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA,
    MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA,
    RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA,
    SCENE_ASSET_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA,
    _AVATAR_PATH_PROPERTY,
    _PROJECT_PATH_PROPERTY,
)


UNITY_READ_TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "vrcforge_build_test_readiness": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "avatarPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "avatarPath": _AVATAR_PATH_PROPERTY,
            "includeQuest": {"type": "boolean", "default": True},
            "maxErrors": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
        },
    },
    "vrcforge_capture_status": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "requirePlayMode": {"type": "boolean", "default": False},
            "captureMode": {"type": "string", "enum": ["auto", "scene_view", "game_view"], "default": "auto"},
        },
    },
    "vrcforge_get_gameobject": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string", "description": "Exact hierarchy path or unique scene GameObject name."},
        },
    },
    "vrcforge_list_execution_targets": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "scope": {"type": "string", "enum": ["project", "scene", "avatar", "object", "component"], "default": "avatar"},
            "avatarGlobalObjectId": {"type": "string"},
            "objectGlobalObjectId": {"type": "string"},
            "componentGlobalObjectId": {"type": "string"},
            "componentType": {"type": "string"},
        },
    },
    "vrcforge_bind_execution_target": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "scope"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "scope": {"type": "string", "enum": ["project", "scene", "avatar", "object", "component"]},
            "avatarGlobalObjectId": {"type": "string"},
            "objectGlobalObjectId": {"type": "string"},
            "componentGlobalObjectId": {"type": "string"},
            "componentType": {"type": "string"},
        },
    },
    "vrcforge_refresh_execution_target": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "executionTargetHandle", "executionTarget"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "executionTargetHandle": {"type": "string", "minLength": 16},
            "executionTarget": {"type": "object", "additionalProperties": True},
        },
    },
    "vrcforge_get_property": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "componentType", "propertyPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "componentType": {"type": "string"},
            "propertyPath": {"type": "string"},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
            "maxItems": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 50},
        },
    },
    "vrcforge_inspect_skinned_mesh_deformation": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "componentIndex"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
        },
    },
    "vrcforge_preview_material_texture_assignment": MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_renderer_material_slot": RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_scene_object_duplicate": SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_scene_asset_duplicate": SCENE_ASSET_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_write_avatar_descriptor": AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_write_animation_curve": ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_manage_expression_parameters": EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_manage_expression_menu": EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_list_avatars": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Optional exact Unity project root; omit only when the active project is authoritative.",
            },
        },
    },
    "vrcforge_read_avatar_descriptor": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Optional exact Unity project root; omit only when the active project is authoritative.",
            },
            "avatarPath": {
                "type": "string",
                "description": "Optional exact loaded-scene Avatar Descriptor hierarchy path.",
            },
        },
    },
    "vrcforge_scan_blendshapes": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Optional exact Unity project root; omit only when the active project is authoritative.",
            },
            "avatarPath": {
                "type": "string",
                "description": "Optional exact loaded-scene avatar hierarchy path.",
            },
        },
    },
    "vrcforge_scan_parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Optional exact Unity project root; omit only when the active project is authoritative.",
            },
            "avatarPath": {
                "type": "string",
                "description": "Optional exact loaded-scene avatar hierarchy path.",
            },
        },
    },
    "vrcforge_external_tool_blocks": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "block": {
                "type": "string",
                "enum": sorted(EXTERNAL_MCP_TOOL_BLOCKS),
                "description": "Optional exact leaf block to expand with compact tool names and read/write modes.",
            },
        },
    },
    "vrcforge_list_installed_skills": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "includeDisabled": {
                "type": "boolean",
                "default": False,
                "description": "Include valid disabled Skills so their package can be explicitly re-enabled.",
            },
        },
    },
    "vrcforge_read_installed_skill": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "pattern": "^[a-z][a-z0-9_.-]{1,80}$",
                "description": "Exact enabled Skill name returned by vrcforge_list_installed_skills.",
            },
            "file": {
                "type": "string",
                "description": "Optional exact support-file path declared by this Skill; omit to read its instructions.",
            },
        },
    },
    "vrcforge_preview_path_to_skill": PATH_TO_SKILL_PREVIEW_INPUT_SCHEMA,
    "vrcforge_preflight_skill_package": {
        "type": "object",
        "additionalProperties": False,
        "required": ["packagePath"],
        "properties": {
            "packagePath": {
                "type": "string",
                "description": "Exact local path to the existing .vsk package to inspect without importing it.",
            },
        },
    },
    "vrcforge_get_build_test_status": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "jobId"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact Unity project root that owns the existing Build & Test job."},
            "jobId": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$", "description": "Exact jobId returned by vrcforge_build_test_avatar."},
        },
    },
    "vrcforge_gesture_manager_status": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Optional exact avatar hierarchy path when more than one Gesture Manager is connected."},
            "includeParameters": {"type": "boolean", "default": False, "description": "Return every runtime parameter. Prefer parameterNames or parameterPrefix for a focused read."},
            "parameterNames": {"type": "array", "items": {"type": "string"}, "maxItems": 128, "description": "Exact runtime parameter names to return while retaining total counts."},
            "parameterPrefix": {"type": "string", "maxLength": 256, "description": "Return runtime parameters whose names start with this exact prefix."},
        },
    },
    "vrcforge_read_vrchat_sdk_builder_alerts": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "avatarPath"],
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Exact Unity project root whose already-open SDK Builder cache will be inspected.",
            },
            "avatarPath": {
                "type": "string",
                "description": "Exact loaded-scene Avatar Descriptor hierarchy path that must match the SDK Builder selection.",
            },
        },
    },
    "vrcforge_preview_texture_import_settings": TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA,
    "vrcforge_preview_manage_fx_animator": MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA,
    "vrcforge_avatar_upload_readiness": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "avatarPath", "uploadMode", "buildType", "platforms", "metadata", "thumbnail"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root."},
            "avatarPath": {"type": "string", "description": "Exact loaded-scene Avatar Descriptor hierarchy path."},
            "uploadMode": {"type": "string", "enum": ["create", "update"]},
            "buildType": {"type": "string", "const": "build_and_upload"},
            "platforms": {"type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "string", "enum": ["StandaloneWindows64", "Android", "iOS"]}},
            "metadata": {"$ref": "#/$defs/avatarUploadMetadata"},
            "thumbnail": {"$ref": "#/$defs/avatarUploadThumbnail"},
        },
        "$defs": {
            "avatarStyle": {
                "type": ["object", "null"],
                "additionalProperties": False,
                "required": ["id", "name"],
                "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
            },
            "avatarUploadMetadata": {
                "type": "object",
                "additionalProperties": False,
                "required": ["mode"],
                "properties": {
                    "mode": {"type": "string", "enum": ["preserve_remote", "replace"]},
                    "name": {"type": "string", "maxLength": 64},
                    "description": {"type": "string", "maxLength": 256},
                    "visibility": {"type": "string", "enum": ["private", "public"]},
                    "primaryStyle": {"$ref": "#/$defs/avatarStyle"},
                    "secondaryStyle": {"$ref": "#/$defs/avatarStyle"},
                    "contentWarnings": {"type": "array", "maxItems": 5, "uniqueItems": True, "items": {"type": "string", "enum": ["content_sex", "content_adult", "content_violence", "content_gore", "content_horror"]}},
                    "authorTags": {"type": "array", "maxItems": 10, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 64}},
                },
            },
            "avatarUploadThumbnail": {
                "type": "object",
                "additionalProperties": False,
                "required": ["mode"],
                "properties": {"mode": {"type": "string", "enum": ["keep", "replace"]}, "path": {"type": "string"}, "sha256": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"}},
            },
        },
    },
    "vrcforge_get_avatar_upload_status": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "jobId"],
        "properties": {
            "projectPath": {"type": "string"},
            "jobId": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"},
        },
    },
    "vrcforge_preview_unity_constraint_conversion": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "scenePath", "avatarPath", "gameObjectPath", "componentType", "componentIndex"],
        "properties": {
            "projectPath": {"type": "string"},
            "scenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$"},
            "avatarPath": {"type": "string"},
            "gameObjectPath": {"type": "string"},
            "componentType": {"type": "string", "enum": ["UnityEngine.Animations.PositionConstraint", "UnityEngine.Animations.RotationConstraint", "UnityEngine.Animations.ScaleConstraint", "UnityEngine.Animations.ParentConstraint", "UnityEngine.Animations.AimConstraint", "UnityEngine.Animations.LookAtConstraint"]},
            "componentIndex": {"type": "integer", "minimum": 0, "maximum": 31},
        },
    },
    "vrcforge_scan_fx_animator": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Scene hierarchy path of the avatar root."},
            "controllerPath": {"type": "string", "description": "Project-relative AnimatorController asset path; overrides the avatar FX controller."},
        },
    },
    "vrcforge_scan_animation_bindings": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Scene hierarchy path of the avatar root."},
            "controllerPath": {"type": "string", "description": "Project-relative AnimatorController asset path."},
            "clipPaths": {"type": "array", "items": {"type": "string"}, "description": "Optional exact project-relative AnimationClip asset paths."},
            "includeAllProjectClips": {"type": "boolean", "description": "Include unrelated project clips; normally leave false."},
            "includeBindingDetails": {"type": "boolean", "description": "Return full per-binding arrays for a narrow clip selection."},
            "maxClips": {"type": "integer", "minimum": 1, "description": "Maximum clips to scan."},
        },
    },
    "vrcforge_scan_avatar_controls": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Scene hierarchy path of the avatar root."},
        },
    },
    "vrcforge_inspect_skinned_mesh_bone_usage": {
        "type": "object",
        "additionalProperties": False,
        "required": ["gameObjectPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "gameObjectPath": {"type": "string", "description": "Exact scene hierarchy path of the SkinnedMeshRenderer GameObject."},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
            "minimumWeight": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.000001},
        },
    },
    "vrcforge_inspect_modular_avatar_component": {
        "type": "object",
        "additionalProperties": False,
        "required": ["gameObjectPath", "componentType"],
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Optional scene hierarchy path of the containing avatar root."},
            "gameObjectPath": {"type": "string", "description": "Exact scene hierarchy path of the component carrier."},
            "componentType": {
                "type": "string",
                "enum": ["MergeArmature", "BoneProxy", "MenuInstaller", "MergeAnimator", "Parameters"],
                "description": "Supported Modular Avatar component family to inspect.",
            },
        },
    },
    "vrcforge_scan_inbound_reference_closure": {
        "type": "object",
        "additionalProperties": False,
        "required": ["avatarPath"],
        "anyOf": [
            {"required": ["targetPaths"]},
            {"required": ["targetComponentSelectors"]},
        ],
        "properties": {
            "projectPath": {"type": "string", "description": "Unity project root; omit for the active project."},
            "avatarPath": {"type": "string", "description": "Exact scene hierarchy path of the avatar root."},
            "targetPaths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exact GameObject roots being considered for deletion.",
            },
            "targetComponentSelectors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["objectPath", "componentType"],
                    "properties": {
                        "objectPath": {"type": "string"},
                        "componentType": {"type": "string"},
                        "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
                    },
                },
                "description": "Exact removable components; a GameObject path alone is not treated as a component reference.",
            },
            "includeProjectAssets": {"type": "boolean", "default": True},
            "includeAnimationBindings": {"type": "boolean", "default": True},
            "includeIndirectParameterEdges": {"type": "boolean", "default": True},
            "maxResults": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 1000},
        },
    },
}
