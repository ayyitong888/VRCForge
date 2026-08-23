"""Indexed lazy-load catalogue for the built-in VRCForge Agent runtime.

This catalogue is intentionally independent from the external MCP catalogue.
Only the underlying Unity handlers are shared; loaded-block state and non-Unity
capabilities never cross the internal/external boundary.
"""

from __future__ import annotations

from typing import Any, Iterable


INTERNAL_DEFAULT_TOOL_BLOCK = "core"

_TOP_LEVEL = (
    ("1", "core", "Conversation control, goals, progress, and this tool index."),
    ("2", "files", "Bounded local file inspection and edits."),
    ("3", "web", "Web search and page retrieval."),
    ("4", "desktop", "User-started Computer Use actions."),
    ("5", "shell", "Host commands and owned process control."),
    ("6", "attachments", "Chat attachments and visual inspection."),
    ("7", "diagnostics", "VRCForge runtime, skills, logs, and connector diagnostics."),
    ("8", "unity", "Unity tools; expand this branch before loading a leaf."),
)

_UNITY_CHILDREN = (
    ("8.1", "unity/core", "Unity connection and basic reads."),
    ("8.2", "unity/project", "Unity projects and package-manager backends."),
    ("8.3", "unity/avatar", "Avatar hierarchy, components, animation, parameters, and menus."),
    ("8.4", "unity/assets", "Asset, prefab, outfit, and wardrobe operations."),
    ("8.5", "unity/materials", "Materials, shaders, and tuning."),
    ("8.6", "unity/integrations", "Installed Unity plugin adapters; expand this branch before loading a family."),
    ("8.7", "unity/optimization", "Avatar optimization inspection and applies."),
    ("8.8", "unity/checkpoint", "Checkpoint, backup, recovery, and explicit restore tools."),
    ("8.9", "unity/diagnostics", "Unity Console, compile, validation, and tool diagnostics."),
    ("8.10", "unity/encryption", "Optional private avatar-encryption integration."),
)

_UNITY_INTEGRATION_CHILDREN = (
    ("8.6.1", "unity/integrations/modular-avatar", "Modular Avatar inspection, Setup Outfit, and atomic components."),
    ("8.6.2", "unity/integrations/vrcfury", "VRCFury inspection and public-API-backed features."),
    ("8.6.3", "unity/integrations/ndmf", "NDMF merged-build information and compatible processors."),
    ("8.6.4", "unity/integrations/gesture-manager", "Gesture Manager Play Mode status, menus, and runtime parameters."),
)

INTERNAL_LOADABLE_TOOL_BLOCKS = frozenset(
    {item[1] for item in _TOP_LEVEL if item[1] != "unity"}
    | {item[1] for item in _UNITY_CHILDREN if item[1] != "unity/integrations"}
    | {item[1] for item in _UNITY_INTEGRATION_CHILDREN}
)

_SELECTORS = {
    selector.casefold(): name
    for selector, name, _description in (
        *_TOP_LEVEL,
        *_UNITY_CHILDREN,
        *_UNITY_INTEGRATION_CHILDREN,
    )
    if name not in {"unity", "unity/integrations"}
}
_SELECTORS.update({name.casefold(): name for name in INTERNAL_LOADABLE_TOOL_BLOCKS})

