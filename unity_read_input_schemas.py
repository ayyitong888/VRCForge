"""Canonical read and preview schemas used by every Agent tool projection."""
from __future__ import annotations

from typing import Any
from component_property_batch import COMPONENT_PROPERTY_QUERIES_SCHEMA, COMPONENT_PROPERTY_TARGET_SCHEMA

from external_mcp_tool_blocks import EXTERNAL_MCP_TOOL_BLOCKS
from path_to_skill_controller import PATH_TO_SKILL_PREVIEW_INPUT_SCHEMA
from unity_shared_input_schemas import (
    ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA,
    AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA,
    EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA,
    EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA,
    MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA,
    MATERIAL_VARIANT_FLATTEN_PUBLIC_INPUT_SCHEMA,
    MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA,
    MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA,
    RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA,
    SCENE_ASSET_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    SHADER_CATEGORY_OVERRIDES_SCHEMA,
    SHADER_CHANGE_INPUT_SCHEMA,
    TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA,
    _AVATAR_PATH_PROPERTY,
    _PROJECT_PATH_PROPERTY,
    outfit_import_input_schema,
)
from checkpoint_recovery_input_schemas import (
    INTERRUPTED_APPLY_LIST_INPUT_SCHEMA,
    INTERRUPTED_APPLY_PREVIEW_INPUT_SCHEMA,
    INTERRUPTED_APPLY_RESOLVE_INPUT_SCHEMA,
)
from package_input_schemas import PACKAGE_INSTALL_INPUT_SCHEMA


_BLENDSHAPE_PREVIEW_ITEM: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "allOf": [
        {"anyOf": [{"required": ["rendererPath"]}, {"required": ["renderer_path"]}]},
        {"anyOf": [{"required": ["blendshapeName"]}, {"required": ["blendshape_name"]}]},
        {"anyOf": [{"required": ["targetWeight"]}, {"required": ["target_weight"]}]},
    ],
    "properties": {
        "rendererPath": {"type": "string", "minLength": 1},
        "renderer_path": {"type": "string", "minLength": 1},
        "blendshapeName": {"type": "string", "minLength": 1},
        "blendshape_name": {"type": "string", "minLength": 1},
        "targetWeight": {"type": "number", "minimum": 0, "maximum": 100},
        "target_weight": {"type": "number", "minimum": 0, "maximum": 100},
    },
}


