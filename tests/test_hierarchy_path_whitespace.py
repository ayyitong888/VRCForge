"""Replay the reported path shape through the production request builders."""
import ast
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", [
    "Avatar/Outfit/Part ",
    " Avatar / Child ",
])
@pytest.mark.parametrize("filename,name", [
    ("dashboard_server.py", "build_gameobject_target"),
    ("dashboard_server.py", "build_component_target"),
    ("dashboard_server.py", "build_inspect_modular_avatar_component_request"),
    ("wardrobe_outfit_workflow_service.py", "build_add_modular_avatar_component_request"),
])
def test_request_builder_preserves_hierarchy_characters(filename, name, path):
    revision = os.environ.get("VRCFORGE_HIERARCHY_GIT_REF")
    source = subprocess.check_output(["git", "show", f"{revision}:{filename}"], cwd=ROOT, text=True, encoding="utf-8") if revision else (ROOT / filename).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {"Any": object}
    helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_normalize_wardrobe_bool"]
    exec(compile(ast.Module(body=[*helpers, function], type_ignores=[]), filename, "exec"), namespace)
    params = {"gameObjectPath": path, "avatarPath": " Avatar ", "componentType": "BlendshapeSync"}
    result = namespace[name](params, False) if name.startswith("build_add_") else namespace[name](params)
    observed = result[0] if isinstance(result, tuple) else result.get("gameObjectPath") if isinstance(result, dict) else result
    assert observed == path
    if isinstance(result, dict):
        assert result["avatarPath"] == " Avatar "


@pytest.mark.parametrize("tool", [
    "add_component", "remove_component", "set_property", "rename_gameobject",
    "reparent_gameobject", "delete_gameobject", "set_gameobject_active",
    "create_gameobject", "remap_skinned_mesh_bone", "unpack_prefab",
])
def test_approved_plan_keeps_exact_object_and_parent_paths(tool):
    from unity_execution_plans_scene import build_scene_execution_plan
    params = {
        "gameObjectPath": " Avatar / Child ", "componentType": "UnityEngine.Transform",
        "propertyPath": "name", "newName": "Renamed", "name": "Created",
        "parentPath": " Avatar ", "newParentPath": " Avatar / Parent ",
        "expectedCurrentBonePath": " Avatar / Bone ", "targetBonePath": " Avatar / New Bone ",
    }
    _, request = build_scene_execution_plan("vrcforge_" + tool, params)[0]
    for key in ("gameObjectPath", "parentPath", "newParentPath", "expectedCurrentBonePath", "targetBonePath"):
        if key in request:
            assert request[key] == params[key]


@pytest.mark.parametrize("tool,key", [
    ("ensure_expression_parameter", "avatarPath"),
    ("ensure_expression_menu_control", "avatarPath"),
    ("ensure_animator_state", "avatarPath"),
    ("select_scene_object", "gameObjectPath"),
    ("instantiate_prefab", "parentPath"),
    ("toggle_scene_object", "objectPath"),
    ("gesture_manager_set_parameter", "avatarPath"),
    ("gesture_manager_enter_play_mode", "avatarPath"),
])
def test_remaining_scene_plans_preserve_reported_path(tool, key):
    from unity_execution_plans_scene import build_scene_execution_plan
    path = "Avatar/Outfit/Part "
    _, request = build_scene_execution_plan("vrcforge_" + tool, {key: path})[0]
    assert request[key] == path


@pytest.mark.parametrize("handler,key", [
    ("select_scene_object_sync", "gameObjectPath"),
    ("toggle_scene_object_sync", "objectPath"),
    ("gesture_manager_set_parameter_sync", "avatarPath"),
    ("gesture_manager_enter_play_mode_sync", "avatarPath"),
])
def test_select_scene_object_handler_preserves_reported_path(handler, key):
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8-sig")
    function = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == handler)
    calls = []
    namespace = {
        "Any": object,
        "load_dashboard_settings": lambda value: value,
        "build_agent_connection_request": lambda value: value,
        "ensure_dict_payload": lambda value, *args: value,
        "extract_tool_result_payload": lambda value: value,
        "invoke_unity_mcp": lambda settings, tool, arguments, **kwargs: calls.append(arguments) or {"ok": True},
        "toggle_scene_object_direct": lambda settings, path, active, save: calls.append({"objectPath": path}) or {"ok": True},
        "emit_log": lambda *args: None,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "dashboard_server.py", "exec"), namespace)
    path = "Avatar/Outfit/Part "
    namespace[handler]({key: path, "active": False, "parameterName": "VelocityX", "value": 1})
    assert calls[0][key] == path
