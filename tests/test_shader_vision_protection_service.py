from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

from shader_vision_protection_service import (
    ProtectionWorkflowPorts,
    ShaderVisionProtectionService,
    ShaderWorkflowPorts,
    VisionAuditWorkflowPorts,
)


ROOT = Path(__file__).parents[1]


def _service() -> tuple[ShaderVisionProtectionService, dict[str, Mock]]:
    calls = {
        name: Mock(return_value={"owner": name})
        for name in (
            "scan",
            "plan",
            "preview_apply",
            "preview_material_assignment",
            "request_write",
            "create_preset",
            "rename_preset",
            "duplicate_preset",
            "delete_preset",
            "load_locks",
            "update_locks",
            "review_vision",
            "request_supervised_capture",
            "capture_status",
            "request_supervised_multi_capture",
            "audit_capture",
            "audit_multi_capture",
            "research_report",
            "protection_scan",
            "protection_plan",
            "protection_preview",
            "addon_status",
            "request_apply",
            "request_remove",
        )
    }
    service = ShaderVisionProtectionService(
        ShaderWorkflowPorts(
            scan=calls["scan"],
            plan=calls["plan"],
            preview_apply=calls["preview_apply"],
            preview_material_assignment=calls["preview_material_assignment"],
            request_supervised_write=calls["request_write"],
            load_history_store=lambda: {
                "records": [
                    {"id": "a", "avatar_path": "Avatar/A"},
                    {"id": "b", "avatar_name": "Avatar B"},
                ]
            },
            load_preset_store=lambda: {
                "presets": [
                    {"id": "p-a", "avatar_path": "Avatar/A"},
                    {"id": "p-b", "avatar_name": "Avatar B"},
                ]
            },
            create_preset=calls["create_preset"],
            rename_preset=calls["rename_preset"],
            duplicate_preset=calls["duplicate_preset"],
            delete_preset=calls["delete_preset"],
            current_avatar_path=lambda: "Avatar/Current",
            load_locks=calls["load_locks"],
            update_locks=calls["update_locks"],
            review_vision=calls["review_vision"],
        ),
        VisionAuditWorkflowPorts(
            request_supervised_capture=calls["request_supervised_capture"],
            read_capture_status=calls["capture_status"],
            request_supervised_multi_capture=calls["request_supervised_multi_capture"],
            audit_capture=calls["audit_capture"],
            audit_multi_capture=calls["audit_multi_capture"],
        ),
        ProtectionWorkflowPorts(
            research_report=calls["research_report"],
            scan=calls["protection_scan"],
            plan=calls["protection_plan"],
            preview=calls["protection_preview"],
            addon_status=calls["addon_status"],
            request_supervised_apply=calls["request_apply"],
            request_supervised_remove=calls["request_remove"],
        ),
    )
    return service, calls


def test_shader_apply_restore_history_and_preset_are_approval_requests() -> None:
    service, calls = _service()
    request = object()

    assert service.request_shader_material_apply(request) == {"owner": "request_write"}
    assert service.request_shader_material_restore(request) == {"owner": "request_write"}
    assert service.request_shader_history_reapply("history-1", request) == {"owner": "request_write"}
    assert service.request_shader_preset_apply("preset-1", request) == {"owner": "request_write"}

    assert calls["request_write"].call_args_list == [
        (("vrcforge_apply_shader_tuning", request), {"reason": "Apply the validated shader/material tuning plan to Unity."}),
        (("vrcforge_restore_shader_tuning", request), {"reason": "Restore the last approved shader/material tuning undo point."}),
        (
            ("vrcforge_reapply_shader_tuning_history", request),
            {
                "reason": "Reapply the selected saved shader tuning history record to Unity.",
                "extra_arguments": {"historyId": "history-1"},
            },
        ),
        (
            ("vrcforge_apply_shader_tuning_preset", request),
            {
                "reason": "Apply the selected saved shader tuning preset to Unity.",
                "extra_arguments": {"presetId": "preset-1"},
            },
        ),
    ]


def test_shader_reads_preserve_filter_and_current_avatar_contracts() -> None:
    service, calls = _service()

    assert service.read_shader_tuning_history("Avatar/A") == {
        "ok": True,
        "records": [{"id": "a", "avatar_path": "Avatar/A"}],
        "count": 1,
    }
    assert service.read_shader_tuning_presets("Avatar B") == {
        "ok": True,
        "presets": [{"id": "p-b", "avatar_name": "Avatar B"}],
        "count": 1,
    }
    assert service.read_shader_tuning_locks() == {
        "ok": True,
        "avatarPath": "Avatar/Current",
        "owner": "load_locks",
    }
    calls["load_locks"].assert_called_once_with("Avatar/Current")