UNITY_READ_TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    **{name: {
        "type": "object", "additionalProperties": False,
        "properties": {
            "avatarPath": {"type": "string", "description": "Optional saved avatar path/name filter; not a project identity lock."},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        },
    } for name in (
        "vrcforge_list_shader_tuning_history", "vrcforge_list_shader_tuning_presets",
        "vrcforge_list_tuning_history", "vrcforge_list_tuning_presets",
    )},
    "vrcforge_inspect_project_chat_store": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {"projectPath": {"type": "string", "minLength": 1}},
        "description": "when-to-use: inspect one selected project's chat-store integrity before a repair request. when-NOT-to-use: do not read chat content, inspect app-global data, or perform repair. Negative example: do not guess a project path.",
    },
    "vrcforge_read_recent_logs": {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "allOf": [
            {
                "if": {"properties": {"source": {"const": "memory"}}},
                "then": {"not": {"anyOf": [
                    {"required": ["file"]},
                    {"required": ["offset"]},
                ]}},
            },
            {
                "if": {"properties": {"offset": {}}, "required": ["offset"]},
                "then": {"required": ["file"]},
            },
        ],
        "properties": {
            "source": {"type": "string", "enum": ["memory", "disk"], "default": "memory", "description": "When to use: select disk only to inspect retained historical logs. When NOT to use: do not use disk for live memory state or provide a host path."},
            "file": {"type": "string", "maxLength": 255, "description": "Optional for disk (omit to list retained filenames); otherwise one retained log filename. Never a host path."},
            "offset": {"type": "integer", "minimum": 0, "description": "Disk only, parsed-entry 0-based page offset; omit for the existing tail page."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 80},
        },
    },
    "vrcforge_package_install_plan": PACKAGE_INSTALL_INPUT_SCHEMA,
    "vrcforge_plan_outfit_import": outfit_import_input_schema(require_project_path=False),
    "vrcforge_get_unitypackage_import_status": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "jobId"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "jobId": {
                "type": "string",
                "pattern": "^[0-9a-fA-F]{32}$",
                "description": "Exact existing UnityPackage import job id; read-only status polling only.",
            },
        },
    },
    "vrcforge_inspect_outfit_package": {
        "type": "object", "additionalProperties": False, "required": ["packagePath"],
        "properties": {
            "packagePath": {"type": "string", "minLength": 1, "description": "Exact existing UnityPackage or loose outfit folder path; loose folders may include prefab, material, texture, model, and Editor assembly definition (.asmdef) files."},
            "maxEntries": {"type": "integer", "minimum": 1, "maximum": 50000, "default": 5000},
        },
    },
    "vrcforge_preview_material_variant_flatten": MATERIAL_VARIANT_FLATTEN_PUBLIC_INPUT_SCHEMA,
    "vrcforge_list_interrupted_apply_recoveries": INTERRUPTED_APPLY_LIST_INPUT_SCHEMA,
    "vrcforge_preview_interrupted_apply_recovery": INTERRUPTED_APPLY_PREVIEW_INPUT_SCHEMA,
    "vrcforge_preview_restore_backup": {
        "type": "object",
        "additionalProperties": False,
        "description": (
            "when-to-use: preview which files an existing project-owned safe backup would overwrite. "
            "when-NOT-to-use: do not restore, write, or use a backup preview for general project inspection."
        ),
        "required": ["projectPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "backupPath": {"type": "string", "minLength": 1, "description": "Exact backup path returned by create_safe_backup."},
            "backupId": {"type": "string", "minLength": 1, "description": "Exact backup id returned by create_safe_backup."},
            "assetPaths": {"type": "array", "maxItems": 256, "items": {"type": "string", "minLength": 1}},
            "allowProjectMismatch": {"type": "boolean", "default": False},
            "allowOverwriteChanged": {
                "const": False,
                "description": "Preview-only safety control; must remain false. Set true only on the separately approved restore write.",
            },
        },
        "anyOf": [{"required": ["backupPath"]}, {"required": ["backupId"]}],
    },
    "vrcforge_resolve_interrupted_apply_recovery": INTERRUPTED_APPLY_RESOLVE_INPUT_SCHEMA,
    "vrcforge_preview_material_shader_assignment": MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA,
    "vrcforge_scan_materials": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "avatarPath": {"type": "string", "description": "Exact loaded-scene avatar hierarchy path; omit only when avatar selection is unambiguous."},
            "outputPath": {"type": "string", "description": "Optional asset-relative or absolute JSON output path."},
            "refreshAssets": {"type": "boolean", "default": False},
            "materialIds": {"type": "array", "items": {"type": "string"}, "maxItems": 2000, "description": "Exact IDs discovered through indexOnly pages; omit to scan all materials."},
            "includeTextures": {"type": "boolean", "default": True},
            "categoryOverrides": SHADER_CATEGORY_OVERRIDES_SCHEMA,
            "offset": {"type": "integer", "minimum": 0, "default": 0, "description": "Zero-based result offset. Follow paging.nextOffset with the same avatar and filters; results reflect each fresh scan."},
            "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 100, "description": "Optional page size. Omit with offset 0 and indexOnly false to preserve the full result. Explicit paging defaults to 25 rows."},
            "indexOnly": {"type": "boolean", "default": False, "description": "First discover exact material IDs, names, renderer paths and shaders in bounded index pages; then request details using materialIds. Follow paging.nextOffset until null. Byte limits may return fewer than limit rows."},
            "index_only": {"type": "boolean", "description": "Alias for indexOnly."},
        },
    },
    "vrcforge_scan_wardrobe": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "avatarPath": {"type": "string", "description": "Exact loaded-scene avatar hierarchy path; omit only when descriptor selection is unambiguous."},
            "outputPath": {"type": "string", "description": "Optional asset-relative or absolute JSON output path."},
        },
    },
    "vrcforge_find_assets": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "query": {"type": "string", "description": "Unity AssetDatabase search filter."},
            "typeName": {"type": "string", "description": "Optional Unity asset type name, applied as t:typeName."},
            "folder": {"type": "string", "description": "Optional existing project folder restricting the search."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 50},
        },
    },
    "vrcforge_get_asset_info": {
        "type": "object", "additionalProperties": False,
        "anyOf": [{"required": ["assetPath"]}, {"required": ["guid"]}],
        "allOf": [
            {"if": {"required": ["includePreview"], "properties": {"includePreview": {"const": True}}}, "then": {"required": ["projectPath"]}},
            {"if": {"required": ["includeText"], "properties": {"includeText": {"const": True}}}, "then": {"required": ["projectPath"]}},
            {"if": {"required": ["textSearch"]}, "then": {"required": ["projectPath"]}},
            {"if": {"required": ["importedTextSearch"]}, "then": {"required": ["projectPath", "localFileId"]}},
            {"if": {"required": ["localFileId"]}, "then": {"required": ["importedTextSearch"]}},
            {"if": {"required": ["includeMetaText"], "properties": {"includeMetaText": {"const": True}}}, "then": {"required": ["projectPath"]}},
        ],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "assetPath": {"type": "string", "description": "Exact project-relative Unity asset path."},
            "guid": {"type": "string", "description": "Exact Unity asset GUID; used when assetPath is omitted."},
            "includePreview": {"type": "boolean", "default": False, "description": "When-to-use: visually inspect an existing PNG/BMP/JPEG Texture2D source after Core identity lookup. Returns a bounded image Resource URI; resources/read retrieves it. When-NOT-to-use: GPU/imported texels, generated images, unsupported formats or texture edits. Requires explicit projectPath; no importer changes."},
            "previewMaxSize": {"type": "integer", "minimum": 16, "maximum": 1024, "default": 256, "description": "Maximum thumbnail edge, retaining aspect ratio; source <=32MiB/16M pixels and preview <=1MiB."},
            "includeText": {"type": "boolean", "default": False, "description": "When-to-use: read an exact UTF-8 text asset after Core identity lookup. Returns a bounded preview plus an immutable complete-text Resource exposed as bounded UTF-8 chunks with continuation metadata for resources/read. When-NOT-to-use: binary assets, GPU/imported shader state, or writes. Requires explicit projectPath and exact assetPath/GUID."},
            "includeMetaText": {"type": "boolean", "default": False, "description": "When-to-use: read only the exact same asset's adjacent .meta UTF-8 source after Core identity and GUID verification, preserving importer metadata for review or transport. Returns raw/text hashes and an immutable Resource without reading the main asset unless includeText is also true. When-NOT-to-use: arbitrary sidecar paths, importer mutation, or GPU/runtime state. Requires explicit projectPath and exact assetPath/GUID."},
            "textMaxBytes": {"type": "integer", "minimum": 1, "maximum": 65536, "default": 65536, "description": "Maximum returned UTF-8 preview bytes."},
            "textStartLine": {"type": "integer", "minimum": 1, "default": 1, "description": "One-based first line to return."},
            "textEndLine": {"type": "integer", "minimum": 1, "description": "Optional one-based inclusive last line."},
            "textSearch": {
                "type": "object", "additionalProperties": False, "required": ["literal"],
                "description": "Optional exact UTF-8 literal search over the complete text source; returns bounded matches and preserves the immutable full-text Resource.",
                "properties": {
                    "literal": {"type": "string", "minLength": 1, "maxLength": 512},
                    "contextBeforeBytes": {"type": "integer", "minimum": 0, "maximum": 4096, "default": 96},
                    "contextAfterBytes": {"type": "integer", "minimum": 0, "maximum": 4096, "default": 160},
                    "maxMatches": {"type": "integer", "minimum": 1, "maximum": 128, "default": 32},
                    "matchStartByte": {"type": "integer", "minimum": 0},
                },
            },
            "localFileId": {"type": "string", "pattern": "^-?[0-9]+$", "description": "When-to-use: identify one persistent imported TextAsset child by exact signed decimal Unity local file ID for importedTextSearch. When-NOT-to-use: main assets, arbitrary files, or runtime/GPU evidence."},
            "importedTextSearch": {
                "type": "object", "additionalProperties": False, "required": ["literal"],
                "description": "When-to-use: bounded read-only literal search over the exact imported TextAsset child selected by localFileId. When-NOT-to-use: disk source, arbitrary files, binary assets, or GPU/runtime evidence. Hash is imported TextAsset.text UTF-8.",
                "properties": {
                    "literal": {"type": "string", "minLength": 1, "maxLength": 512},
                    "maxMatches": {"type": "integer", "minimum": 1, "maximum": 32, "default": 8},
                    "contextCharacters": {"type": "integer", "minimum": 0, "maximum": 2048, "default": 128},
                },
            },
            "includeObjects": {"type": "boolean", "default": False, "description": "Include a read-only page of loaded asset objects and exact copy-layout entries/digest; no import, refresh, or save."},
            "objectOffset": {"type": "integer", "minimum": 0, "maximum": 4096, "default": 0},
            "objectLimit": {"type": "integer", "minimum": 1, "maximum": 128, "default": 64},
            "includeRendererMaterials": {"type": "boolean", "default": False, "description": "When-to-use: inspect a prefab asset's descendant renderer mesh and shared-material slot identities, including inactive objects. When-NOT-to-use: scene objects or non-prefab assets. Read-only, without instantiation, import, or save."},
            "rendererOffset": {"type": "integer", "minimum": 0, "maximum": 4096, "default": 0},
            "rendererLimit": {"type": "integer", "minimum": 1, "maximum": 128, "default": 64, "description": "Renderer page size; follow rendererMaterialListing.nextOffset while truncated. Exact slots are never silently truncated; an oversized response fails."},
        },
    },
    "vrcforge_scan_avatar_items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "avatarPath": {"type": "string", "description": "Optional exact avatar root hierarchy path; omit to scan all scene avatar roots."},
            "outputPath": {"type": "string", "description": "Optional asset-relative or absolute JSON output path; empty skips artifact writing."},
            "maxItems": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 2000},
            "refreshAssets": {"type": "boolean", "default": False},
        },
    },
    "vrcforge_preview_shader_apply": {
        "type": "object", "additionalProperties": False,
        "required": ["projectPath", "changes"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY, "avatarPath": {"type": "string"}, "instruction": {"type": "string", "minLength": 1},
            "inventory": {"type": "object", "additionalProperties": True}, "categoryOverrides": SHADER_CATEGORY_OVERRIDES_SCHEMA,
            "lockedMaterials": {"type": "array", "items": {"type": "string"}}, "lockedProperties": {"type": "array", "items": {"type": "string"}},
            "changes": {"type": "array", "minItems": 1, "maxItems": 2000, "items": SHADER_CHANGE_INPUT_SCHEMA}, "historyId": {"type": "string"},
        },
    },
    "vrcforge_plan_shader_tuning": {
        "type": "object", "additionalProperties": False,
        "required": ["projectPath", "instruction"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY, "avatarPath": {"type": "string"}, "instruction": {"type": "string", "minLength": 1},
            "inventory": {"type": "object", "additionalProperties": True}, "categoryOverrides": SHADER_CATEGORY_OVERRIDES_SCHEMA,
            "lockedMaterials": {"type": "array", "items": {"type": "string"}}, "lockedProperties": {"type": "array", "items": {"type": "string"}},
        },
    },
    "vrcforge_preview_project_asset_duplicate": {
        "type": "object", "additionalProperties": False,
        "required": ["projectPath"],
        "oneOf": [
            {"required": ["sourceAssetPath", "destinationAssetPath"], "not": {"required": ["copies"]}},
            {"required": ["copies"], "not": {"anyOf": [{"required": ["sourceAssetPath"]}, {"required": ["destinationAssetPath"]}]}},
        ],
        "properties": {
            "copies": {"type": "array", "minItems": 1, "maxItems": 128, "description": "Create-new copies with existing parents only; 512 KiB including sealed evidence. All-or-compensate, unknown changes preserved.",
                "items": {"type": "object", "additionalProperties": False, "required": ["sourceAssetPath", "destinationAssetPath"], "properties": {
                    "sourceAssetPath": {"type": "string", "pattern": "^Assets/.+"}, "destinationAssetPath": {"type": "string", "pattern": "^Assets/VRCForgeGenerated/.+"}}}},
            "projectPath": _PROJECT_PATH_PROPERTY, "sourceAssetPath": {"type": "string", "pattern": "^Assets/.+"},
            "destinationAssetPath": {"type": "string", "pattern": "^Assets/VRCForgeGenerated/.+", "description": "Exact create-new asset path. Missing classified parent folders are listed in preview and created within the approved copy."}, "preview": {"type": "boolean", "const": True}, "overwrite": {"type": "boolean", "const": False},
        },
    },
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
            "offset": {"type": "integer", "minimum": 0, "default": 0, "description": "Zero-based page offset for Avatar-scoped component discovery."},
            "maxItems": {"type": "integer", "minimum": 1, "maximum": 128, "default": 50, "description": "Maximum exact component targets returned in one page."},
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
        "required": ["projectPath"],
        "oneOf": [
            {"required": ["gameObjectPath", "componentType", "propertyPath"], "not": {"required": ["queries"]}},
            {"required": ["queries", "executionTarget"], "not": {"anyOf": [
                {"required": [field]} for field in ("gameObjectPath", "componentType", "propertyPath", "componentIndex", "maxItems")
            ]}},
        ],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "queries": COMPONENT_PROPERTY_QUERIES_SCHEMA,
            "executionTarget": {**COMPONENT_PROPERTY_TARGET_SCHEMA, "description": "Exact component identity. For queries this is a same-namespace anchor only; every query still requires its own bound component identity."},
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
            "scope": {
                "type": "string",
                "enum": ["face", "all"],
                "default": "face",
                "description": "Use all to inspect body, clothing and accessory shapes; omission preserves the face-editor filter.",
            },
            "rendererPaths": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "pattern": "\\S"},
                "description": "Optional exact exported rendererPath hierarchy paths inside the selected avatar. Unknown paths fail; this selection does not override scope.",
            },
        },
    },
    "vrcforge_preview_blendshape_apply": {
        "type": "object",
        "additionalProperties": True,
        "description": "when-to-use: validate exact BlendShape targets and weights before an approved apply. when-NOT-to-use: this preview does not write. Its applyPayload is a Core payload; public vrcforge_apply_blendshapes uses avatar and snake_case adjustment fields.",
        "required": ["adjustments"],
        "anyOf": [{"required": ["avatarPath"]}, {"required": ["avatar_path"]}, {"required": ["avatar"]}],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "avatarPath": {"type": "string", "minLength": 1},
            "avatar_path": {"type": "string", "minLength": 1},
            "avatar": {"type": "string", "minLength": 1},
            "adjustments": {"type": "array", "minItems": 1, "items": _BLENDSHAPE_PREVIEW_ITEM,
                            "description": "Exact renderer hierarchy paths and BlendShape names obtained from a scan; weights range from 0 to 100."},
            "source_mode": {"type": "string", "enum": ["unity_live_export", "configured_export", "custom_export", "mvp_sample"], "default": "unity_live_export"},
            "mock_execute": {"type": "boolean", "default": False},
            "save_artifacts": {"type": "boolean", "default": True, "description": "Controls workflow artifacts; preview never applies Unity changes."},
            "scope": {"type": ["string", "null"], "description": "Use all for clothing or body shapes; face limits face-oriented workflows."},
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
            "executionTarget": {"type": "object", "additionalProperties": True, "description": "Optional exact envelope returned by vrcforge_bind_execution_target; when omitted, the scan remains historical and its Resource cannot prove current project identity."},
            "avatarPath": {"type": "string", "description": "Scene hierarchy path of the avatar root. Used for discovery only when clipPaths is empty."},
            "controllerPath": {"type": "string", "description": "Project-relative AnimatorController asset path. Used for discovery only when clipPaths is empty."},
            "clipPaths": {"type": "array", "items": {"type": "string"}, "maxItems": 2000, "description": "Exact project-relative AnimationClip asset paths. When non-empty, these are the only clips scanned; avatar/controller/project discovery is ignored."},
            "includeAllProjectClips": {"type": "boolean", "default": False, "description": "Include all project clips only when clipPaths and controllerPath are empty; normally leave false."},
            "includeBindingDetails": {"type": "boolean", "default": True, "description": "Return bounded keyframe and object-reference arrays. Counts and truncation flags remain available when false."},
            "bindingView": {"type": "string", "enum": ["summary", "index", "details"], "description": "For large controllers start with summary (clip identities/counts, no curves), then index (binding identities/counts), then exact selectors with details. Omitting all selection fields preserves legacy output. Selected reads are paged and do not write Unity files."},
            "bindingSelectors": {"type": "array", "maxItems": 32, "items": {"type": "object", "additionalProperties": False, "anyOf": [{"required": ["path"]}, {"required": ["propertyName"]}], "properties": {"path": {"type": "string", "maxLength": 1024}, "propertyName": {"type": "string", "minLength": 1, "maxLength": 1024}, "componentType": {"type": "string", "minLength": 1, "maxLength": 1024}}}, "description": "Exact case-sensitive path/propertyName/componentType match. AND within a row, OR across rows. Empty path means animator root; omitted fields unrestricted. No wildcards. A no_matches result explicitly means zero matched bindings."},
            "bindingOffset": {"type": "integer", "minimum": 0, "maximum": 2147483647, "description": "Global sorted binding offset within current clip page. Follow paging.nextRequest."},
            "bindingLimit": {"type": "integer", "minimum": 1, "maximum": 256, "description": "Selected binding page size, default 16. Small exact pages reduce agent tokens."},
            "clipOffset": {"type": "integer", "minimum": 0, "maximum": 2147483647, "description": "Sorted discovered clip offset. Follow paging.nextRequest."},
            "keyOffset": {"type": "integer", "minimum": 0, "maximum": 2147483647, "description": "Key continuation offset; use each incomplete binding's nextKeyRequest."},
            "maxTotalKeys": {"type": "integer", "minimum": 1, "maximum": 4096, "description": "Total returned key budget across one selected read, default 4096. Omitted keys and continuation are explicit."},
            "expectedSnapshotDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$", "description": "Snapshot digest from prior selected read. Continuations reject changed clip dependencies."},
            "maxClips": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 300, "description": "Maximum clips to scan."},
            "maxKeysPerBinding": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 256, "description": "Maximum float or object-reference keys returned per binding; excess keys set an explicit truncation flag."},
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

UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_get_runtime_observation"] = {
    "type": "object", "properties": {"stateDetail": {"type": "string", "enum": ["resource", "inline"], "default": "resource", "description": "Resource returns state counts and bounded resources/read page URIs; inline preserves full states arrays."}, "stateSelection": {"type": "object", "additionalProperties": False, "required": ["layerName"], "properties": {"layerName": {"type": "string", "minLength": 1, "maxLength": 128}}, "description": "Optional Gateway-only inline projection; raw observation validation still covers every state row."}, "projectPath": {"type": "string"}, "avatarPath": {"type": "string", "minLength": 1}, "jobId": {"type": "string", "pattern": "^[0-9a-f]{32}$"}},
    "required": ["projectPath", "avatarPath", "jobId"],
}


# Diagnostic reads preserve existing optional arguments and snake_case aliases.
# Unknown fields remain accepted, as in the previous canonical projection.
_DIAGNOSTIC_TARGET_PROPERTIES = {
    "projectPath": {**_PROJECT_PATH_PROPERTY, "type": ["string", "null"], "description": "Unity project root; omit for the selected project. Alias: project_path."},
    "avatarPath": {**_AVATAR_PATH_PROPERTY, "type": ["string", "null"], "description": "Avatar hierarchy path; omit for the handler's current selection/default. Alias: avatar_path."},
}
_OPTIMIZATION_CONTEXT_PROPERTIES = {
    **_DIAGNOSTIC_TARGET_PROPERTIES,
    "includeQuest": {"type": "boolean", "default": True, "description": "Include Quest performance evidence. Alias: include_quest."},
    "maxErrors": {"type": "integer", "default": 50, "description": "Compile-error detail budget; the validation reader clamps to 1..200. Alias: max_errors."},
}
_OPTIMIZATION_PROFILE_PROPERTIES = {
    "targetProfile": {"type": "string", "default": "pc_conservative", "description": "pc_conservative, pc_medium, quest_medium, event_light, or custom. Other nonempty labels select custom. Aliases: target_profile, target."},
    "customProfile": {"type": "object", "additionalProperties": True, "description": "Custom profile options. Alias: custom_profile.", "properties": {
        "label": {"type": "string", "description": "Display label; output is shortened to 80 characters."},
        "weights": {"type": "object", "additionalProperties": True, "properties": {
            key: {"type": "number", "description": "Relative weight, clamped by the handler to 0..1."}
            for key in ("visualFidelity", "vram", "materials", "animator", "triangles")
        }},
    }},
}
_VALIDATION_SNAPSHOT_PROPERTIES = {
    name: {"type": "object", "additionalProperties": True, "description": description}
    for name, description in {
        "beforeValidation": "Before vrcforge.validation.v1 report. Aliases: before_validation, before.",
        "afterValidation": "After vrcforge.validation.v1 report. Aliases: after_validation, after.",
        "rollbackValidation": "Post-restore vrcforge.validation.v1 report. Aliases: rollback_validation, rollback. Supplied evidence is not independent restore verification.",
    }.items()
}
for _diagnostic_tool_name in ('vrcforge_optimization_aao_hidden_body_cut_plan',
 'vrcforge_optimization_aao_trace_plan',
 'vrcforge_optimization_baseline_scan',
 'vrcforge_optimization_lac_profile_plan',
 'vrcforge_optimization_ma2bt_convertibility_plan',
 'vrcforge_optimization_ma2bt_skipped_reasons',
 'vrcforge_optimization_ma_responsive_layer_audit',
 'vrcforge_optimization_material_slot_audit',
 'vrcforge_optimization_mesh_triangle_audit',
 'vrcforge_optimization_meshia_simplify_plan',
 'vrcforge_optimization_parameter_animator_usage',
 'vrcforge_optimization_parameter_behavior_regression',
 'vrcforge_optimization_parameter_budget_audit',
 'vrcforge_optimization_parameter_compressibility_plan',
 'vrcforge_optimization_parameter_inventory',
 'vrcforge_optimization_parameter_menu_map',
 'vrcforge_optimization_parameter_path_to_skill',
 'vrcforge_optimization_parameter_vrcfury_compressor_plan',
 'vrcforge_optimization_performance_tools_report',
 'vrcforge_optimization_physbone_audit',
 'vrcforge_optimization_physbone_reduce_plan',
 'vrcforge_optimization_plan',
 'vrcforge_optimization_profile_diff',
 'vrcforge_optimization_rollback_verify',
 'vrcforge_optimization_shader_adapter_registry',
 'vrcforge_optimization_texture_vram_audit',
 'vrcforge_optimization_ttt_atlas_plan',
 'vrcforge_optimization_upload_gate_audit',
 'vrcforge_optimization_upload_gate_fix_plan',
 'vrcforge_optimization_visual_regression_plan',
 'vrcforge_optimization_vrcfury_compatibility_report'):
    UNITY_READ_TOOL_INPUT_SCHEMAS[_diagnostic_tool_name] = {
        "type": "object", "additionalProperties": True, "required": [],
        "properties": {**_OPTIMIZATION_CONTEXT_PROPERTIES},
    }
