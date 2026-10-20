"""Single FX writes retain their execution shape while checkpointing exact files."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import zipfile

import pytest

from agent_approval_transactions import _checkpoint_archive_files_for_write
from agent_gateway import AgentGateway


CONTROLLER = "Assets/Generated/FX.controller"


@pytest.mark.parametrize("envelope", [None, "arguments", "params"])
def test_single_fx_scope_does_not_rewrite_execution_arguments(envelope):
    edit = {"controllerPath": CONTROLLER, "action": "update_state",
            "layerName": "Layer", "stateName": "State", "speed": 0.75}
    arguments = {envelope: edit} if envelope else edit
    original = deepcopy(arguments)
    assert _checkpoint_archive_files_for_write("vrcforge_manage_fx_animator", arguments) == [CONTROLLER]
    assert arguments == original
    assert "edits" not in edit


@pytest.mark.parametrize("mode", ["single", "batch", "null_edits", "empty_edits", "missing_motion", "missing_meta"])
def test_fx_prepare_and_archive_use_proven_scope(tmp_path, mode):
    project = tmp_path / "Project"
    target = project / CONTROLLER
    target.parent.mkdir(parents=True)
    target.write_bytes(b"controller-before")
    if mode != "missing_meta":
        Path(str(target) + ".meta").write_bytes(b"guid: controller")
    unrelated = project / "Assets/Unrelated.shader"
    unrelated.write_bytes(b"preserve-unrelated")
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3\n")
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    owner = gateway.approval_transactions
    owner._ports = replace(owner._ports, run_git=lambda *_args: {"ok": False})
    calls = []

    def prepare(root, *, checkpoint_files=None):
        calls.append((root, checkpoint_files))
        return {"ok": True, "saved": False, "mutationStarted": False}

    owner.checkpoint_prepare_handler = prepare
    arguments = {"projectPath": str(project), "controllerPath": CONTROLLER,
                 "action": "update_state", "layerName": "Layer", "stateName": "State", "speed": 0.75}
    if mode == "batch":
        arguments = {"projectPath": str(project), "controllerPath": CONTROLLER,
                     "edits": [{key: value for key, value in arguments.items()
                                if key not in {"projectPath", "controllerPath"}}]}
    elif mode == "null_edits":
        arguments["edits"] = None
    elif mode == "empty_edits":
        arguments["edits"] = []
    elif mode == "missing_motion":
        arguments["motionClipPath"] = "Assets/NewMotion.anim"
    original = deepcopy(arguments)
    exact = mode in {"single", "batch"}
    expected_scope = [CONTROLLER] if exact else None
    assert _checkpoint_archive_files_for_write("vrcforge_manage_fx_animator", arguments) == (
        [] if mode in {"null_edits", "empty_edits"} else
        sorted([CONTROLLER, "Assets/NewMotion.anim"]) if mode == "missing_motion" else [CONTROLLER]
    )
    checkpoint = owner._create_pre_write_checkpoint(
        {"id": "fx-scope-approval", "targetTool": "vrcforge_manage_fx_animator"}, arguments
    )
    assert checkpoint["ok"] is True, checkpoint
    assert calls == [(project.resolve(), expected_scope)]
    assert arguments == original
    assert ("edits" in arguments) is (mode in {"batch", "null_edits", "empty_edits"})
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        names = set(archive.namelist())
        assert archive.read(CONTROLLER) == b"controller-before"
    if exact:
        assert names == {CONTROLLER, CONTROLLER + ".meta"}
        assert set(checkpoint["archiveFiles"]) == names
    else:
        assert "archiveFiles" not in checkpoint
        assert {"Assets/Unrelated.shader", "ProjectSettings/ProjectVersion.txt"} <= names
    assert unrelated.read_bytes() == b"preserve-unrelated"
