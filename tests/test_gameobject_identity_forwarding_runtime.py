"""Execute public Python callback and exact C# creation statement, without Unity writes."""
import ast
import os
from pathlib import Path
import subprocess

import pytest

from test_constraint_conversion_scope_runtime import run

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    revision = os.environ.get("VRCFORGE_GAMEOBJECT_GIT_REF")
    return (subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT,
        text=True, encoding="utf-8") if revision else (ROOT/path).read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("preview", [False, True])
def test_unpack_public_callback_preserves_identity_guards(preview):
    module = ast.parse(read("dashboard_server.py"))
    callback = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "unpack_prefab_sync")
    calls = []
    env = {"Any": object, "build_gameobject_target": lambda p: p["gameObjectPath"],
        "build_agent_connection_request": lambda p: p, "load_dashboard_settings": lambda p: {},
        "invoke_unity_mcp": lambda settings, tool, request: calls.append((tool, request)) or {"ok": False, "error": "identity mismatch"},
        "extract_tool_result_payload": lambda p: p, "ensure_dict_payload": lambda p, label: p,
        "emit_log": lambda *args: None}
    exec(compile(ast.Module(body=[callback], type_ignores=[]), "dashboard_server.py", "exec"), env)
    identities = {"expectedGlobalObjectId": "object-before", "expectedPrefabGuid": "prefab-before",
        "expectedAssetDependencyHash": "dependency-before", "expectedScenePath": "Assets/Before.unity",
        "approvedObjectReceiptNonce": "a" * 64}
    params = {"projectPath": "D:/Example", "gameObjectPath": "Avatar/Clothing", "preview": preview, **identities}
    result = env["unpack_prefab_sync"](params)
    assert result["ok"] is False
    assert calls[0][0] == "vrc_unpack_prefab"
    for key, value in identities.items():
        assert calls[0][1].get(key) == value, f"Public callback discarded {key}"
    assert calls[0][1]["preview"] is preview
    env["unpack_prefab_sync"]({"gameObjectPath": "Avatar/Clothing"})
    assert not any(key in calls[1][1] for key in identities), "optional guards were invented"


def test_prefab_is_created_in_resolved_parent_scene(tmp_path):
    source = read("Assets/VRCForge/Editor/Generic/UnityAssetPrefabCrud.cs")
    source = source.split("public static class InstantiatePrefabTool", 1)[1].split("public static class UnpackPrefabTool", 1)[0]
    start = source.index("var instance = PrefabUtility.InstantiatePrefab(")
    creation = source[start:source.index(";", start) + 1]
    run(tmp_path, r'''
      using System;
      class Scene { public string name; public int creations; }
      class GameObject { public Scene scene; }
      class PrefabUtility {
        public static Scene active = new Scene { name = "UnrelatedActive" };
        // Unity 2022.3 documented default overload creates in active scene.
        public static object InstantiatePrefab(object asset) { return InstantiatePrefab(asset, active); }
        public static object InstantiatePrefab(object asset, Scene scene) { scene.creations++; return new GameObject { scene = scene }; }
      }
      class Probe {
        static GameObject Create(Scene scene) { object asset = new object();
    ''' + creation + r'''
          return instance;
        }
        static int Main() {
          var parentScene = new Scene { name = "SelectedParentScene" };
          var created = Create(parentScene);
          if(created.scene != parentScene || parentScene.creations != 1 || PrefabUtility.active.creations != 0)
            throw new Exception("Prefab created in unrelated active scene instead of resolved parent scene");
          var root = Create(PrefabUtility.active);
          if(root.scene != PrefabUtility.active) throw new Exception("root creation changed");
          return 0;
        }
      }
    ''')