def test_shader_read_plan_review_and_preset_metadata_delegate_exactly() -> None:
    service, calls = _service()
    request = object()

    assert service.scan_shader_materials(request) == {"owner": "scan"}
    assert service.generate_shader_material_plan(request) == {"owner": "plan"}
    assert service.preview_shader_apply({"changes": []}) == {"owner": "preview_apply"}
    assert service.preview_material_shader_assignment({"material": "A"}) == {
        "owner": "preview_material_assignment"
    }
    assert service.create_shader_tuning_preset(request) == {"owner": "create_preset"}
    assert service.rename_shader_tuning_preset("p", request) == {"owner": "rename_preset"}
    assert service.duplicate_shader_tuning_preset("p", request) == {"owner": "duplicate_preset"}
    assert service.delete_shader_tuning_preset("p") == {"owner": "delete_preset"}
    assert service.update_shader_tuning_locks(request) == {"owner": "update_locks"}
    assert service.review_shader_material_vision(request) == {"owner": "review_vision"}

    calls["rename_preset"].assert_called_once_with("p", request)
    calls["duplicate_preset"].assert_called_once_with("p", request)
    calls["delete_preset"].assert_called_once_with("p")


def test_vision_capture_paths_only_reach_request_ports_and_audits_stay_read_only() -> None:
    service, calls = _service()
    request = object()

    assert service.request_avatar_screenshot(request) == {"owner": "request_supervised_capture"}
    assert service.request_avatar_multi_screenshot(request) == {"owner": "request_supervised_multi_capture"}
    assert service.read_vision_capture_status(request) == {"owner": "capture_status"}
    assert service.audit_avatar_screenshot(request) == {"owner": "audit_capture"}
    assert service.audit_avatar_multi_screenshot(request) == {"owner": "audit_multi_capture"}

    calls["request_supervised_capture"].assert_called_once_with(
        "vrcforge_capture_screenshot",
        request,
        reason="Capture one approved Unity scene-view artifact.",
    )
    calls["request_supervised_multi_capture"].assert_called_once_with(
        "vrcforge_capture_multi_screenshot",
        request,
        reason="Capture approved fixed-angle Unity scene-view artifacts.",
    )


def test_protection_reads_and_supervised_requests_remain_separate() -> None:
    service, calls = _service()
    request = object()
    payload = {"avatarPath": "Avatar/A"}

    assert service.build_protection_research_report(request) == {"owner": "research_report"}
    assert service.scan_protection_candidates(request) == {"owner": "protection_scan"}
    assert service.plan_protection(request) == {"owner": "protection_plan"}
    assert service.preview_protection(request) == {"owner": "protection_preview"}
    assert service.read_protection_addon_status() == {"owner": "addon_status"}
    assert service.request_protection_apply(
        payload, "liltoon", agent_name="desktop-agent"
    ) == {"owner": "request_apply"}
    assert service.request_protection_remove(payload, agent_name="external-agent") == {
        "owner": "request_remove"
    }

    calls["request_apply"].assert_called_once_with(payload, "liltoon", "desktop-agent")
    calls["request_remove"].assert_called_once_with(payload, "external-agent")


def test_shader_vision_protection_owner_has_no_direct_execution_or_migration_seam() -> None:
    source = (ROOT / "shader_vision_protection_service.py").read_text(encoding="utf-8")
    for forbidden in (
        "_host",
        "_impl_",
        "sys.modules",
        "__getattr__",
        "apply_shader_material_plan_approved_sync",
        "restore_shader_material_plan_approved_sync",
        "capture_avatar_screenshot_approved_sync",
        "capture_avatar_multi_screenshot_approved_sync",
        "_execute_prepared_scene_view_capture",
        "apply_avatar_encryption_sync",
        "remove_avatar_encryption_sync",
        "invoke_unity_mcp",
    ):
        assert forbidden not in source

    assert "request_capture:" not in source
    assert "request_multi_capture:" not in source
    assert "request_supervised_capture: SupervisedCaptureRequestPort" in source
    assert "request_supervised_multi_capture: SupervisedCaptureRequestPort" in source