_CORE_TOOLS = frozenset(
    {
        "vrcforge_get_goal",
        "vrcforge_create_goal",
        "vrcforge_update_goal",
        "vrcforge_progress_list",
        "vrcforge_progress_replace",
        "vrcforge_progress_create",
        "vrcforge_progress_update",
        "vrcforge_progress_delete",
        "vrcforge_ask_user",
        "vrcforge_delegate_subagent",
        "vrcforge_list_internal_tool_blocks",
        "vrcforge_load_internal_tool_block",
        "vrcforge_unload_internal_tool_block",
    }
)
_FILE_TOOLS = frozenset(
    {
        "vrcforge_list_directory",
        "vrcforge_read_text_file",
        "vrcforge_find_files",
        "vrcforge_search_text",
        "vrcforge_edit_file",
        "vrcforge_write_file",
        "vrcforge_delete_path",
        "vrcforge_move_path",
        "vrcforge_apply_patch",
    }
)
_WEB_TOOLS = frozenset({"vrcforge_web_fetch", "vrcforge_web_search"})
_DESKTOP_TOOLS = frozenset({"vrcforge_agent_desktop_action"})
_SHELL_TOOLS = frozenset(
    {
        "vrcforge_classify_shell",
        "vrcforge_execute_shell",
        "vrcforge_execute_approved_shell",
        "vrcforge_shell_process",
    }
)
_ATTACHMENT_TOOLS = frozenset(
    {
        "vrcforge_capture_multi_screenshot",
        "vrcforge_capture_screenshot",
        "vrcforge_capture_status",
        "vrcforge_import_chat_archive",
        "vrcforge_import_chat_image",
        "vrcforge_inspect_chat_attachment",
        "vrcforge_vision_audit",
        "vrcforge_vision_audit_multi",
    }
)
_DIAGNOSTIC_TOOLS = frozenset(
    {
        "vrcforge_agent_observe",
        "vrcforge_apply_approved",
        "vrcforge_export_skill_package",
        "vrcforge_external_agent_connectors",
        "vrcforge_health",
        "vrcforge_import_skill_package",
        "vrcforge_list_skill_packages",
        "vrcforge_mcp_write",
        "vrcforge_preflight_skill_package",
        "vrcforge_preview_path_to_skill",
        "vrcforge_read_recent_logs",
        "vrcforge_request_apply",
        "vrcforge_set_skill_package_enabled",
        "vrcforge_skill_check",
        "vrcforge_skill_manifest",
        "vrcforge_tool_registry",
        "vrcforge_uninstall_skill_package",
        "vrcforge_write_path_to_skill",
    }
)

INTERNAL_GENERAL_TOOL_NAMES = frozenset(
    _CORE_TOOLS
    | _FILE_TOOLS
    | _WEB_TOOLS
    | _DESKTOP_TOOLS
    | _SHELL_TOOLS
    | _ATTACHMENT_TOOLS
    | _DIAGNOSTIC_TOOLS
)


def resolve_internal_tool_block_selector(value: Any) -> str:
    """Resolve one loadable block by index or name; branch-only nodes return empty."""

    return _SELECTORS.get(str(value or "").strip().casefold(), "")


