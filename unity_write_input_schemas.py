"""Canonical supervised-write input schemas; execution remains in registered handlers."""
from __future__ import annotations

from typing import Any

from path_to_skill_controller import PATH_TO_SKILL_WRITE_INPUT_SCHEMA
from checkpoint_recovery_input_schemas import INTERRUPTED_APPLY_RESOLVE_INPUT_SCHEMA
from unity_read_input_schemas import UNITY_READ_TOOL_INPUT_SCHEMAS
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
    outfit_import_input_schema,
    _PROJECT_PATH_PROPERTY,
    _AVATAR_PATH_PROPERTY,
)
from package_input_schemas import PACKAGE_INSTALL_INPUT_SCHEMA

# Keep the FX write and preview projections aligned while retaining the shared
# schema owner used by the public read-tool alias.
MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA["properties"]["sourceStateName"] = {
    "type": "string",
    "description": "Optional exact source state for a directed transition; omit to target from Any State.",
}


EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    # Preserve existing handler aliases while publishing canonical arguments.
    # Required argument checks remain in the handlers for both naming styles.
    "vrcforge_add_component": {
        "type": "object", "additionalProperties": True,
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string", "description": "Exact scene GameObject hierarchy path."},
            "componentType": {"type": "string", "description": "Exact Unity component type to add."},
            "preview": {"type": "boolean", "default": False},
        },
    },
    "vrcforge_rename_gameobject": {
        "type": "object", "additionalProperties": True,
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string", "description": "Exact current scene GameObject hierarchy path."},
            "newName": {"type": "string", "description": "New object name, not a hierarchy path."},
            "preview": {"type": "boolean", "default": False},
        },
    },
    "vrcforge_toggle_scene_object": {
        "type": "object", "additionalProperties": True,
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "objectPath": {"type": "string", "description": "Exact scene object hierarchy path."},
            "active": {"type": "boolean", "description": "Desired active state; send a JSON boolean."},
        },
    },
    "vrcforge_unpack_prefab": {
        "type": "object", "additionalProperties": True,
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string", "description": "Exact prefab instance hierarchy path."},
            "mode": {"type": "string", "default": "outermost", "description": "Prefab unpack mode accepted by the installed Core."},
            "preview": {"type": "boolean", "default": False},
        },
    },
    "vrcforge_repair_project_chat_store": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "expectedDigest", "storeId"],
        "properties": {
            "projectPath": {"type": "string", "minLength": 1, "description": "Exact project root containing .vrcforge/chat-transcripts.json."},
            "projectRoot": {"type": "string", "minLength": 1, "description": "Optional duplicate project root; when supplied it must identify the same project as projectPath."},
            "expectedDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$", "description": "SHA-256 digest of the observed corrupt transcript store."},
            "storeId": {"type": "string", "pattern": "^session\\.chat\\.project\\.[0-9a-f]{16}$", "description": "Digest-bound project chat store identifier returned by the diagnostic result."},
        },
        "description": "when-to-use: after read-only chat-store diagnosis identifies one corrupt project transcript store. when-NOT-to-use: do not use for healthy stores, app-global chat, or guessed digests/store IDs; the external Agent must obtain explicit user approval before mutation.",
    },
    "vrcforge_apply_blendshapes": {
        "type": "object",
        "additionalProperties": True,
        "description": "when-to-use: apply approved BlendShape weights after inspecting exact targets. when-NOT-to-use: do not use for read-only inspection or pass the preview's Core applyPayload directly. Live editing requires source_mode=unity_live_export and mock_execute=false.",
        "required": ["adjustments"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "avatar": {"type": ["string", "null"], "description": "Avatar path/name accepted by the manual editor. Use the exact scanned avatar path for live edits."},
            "adjustments": {
                "type": "array", "minItems": 1,
                "description": "Public manual-editor inputs use snake_case fields; previous_weight is optional metadata, while undo captures the actual current weight.",
                "items": {
                    "type": "object", "additionalProperties": True,
                    "required": ["renderer_path", "blendshape_name", "target_weight"],
                    "properties": {
                        "renderer_path": {"type": "string", "minLength": 1, "description": "Exact renderer hierarchy path from scan_blendshapes."},
                        "blendshape_name": {"type": "string", "minLength": 1, "description": "Exact case-sensitive BlendShape name from the renderer scan."},
                        "target_weight": {"type": "number", "minimum": 0, "maximum": 100},
                        "previous_weight": {"type": ["number", "null"], "minimum": 0, "maximum": 100},
                    },
                },
            },
            "source_mode": {"type": "string", "enum": ["unity_live_export", "configured_export", "custom_export", "mvp_sample"], "default": "mvp_sample", "description": "Set unity_live_export for a live Unity edit; the manual request model otherwise defaults to mvp_sample."},
            "mock_execute": {"type": "boolean", "default": True, "description": "Set false for an approved live Unity edit; the manual request model defaults to true and a mock apply is rejected by the write workflow."},
            "save_artifacts": {"type": "boolean", "default": True, "description": "Controls workflow artifacts, not whether the approved Unity write saves assets."},
            "scope": {"type": ["string", "null"], "description": "Use all for clothing or body shapes; face limits face-oriented workflows."},
        },
    },
    "vrcforge_install_vpm_package": PACKAGE_INSTALL_INPUT_SCHEMA,
    "vrcforge_import_outfit_package": outfit_import_input_schema(require_project_path=True),
    "vrcforge_refresh_asset_database": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectPath": {
                "type": "string",
                "minLength": 1,
                "description": "Exact Unity project root; omit only when the active selected project is the intended target.",
            },
            "resolvePackages": {
                "type": "boolean",
                "default": False,
                "description": "Resolve Package Manager dependencies during refresh only when explicitly needed.",
            },
            "packageResolveTimeoutSeconds": {
                "type": "integer",
                "minimum": 5,
                "maximum": 300,
                "default": 120,
                "description": "Bounded Package Manager resolve timeout in seconds.",
            },
            "reimportAssets": {
                "type": "array", "minItems": 1, "maxItems": 16, "additionalItems": False,
                "description": "Optional exact Assets files or embedded Packages/<packageId>/**/*.cs scripts to force-reimport after all identities and source hashes preflight successfully. PackageCache, .meta, Library, traversal, and non-embedded package paths are rejected.",
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["assetPath", "guid", "expectedSourceSha256", "expectedImporterType"],
                    "properties": {
                        "assetPath": {
                            "type": "string",
                            "anyOf": [
                                {"pattern": "^Assets/[^.].*"},
                                {"pattern": "^Packages/(?!PackageCache(?:/|$))(?!.*(?:^|/)\\.\\.(?:/|$))[a-z0-9][a-z0-9._-]*/.+\\.cs$"},
                            ],
                            "description": "Exact project Assets file, or an embedded package C# source path under Packages/<packageId>; package manifest, package root, PackageCache, .meta, Library, and traversal paths are invalid.",
                        },
                        "guid": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"},
                        "expectedSourceSha256": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
                        "expectedImporterType": {"type": "string", "minLength": 1, "maxLength": 512,
                            "description": "Exact effective registered importer type from get_asset_info: overrideImporterType when set, otherwise defaultImporterType."},
                        "restoreScriptedImporterReference": {
                            "type": "object", "additionalProperties": False,
                            "description": "Explicitly restore one null ScriptedImporter reference on an Assets file before reimport. Requires a missing importer instance, its exact currently bound default-importer MonoScript GUID, and hashes proving that only the null reference changes. Non-null references and importer overrides are rejected. Failure preserves unknown metadata changes and declares checkpoint recovery.",
                            "required": ["scriptGuid", "expectedMetadataSha256", "restoredMetadataSha256"],
                            "properties": {
                                "scriptGuid": {"type": "string", "pattern": "^[0-9a-f]{32}$", "description": "Exact MonoScript GUID; its loaded class must match the registered default ScriptedImporter."},
                                "expectedMetadataSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "description": "SHA256 of current .meta bytes from independent readback."},
                                "restoredMetadataSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "description": "SHA256 after replacing only the null script reference with fileID 11500000 and this script GUID."},
                            },
                        },
                    },
                    "allOf": [{
                        "if": {"required": ["restoreScriptedImporterReference"]},
                        "then": {"properties": {"assetPath": {"pattern": "^Assets/"}}},
                    }],
                },
            },
        },
    },
    # This handler is registered on the write/approval route. Keep its public
    # contract here as well as the read projection so tools/list cannot fall
    # back to the permissive empty schema.
    "vrcforge_resolve_interrupted_apply_recovery": INTERRUPTED_APPLY_RESOLVE_INPUT_SCHEMA,
    "vrcforge_restore_shader_tuning": {
        "type": "object", "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "avatarPath": {"type": "string", "description": "Exact avatar whose latest in-memory shader undo point is restored; omit only when the current avatar is already selected. Caller-supplied changes are not used."},
        },
    },
    "vrcforge_reapply_shader_tuning_history": {
        "type": "object", "additionalProperties": False,
        "required": ["projectPath", "historyId"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "historyId": {"type": "string", "minLength": 1, "description": "Exact existing saved shader history record ID; its stored changes are revalidated against current Unity facts."},
            "avatarPath": {"type": "string", "description": "Exact avatar; omit only when bound by the saved record or current selection."},
            "categoryOverrides": SHADER_CATEGORY_OVERRIDES_SCHEMA,
            "locked_materials": {"type": "array", "items": {"type": "string"}, "description": "Additional exact material IDs to lock during revalidation; existing saved/global locks remain effective."},
            "locked_properties": {"type": "array", "items": {"type": "string"}, "description": "Additional semantic property keys to lock during revalidation."},
        },
    },
    "vrcforge_apply_shader_tuning_preset": {
        "type": "object", "additionalProperties": False,
        "required": ["projectPath", "presetId"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "presetId": {"type": "string", "minLength": 1, "description": "Exact existing saved shader preset ID; its stored changes are revalidated against current Unity facts."},
            "avatarPath": {"type": "string", "description": "Exact avatar; omit only when bound by the saved record or current selection."},
            "categoryOverrides": SHADER_CATEGORY_OVERRIDES_SCHEMA,
            "locked_materials": {"type": "array", "items": {"type": "string"}, "description": "Additional exact material IDs to lock during revalidation; existing saved/global locks remain effective."},
            "locked_properties": {"type": "array", "items": {"type": "string"}, "description": "Additional semantic property keys to lock during revalidation."},
        },
    },

    "vrcforge_apply_shader_tuning": {
        "type": "object", "additionalProperties": False,
        "required": ["projectPath", "changes"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "avatarPath": {"type": "string"}, "instruction": {"type": "string", "minLength": 1},
            "inventory": {"type": "object", "additionalProperties": True},
            "categoryOverrides": SHADER_CATEGORY_OVERRIDES_SCHEMA,
            "lockedMaterials": {"type": "array", "items": {"type": "string"}, "maxItems": 2000},
            "lockedProperties": {"type": "array", "items": {"type": "string"}, "maxItems": 2000},
            "changes": {"type": "array", "minItems": 1, "maxItems": 2000, "items": SHADER_CHANGE_INPUT_SCHEMA},
            "historyId": {"type": "string"},
        },
    },
    "vrcforge_apply_clothing_fx": {
        "type": "object", "additionalProperties": False,
        "required": ["projectPath", "items"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY, "avatarPath": {"type": "string"},
            "dryRun": {"type": "boolean", "default": True},
            "items": {"type": "array", "minItems": 1, "maxItems": 256, "items": {"type": "object", "additionalProperties": False, "properties": {
                "displayName": {"type": "string"}, "name": {"type": "string"}, "parameterName": {"type": "string"},
                "animationClipName": {"type": "string"}, "sampleObjectPath": {"type": "string"}, "objectPath": {"type": "string"},
            }}},
        },
    },
    "vrcforge_duplicate_project_asset": {
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
            "destinationAssetPath": {"type": "string", "pattern": "^Assets/VRCForgeGenerated/.+", "description": "Exact create-new asset path. Missing classified parent folders are listed in preview and created within the approved copy."},
            "preview": {"type": "boolean", "default": False}, "overwrite": {"type": "boolean", "const": False},
            "expectedProjectPath": {"type": "string"}, "expectedSourceGuid": {"type": "string", "pattern": "^[0-9a-fA-F]{32}$"},
            "expectedSourceFileDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"}, "expectedSourceFileIdentity": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            "expectedSourceMetaDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"}, "expectedSourceMetaIdentity": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            "expectedSourceMainAssetType": {"type": "string"}, "expectedSourceObjectLayoutDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            "expectedGeneratedRootExists": {"type": "boolean"}, "expectedGeneratedRootGuid": {"type": "string"}, "expectedGeneratedRootIdentity": {"type": "string"},
            "expectedAnchorFolderGuid": {"type": "string"}, "expectedAnchorFolderIdentity": {"type": "string"}, "expectedDestinationParentFolderGuid": {"type": "string"}, "expectedDestinationParentFolderIdentity": {"type": "string"},
            "expectedDestinationAbsent": {"type": "boolean"}, "expectedPreviewDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
        },
    },
    "vrcforge_relocate_generated_assets": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "entries"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "preview": {"type": "boolean", "default": True},
            "entries": {
                "type": "array", "minItems": 1, "maxItems": 500,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["sourceAssetPath", "destinationAssetPath", "expectedGuid", "expectedAssetSha256", "expectedMetaSha256"],
                    "properties": {
                        "sourceAssetPath": {"type": "string", "pattern": "^Assets/VRCForge/Generated/.+"},
                        "destinationAssetPath": {"type": "string", "pattern": "^Assets/VRCForgeGenerated/.+"},
                        "expectedGuid": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                        "expectedAssetSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "expectedMetaSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    },
                },
            },
        },
    },
    "vrcforge_scene_save": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "action", "scenePath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "action": {"type": "string", "enum": ["save", "save_as"]},
            "scenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$", "description": "Exact active scene identity; never a hierarchy-path fallback."},
            "destinationScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$", "description": "Required only for save_as and must not exist."},
        },
    },
    "vrcforge_scene_transition": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "action"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "action": {"type": "string", "enum": ["open_single", "open_additive", "new_saved", "set_active", "unload", "reload_saved"]},
            "scenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$"},
            "destinationScenePath": {"type": "string", "pattern": "^Assets/.*\\.unity$", "description": "Required only for new_saved and must not exist."},
        },
    },
    "vrcforge_add_modular_avatar_component": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "componentType"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string", "minLength": 1},
            "avatarPath": {"type": "string"},
            "componentType": {"type": "string", "minLength": 1},
            "references": {"type": "object", "additionalProperties": True},
            "fields": {"type": "object", "additionalProperties": True},
            "saveScene": {"type": "boolean", "default": False},
            "allowDuplicate": {"type": "boolean", "default": False},
        },
    },
    "vrcforge_texture_patch": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "sourceTexturePath", "targetTexturePath", "region", "red", "green", "blue", "alpha", "opacity", "featherPixels"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "sourceTexturePath": {"type": "string", "pattern": "^Assets/.*\\.png$"},
            "targetTexturePath": {"type": "string", "pattern": "^Assets/.*\\.png$", "description": "Create-new output; it and its .meta must be absent."},
            "region": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "integer", "minimum": 0}, "description": "Exact [x,y,width,height] pixel rectangle; width and height must be positive."},
            "protectedRegions": {"type": "array", "maxItems": 128, "items": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "integer", "minimum": 0}}},
            "red": {"type": "integer", "minimum": 0, "maximum": 255},
            "green": {"type": "integer", "minimum": 0, "maximum": 255},
            "blue": {"type": "integer", "minimum": 0, "maximum": 255},
            "alpha": {"type": "integer", "minimum": 0, "maximum": 255},
            "opacity": {"type": "number", "minimum": 0, "maximum": 1, "default": 1},
            "featherPixels": {"type": "integer", "minimum": 0, "maximum": 256, "default": 0},
        },
    },
    "vrcforge_user_adjustment_handoff": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "action", "mode", "avatarGlobalObjectId", "targetGlobalObjectId"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "action": {"type": "string", "enum": ["prepare", "finalize", "abort"]},
            "mode": {"type": "string", "enum": ["transform", "skinned_bone_proxy", "constraint_bound", "physbone_chain"]},
            "avatarGlobalObjectId": {"type": "string", "pattern": "^GlobalObjectId_V1-"},
            "targetGlobalObjectId": {"type": "string", "pattern": "^GlobalObjectId_V1-"},
            "handoffId": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        },
    },
    "vrcforge_remap_skinned_mesh_bone": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "componentIndex", "boneIndex", "expectedCurrentBonePath", "targetBonePath", "expectedMeshName", "preview"],
        "properties": {
            "projectPath": {"type": "string"},
            "gameObjectPath": {"type": "string"},
            "componentIndex": {"type": "integer", "minimum": 0},
            "boneIndex": {"type": "integer", "minimum": 0},
            "expectedCurrentBonePath": {"type": "string"},
            "targetBonePath": {"type": "string"},
            "expectedMeshName": {"type": "string"},
            "preview": {"type": "boolean"},
        },
    },
    "vrcforge_duplicate_scene_object": SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_duplicate_scene_asset": SCENE_ASSET_DUPLICATE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_write_avatar_descriptor": AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_write_animation_curve": ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_manage_expression_parameters": EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_manage_expression_menu": EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA,
    "vrcforge_remove_component": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "componentType"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "componentType": {"type": "string"},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
        },
    },
    "vrcforge_reparent_gameobject": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "newParentPath": {"type": "string"},
            "worldPositionStays": {"type": "boolean", "default": True},
        },
    },
    "vrcforge_set_gameobject_active": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "active"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "active": {"type": "boolean"},
        },
    },
    "vrcforge_set_property": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath", "componentType", "propertyPath", "value"],
        "properties": {
            "projectPath": _PROJECT_PATH_PROPERTY,
            "gameObjectPath": {"type": "string"},
            "componentType": {"type": "string"},
            "propertyPath": {"type": "string"},
            "value": {"description": "Exact JSON value to assign to the field or property."},
            "componentIndex": {"type": "integer", "minimum": 0, "default": 0},
        },
    },
    "vrcforge_save_current_scene": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "scenePath"],
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Exact existing Unity project root whose current saved scene must be dirty.",
            },
            "scenePath": {
                "type": "string",
                "pattern": "^Assets/.*\\.unity$",
                "description": "Exact current Assets/... .unity path used as an identity check.",
            },
        },
    },
    "vrcforge_create_installed_skill": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "description", "instructions"],
        "properties": {
            "name": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]{1,80}$"},
            "title": {"type": "string", "maxLength": 120},
            "description": {"type": "string", "minLength": 1, "maxLength": 500},
            "instructions": {"type": "string", "minLength": 1, "maxLength": 262144},
            "allowedTools": {
                "type": "array", "items": {"type": "string"}, "maxItems": 64
            },
            "entrypointTool": {"type": "string"},
            "permissionMode": {
                "type": "string",
                "enum": ["instruction_only", "read_only", "preview", "approval_required"],
            },
            "riskLevel": {"type": "string", "enum": ["low", "medium", "high"]},
            "whenToUse": {"type": "string", "maxLength": 1000},
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
        },
    },
    "vrcforge_write_path_to_skill": PATH_TO_SKILL_WRITE_INPUT_SCHEMA,
    "vrcforge_set_skill_package_enabled": {
        "type": "object",
        "additionalProperties": False,
        "required": ["skillPackageId", "enabled"],
        "properties": {
            "skillPackageId": {"type": "string", "minLength": 1},
            "enabled": {"type": "boolean"},
        },
    },
    "vrcforge_import_skill_package": {
        "type": "object",
        "additionalProperties": False,
        "required": ["packagePath"],
        "properties": {
            "packagePath": {"type": "string", "description": "Exact local path to the existing .vsk package."},
            "source": {"type": "string", "maxLength": 160, "description": "Optional audit label for this local import."},
            "projectToUserSkills": {"type": "boolean", "default": True, "description": "Project the installed package into the user Skill directory."},
        },
    },
    "vrcforge_export_skill_package": {
        "type": "object",
        "additionalProperties": False,
        "required": ["skillName", "outputPath"],
        "properties": {
            "skillName": {"type": "string", "description": "Exact installed user Skill name to export."},
            "outputPath": {"type": "string", "description": "Exact new local .vsk destination; it must not already exist."},
            "release": {"type": "boolean", "default": False, "description": "Sign a release package instead of creating a development package."},
            "privateKeyPath": {"type": "string", "description": "Local Ed25519 private-key file path required only for release export. Key material is never accepted inline."},
        },
    },
    "vrcforge_set_texture_import_settings": TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA,
    "vrcforge_manage_fx_animator": MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA,
    "vrcforge_flatten_material_variant": MATERIAL_VARIANT_FLATTEN_PUBLIC_INPUT_SCHEMA,
    "vrcforge_set_material_shader": MATERIAL_SHADER_ASSIGNMENT_PUBLIC_INPUT_SCHEMA,
    "vrcforge_set_material_texture": MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA,
    "vrcforge_set_renderer_material_slot": RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA,
    "vrcforge_set_constraint_sources": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "scenePath", "gameObjectPath", "constraintKind", "componentIndex", "sources"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this scene write."},
            "scenePath": {"type": "string", "description": "Exact Assets/... .unity scene containing the constraint."},
            "gameObjectPath": {"type": "string", "description": "Exact hierarchy path of the GameObject carrying the constraint."},
            "constraintKind": {"type": "string", "enum": ["position", "rotation", "scale", "parent", "aim", "look_at"]},
            "componentIndex": {"type": "integer", "minimum": 0, "maximum": 31},
            "sources": {
                "type": "array",
                "maxItems": 64,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sourcePath", "weight"],
                    "properties": {
                        "sourcePath": {"type": "string", "description": "Exact source Transform hierarchy path."},
                        "weight": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                },
            },
        },
    },
    "vrcforge_save_scene_object_as_prefab": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "sourceScenePath", "sourceObjectPath", "prefabAssetPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this create-new asset write."},
            "sourceScenePath": {"type": "string", "description": "Exact Assets/... .unity scene containing the source object."},
            "sourceObjectPath": {"type": "string", "description": "Exact hierarchy path of the source scene object."},
            "prefabAssetPath": {"type": "string", "description": "Exact absent .prefab destination in an existing classified folder under Assets/VRCForgeGenerated/Prefabs/."},
        },
    },
    "vrcforge_build_parameter_bit_packed_clone": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "sourceScenePath", "sourceAvatarPath", "outputCloneName"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this source-preserving optimization."},
            "sourceScenePath": {"type": "string", "description": "Exact Assets/... .unity scene containing the source Avatar."},
            "sourceAvatarPath": {"type": "string", "description": "Exact source Avatar hierarchy path; the source is preserved."},
            "outputCloneName": {"type": "string", "description": "Exact new sibling clone name for the packed result."},
        },
    },
    "vrcforge_atomic_reference_rename": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "operationKind", "scenePath", "avatarPath"],
        "oneOf": [
            {"properties": {"operationKind": {"const": "game_object"}}, "required": ["targetObjectPath", "newName"]},
            {"properties": {"operationKind": {"const": "parameter"}}, "required": ["oldParameterName", "newParameterName"]},
        ],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this complete reference migration."},
            "operationKind": {"type": "string", "enum": ["game_object", "parameter"]},
            "scenePath": {"type": "string", "description": "Exact Assets/... .unity scene containing the Avatar."},
            "avatarPath": {"type": "string", "description": "Exact Avatar root hierarchy path that bounds the migration."},
            "targetObjectPath": {"type": "string", "description": "Exact descendant hierarchy path for a game_object migration."},
            "newName": {"type": "string", "description": "New leaf GameObject name for a game_object migration."},
            "oldParameterName": {"type": "string", "description": "Exact existing expression/Animator parameter name."},
            "newParameterName": {"type": "string", "description": "Exact replacement parameter name."},
        },
    },
    "vrcforge_delete_gameobject": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "anyOf": [
            {"required": ["gameObjectPath"]},
            {"required": ["globalObjectId"]},
        ],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "gameObjectPath": {"type": "string", "description": "Exact hierarchy path when it is unique."},
            "globalObjectId": {"type": "string", "description": "Exact Unity GlobalObjectId; prefer this when hierarchy names are duplicated."},
            "preview": {"type": "boolean", "default": False},
        },
    },
    "vrcforge_instantiate_prefab": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "anyOf": [
            {"required": ["assetPath"]},
            {"required": ["guid"]},
        ],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "assetPath": {"type": "string", "description": "Exact project-relative prefab asset path, including Packages/... prefabs."},
            "guid": {"type": "string", "description": "Exact prefab asset GUID when assetPath is omitted."},
            "parentPath": {"type": "string", "description": "Optional exact hierarchy path of the parent. Omit or pass an empty string for the scene root."},
            "name": {"type": "string", "description": "Optional exact instance name override."},
            "worldPositionStays": {"type": "boolean", "default": True},
            "preview": {"type": "boolean", "default": False},
        },
    },
    "vrcforge_build_test_avatar": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "avatarPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this local build."},
            "avatarPath": {"type": "string", "description": "Exact loaded-scene hierarchy path of the avatar to Build & Test locally."},
        },
    },
    "vrcforge_build_and_upload_avatar": {
        **UNITY_READ_TOOL_INPUT_SCHEMAS.get("vrcforge_avatar_upload_readiness", {}),
        "required": [
            "projectPath", "avatarPath", "uploadMode", "buildType", "platforms", "metadata", "thumbnail",
            "expectedAvatarGlobalObjectId", "expectedCurrentPipelineId", "expectedSdkUserId", "expectedPlatform", "readinessDigest",
        ],
        "properties": {
            **UNITY_READ_TOOL_INPUT_SCHEMAS.get("vrcforge_avatar_upload_readiness", {}).get("properties", {}),
            "expectedAvatarGlobalObjectId": {"type": "string"},
            "expectedCurrentPipelineId": {"type": "string"},
            "expectedSdkUserId": {"type": "string"},
            "expectedPlatform": {"type": "string"},
            "readinessDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
        },
    },
    "vrcforge_convert_unity_constraint": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "scenePath", "avatarPath", "gameObjectPath", "componentType", "componentIndex", "expectedSceneGuid", "expectedSceneFileDigest", "expectedAvatarGlobalObjectId", "expectedComponentGlobalObjectId", "expectedBeforeDigest"],
        "properties": {
            **UNITY_READ_TOOL_INPUT_SCHEMAS.get("vrcforge_preview_unity_constraint_conversion", {}).get("properties", {}),
            "expectedSceneGuid": {"type": "string"},
            "expectedSceneFileDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
            "expectedAvatarGlobalObjectId": {"type": "string"},
            "expectedComponentGlobalObjectId": {"type": "string"},
            "expectedBeforeDigest": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
        },
    },
    "vrcforge_install_unity_core": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Exact existing Unity project root that will receive the Core bundled with this running VRCForge build.",
            },
        },
    },
    "vrcforge_gesture_manager_set_parameter": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "parameterName", "value"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "avatarPath": {"type": "string", "description": "Optional exact avatar hierarchy path; required only when multiple managers are connected."},
            "parameterName": {"type": "string", "description": "Exact existing Gesture Manager runtime parameter, for example VelocityZ or Grounded."},
            "value": {"type": "number", "description": "Runtime value; the tool reads back the applied value."},
        },
    },
    "vrcforge_gesture_manager_enter_play_mode": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external editor-state call."},
            "avatarPath": {"type": "string", "description": "Optional exact active avatar hierarchy path; required when multiple active avatars exist."},
        },
    },
    "vrcforge_capture_screenshot": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this capture."},
            "avatarPath": {"type": "string", "description": "Optional exact avatar hierarchy path; omit only when the active avatar is unambiguous."},
            "cameraMode": {"type": "string", "enum": ["framed", "free"], "default": "framed"},
            "cameraPosition": {"$ref": "#/$defs/vector3"},
            "targetPosition": {"$ref": "#/$defs/vector3"},
            "upVector": {"$ref": "#/$defs/vector3"},
            "projection": {"type": "string", "enum": ["perspective", "orthographic"]},
            "orthographicSize": {"type": "number", "exclusiveMinimum": 0},
            "fieldOfView": {"type": "number", "exclusiveMinimum": 0, "maximum": 179},
            "angle": {
                "type": "string",
                "enum": ["front", "side_left", "side_right", "back", "bottom"],
                "description": "One fixed view angle. bottom is a true underneath view. Do not combine a named angle with pitch, yaw, or roll.",
            },
            "framing": {
                "type": "string",
                "enum": ["face", "avatar"],
                "description": "face frames the head; avatar frames the complete avatar including feet and tail. Named angles default to face for compatibility.",
            },
            "captureScope": {
                "type": "string",
                "enum": ["face", "avatar"],
                "description": "Exact Core capture scope. It is an alias of framing and must match framing when both are supplied.",
            },
            "pitch": {
                "type": "number",
                "minimum": -180.0,
                "maximum": 180.0,
                "description": "Explicit camera pitch in degrees. Do not combine with angle.",
            },
            "yaw": {
                "type": "number",
                "minimum": -180.0,
                "maximum": 180.0,
                "description": "Explicit camera yaw in degrees. Do not combine with angle.",
            },
            "roll": {
                "type": "number",
                "minimum": -180.0,
                "maximum": 180.0,
                "description": "Explicit camera roll in degrees. Do not combine with angle.",
            },
            "width": {"type": "integer", "minimum": 256, "maximum": 2048, "default": 960},
            "height": {"type": "integer", "minimum": 256, "maximum": 2048, "default": 960},
            "requirePlayMode": {"type": "boolean", "default": False},
            "captureMode": {
                "type": "string",
                "enum": ["auto", "scene_view", "game_view"],
                "default": "auto",
                "description": "scene_view captures the Unity Scene view even during Gesture Manager Play Mode.",
            },
        },
        "$defs": {"vector3": {"type": "object", "additionalProperties": False, "required": ["x", "y", "z"], "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}}}},
        "allOf": [
            {"if": {"properties": {"cameraMode": {"const": "free"}}}, "then": {"required": ["cameraMode", "cameraPosition", "targetPosition", "upVector", "projection"], "oneOf": [{"properties": {"projection": {"const": "perspective"}}, "required": ["fieldOfView"], "not": {"required": ["orthographicSize"]}}, {"properties": {"projection": {"const": "orthographic"}}, "required": ["orthographicSize"], "not": {"required": ["fieldOfView"]}}]}},
            {"if": {"properties": {"cameraMode": {"const": "framed"}}}, "then": {"not": {"anyOf": [{"required": ["cameraPosition"]}, {"required": ["targetPosition"]}, {"required": ["upVector"]}, {"required": ["projection"]}, {"required": ["orthographicSize"]}, {"required": ["fieldOfView"]}]}}}
        ],
    },
    "vrcforge_capture_multi_screenshot": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath"],
        "properties": {
            "projectPath": {"type": "string"},
            "avatarPath": {"type": "string"},
            "angles": {
                "type": "array",
                "items": {"type": "string", "enum": ["front", "side_left", "side_right", "back", "bottom"]},
                "default": ["front", "side_left", "side_right", "back", "bottom"],
                "minItems": 1,
                "maxItems": 5,
                "uniqueItems": True,
            },
            "framing": {"type": "string", "enum": ["face", "avatar"]},
            "width": {"type": "integer", "minimum": 256, "maximum": 2048, "default": 960},
            "height": {"type": "integer", "minimum": 256, "maximum": 2048, "default": 960},
            "requirePlayMode": {"type": "boolean", "default": False},
            "captureMode": {"type": "string", "enum": ["auto", "scene_view", "game_view"], "default": "auto"},
        },
    },
    "vrcforge_select_scene_object": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "gameObjectPath"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "gameObjectPath": {"type": "string", "description": "Exact loaded-scene hierarchy path to select and show in Inspector."},
        },
    },
    "vrcforge_set_play_mode": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "isPlaying"],
        "properties": {
            "projectPath": {"type": "string", "description": "Exact existing Unity project root bound to this external write call."},
            "isPlaying": {"type": "boolean", "description": "True to enter Play Mode; false to exit Play Mode."},
        },
    },
    "vrcforge_confirm_unity_reload_dialog": {
        "type": "object",
        "additionalProperties": False,
        "required": ["projectPath", "confirmReload"],
        "properties": {
            "projectPath": {
                "type": "string",
                "description": "Exact existing Unity project whose bound Editor displays the Reload dialog.",
            },
            "confirmReload": {"type": "boolean", "const": True},
        },
    },
}

EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_start_runtime_observation"] = {
    "type": "object", "properties": {
        "stateDetail": {"type": "string", "enum": ["resource", "inline"], "default": "resource", "description": "Resource returns state counts and bounded resources/read page URIs; inline preserves full states arrays."},
        "projectPath": {"type": "string"}, "avatarPath": {"type": "string", "minLength": 1},
        "parameterName": {"type": "string", "minLength": 1}, "value": {"type": "number"},
        "durationSeconds": {"type": "number", "minimum": 0.1, "maximum": 10},
        "frameCount": {"type": "integer", "minimum": 2, "maximum": 32},
        "width": {"type": "integer", "minimum": 128, "maximum": 512}, "height": {"type": "integer", "minimum": 128, "maximum": 512},
        "fieldOfView": {"type": "number", "minimum": 1, "maximum": 179},
        "parameterSteps": {"type": "array", "maxItems": 64, "items": {"type": "object", "additionalProperties": False, "required": ["timeSeconds", "parameterName", "value"], "properties": {"timeSeconds": {"type": "number", "minimum": 0}, "parameterName": {"type": "string", "minLength": 1}, "value": {"type": "number"}}}},
        "rendererProbes": {"type": "array", "maxItems": 8, "description": "Optional exact Avatar descendant renderer/material probes captured with each frame; read-only shared material and effective property block values. A SkinnedMeshRenderer may also request exact blendShapeNames for per-frame weights. *_ST may refer to a declared texture's scale/offset.", "items": {
            "type": "object", "additionalProperties": False, "required": ["rendererPath", "materialIndex", "propertyNames"], "properties": {
                "rendererPath": {"type": "string", "minLength": 1, "maxLength": 2048},
                "materialIndex": {"type": "integer", "minimum": 0},
                "rendererComponentIndex": {"type": "integer", "minimum": 0, "description": "Required if multiple Renderer components exist on the exact object."},
                "propertyNames": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]{0,127}$"}},
                "blendShapeNames": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "minLength": 1, "maxLength": 128}}
            }}},
        **{key: {"type": "object", "properties": {axis: {"type": "number"} for axis in ("x", "y", "z")}, "required": ["x", "y", "z"], "additionalProperties": False} for key in ("cameraPosition", "targetPosition", "upVector")},
    },
    "required": ["projectPath", "avatarPath", "parameterName", "value", "durationSeconds", "frameCount", "width", "height", "cameraPosition", "targetPosition", "upVector", "fieldOfView"],
}

