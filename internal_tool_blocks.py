"""The single canonical Tool Tree used by internal and external Agents.

Tool definitions and handlers remain in their existing registries.  This
module owns only routing metadata and compatibility aliases; projections apply
mode/permission filtering after a leaf is lazily loaded.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping
from agent_memory_tool_contract import MEMORY_TOOL_NAMES


INTERNAL_DEFAULT_TOOL_BLOCK = "core"

ROUTING_FIELDS = ("useWhen", "doNotUse", "provides", "planningExposure", "executionExposure")


def _routing(use_when: tuple[str, ...], do_not_use: tuple[str, ...], provides: tuple[str, ...], planning: str, execution: str) -> dict[str, Any]:
    return {
        "useWhen": use_when,
        "doNotUse": do_not_use,
        "provides": provides,
        "planningExposure": planning,
        "executionExposure": execution,
    }


# Parent nodes are routing-only.  Leaf IDs intentionally remain stable and
# may contain the older registry names so existing lazy-load callers continue
# to work while the first level has exactly six capability categories.
CANONICAL_TOOL_BLOCKS: dict[str, dict[str, Any]] = {
    "avatar_structure": {"title": "Avatar Structure", "children": ("hierarchy_components", "rig_constraints", "mesh_shape_data"), "routing": _routing(("hierarchy, rig, mesh, bones, constraints, or structural compatibility",), ("materials, animation logic, files, web research, builds",), ("avatar identity, hierarchy, components, rig and mesh inspection",), "read, inspect, and preview only", "approved structural writes only")},
    "appearance": {"title": "Appearance", "children": ("renderers", "materials_shaders", "textures_visual_properties"), "routing": _routing(("materials, shaders, textures, renderers, lighting, visual seams, or a solid-color/full-red/opaque-overlay artifact even when an expression triggers it",), ("rig, mesh topology, Animator control logic, files, packages, builds",), ("visual state inspection, comparison, and previews",), "read, inspect, and preview only", "approved appearance writes only")},
    "behavior": {"title": "Behavior", "children": ("animator_clips_bindings", "parameters_menus_layers", "face_eye_lipsync", "interaction_generated_systems"), "routing": _routing(("Animator, FX, parameters, menus, viseme, eye-look, or interaction control behavior",), ("mesh geometry, shader tuning, or visible solid-color/full-red/opaque-overlay artifacts; route those to Appearance even if an expression triggers them",), ("control graphs, bindings, and behavior previews",), "read, inspect, and preview only", "approved behavior writes only")},
    "project_environment": {"title": "Project & Environment", "children": ("assets_packages", "files", "shell"), "routing": _routing(("project files, packages, assets, dependencies, or controlled local tooling",), ("live Avatar mutation, materials, Animator authoring, web research, canonical builds",), ("local project inspection, package/asset inventory, bounded files, and profiled commands",), "read, inspect, and preview only", "approved file writes and controlled commands only")},
    "diagnostics_build": {"title": "Diagnostics & Build", "children": ("compile_logs", "validation_performance", "checkpoints_history", "build_runtime"), "routing": _routing(("compile errors, validation, performance, checkpoints, builds, exports, or verification",), ("ordinary Avatar edits, raw file maintenance, material tuning, web research",), ("diagnostics, validation, recovery, build previews, and receipts",), "diagnostics, validation, history, and previews only", "approved recovery, test, build, or upload actions")},
    "research": {"title": "Documentation & Web Research", "children": ("web_research",), "routing": _routing(("official documentation, current compatibility, tutorials, examples, or cited web evidence",), ("local installed state, project files, commands, or Unity mutation",), ("read-only external sources with provenance",), "read-only search and retrieval", "read-only search and retrieval")},
}

CANONICAL_TOOL_LEAVES: dict[str, dict[str, Any]] = {
    "avatar_structure/hierarchy_components": {"title": "Hierarchy & Components", "routing": _routing(("locate an Avatar, GameObject, or Component and inspect its exact stable identity",), ("materials, animation graphs, project files, or builds",), ("Avatar roots, hierarchy, components, and stable target evidence",), "read, inspect, and preview only", "approved hierarchy/component writes only")},
    "avatar_structure/rig_constraints": {"title": "Rig & Constraints", "routing": _routing(("inspect or change bones, humanoid rig mappings, constraints, or PhysBones",), ("materials, textures, menus, files, or builds",), ("rig, bone, constraint, and physics-component operations",), "read, inspect, and preview only", "approved rig or constraint writes only")},
    "avatar_structure/mesh_shape_data": {"title": "Mesh & Shape Data", "routing": _routing(("inspect meshes, blendshapes, bounds, or vertex-level compatibility evidence",), ("shader tuning, Animator logic, files, or package management",), ("mesh and blendshape inspection plus bounded structural operations",), "read, inspect, and preview only", "approved mesh/shape writes only")},
    "appearance/renderers": {"title": "Renderers", "routing": _routing(("inspect Renderer state, diagnose a wrong material assignment or opaque overlay, or replace one verified material slot",), ("mesh topology, Animator control logic, raw file edits, or builds",), ("renderer identities, slot inventories, previews, and exact slot writes",), "read, inspect, and preview only", "approved renderer writes with fresh readback")},
    "appearance/materials_shaders": {"title": "Materials & Shaders", "routing": _routing(("inspect or tune materials, shaders, render queues, transparency, or a face/body that becomes solid red or opaque",), ("rigging, menus, project files, or builds",), ("material/shader evidence and bounded material operations",), "read, inspect, and preview only", "approved material writes with fresh readback")},
    "appearance/textures_visual_properties": {"title": "Textures & Visual Properties", "routing": _routing(("inspect textures or change bounded visual properties such as color, masks, or dissolve inputs",), ("hierarchy, Animator graph structure, shell commands, or builds",), ("texture metadata, visual-property previews, and exact patches",), "read, inspect, and preview only", "approved visual-property writes only")},
    "behavior/animator_clips_bindings": {"title": "Animator, Clips & Bindings", "routing": _routing(("inspect or author Animator state machines, clips, transitions, or animation bindings",), ("mesh geometry, shader tuning, files, or package installation",), ("Animator graphs, clips, bindings, and transition operations",), "read, inspect, and preview only", "approved animation writes only")},
    "behavior/parameters_menus_layers": {"title": "Parameters, Menus & Layers", "routing": _routing(("inspect or modify expression parameters, menus, playable layers, or wardrobe controls",), ("mesh, materials, files, or web research",), ("parameter budgets, menu controls, layers, and wardrobe behavior",), "read, inspect, and preview only", "approved parameter/menu/layer writes only")},
    "behavior/face_eye_lipsync": {"title": "Face, Eye & Lip Sync", "routing": _routing(("inspect or migrate face tracking, eye look, visemes, lip sync, or facial-expression control/bindings",), ("a facial expression merely triggers a solid-red, solid-color, opaque-overlay, transparency, render-queue, shader, or material-slot artifact; use Appearance/Renderers or Appearance/Materials & Shaders",), ("facial behavior evidence and bounded migration operations",), "read, inspect, and preview only", "approved face/eye/lip-sync writes only")},
    "behavior/interaction_generated_systems": {"title": "Interactions & Generated Systems", "routing": _routing(("inspect or maintain Modular Avatar, VRCFury, Gesture Manager, contacts, toggles, or generated systems",), ("plain mesh editing, shader tuning, raw files, or web-only research",), ("integration-aware behavior and generated-system operations",), "read, inspect, and preview only", "approved integration-backed writes only")},
    "project_environment/assets_packages": {"title": "Assets & Packages", "routing": _routing(("inspect project assets, prefabs, packages, dependencies, or imports",), ("live Avatar component edits, material tuning, or arbitrary shell commands",), ("asset/package inventory and bounded project operations",), "read, inspect, and preview only", "approved asset/package writes only")},
    "project_environment/files": {"title": "Files", "routing": _routing(("read, search, or patch ordinary project files through a bounded root and relative path",), ("Unity serialized scenes, prefabs, materials, arbitrary host paths, or command execution",), ("bounded file reads and atomic modular patch sets",), "read and search only", "approved atomic file writes only")},
    "project_environment/shell": {"title": "Shell", "routing": _routing(("run a profiled local observation or a specifically approved command needed by the workflow",), ("a purpose-built Tool exists, the command is unbounded, or planning mode would mutate state",), ("classified commands, owned processes, and captured command receipts",), "observation profiles only", "approved controlled commands only")},
    "diagnostics_build/compile_logs": {"title": "Compile & Logs", "routing": _routing(("inspect compile errors, Console evidence, service health, or runtime logs",), ("ordinary Avatar edits, material tuning, or file mutation",), ("compile, log, connector, and runtime diagnostics",), "read-only diagnostics", "read-only diagnostics")},
    "diagnostics_build/validation_performance": {"title": "Validation & Performance", "routing": _routing(("validate an Avatar, inspect performance, compare screenshots, or verify a completed operation",), ("unverified mutation, package installation, or web research",), ("validation findings, visual evidence, and performance analysis",), "read-only validation and previews", "approved optimization writes only")},
    "diagnostics_build/checkpoints_history": {"title": "Checkpoints & History", "routing": _routing(("create or inspect recovery evidence, checkpoints, operation history, or receipts",), ("automatic rollback without approval or ordinary editing",), ("checkpoints, history, receipts, and explicit recovery",), "read history and preview recovery", "approved checkpoint or restore actions only")},
    "diagnostics_build/build_runtime": {"title": "Build & Runtime", "routing": _routing(("build, export, upload, enter runtime tests, or inspect their results",), ("ordinary authoring before validation or planning-only mode",), ("build/runtime previews, execution, and receipts",), "build and runtime previews only", "approved build, export, upload, or runtime actions")},
    "research/web_research": {"title": "Documentation & Web Research", "routing": _routing(("find official documentation, current compatibility notes, tutorials, or cited community evidence",), ("local installed state, project files, shell commands, or Unity mutation",), ("read-only external sources with URLs and provenance",), "read-only search and retrieval", "read-only search and retrieval")},
}

CANONICAL_TOOL_BLOCK_ALIASES = {
    "project": "project_environment/assets_packages",
    "avatar": "avatar_structure/hierarchy_components",
    "assets": "project_environment/assets_packages",
    "materials": "appearance/materials_shaders",
    "integrations": "behavior/interaction_generated_systems",
    "optimization": "diagnostics_build/validation_performance",
    "checkpoint": "diagnostics_build/checkpoints_history",
    "encryption": "behavior/interaction_generated_systems",
    "skills": "project_environment/files",
    "web": "research/web_research",
    "files": "project_environment/files",
    "shell": "project_environment/shell",
    "diagnostics": "diagnostics_build/compile_logs",
    "integrations/modular-avatar": "behavior/interaction_generated_systems",
    "integrations/vrcfury": "behavior/interaction_generated_systems",
    "integrations/gesture-manager": "behavior/interaction_generated_systems",
    "skills/vsk": "project_environment/files",
    "skills/installed": "project_environment/files",
    "unity/project": "project_environment/assets_packages",
    "unity/avatar": "avatar_structure/hierarchy_components",
    "unity/assets": "project_environment/assets_packages",
    "unity/materials": "appearance/materials_shaders",
    "unity/integrations": "behavior/interaction_generated_systems",
    "unity/optimization": "diagnostics_build/validation_performance",
    "unity/checkpoint": "diagnostics_build/checkpoints_history",
    "unity/diagnostics": "diagnostics_build/compile_logs",
    "unity/encryption": "behavior/interaction_generated_systems",
}


def canonical_external_block(value: Any) -> str:
    """Map legacy gateway block IDs to one canonical leaf/root ID."""
    raw = str(value or "").strip().casefold()
    if raw in CANONICAL_TOOL_BLOCKS:
        return raw
    return canonical_leaf_id(raw)


def canonical_block_routing(value: Any) -> dict[str, Any]:
    block = canonical_external_block(value)
    if block in CANONICAL_TOOL_LEAVES:
        return dict(CANONICAL_TOOL_LEAVES[block]["routing"])
    root = block.split("/", 1)[0]
    return dict(CANONICAL_TOOL_BLOCKS.get(root, {}).get("routing", {}))


def canonical_tool_tree() -> dict[str, Any]:
    """Return a JSON-safe copy of the routing-only canonical block graph."""
    roots = []
    for block_id, spec in CANONICAL_TOOL_BLOCKS.items():
        roots.append({"id": block_id, "parentId": None, "depth": 1, "title": spec["title"], "childBlockIds": list(spec["children"]), "ownedToolNames": [], "routingDescription": dict(spec["routing"])})
        for leaf in spec["children"]:
            leaf_id = f"{block_id}/{leaf}"
            leaf_spec = CANONICAL_TOOL_LEAVES[leaf_id]
            roots.append({"id": leaf_id, "parentId": block_id, "depth": 2, "title": leaf_spec["title"], "childBlockIds": [], "ownedToolNames": [], "routingDescription": dict(leaf_spec["routing"])})
    return {"schema": "vrcforge.canonical_tool_tree.v1", "roots": roots}


def canonical_leaf_id(value: Any) -> str:
    value = str(value or "").strip().casefold()
    return CANONICAL_TOOL_BLOCK_ALIASES.get(value, value)


def canonical_tool_block_description(block_id: str) -> str:
    canonical = canonical_leaf_id(block_id)
    spec = CANONICAL_TOOL_LEAVES.get(canonical) or CANONICAL_TOOL_BLOCKS.get(canonical)
    if not spec:
        return ""
    route = spec["routing"]
    return "Use when: {}. Do not use when: {}. Provides: {} Planning: {} Execution: {}.".format(
        " / ".join(route["useWhen"]), " / ".join(route["doNotUse"]), " / ".join(route["provides"]), route["planningExposure"], route["executionExposure"]
    )


def canonical_tool_owner(block: str, name: str) -> str:
    # Internal Unity projections namespace the same legacy external block.
    # Remove that transport prefix before applying its routing semantics.
    block = str(block or "").strip().casefold().removeprefix("unity/")
    canonical_block = canonical_external_block(block)
    if str(block or "").strip().casefold() in CANONICAL_TOOL_LEAVES:
        return canonical_block
    raw_block = str(block or "").strip().casefold()
    lowered = str(name or "").casefold()
    # A legacy Skill leaf already has a precise canonical owner. Its package
    # wording must not reroute it into generic asset/package management.
    if raw_block.startswith("skills/") and canonical_block in CANONICAL_TOOL_LEAVES:
        return canonical_block
    if "renderer_material_slot" in lowered or "renderer" in lowered:
        return "appearance/renderers"
    if "texture" in lowered:
        return "appearance/textures_visual_properties"
    if "material" in lowered or "shader" in lowered:
        return "appearance/materials_shaders"
    if any(token in lowered for token in ("vrcfury", "modular_avatar", "gesture", "contact", "interaction", "generated")) or raw_block in {"integrations", "encryption", "skills"}:
        return "behavior/interaction_generated_systems"
    if any(token in lowered for token in ("face", "viseme", "eye_look", "lipsync", "lip_sync")):
        return "behavior/face_eye_lipsync"
    if any(token in lowered for token in ("animat", "clip", "binding", "transition", "state_machine")):
        return "behavior/animator_clips_bindings"
    if "parameter" in lowered or "menu" in lowered or "playable_layer" in lowered or "wardrobe" in lowered or "outfit" in lowered:
        return "behavior/parameters_menus_layers"
    if "mesh" in lowered or "blendshape" in lowered or "bounds" in lowered:
        return "avatar_structure/mesh_shape_data"
    if "bone" in lowered or "constraint" in lowered or "physbone" in lowered or "rig" in lowered:
        return "avatar_structure/rig_constraints"
    if "hierarchy" in lowered or "gameobject" in lowered or "component" in lowered or "avatar" in lowered:
        return "avatar_structure/hierarchy_components"
    if raw_block in {"files", "project", "assets", "assets_packages"} or any(token in lowered for token in ("package", "asset", "prefab", "vpm", "project")):
        return "project_environment/files" if raw_block == "files" else "project_environment/assets_packages"
    if raw_block == "shell" or "shell" in lowered:
        return "project_environment/shell"
    if raw_block in {"web", "web_research"}:
        return "research/web_research"
    if "skill" in lowered:
        return "project_environment/files"
    if raw_block == "checkpoint" or any(token in lowered for token in ("checkpoint", "backup", "restore", "history", "receipt")):
        return "diagnostics_build/checkpoints_history"
    if any(token in lowered for token in ("build", "upload", "export", "runtime", "play_mode")):
        return "diagnostics_build/build_runtime"
    if raw_block == "optimization" or any(token in lowered for token in ("validation", "optim", "performance", "screenshot", "vision")):
        return "diagnostics_build/validation_performance"
    if raw_block in {"diagnostics", "compile_logs", "core"} or any(token in lowered for token in ("compile", "console", "health", "log")):
        return "diagnostics_build/compile_logs"
    if raw_block == "avatar":
        return "avatar_structure/hierarchy_components"
    return "diagnostics_build/validation_performance"

INTERNAL_LOADABLE_TOOL_BLOCKS = frozenset(
    {f"{root}/{leaf}" for root, spec in CANONICAL_TOOL_BLOCKS.items() for leaf in spec["children"]}
    | {INTERNAL_DEFAULT_TOOL_BLOCK}
)

_SELECTORS = {name.casefold(): name for name in INTERNAL_LOADABLE_TOOL_BLOCKS}
_SELECTORS.update({alias.casefold(): target for alias, target in CANONICAL_TOOL_BLOCK_ALIASES.items()})

_CORE_TOOLS = frozenset(
    {
        # Basic bounded reads need no discovery round trip. Their handlers
        # still enforce the same path authorization and project boundaries.
        "vrcforge_list_directory",
        "vrcforge_read_text_file",
        "vrcforge_read_tool_result",
        "vrcforge_find_files",
        "vrcforge_search_text",
        "vrcforge_web_fetch",
        "vrcforge_web_search",
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
        "vrcforge_exit_skill",
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
        "vrcforge_list_installed_skills",
        "vrcforge_list_user_unity_tools",
        "vrcforge_invoke_user_unity_tool",
        "vrcforge_read_installed_skill",
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
    raw = str(value or "").strip().casefold()
    if raw in CANONICAL_TOOL_BLOCK_ALIASES:
        return canonical_leaf_id(raw)
    return _SELECTORS.get(raw, "")


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
    return canonical_tool_owner("avatar", name)


def internal_tool_block_for_name(name: str, tool_set: str) -> str:
    """Classify one internal model tool without changing handler ownership."""

    normalized_name = str(name or "").strip()
    if normalized_name in _CORE_TOOLS or normalized_name in MEMORY_TOOL_NAMES:
        return "core"
    if normalized_name in _FILE_TOOLS:
        return "project_environment/files"
    if normalized_name in _WEB_TOOLS:
        return "research/web_research"
    if normalized_name in _DESKTOP_TOOLS:
        return "project_environment/shell"
    if normalized_name in _SHELL_TOOLS:
        return "project_environment/shell"
    if normalized_name in _ATTACHMENT_TOOLS:
        return "diagnostics_build/validation_performance"
    if normalized_name in _DIAGNOSTIC_TOOLS:
        return canonical_tool_owner("diagnostics", normalized_name)
    if str(tool_set or "").strip().casefold() == "unity":
        return _unity_leaf_for_name(normalized_name)
    return "diagnostics_build/compile_logs"


def build_internal_tool_block_tree(
    *,
    selector: Any = None,
    loaded_blocks: Iterable[str] = (),
    leaves: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build a compact tree with names for discovery and schemas only after load."""

    # The public tree is canonical and intentionally contains no Tool schemas.
    # Legacy block names are accepted as selectors and mapped to their owner
    # leaf, so existing lazy-load calls remain valid during the transition.
    loaded = set(normalize_internal_tool_blocks(loaded_blocks))
    leaf_items = [dict(item) for item in leaves]
    canonical_leaves: dict[str, list[dict[str, Any]]] = {f"{root}/{leaf}": [] for root, spec in CANONICAL_TOOL_BLOCKS.items() for leaf in spec["children"]}
    for item in leaf_items:
        name = str(item.get("name") or "").strip()
        owner = canonical_tool_owner(str(item.get("block") or ""), name)
        if owner in canonical_leaves and name:
            canonical_leaves[owner].append(item)
    selected = canonical_leaf_id(selector) if selector is not None and str(selector).strip() else ""
    if selected and selected not in canonical_leaves and selected not in CANONICAL_TOOL_BLOCKS:
        # Preserve the old nested Unity index/detail selectors for callers not
        # yet migrated; the canonical root remains the default first level.
        selected = canonical_leaf_id(selected)
    children = []
    for root_id, spec in CANONICAL_TOOL_BLOCKS.items():
        children.append({
            "id": root_id,
            "name": root_id,
            "title": spec["title"],
            "depth": 1,
            "description": canonical_tool_block_description(root_id),
            "loaded": any(f"{root_id}/{leaf}" in loaded for leaf in spec["children"]),
            "expandable": True,
            "children": [
                {
                    "id": f"{root_id}/{leaf}", "name": f"{root_id}/{leaf}", "depth": 2,
                    "title": CANONICAL_TOOL_LEAVES[f"{root_id}/{leaf}"]["title"],
                    "description": canonical_tool_block_description(f"{root_id}/{leaf}"),
                    "loaded": f"{root_id}/{leaf}" in loaded,
                    "toolNames": [str(item.get("name")) for item in sorted(canonical_leaves[f"{root_id}/{leaf}"], key=lambda v: str(v.get("name") or ""))],
                    "loadCall": {"skill_tool": "load_internal_tool_block", "skill_params": {"block": f"{root_id}/{leaf}"}},
                }
                for leaf in spec["children"]
            ],
        })
    tree: dict[str, Any] = {"index": "0", "name": "internal", "children": children}
    if selected in CANONICAL_TOOL_BLOCKS:
        tree = next(item for item in children if item["name"] == selected)
    if selected in canonical_leaves:
        tree = {"index": selected, "name": selected, "depth": 2, "loaded": selected in loaded, "description": canonical_tool_block_description(selected), "tools": [{"index": f"{selected}.{i}", **item} for i, item in enumerate(sorted(canonical_leaves[selected], key=lambda v: str(v.get("name") or "")), 1)]}
    return {"ok": True, "schema": "vrcforge.internal_tool_blocks.v1", "loadedBlocks": sorted(loaded), "blocks": children, "tree": tree}


__all__ = [
    "INTERNAL_DEFAULT_TOOL_BLOCK",
    "INTERNAL_GENERAL_TOOL_NAMES",
    "INTERNAL_LOADABLE_TOOL_BLOCKS",
    "CANONICAL_TOOL_BLOCKS",
    "CANONICAL_TOOL_LEAVES",
    "build_internal_tool_block_tree",
    "canonical_block_routing",
    "canonical_external_block",
    "canonical_tool_owner",
    "internal_tool_block_for_name",
    "normalize_internal_tool_blocks",
    "resolve_internal_tool_block_selector",
]