for _diagnostic_tool_name in ("vrcforge_optimization_plan", "vrcforge_optimization_lac_profile_plan"):
    UNITY_READ_TOOL_INPUT_SCHEMAS[_diagnostic_tool_name]["properties"].update(_OPTIMIZATION_PROFILE_PROPERTIES)
UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_optimization_profile_diff"]["properties"].update(_VALIDATION_SNAPSHOT_PROPERTIES)
UNITY_READ_TOOL_INPUT_SCHEMAS.update({
    "vrcforge_optimization_target_profile": {
        "type": "object", "additionalProperties": True, "required": [],
        "properties": _OPTIMIZATION_PROFILE_PROPERTIES,
    },
    "vrcforge_optimization_dependency_doctor": {
        "type": "object", "additionalProperties": True, "required": [],
        "properties": {"projectPath": _DIAGNOSTIC_TARGET_PROPERTIES["projectPath"]},
    },
    "vrcforge_optimization_validation_delta": {
        "type": "object", "additionalProperties": True, "required": [],
        "properties": {
            **_VALIDATION_SNAPSHOT_PROPERTIES,
            "optimizerTool": {"type": "string", "description": "Optimizer identifier recorded with the comparison. Alias: optimizer_tool."},
            "approvalId": {"type": "string", "description": "Related approval identifier. Alias: approval_id."},
            "checkpointId": {"type": "string", "description": "Related checkpoint identifier. Alias: checkpoint_id."},
        },
    },
    "vrcforge_run_validation_report": {
        "type": "object", "additionalProperties": True, "required": [],
        "properties": {
            **_OPTIMIZATION_CONTEXT_PROPERTIES,
            "includeSources": {"type": "boolean", "default": False, "description": "Include source payloads. Alias: include_sources."},
            "includeReadiness": {"type": "boolean", "default": True, "description": "Include dependency/environment readiness evidence. Alias: include_readiness."},
            "gateBuild": {"type": "boolean", "default": True, "description": "Evaluate build-gate findings; does not start a build. Alias: gate_build."},
        },
    },
    "vrcforge_inspect_primitive_basis_fixture": {
        "type": "object", "additionalProperties": True, "required": [],
        "anyOf": [{"required": ["expectedRunIdDigest"]}, {"required": ["expected_run_id_digest"]}],
        "properties": {
            "projectPath": _DIAGNOSTIC_TARGET_PROPERTIES["projectPath"],
            "expectedRunIdDigest": {"type": "string", "pattern": "^[0-9a-f]{64}$", "description": "Required expected fixture run identity, lowercase SHA-256."},
            "expected_run_id_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$", "description": "Existing alias of expectedRunIdDigest."},
        },
    },
})
_AVATAR_ENCRYPTION_SCAN_PROPERTIES = {
    **_DIAGNOSTIC_TARGET_PROPERTIES,
    "inventory": {"type": ["object", "null"], "additionalProperties": True, "description": "Optional previously captured shader-material inventory; omit to scan the selected avatar."},
    "includeCompatibility": {"type": "boolean", "default": True, "description": "Include compatibility evidence. Alias: include_compatibility."},
}
_AVATAR_ENCRYPTION_PLAN_PROPERTIES = {
    **_AVATAR_ENCRYPTION_SCAN_PROPERTIES,
    "targetShaderFamilies": {"type": "array", "items": {"type": "string"}, "default": ["liltoon", "poiyomi"], "description": "Shader families to consider. Alias: target_shader_families."},
    "materialIds": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Selected material identities. Alias: material_ids."},
    "rendererPaths": {"type": "array", "items": {"type": "string"}, "default": [], "description": "Selected renderer paths. Alias: renderer_paths."},
    "targets": {"type": "array", "items": {"type": "object", "additionalProperties": True}, "default": [], "description": "Explicit targets from the compatibility scan."},
    "profile": {"type": "string", "default": "standard", "description": "Protection profile name."},
    "protectionProfile": {"type": ["string", "null"], "description": "Optional profile override. Alias: protection_profile."},
    "platform": {"type": "string", "default": "pc", "description": "Target platform."},
    "targetPlatform": {"type": ["string", "null"], "description": "Optional platform override. Alias: target_platform."},
    "confirmCreatorOwnedAssets": {"type": "boolean", "default": False, "description": "Record that the selected assets are creator-owned; this does not authorize writes. Alias: confirm_creator_owned_assets."},
}
UNITY_READ_TOOL_INPUT_SCHEMAS.update({
    "vrcforge_avatar_encryption_research_report": {
        "type": "object", "additionalProperties": True, "required": [],
        "properties": {"includeExternalReferences": {"type": "boolean", "default": True, "description": "Include reference links in the report. Alias: include_external_references."}},
    },
    "vrcforge_avatar_encryption_scan": {
        "type": "object", "additionalProperties": True, "required": [],
        "properties": _AVATAR_ENCRYPTION_SCAN_PROPERTIES,
    },
    "vrcforge_avatar_encryption_plan": {
        "type": "object", "additionalProperties": True, "required": [],
        "properties": _AVATAR_ENCRYPTION_PLAN_PROPERTIES,
    },
    "vrcforge_avatar_encryption_preview": {
        "type": "object", "additionalProperties": True, "required": [],
        "properties": {**_AVATAR_ENCRYPTION_PLAN_PROPERTIES, "plan": {"type": ["object", "null"], "additionalProperties": True, "description": "Optional existing protection plan to preview; no write occurs."}},
    },
})
_PARAMETER_BIT_PACKING_IDENTITY_PROPERTIES = {
    "projectPath": _PROJECT_PATH_PROPERTY,
    "sourceScenePath": {"type": "string", "description": "Exact source .unity scene asset path."},
    "sourceAvatarPath": {"type": "string", "description": "Exact source avatar hierarchy path in sourceScenePath."},
    "outputCloneName": {"type": "string", "description": "Proposed clone name; preview creates no clone."},
}
UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_parameter_bit_packing"] = {
    "type": "object", "additionalProperties": True, "required": [],
    "properties": {
        **_PARAMETER_BIT_PACKING_IDENTITY_PROPERTIES,
        **{key: {"type": "object", "additionalProperties": True, "properties": _PARAMETER_BIT_PACKING_IDENTITY_PROPERTIES, "description": "Existing alternate wrapper for source identity fields; projectPath may remain at top level."} for key in ("arguments", "params")},
    },
    "anyOf": [
        {"required": list(_PARAMETER_BIT_PACKING_IDENTITY_PROPERTIES)},
        *[{
            "required": [key],
            "properties": {key: {"required": ["sourceScenePath", "sourceAvatarPath", "outputCloneName"]}},
            "anyOf": [{"required": ["projectPath"]}, {"properties": {key: {"required": ["projectPath"]}}}],
        } for key in ("arguments", "params")],
    ],
}