_SAFE_BACKUP_WHEN_TO_USE = "when-to-use: create or restore a project-owned safe backup for an explicit approved file protection or recovery operation."
_SAFE_BACKUP_WHEN_NOT_TO_USE = "when-NOT-to-use: do not call for a general explanation, hypothetical request, unapproved write, or as a substitute for reading current project state."

EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_create_safe_backup"] = {
    "type": "object",
    "additionalProperties": False,
    "description": _SAFE_BACKUP_WHEN_TO_USE + " " + _SAFE_BACKUP_WHEN_NOT_TO_USE,
    "required": ["projectPath"],
    "properties": {
        "projectPath": {"type": "string", "minLength": 1, "description": "Exact existing Unity project root bound to the backup."},
        "avatarPath": {"type": "string", "description": "Optional exact avatar hierarchy path recorded with the backup."},
        "assetPaths": {"type": "array", "maxItems": 256, "items": {"type": "string", "minLength": 1}, "description": "Optional exact project-relative asset paths to protect; omit or use an empty array for the approved default scope."},
        "includeOpenScenes": {"type": "boolean", "default": True, "description": "Include currently open scenes in the backup manifest."},
    },
}

_SAFE_BACKUP_RESTORE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "description": _SAFE_BACKUP_WHEN_TO_USE + " " + _SAFE_BACKUP_WHEN_NOT_TO_USE,
    "required": ["projectPath"],
    "properties": {
        "projectPath": {"type": "string", "minLength": 1, "description": "Exact existing Unity project root bound to the backup operation."},
        "backupPath": {"type": "string", "description": "Exact backup path returned by create_safe_backup; use this or backupId."},
        "backupId": {"type": "string", "description": "Exact backup id returned by create_safe_backup; use this or backupPath."},
        "assetPaths": {"type": "array", "maxItems": 256, "items": {"type": "string", "minLength": 1}, "description": "Optional exact subset of paths from the backup to preview or restore."},
        "allowProjectMismatch": {"type": "boolean", "default": False, "description": "Allow a project identity mismatch only when explicitly approved."},
        "allowOverwriteChanged": {"type": "boolean", "default": False, "description": "Allow overwriting files changed after the backup only when explicitly approved."},
    },
    "anyOf": [{"required": ["backupPath"]}, {"required": ["backupId"]}],
}

EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_restore_safe_backup"] = _SAFE_BACKUP_RESTORE_SCHEMA


EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_apply_parameter_optimization"] = {
    "type": "object", "additionalProperties": True, "required": ["suggestions"],
    "properties": {
        "projectPath": {**_PROJECT_PATH_PROPERTY, "type": ["string", "null"], "description": "Unity project root. Alias: project_path."},
        "avatarPath": {**_AVATAR_PATH_PROPERTY, "type": ["string", "null"], "description": "Avatar containing the existing expression-parameters asset. Alias: avatar_path."},
        "suggestions": {
            "type": "array", "minItems": 1,
            "description": "Selected parameter optimization suggestions. Mutation requires each existing exact parameter name; dry-run previews the payload.",
            "items": {"type": "object", "additionalProperties": True, "properties": {
                "name": {"type": "string", "description": "Exact existing parameter name; required when applying in Unity."},
                "currentType": {"type": "string", "default": "Int", "description": "Reported original type for the preview diff."},
                "suggestedType": {"type": "string", "default": "Bool", "description": "Reported target type for the preview diff; the predefined apply operation converts to Bool."},
            }},
        },
        "dry_run": {"type": "boolean", "default": True, "description": "Return the proposed payload without applying. Set false only for the approved parameter optimization write."},
    },
}

EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_restore_checkpoint"] = {
    **UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_restore_checkpoint"],
    "properties": {
        **UNITY_READ_TOOL_INPUT_SCHEMAS["vrcforge_preview_restore_checkpoint"]["properties"],
        "confirmRestore": {"type": "boolean", "description": "Must be true, or confirm_restore must be true, to confirm restoring the selected checkpoint. The normal approval flow still applies."},
        "confirm_restore": {"type": "boolean", "description": "Existing alias of confirmRestore."},
    },
    "allOf": [{"anyOf": [
        {"required": ["confirmRestore"], "properties": {"confirmRestore": {"const": True}}},
        {"required": ["confirm_restore"], "properties": {"confirm_restore": {"const": True}}},
    ]}],
}
