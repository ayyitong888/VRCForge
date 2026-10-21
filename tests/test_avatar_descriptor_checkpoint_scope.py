from __future__ import annotations

from pathlib import Path
import dashboard_server as server
from agent_gateway import AgentGateway


SCENE_PATH = "Assets/Avatars/FullRun.unity"


def _target(project: Path) -> dict:
    return {
        "schema": "vrcforge.execution_target.v1",
        "scope": "avatar",
        "ambiguous": False,
        "resolutionCandidateCount": 1,
        "project": {"root": str(project.resolve()), "projectId": "project-id"},
        "editor": {"unityPid": 10, "processStartTime": "start", "coreInstanceId": "core"},
        "scene": {"assetPath": SCENE_PATH, "guid": "scene-guid", "revision": "1", "digest": "scene-digest"},
        "avatar": {"globalObjectId": "avatar-global-id", "exactHierarchyPath": "Avatar"},
    }


def _arguments(project: Path, target: dict) -> dict:
    return {
        "projectPath": str(project.resolve()),
        "toolName": "vrc_write_avatar_descriptor",
        "executionTarget": target,
        "arguments": {"avatarPath": "Avatar", "lipSync": "VisemeBlendShape"},
    }


def _unity_project(tmp_path: Path) -> Path:
    project = tmp_path / "Project"
    for name in ("Assets", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True)
    scene = project / SCENE_PATH
    scene.parent.mkdir(parents=True, exist_ok=True)
    scene.write_bytes(b"saved scene bytes")
    Path(str(scene) + ".meta").write_text("guid: scene-guid\n", encoding="utf-8")
    (project / "Assets" / "Other.shader").write_bytes(b"unrelated dirty asset")
    return project


def test_descriptor_archive_scope_reaches_core_and_approval_transaction(tmp_path, monkeypatch):
    project = _unity_project(tmp_path)
    target = _target(project)
    core_prepares = []
    monkeypatch.setattr(server, "list_execution_targets_sync", lambda query: {"ok": True, "targets": [target]})

    def prepare_core(root, checkpoint_asset_paths=None, *, checkpoint_files=None):
        core_prepares.append((root, checkpoint_asset_paths, checkpoint_files))
        assert checkpoint_files == [SCENE_PATH]
        assert checkpoint_asset_paths is None
        return {"ok": True, "checkpointId": "synthetic-core-preflight"}

    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", prepare_core)
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.register_write_handler(
        "vrcforge_write_avatar_descriptor", "Descriptor write", "high",
        lambda _args: {"ok": True},
        checkpoint_prepare_handler=server.prepare_authoritative_unity_checkpoint_sync,
    )

    result = gateway.approval_transactions._create_pre_write_checkpoint(
        {"id": "approval-test", "targetTool": "vrcforge_write_avatar_descriptor"},
        _arguments(project, target),
    )

    assert result["ok"] is True, result
    assert core_prepares == [(project.resolve(), None, [SCENE_PATH])]
    assert result["archiveFiles"] == [SCENE_PATH, SCENE_PATH + ".meta"]
    assert "Assets/Other.shader" not in result["archiveFiles"]


def test_descriptor_checkpoint_rejects_stale_authoritative_scene_before_core_prepare(tmp_path, monkeypatch):
    project = _unity_project(tmp_path)
    target = _target(project)
    changed = {**target, "scene": {**target["scene"], "assetPath": "Assets/Other.unity"}}
    monkeypatch.setattr(server, "list_execution_targets_sync", lambda query: {"ok": True, "targets": [changed]})
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("stale target must not checkpoint")))

    result = server.prepare_authoritative_unity_checkpoint_sync(
        project, {**_arguments(project, target), "_checkpointTargetTool": "vrcforge_write_avatar_descriptor"}
    )

    assert result["ok"] is False
    assert result["code"] == "checkpoint_scope_unresolved"
    assert result["mutationStarted"] is False


def _method_body(source: str, class_name: str, signature: str) -> str:
    class_start = source.index(f"class {class_name}")
    method_start = source.index(signature, class_start)
    open_brace = source.index("{", method_start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace:index + 1]
    raise AssertionError(f"Unclosed method body: {class_name}.{signature}")


def test_descriptor_persistence_saves_only_the_owning_scene():
    source_path = Path("Assets/VRCForge/Editor/Generic/UnityAvatarPrimitiveCrud.cs")
    source = source_path.read_text(encoding="utf-8")
    writer = _method_body(source, "WriteAvatarDescriptorTool", "public static object HandleCommand(JObject @params)")
    component_source = Path("Assets/VRCForge/Editor/Generic/UnityComponentCrud.cs").read_text(encoding="utf-8")
    scene_save = _method_body(component_source, "ComponentCrudCore", "internal static SavedSceneSnapshot SaveAndResolveScene(")

    assert "AssetDatabase.SaveAssets(" not in writer
    assert "ComponentCrudCore.SaveAndResolveScene(beforeScene)" in writer
    assert "EditorSceneManager.SaveScene(beforeScene.Scene)" in scene_save
    assert "AssetDatabase.SaveAssets(" not in scene_save