def test_dashboard_routes_and_mcp_entries_use_the_typed_controller_only() -> None:
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    route_methods = {
        "scan_shader_materials": "scan_shader_materials",
        "generate_shader_material_plan": "generate_shader_material_plan",
        "apply_shader_material_plan": "request_shader_material_apply",
        "restore_shader_material_plan": "request_shader_material_restore",
        "read_shader_tuning_history": "read_shader_tuning_history",
        "reapply_shader_tuning_history": "request_shader_history_reapply",
        "read_shader_tuning_presets": "read_shader_tuning_presets",
        "create_shader_tuning_preset": "create_shader_tuning_preset",
        "apply_shader_tuning_preset": "request_shader_preset_apply",
        "rename_shader_tuning_preset": "rename_shader_tuning_preset",
        "duplicate_shader_tuning_preset": "duplicate_shader_tuning_preset",
        "delete_shader_tuning_preset": "delete_shader_tuning_preset",
        "read_shader_tuning_locks": "read_shader_tuning_locks",
        "update_shader_tuning_locks": "update_shader_tuning_locks",
        "review_shader_material_vision": "review_shader_material_vision",
        "avatar_encryption_research_report": "build_protection_research_report",
        "avatar_encryption_scan": "scan_protection_candidates",
        "avatar_encryption_plan": "plan_protection",
        "avatar_encryption_preview": "preview_protection",
        "avatar_encryption_apply_request": "request_protection_apply",
        "avatar_encryption_remove_request": "request_protection_remove",
        "capture_avatar_screenshot": "request_avatar_screenshot",
        "read_vision_capture_status": "read_vision_capture_status",
        "capture_avatar_multi_screenshot": "request_avatar_multi_screenshot",
        "audit_avatar_screenshot": "audit_avatar_screenshot",
        "audit_avatar_multi_screenshot": "audit_avatar_multi_screenshot",
    }
    for route, method in route_methods.items():
        assert f"SHADER_VISION_PROTECTION.{method}" in functions[route]

    registry = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "register_agent_gateway_tools"
    )
    tool_calls = [
        node
        for node in ast.walk(registry)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register_tool"
    ]
    tool_source = "\n".join(ast.get_source_segment(source, call) or "" for call in tool_calls)
    for method in (
        "scan_shader_materials",
        "generate_shader_material_plan",
        "preview_shader_apply",
        "preview_material_shader_assignment",
        "read_vision_capture_status",
        "audit_avatar_screenshot",
        "build_protection_research_report",
        "scan_protection_candidates",
        "plan_protection",
        "preview_protection",
        "read_protection_addon_status",
        "request_protection_apply",
        "request_protection_remove",
    ):
        assert f"SHADER_VISION_PROTECTION.{method}" in tool_source


def test_dashboard_keeps_only_approved_execution_roots_and_raw_capture_request_port() -> None:
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    bindings = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert bindings.isdisjoint(
        {
            "apply_shader_material_plan_sync",
            "restore_shader_material_plan_sync",
            "apply_saved_shader_history_sync",
            "apply_saved_shader_preset_sync",
            "apply_saved_shader_payload",
            "capture_avatar_screenshot_sync",
            "capture_avatar_multi_screenshot_sync",
        }
    )
    capture_adapter = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "request_supervised_vision_capture"
    )
    capture_source = ast.get_source_segment(source, capture_adapter) or ""
    assert "AGENT_GATEWAY.approval_transactions.create_apply_request" in capture_source
    assert '"target_tool": target_tool' in capture_source
    assert '"arguments": request.model_dump()' in capture_source
    assert '"reason": reason' in capture_source
    assert "request_supervised_unity_write" not in capture_source

    registry = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "register_agent_gateway_tools"
    )
    tool_calls = [
        ast.get_source_segment(source, node) or ""
        for node in ast.walk(registry)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register_tool"
    ]
    exposed = "\n".join(tool_calls)
    for forbidden in (
        "apply_shader_material_plan_approved_sync",
        "restore_shader_material_plan_approved_sync",
        "reapply_shader_tuning_history_approved_sync",
        "apply_shader_tuning_preset_approved_sync",
        "capture_avatar_screenshot_approved_sync",
        "capture_avatar_multi_screenshot_approved_sync",
        "apply_avatar_encryption_sync",
        "remove_avatar_encryption_sync",
        "_execute_prepared_scene_view_capture",
        "apply_shader_material_tuning_direct",
        "call_avatar_encryption_addon",
    ):
        assert forbidden not in exposed

    write_calls = "\n".join(
        ast.get_source_segment(source, node) or ""
        for node in ast.walk(registry)
        if isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "register_write_handler"
            )
            or (
                isinstance(node.func, ast.Name)
                and node.func.id == "register_write_handler"
            )
        )
    )
    for executor in (
        "apply_shader_material_plan_approved_sync",
        "restore_shader_material_plan_approved_sync",
        "reapply_shader_tuning_history_approved_sync",
        "apply_shader_tuning_preset_approved_sync",
        "capture_avatar_screenshot_approved_sync",
        "capture_avatar_multi_screenshot_approved_sync",
        "apply_avatar_encryption_sync",
        "remove_avatar_encryption_sync",
    ):
        assert executor in write_calls
