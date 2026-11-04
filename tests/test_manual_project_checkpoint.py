from __future__ import annotations

import zipfile
from pathlib import Path

import agent_checkpoint_recovery
from agent_gateway import AgentGateway
from external_mcp_tool_blocks import EXTERNAL_MCP_WRITE_TOOL_BLOCKS
from mcp_tool_descriptor import write_runtime_identity_required
from unity_tool_schema_projection import canonical_unity_write_tool_input_schema


def test_manual_checkpoint_archives_and_restores_project_files_including_packages(tmp_path: Path) -> None:
    project = tmp_path / "UnityProject"
    for directory in ("Assets", "Packages", "ProjectSettings"):
        (project / directory).mkdir(parents=True)
    original = {
        "Assets/asset.txt": b"original asset\n",
        "Packages/manifest.json": b'{"dependencies":{"com.example":"1.0.0"}}\n',
        "ProjectSettings/ProjectVersion.txt": b"m_EditorVersion: 2022.3.0f1\n",
    }
    for relative, content in original.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = "approval"
    gateway.save_config(config)
    tool = "vrcforge_create_project_checkpoint"
    gateway.approval_transactions.register_write_handler(
        tool,
        "Create an explicitly requested project checkpoint.",
        "medium",
        gateway.approval_transactions.create_manual_project_checkpoint,
        pre_write_checkpoint_required=False,
    )
    gateway.register_external_mcp_unity_tool(tool, "checkpoint")
    session = "external:manual-checkpoint"
    gateway._runtime_session_state.set_checkpoint_choice(
        session, {"mode": "skip", "quote": "Skip automatic checkpoints for this session"},
    )

    request = {"projectPath": str(project)}
    with gateway.resource_context(session):
        proposal = gateway.call_external_mcp_tool(tool, request)
        assert proposal["status"] == "user_confirmation_required", proposal
        receipt = gateway.call_external_mcp_tool(tool, {
            **request,
            "confirmation": {**proposal["confirmation"], "decision": "approve"},
        })
    assert receipt["ok"] is True and receipt["status"] == "executed"
    assert "checkpointSkipped" not in receipt
    result = receipt["result"]
    assert result["ok"] is True and result["status"] == "ready"
    assert result["targetTool"] == tool
    assert set(result["pathspecs"]) == {"Assets", "Packages", "ProjectSettings"}
    with zipfile.ZipFile(result["archivePath"]) as archive:
        assert {"Assets/asset.txt", "Packages/manifest.json", "ProjectSettings/ProjectVersion.txt"} <= set(archive.namelist())

    (project / "Packages/manifest.json").write_text('{"dependencies":{"com.example":"2.0.0"}}\n', encoding="utf-8")
    (project / "Assets/asset.txt").write_text("changed asset\n", encoding="utf-8")
    (project / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: changed\n", encoding="utf-8")
    (project / "Packages/new-package.txt").write_text("created after checkpoint\n", encoding="utf-8")

    service = gateway.checkpoint_recovery
    preview = service.preview_restore_checkpoint({"checkpointId": result["id"], "projectRoot": str(project)})
    assert preview["ok"] is True
    restored = service.restore_checkpoint({
        "checkpointId": result["id"], "projectRoot": str(project), "confirmRestore": True,
    })
    assert restored["ok"] is True, restored
    for relative, content in original.items():
        assert (project / relative).read_bytes() == content
    assert not (project / "Packages/new-package.txt").exists()


def test_manual_checkpoint_contract_is_application_owned_and_in_checkpoint_block() -> None:
    tool = "vrcforge_create_project_checkpoint"
    schema = canonical_unity_write_tool_input_schema(tool)
    assert schema["required"] == ["projectPath"]
    assert "executionTarget" not in schema["properties"]
    assert "checkpointChoice" not in schema["properties"]
    assert write_runtime_identity_required(tool) is False
    assert tool in EXTERNAL_MCP_WRITE_TOOL_BLOCKS["checkpoint"]


def test_manual_checkpoint_archive_failure_removes_partial_archive(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "UnityProject"
    for directory in ("Assets", "Packages", "ProjectSettings"):
        (project / directory).mkdir(parents=True)
    (project / "Packages/manifest.json").write_text("{}", encoding="utf-8")
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")

    def fail_archive_write(*_args, **_kwargs):
        raise OSError("fixture archive write failure")

    monkeypatch.setattr(agent_checkpoint_recovery.zipfile.ZipFile, "write", fail_archive_write)
    result = gateway.approval_transactions.create_manual_project_checkpoint({"projectPath": str(project)})
    assert result["ok"] is False and result["status"] == "failed"
    archive_path = Path(result["archivePath"])
    assert not archive_path.exists()
    assert not archive_path.with_suffix(".zip.tmp").exists()
