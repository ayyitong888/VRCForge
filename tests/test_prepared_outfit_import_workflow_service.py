from __future__ import annotations

from dataclasses import fields
import inspect
from pathlib import Path

import pytest

import dashboard_server
from agent_gateway import AgentGateway, AgentGatewayError
from prepared_outfit_import_workflow_service import (
    PreparedOutfitImportApprovedWritePorts,
    PreparedOutfitImportApprovedWriteService,
    PreparedOutfitImportPreparer,
    PreparedOutfitImportPreparerPorts,
)
from prepared_unity_execution import PREPARED_UNITY_EXECUTION_ARGUMENT_KEY


def test_typed_ports_expose_only_fixed_import_capabilities() -> None:
    preparer_fields = {field.name for field in fields(PreparedOutfitImportPreparerPorts)}
    approved_fields = {
        field.name for field in fields(PreparedOutfitImportApprovedWritePorts)
    }
    assert {
        "plan_outfit_import",
        "resolve_project_root",
        "capture_project_identity",
        "prepare_loose_import",
        "prepare_zip_member",
        "temp_parent",
    } <= preparer_fields
    assert {
        "execute_loose_import",
        "execute_zip_member",
        "cleanup_zip_member",
        "start_import",
        "poll_import",
        "refresh_assets",
    } <= approved_fields
    forbidden = {
        "host",
        "gateway",
        "invoke_unity_mcp",
        "tool_name",
        "create_approval",
        "create_checkpoint",
        "execute_shell",
    }
    assert preparer_fields.isdisjoint(forbidden)
    assert approved_fields.isdisjoint(forbidden)
    source = inspect.getsource(
        __import__("prepared_outfit_import_workflow_service")
    )
    assert "dashboard_server" not in source
    assert "agent_gateway" not in source
    assert "invoke_unity_mcp" not in source


def test_reserved_key_is_rejected_before_any_read_or_temp_creation(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    temp_parent = tmp_path / "temp"
    preparer = PreparedOutfitImportPreparer(
        PreparedOutfitImportPreparerPorts(
            plan_outfit_import=lambda _arguments: calls.append("plan") or {},
            plan_error_type=RuntimeError,
            map_plan_error=lambda exc: exc,
            resolve_project_root=lambda _arguments, _plan: (_ for _ in ()).throw(
                AssertionError("project resolver must not run")
            ),
            capture_project_identity=lambda _path: {},
            capture_regular_file=lambda _path, _label: ({}, ""),
            capture_directory=lambda _path, _label: {},
            prepare_loose_import=lambda **_kwargs: {},
            prepare_zip_member=lambda **_kwargs: {},
            normalize_archive_name=str,
            digest=lambda _value: "",
            ensure_dict=lambda value, _label: value if isinstance(value, dict) else {},
            nonce_hex=lambda _size: "0" * 32,
            temp_parent=temp_parent,
            allowed_loose_suffixes=frozenset({".prefab"}),
        )
    )
    with pytest.raises(RuntimeError, match="reserved"):
        preparer.prepare({PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: None}, None)
    assert calls == []
    assert not temp_parent.exists()


def test_missing_package_path_preserves_supervised_request_400(
    tmp_path: Path,
) -> None:
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.approval_transactions.register_write_handler(
        "vrcforge_import_outfit_package",
        "Import an outfit package.",
        "high",
        lambda _arguments: {"ok": True},
        request_preparer=dashboard_server.PREPARED_OUTFIT_IMPORT_PREPARER.prepare,
    )

    with pytest.raises(AgentGatewayError) as exc_info:
        gateway.approval_transactions.create_apply_request(
            {
                "target_tool": "vrcforge_import_outfit_package",
                "arguments": {},
            }
        )

    assert exc_info.value.status_code == 400
    assert str(exc_info.value) == "packagePath is required."


def test_dashboard_registry_and_chat_archive_share_the_typed_owners() -> None:
    handler = dashboard_server.AGENT_GATEWAY._write_handlers[
        "vrcforge_import_outfit_package"
    ]
    assert (
        handler.request_preparer
        == dashboard_server.PREPARED_OUTFIT_IMPORT_PREPARER.prepare
    )
    assert (
        handler.handler
        == dashboard_server.PREPARED_OUTFIT_IMPORT_APPROVED_WRITE.execute
    )
    assert isinstance(
        dashboard_server.PREPARED_OUTFIT_IMPORT_PREPARER,
        PreparedOutfitImportPreparer,
    )
    assert isinstance(
        dashboard_server.PREPARED_OUTFIT_IMPORT_APPROVED_WRITE,
        PreparedOutfitImportApprovedWriteService,
    )
    prepare_source = inspect.getsource(
        dashboard_server.prepare_import_chat_archive_request
    )
    execute_source = inspect.getsource(
        dashboard_server.import_chat_archive_approved_sync
    )
    assert "PREPARED_OUTFIT_IMPORT_PREPARER.prepare" in prepare_source
    assert "PREPARED_OUTFIT_IMPORT_APPROVED_WRITE.execute" in execute_source
    assert "if kind == \"unitypackage\"" in prepare_source
    assert "if branch == \"unitypackage\"" in execute_source


def test_legacy_dashboard_import_roots_are_absent() -> None:
    assert not hasattr(dashboard_server, "prepare_outfit_import_package_request")
    assert not hasattr(dashboard_server, "import_outfit_package_approved_sync")
    assert not hasattr(dashboard_server, "_wait_for_unitypackage_import_job")
    assert not hasattr(dashboard_server, "_prepared_outfit_unitypackage_queue")
