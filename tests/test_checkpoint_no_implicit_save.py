from copy import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard_server as server
from agent_gateway import AgentGateway, AgentGatewayError


PLAY_TOOLS = (
    (
        "vrcforge_gesture_manager_enter_play_mode",
        {"avatarPath": "Avatar"},
    ),
    (
        "vrcforge_set_play_mode",
        {"isPlaying": True},
    ),
)


def _gateway(tmp_path: Path) -> AgentGateway:
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    gateway.save_config(config)
    return gateway


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "Project"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True)
    return project


def test_checkpoint_prepare_refuses_dirty_editor_state_without_saving() -> None:
    source = (
        Path(__file__).parents[1]
        / "Assets"
        / "VRCForge"
        / "Editor"
        / "CheckpointRecoveryTool.cs"
    ).read_text(encoding="utf-8")
    block = source[
        source.index("public static class CheckpointPrepareTool") :
        source.index("public static class CheckpointReloadTool")
    ]

    assert "EditorSceneManager.SaveScene" not in block
    assert "AssetDatabase.SaveAssets" not in block
    assert '? "dirty_editor_state" : ""' in block
    assert "snapshotIncludesUnsavedState = false" in block
    assert "saved = false" in block
    assert "mutationStarted = false" in block


@pytest.mark.parametrize(
    "core_info",
    ({}, {"checkpointPrepareSavesDirtyAssets": True}, {"checkpointPrepareSavesDirtyAssets": None}),
)
def test_checkpoint_prepare_blocks_core_without_explicit_non_saving_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, core_info: dict
) -> None:
    project = _project(tmp_path)
    calls: list[tuple] = []

    class Client:
        def __init__(self, root, *, timeout_seconds):
            calls.append(("client", Path(root), timeout_seconds))

        def core_info(self):
            calls.append(("core_info",))
            return core_info

    monkeypatch.setattr(server, "UnityMcpCoreClient", Client)
    monkeypatch.setattr(
        server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30),
    )
    monkeypatch.setattr(
        server,
        "invoke_unity_mcp",
        lambda *_args, **_kwargs: pytest.fail("incompatible Core reached checkpoint tool"),
    )
    monkeypatch.setattr(
        server,
        "PRIMITIVE_BASIS_LIVE_CONNECTION",
        SimpleNamespace(
            prepare_checkpoint=lambda *_args: pytest.fail(
                "incompatible Core reached primitive live checkpoint"
            )
        ),
    )

    result = server.prepare_unity_checkpoint_sync(project, checkpoint_files=["Assets/Avatar.asset"])

    assert result["ok"] is False
    assert result["blocking"] is True
    assert result["code"] == "checkpoint_core_upgrade_required"
    assert result["mutationStarted"] is False
    assert result["saved"] is False
    assert calls == [("client", project, 3.0), ("core_info",)]


def test_checkpoint_prepare_allows_explicit_non_saving_core_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    calls: list[tuple] = []

    class Client:
        def __init__(self, root, *, timeout_seconds):
            calls.append(("client", Path(root), timeout_seconds))

        def core_info(self):
            calls.append(("core_info",))
            return {"checkpointPrepareSavesDirtyAssets": False}

    monkeypatch.setattr(server, "UnityMcpCoreClient", Client)
    monkeypatch.setattr(server, "PRIMITIVE_BASIS_LIVE_CONNECTION", None)
    monkeypatch.setattr(
        server,
        "load_dashboard_settings",
        lambda _request: SimpleNamespace(unity_mcp_timeout_seconds=30),
    )
    monkeypatch.setattr(server, "build_agent_connection_request", lambda request: request)

    def invoke(_settings, tool, arguments, **kwargs):
        calls.append(("invoke", tool, arguments, kwargs))
        return {"ok": True, "sentinel": "raw"}

    monkeypatch.setattr(server, "invoke_unity_mcp", invoke)
    monkeypatch.setattr(
        server,
        "normalize_unity_checkpoint_result",
        lambda result, root: {"ok": True, "raw": result, "projectRoot": str(root)},
    )

    result = server.prepare_unity_checkpoint_sync(project, ["Assets/Avatar.asset"], checkpoint_files=["Assets/Avatar.asset"])

    assert result["ok"] is True
    assert result["raw"]["sentinel"] == "raw"
    assert calls[:2] == [("client", project, 3.0), ("core_info",)]
    assert calls[2][0:2] == ("invoke", "vrc_prepare_checkpoint")
    assert calls[2][2] == {
        "projectPath": str(project),
        "checkpointAssetPaths": ["Assets/Avatar.asset"],
        "checkpointFiles": ["Assets/Avatar.asset"],
    }


@pytest.mark.parametrize(("name", "arguments"), PLAY_TOOLS)
def test_production_play_registration_prepare_execute_never_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict
) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    gateway._write_handlers[name] = copy(server.AGENT_GATEWAY._write_handlers[name])
    gateway._write_handlers[name].verification_finalize_handler = None
    service = gateway.approval_transactions
    checkpoint_calls: list[object] = []
    transport_calls: list[tuple] = []

    monkeypatch.setattr(
        type(service),
        "_create_pre_write_checkpoint",
        lambda *_args: checkpoint_calls.append(_args) or pytest.fail("checkpoint invoked"),
    )

    def transport(*args, **kwargs):
        transport_calls.append((args, kwargs))
        return {
            "ok": True,
            "status": "done",
            "completionKnown": True,
            "mutationStarted": True,
            "mutationApplied": True,
            "committed": True,
            "commitState": "committed",
            "verified": True,
            "readback": {"verified": True},
        }

    monkeypatch.setattr(type(service), "_call_external_mcp_write_handler", transport)
    prepared = service.prepare_external_mcp_write(
        name, {"projectPath": str(project), **arguments}
    )
    result = service.execute_prepared_external_mcp_write(prepared)

    assert result["ok"] is True, result
    assert checkpoint_calls == []
    assert len(transport_calls) == 1
    assert not result.get("checkpoint")


@pytest.mark.parametrize(("name", "arguments"), PLAY_TOOLS)
def test_production_play_registration_denies_unauthorized_write_before_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict
) -> None:
    gateway = _gateway(tmp_path)
    project = _project(tmp_path)
    gateway._write_handlers[name] = copy(server.AGENT_GATEWAY._write_handlers[name])
    config = gateway.ensure_config()
    config.allow_write_requests = False
    gateway.save_config(config)
    service = gateway.approval_transactions

    monkeypatch.setattr(
        type(service),
        "_create_pre_write_checkpoint",
        lambda *_args: pytest.fail("unauthorized request reached checkpoint creation"),
    )
    monkeypatch.setattr(
        type(service),
        "_call_external_mcp_write_handler",
        lambda *_args, **_kwargs: pytest.fail("unauthorized request reached execution"),
    )

    with pytest.raises(AgentGatewayError):
        service.prepare_external_mcp_write(
            name, {"projectPath": str(project), **arguments}
        )


def test_production_safe_backup_copies_sources_without_pre_save(tmp_path, monkeypatch):
    test_production_play_registration_prepare_execute_never_checkpoints(
        tmp_path, monkeypatch, "vrcforge_create_safe_backup",
        {"assetPaths": ["Assets"], "includeOpenScenes": True, "refreshAssets": False},
    )


def test_production_safe_backup_still_requires_authorization(tmp_path, monkeypatch):
    test_production_play_registration_denies_unauthorized_write_before_checkpoint(
        tmp_path, monkeypatch, "vrcforge_create_safe_backup",
        {"assetPaths": ["Assets"], "includeOpenScenes": True, "refreshAssets": False},
    )