# Keep checkpoint preview separate from the confirmed restore write schema.
UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_restore_checkpoint"] = {
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "checkpointId": {"type": "string", "description": "Retained checkpoint id returned by list_checkpoints. The checkpoint determines the project and restore scope."},
        "checkpoint_id": {"type": "string", "description": "Existing alias of checkpointId."},
        "projectPath": {"type": "string", "description": "Optional expected project root. If supplied, it must match the selected checkpoint; it never redirects a restore."},
        "project_path": {"type": "string", "description": "Alias of projectPath; all supplied project aliases must agree with the checkpoint."},
        "projectRoot": {"type": "string", "description": "Alias of projectPath; all supplied project aliases must agree with the checkpoint."},
        "project_root": {"type": "string", "description": "Alias of projectPath; all supplied project aliases must agree with the checkpoint."},
    },
    "anyOf": [{"required": ["checkpointId"]}, {"required": ["checkpoint_id"]}],
}


FACE_TUNING_PUBLIC_INPUT_SCHEMA = {
    "type": "object", "additionalProperties": True, "required": [],
    "properties": {
        "projectPath": {**_PROJECT_PATH_PROPERTY, "type": ["string", "null"], "description": "Optional project root. Alias: project_path."},
        "avatar": {"type": ["string", "null"], "description": "Target avatar path/name. Aliases avatar_path and avatarPath must agree when supplied together."},
        "instruction": {"type": ["string", "null"], "description": "Requested face adjustment; an existing plan_json may supply a plan instead."},
        "source_mode": {"type": "string", "default": "unity_live_export", "description": "Existing input modes: unity_live_export, configured_export, custom_export, mvp_sample. This does not change the execution approval policy."},
        "mock_execute": {"type": ["boolean", "number", "string"], "default": False, "description": "Mock planning only; an approved run rejects mock execution. Preserves existing Pydantic boolean conversion including 0/1 and boolean strings."},
        "min_confidence": {"type": ["number", "string", "boolean", "null"], "description": "Optional planning confidence threshold; existing float conversion accepts numeric strings and booleans."},
        "allow_low_confidence": {"type": ["boolean", "number", "string"], "default": False, "description": "Preserves existing Pydantic boolean conversion including 0/1 and boolean strings."},
        "save_artifacts": {"type": ["boolean", "number", "string"], "default": True, "description": "Preserves existing Pydantic boolean conversion including 0/1 and boolean strings."},
        "export_json": {"type": ["string", "null"], "description": "Existing export JSON for the selected input mode."},
        "plan_json": {"type": ["string", "null"], "description": "Optional existing plan JSON."},
        "reference_image_path": {"type": ["string", "null"], "description": "Optional reference image path or artifact URL."},
        "reference_image_data_url": {"type": ["string", "null"], "description": "Optional uploaded reference image data URL."},
        **{name: {"type": "array", "items": {"type": "string"}, "default": [], "description": "User-supplied source/target reference images for planning."} for name in ("source_reference_image_paths", "source_reference_image_data_urls", "target_reference_image_paths", "target_reference_image_data_urls")},
        "scope": {"type": ["string", "null"], "description": "Optional Blendshape scan scope."},
        "filterScope": {"type": ["string", "null"], "description": "Optional scan filter; alias filter_scope."},
    },
}
for _face_alias, _face_name in {"project_path": "projectPath", "avatar_path": "avatar", "avatarPath": "avatar", "filter_scope": "filterScope"}.items():
    FACE_TUNING_PUBLIC_INPUT_SCHEMA["properties"][_face_alias] = {
        **FACE_TUNING_PUBLIC_INPUT_SCHEMA["properties"][_face_name], "description": "Existing alias of " + _face_name + ".",
    }
UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_plan_face_tuning"] = FACE_TUNING_PUBLIC_INPUT_SCHEMA

# Project/environment reads describe existing coercion and aliases without
# exposing connection credentials or changing handler validation.
_PROJECT_CONTEXT_PROPERTIES = {
    'projectPath': {'type': ['string', 'null'], 'description': 'Optional absolute Unity project root; defaults to the selected/configured project.'},
    'project_path': {'type': ['string', 'null'], 'description': 'Existing alias of projectPath.'},
}
PROJECT_CREATE_PUBLIC_INPUT_SCHEMA = {
    'type': 'object', 'additionalProperties': True,
    'properties': {
        'projectPath': {'type': 'string', 'description': 'Exact absolute new project directory. Must be absent and its parent must exist.'},
        'projectRoot': {'type': 'string', 'description': 'Existing alias of projectPath.'},
        'projectName': {'type': ['string', 'null'], 'description': 'Defaults to the final project directory name; if provided must match it case-insensitively.'},
        'template': {'type': ['string', 'null'], 'default': 'Avatar', 'description': 'Installed local Unity template name, used when templatePath is absent.'},
        'templatePath': {'type': ['string', 'null'], 'description': 'Explicit absolute existing Unity template directory; overrides template.'},
        'template_path': {'type': ['string', 'null'], 'description': 'Existing alias of templatePath.'},
    },
    'anyOf': [{'required': ['projectPath']}, {'required': ['projectRoot']}],
}
UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_project_create_plan'] = PROJECT_CREATE_PUBLIC_INPUT_SCHEMA
for _environment_tool in ('package_manager_status', 'unity_status', 'unity_tools'):
    UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_' + _environment_tool] = {
        'type': 'object', 'additionalProperties': True, 'properties': dict(_PROJECT_CONTEXT_PROPERTIES),
    }
UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_diagnose_package_install_errors'] = {
    'type': 'object', 'additionalProperties': True,
    'properties': {
        **_PROJECT_CONTEXT_PROPERTIES,
        'packageId': {'description': 'Optional package id, converted to text and lowercase; defaults to empty (all packages).'},
        'package_id': {'description': 'Existing alias of packageId.'},
        'maxCompileErrors': {'type': ['number', 'string', 'boolean', 'null'], 'default': 30, 'description': 'Compile-error limit; existing int(value or 30) conversion accepts numeric strings and truncates numbers. Zero/null use 30.'},
        'max_compile_errors': {'type': ['number', 'string', 'boolean', 'null'], 'description': 'Existing alias of maxCompileErrors.'},
        **{key: {'description': 'Optional diagnostic text; existing values are converted to text, combined, sanitized and capped at 5000 characters.'} for key in ('stdoutSummary', 'stdout_summary', 'stderrSummary', 'stderr_summary', 'logText', 'log_text')},
    },
}
UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_list_checkpoints'] = {
    'type': 'object', 'additionalProperties': True,
    'properties': {
        'projectRoot': {'type': ['string', 'null'], 'description': 'Optional project filter applied before the limit; omission lists checkpoints across projects.'},
        'project_root': {'type': ['string', 'null'], 'description': 'Existing alias of projectRoot; takes precedence when both are supplied.'},
        'limit': {'type': ['number', 'string', 'boolean', 'null'], 'default': 50, 'description': 'Existing int(value or 50) conversion, clamped to 1..500. Zero/null use 50; numeric strings remain accepted.'},
    },
}
UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_export_interrupted_apply_incident_bundle'] = {
    'type': 'object', 'additionalProperties': True,
    'properties': {
        **{key: {'type': ['string', 'null'], 'description': 'Optional recovery id. Existing selector precedence: recovery_id, recoveryId, id, checkpoint_id, checkpointId, latest recovery record.'} for key in ('recoveryId', 'recovery_id', 'id')},
        **{key: {'type': ['string', 'null'], 'description': 'Optional checkpoint id used when no recovery id is supplied.'} for key in ('checkpointId', 'checkpoint_id')},
    },
}
for _addon_tool in ('scan_modular_avatar', 'scan_vrcfury'):
    UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_' + _addon_tool] = {
        'type': 'object', 'additionalProperties': True,
        'properties': {
            **_PROJECT_CONTEXT_PROPERTIES,
            **{key: {'type': ['string', 'null'], 'description': 'Optional avatar hierarchy path. Existing precedence: source_avatar_path, sourceAvatarPath, avatar_path, avatarPath; omission uses the scanner default.'} for key in ('sourceAvatarPath', 'source_avatar_path', 'avatarPath', 'avatar_path')},
            'skipUnity': {'default': False, 'description': 'Only literal boolean true skips Unity inspection; other existing values do not. Package inspection still runs.'},
            'skip_unity': {'description': 'Existing alias; either skipUnity or skip_unity being literal true skips Unity inspection.'},
        },
    }
UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_core_upgrade_status'] = {
    'type': 'object', 'additionalProperties': True, 'required': ['projectPath'],
    'properties': {
        'projectPath': {'type': 'string', 'description': 'Exact Unity project root to inspect.'},
        'installedAt': {'type': ['string', 'null'], 'default': '', 'description': 'Optional installation timestamp included in the status comparison.'},
    },
}
UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_project_catalog_registration_status'] = {
    'type': 'object', 'additionalProperties': True,
    'properties': {key: {'type': ['string', 'null'], 'description': 'Optional existing Unity project root; omit to list catalog registration status. projectRoot is the existing alias of projectPath.'} for key in ('projectPath', 'projectRoot')},
}
UNITY_READ_TOOL_INPUT_SCHEMAS['vrcforge_scan_project_index'] = {
    'type': 'object', 'additionalProperties': True,
    'properties': {
        **_PROJECT_CONTEXT_PROPERTIES,
        'maxFiles': {'type': ['number', 'string', 'boolean', 'null'], 'default': 100000, 'description': 'Maximum files scanned, using int(value or 100000). Zero/null use 100000; numeric strings remain accepted. Partial scans retain unscanned index entries.'},
        'max_files': {'type': ['number', 'string', 'boolean', 'null'], 'description': 'Existing alias of maxFiles.'},
    },
    'anyOf': [{'required': ['projectPath']}, {'required': ['project_path']}],
}

# General source evidence is also exposed through the external MCP project
# leaf.  The Gateway makes projectPath mandatory for that boundary and mints
# the private authorization root; internal runtime calls retain their injected
# server-owned root.
for _general_read_tool, _general_read_properties in {
    'vrcforge_list_directory': {
        'path': {'type': 'string', 'description': 'Project-relative directory path; external MCP callers must use a relative path under projectPath.'},
        'projectPath': {'type': 'string', 'description': 'Absolute existing source workspace root; required for external MCP calls.'},
        'maxDepth': {'type': 'integer'}, 'maxCount': {'type': 'integer'},
    },
    'vrcforge_read_text_file': {
        'path': {'type': 'string', 'description': 'Project-relative UTF-8 text file path under projectPath.'},
        'projectPath': {'type': 'string', 'description': 'Absolute existing source workspace root; required for external MCP calls.'},
        'maxBytes': {'type': 'integer'}, 'maxOutputChars': {'type': 'integer'},
    },
    'vrcforge_find_files': {
        'path': {'type': 'string', 'description': 'Project-relative directory path under projectPath.'},
        'projectPath': {'type': 'string', 'description': 'Absolute existing source workspace root; required for external MCP calls.'},
        'pattern': {'type': 'string'}, 'maxDepth': {'type': 'integer'}, 'maxCount': {'type': 'integer'},
    },
    'vrcforge_search_text': {
        'path': {'type': 'string', 'description': 'Project-relative directory path under projectPath.'},
        'projectPath': {'type': 'string', 'description': 'Absolute existing source workspace root; required for external MCP calls.'},
        'query': {'type': 'string'}, 'pattern': {'type': 'string'}, 'maxDepth': {'type': 'integer'},
        'maxCount': {'type': 'integer'}, 'maxFileBytes': {'type': 'integer'}, 'caseSensitive': {'type': 'boolean'},
    },
}.items():
    UNITY_READ_TOOL_INPUT_SCHEMAS[_general_read_tool] = {
        'type': 'object', 'additionalProperties': False,
        'required': ['path', 'query'] if _general_read_tool == 'vrcforge_search_text' else ['path'],
        'properties': _general_read_properties,
    }
