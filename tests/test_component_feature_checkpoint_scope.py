"""Component feature writes bind their frozen Core call to one scene checkpoint."""
from copy import deepcopy
from dataclasses import replace
import zipfile

import pytest

from test_agent_checkpoint_recovery_service import _gateway
from test_component_feature_write import toggle_request, preview_payload, wrapper
from test_scene_workflow_checkpoint_scope import _target
from component_feature_write import bind_authoritative_preview, compute_preview_digest


TOOL = "vrcforge_create_component_feature"


@pytest.fixture
def case(tmp_path, monkeypatch):
    import dashboard_server as server

    root = tmp_path / "project"
    for folder in ("Assets", "Packages", "ProjectSettings"):
        (root / folder).mkdir(parents=True)
    for name in ("Assets/Main.unity", "Assets/Main.unity.meta", "Assets/Unrelated.mat", "Assets/Unrelated.mat.meta"):
        (root / name).write_text(name, encoding="utf-8")
    target = _target(root, "object")
    target["scene"]["guid"] = "1" * 32
    target["object"]["globalObjectId"] = "GlobalObjectId_V1-2-300-0-0"
    raw = toggle_request()
    raw.update(scenePath="Assets/Main.unity", gameObjectPath="Avatar/Outfit")
    preview = preview_payload(raw)
    preview["projectPath"] = str(root).replace("\\", "/")
    preview["scene"]["path"] = "Assets/Main.unity"
    preview["scene"]["guid"] = target["scene"]["guid"]
    preview["host"]["objectId"] = target["object"]["globalObjectId"]
    preview["previewDigest"] = compute_preview_digest(preview)
    source = wrapper(raw)
    source["projectPath"] = str(root).replace("\\", "/")
    bound, _approval = bind_authoritative_preview(source, preview)
    request = {**bound, "executionTarget": target}
    observed = []
    monkeypatch.setattr(server, "list_execution_targets_sync",
                        lambda query: observed.append(("discover", deepcopy(query))) or
                        {"ok": True, "targets": [deepcopy(target)]})
    monkeypatch.setattr(server, "prepare_unity_checkpoint_sync",
                        lambda project, **kwargs: observed.append(("prepare", project, kwargs)) or {"ok": True})
    return server, root, target, request, observed


def test_registered_component_feature_checkpoint_archives_and_restores_only_scene(case, tmp_path):
    server, root, target, request, observed = case
    original_request = deepcopy(request)
    handler = server.AGENT_GATEWAY._write_handlers[TOOL]
    core_plan = handler.approved_execution_plan_builder(request)
    assert core_plan == [("vrc_create_component_feature", request["arguments"])]
    prepared = handler.checkpoint_prepare_handler(root, {**request, "_checkpointTargetTool": TOOL})
    assert prepared["ok"] is True, prepared
    assert prepared["archiveAssetPaths"] == ["Assets/Main.unity"]
    assert observed[-1] == ("prepare", root, {"checkpoint_files": ["Assets/Main.unity"]})
    assert request == original_request

    gateway = _gateway(tmp_path / "app")
    service = gateway.approval_transactions
    service.register_write_handler(TOOL, "component feature", "medium", lambda _: None,
                                   checkpoint_prepare_handler=handler.checkpoint_prepare_handler)
    service._ports = replace(service._ports, run_git=lambda *a, **k:
                             {"ok": False, "returncode": 1, "stdout": "", "error": "not git"})
    checkpoint = service._create_pre_write_checkpoint(
        {"id": "approval", "targetTool": TOOL},
        {**request, "archiveFiles": ["Assets/Unrelated.mat"]})
    assert checkpoint["ok"] is True, checkpoint
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert set(archive.namelist()) == {"Assets/Main.unity", "Assets/Main.unity.meta"}
    (root / "Assets/Main.unity").write_text("changed", encoding="utf-8")
    (root / "Assets/Unrelated.mat").write_text("preserve", encoding="utf-8")
    assert gateway.checkpoint_recovery._restore_archive_checkpoint(checkpoint)["ok"] is True
    assert (root / "Assets/Main.unity").read_text(encoding="utf-8") == "Assets/Main.unity"
    assert (root / "Assets/Unrelated.mat").read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize("kind", ["project", "scene_path", "scene_guid", "host", "fresh_scene", "fresh_host"])
def test_component_feature_checkpoint_rejects_scene_or_host_identity_drift(case, monkeypatch, kind):
    server, root, target, request, observed = case
    if kind == "project":
        request["arguments"]["expectedProjectPath"] = str(root / "Other")
    elif kind == "scene_path":
        request["arguments"]["scenePath"] = "Assets/Other.unity"
    elif kind == "scene_guid":
        request["arguments"]["expectedSceneGuid"] = "f" * 32
    elif kind == "host":
        request["arguments"]["expectedHostObjectId"] = "other-object"
    elif kind == "fresh_scene":
        fresh = deepcopy(target)
        fresh["scene"]["guid"] = "f" * 32
        monkeypatch.setattr(server, "list_execution_targets_sync", lambda _: {"ok": True, "targets": [fresh]})
    else:
        fresh = deepcopy(target)
        fresh["object"]["globalObjectId"] = "other-object"
        monkeypatch.setattr(server, "list_execution_targets_sync", lambda _: {"ok": True, "targets": [fresh]})
    result = server.AGENT_GATEWAY._write_handlers[TOOL].checkpoint_prepare_handler(
        root, {**request, "_checkpointTargetTool": TOOL})
    assert result["ok"] is False
    assert "archiveAssetPaths" not in result
    assert not any(call[0] == "prepare" for call in observed)