def normalize_internal_tool_blocks(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset({INTERNAL_DEFAULT_TOOL_BLOCK})
    raw = [value] if isinstance(value, str) else list(value) if isinstance(value, Iterable) else []
    blocks = {
        resolved
        for item in raw
        for resolved in [resolve_internal_tool_block_selector(item)]
        if resolved
    }
    return frozenset(blocks | {INTERNAL_DEFAULT_TOOL_BLOCK})


def _unity_leaf_for_name(name: str) -> str:
    lowered = name.casefold()
    if "encrypt" in lowered or "anti_rip" in lowered:
        return "unity/encryption"
    if any(token in lowered for token in ("checkpoint", "backup", "restore", "recovery")):
        return "unity/checkpoint"
    if "optim" in lowered:
        return "unity/optimization"
    if "vrcfury" in lowered or "component_feature" in lowered:
        return "unity/integrations/vrcfury"
    if "modular_avatar" in lowered or "setup_outfit" in lowered:
        return "unity/integrations/modular-avatar"
    if "ndmf" in lowered:
        return "unity/integrations/ndmf"
    if "gesture_manager" in lowered:
        return "unity/integrations/gesture-manager"
    if "play_mode" in lowered:
        return "unity/project"
    if any(token in lowered for token in ("material", "shader")):
        return "unity/materials"
    if any(token in lowered for token in ("vpm", "vrc_get", "project", "unity_version", "package_manager")):
        return "unity/project"
    if any(token in lowered for token in ("outfit", "wardrobe", "prefab", "asset", "unitypackage")):
        return "unity/assets"
    if any(token in lowered for token in ("console", "compile", "validation", "build_test", "sdk_builder_alert", "unity_status", "unity_tools", "diagnos")):
        return "unity/diagnostics"
    if any(token in lowered for token in ("avatar", "blendshape", "animat", "parameter", "menu", "gameobject", "component", "property", "scene", "hierarchy", "vrm")):
        return "unity/avatar"
    return "unity/core"


def internal_tool_block_for_name(name: str, tool_set: str) -> str:
    """Classify one internal model tool without changing handler ownership."""

    normalized_name = str(name or "").strip()
    if normalized_name in _CORE_TOOLS:
        return "core"
    if normalized_name in _FILE_TOOLS:
        return "files"
    if normalized_name in _WEB_TOOLS:
        return "web"
    if normalized_name in _DESKTOP_TOOLS:
        return "desktop"
    if normalized_name in _SHELL_TOOLS:
        return "shell"
    if normalized_name in _ATTACHMENT_TOOLS:
        return "attachments"
    if normalized_name in _DIAGNOSTIC_TOOLS:
        return "diagnostics"
    if str(tool_set or "").strip().casefold() == "unity":
        return _unity_leaf_for_name(normalized_name)
    return "diagnostics"


def build_internal_tool_block_tree(
    *,
    selector: Any = None,
    loaded_blocks: Iterable[str] = (),
    leaves: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build a compact tree with names for discovery and schemas only after load."""

    loaded = set(normalize_internal_tool_blocks(loaded_blocks))
    leaf_items = [dict(item) for item in leaves]
    directory = []
    for index, name, description in (
        *(item for item in _TOP_LEVEL if item[1] != "unity"),
        *_UNITY_CHILDREN[:5],
        *_UNITY_INTEGRATION_CHILDREN,
        *_UNITY_CHILDREN[6:],
    ):
        tool_names = sorted(
            str(item.get("name") or "").strip()
            for item in leaf_items
            if str(item.get("block") or "") == name
            and str(item.get("name") or "").strip()
        )
        directory.append(
            {
                "index": index,
                "name": name,
                "description": description,
                "loaded": name in loaded,
                "toolNames": tool_names,
                "loadCall": {
                    "skill_tool": "load_internal_tool_block",
                    "skill_params": {"block": name},
                },
            }
        )
    selected_text = str(selector or "").strip()
    if not selected_text:
        children = [
            {
                "index": index,
                "name": name,
                "description": description,
                "loaded": name in loaded,
                "expandable": name == "unity",
            }
            for index, name, description in _TOP_LEVEL
        ]
        tree: dict[str, Any] = {"index": "0", "name": "internal", "children": children}
    elif selected_text.casefold() in {"8", "unity"}:
        tree = {
            "index": "8",
            "name": "unity",
            "children": [
                {
                    "index": index,
                    "name": name,
                    "description": description,
                    "loaded": name in loaded,
                    "expandable": name == "unity/integrations",
                }
                for index, name, description in _UNITY_CHILDREN
            ],
        }
    elif selected_text.casefold() in {"8.6", "unity/integrations"}:
        tree = {
            "index": "8.6",
            "name": "unity/integrations",
            "children": [
                {
                    "index": index,
                    "name": name,
                    "description": description,
                    "loaded": name in loaded,
                    "expandable": False,
                }
                for index, name, description in _UNITY_INTEGRATION_CHILDREN
            ],
        }
    else:
        resolved = resolve_internal_tool_block_selector(selected_text)
        if not resolved:
            raise ValueError(f"Unknown internal tool block: {selected_text or 'missing'}")
        index = next(
            item[0]
            for item in (*_TOP_LEVEL, *_UNITY_CHILDREN, *_UNITY_INTEGRATION_CHILDREN)
            if item[1] == resolved
        )
        selected_leaves = sorted(
            (
                dict(item)
                for item in leaves
                if str(item.get("block") or "") == resolved
            ),
            key=lambda item: str(item.get("name") or ""),
        )
        tree = {
            "index": index,
            "name": resolved,
            "loaded": resolved in loaded,
            "tools": [
                {"index": f"{index}.{position}", **item}
                for position, item in enumerate(selected_leaves, start=1)
            ],
        }
    return {
        "ok": True,
        "schema": "vrcforge.internal_tool_blocks.v1",
        "loadedBlocks": sorted(loaded),
        "blocks": (
            [item for item in directory if item["name"].startswith("unity/")]
            if selected_text.casefold() in {"8", "unity"}
            else [
                item
                for item in directory
                if item["name"].startswith("unity/integrations/")
            ]
            if selected_text.casefold() in {"8.6", "unity/integrations"}
            else [
                item
                for item in directory
                if not selected_text
                or item["name"] == resolve_internal_tool_block_selector(selected_text)
            ]
        ),
        "tree": tree,
    }


__all__ = [
    "INTERNAL_DEFAULT_TOOL_BLOCK",
    "INTERNAL_GENERAL_TOOL_NAMES",
    "INTERNAL_LOADABLE_TOOL_BLOCKS",
    "build_internal_tool_block_tree",
    "internal_tool_block_for_name",
    "normalize_internal_tool_blocks",
    "resolve_internal_tool_block_selector",
]
