from __future__ import annotations

import threading

import pytest

import sub_agent_delegate
from sub_agent_delegate import (
    build_sub_agent_role_handlers,
    build_sub_agent_roles,
    run_outfit_import_plan_review,
    run_outfit_package_inspection,
    run_package_install_diagnosis,
    run_project_index_review,
    run_selected_context_review,
    run_skill_delegate,
    run_validation_triage,
)
from sub_agent_tasks import CancelledError


class FakeGateway:
    """最小 runtime allowlist gateway 假件：记录调用并返回预置 envelope。"""

    def __init__(
        self,
        result: dict[str, object] | None = None,
        *,
        blocked: bool = False,
        error: str | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object], str]] = []
        self._result = result if result is not None else {"ok": True}
        self._blocked = blocked
        self._error = error

    def execute_runtime_skill(
        self,
        tool_name: str,
        params: dict[str, object],
        agent_name: str,
    ) -> dict[str, object]:
        self.calls.append((tool_name, dict(params), agent_name))
        if self._blocked:
            return {
                "ok": False,
                "status": "blocked",
                "tool": tool_name,
                "error": self._error or "Skill is not allowed for direct runtime execution.",
            }
        return {"ok": True, "status": "executed", "tool": tool_name, "result": self._result}


def _idle_event() -> threading.Event:
    return threading.Event()


def test_project_index_review_dispatches_through_gateway_and_keeps_envelope():
    gateway = FakeGateway(
        {
            "ok": True,
            "summary": {
                "changed": True,
                "addedFiles": 3,
                "modifiedFiles": 1,
                "deletedFiles": 0,
                "scannerFamilies": ["materials", "physbones"],
            },
        }
    )
    result = run_project_index_review(gateway, {"projectPath": "ProjectA"}, _idle_event())

    assert gateway.calls == [
        (
            "vrcforge_scan_project_index",
            {"projectPath": "ProjectA", "maxFiles": 100000},
            "sub-agent:project_index_review",
        )
    ]
    assert result["schema"] == "vrcforge.sub_agent.project_index_review.v1"
    assert result["role"] == "project_index_review"
    assert result["readOnly"] is True
    assert result["ok"] is True
    assert "materials, physbones" in result["summaryText"]
    assert result["projectIndex"]["summary"]["addedFiles"] == 3
    assert "targeted scanners" in result["proposedNextAction"]


def test_preset_roles_route_to_expected_tools():
    expectations = [
        (
            run_outfit_package_inspection,
            {"packagePath": "C:/pkg.zip"},
            "vrcforge_inspect_outfit_package",
            "sub-agent:outfit_package_inspection",
            "inspection",
        ),
        (
            run_validation_triage,
            {"projectPath": "ProjectA"},
            "vrcforge_run_validation_report",
            "sub-agent:validation_triage",
            "validation",
        ),
        (
            run_package_install_diagnosis,
            {"logText": "error CS0246"},
            "vrcforge_diagnose_package_install_errors",
            "sub-agent:package_install_diagnosis",
            "diagnostics",
        ),
        (
            run_outfit_import_plan_review,
            {"packagePath": "C:/pkg.zip"},
            "vrcforge_plan_outfit_import",
            "sub-agent:outfit_import_plan_review",
            "importPlan",
        ),
    ]
    for runner, payload, tool_name, agent_name, payload_key in expectations:
        gateway = FakeGateway({"ok": True})
        result = runner(gateway, payload, _idle_event())
        assert len(gateway.calls) == 1
        called_tool, _called_params, called_agent = gateway.calls[0]
        assert called_tool == tool_name
        assert called_agent == agent_name
        assert payload_key in result
        assert result["readOnly"] is True
        assert result["schema"].startswith("vrcforge.sub_agent.")


def test_blocked_skill_surfaces_block_reason_as_failure():
    gateway = FakeGateway(blocked=True, error="Tool category is not runtime-executable: supervised-write")
    with pytest.raises(RuntimeError) as exc_info:
        run_project_index_review(gateway, {"projectPath": "ProjectA"}, _idle_event())
    assert "not runtime-executable" in str(exc_info.value)


def test_skill_delegate_requires_tool_name():
    gateway = FakeGateway()
    with pytest.raises(RuntimeError) as exc_info:
        run_skill_delegate(gateway, {"projectPath": "ProjectA"}, _idle_event())
    assert "toolName" in str(exc_info.value)
    assert gateway.calls == []


def test_skill_delegate_passes_payload_without_control_keys():
    gateway = FakeGateway({"ok": True, "summary": {"summaryText": "12 materials scanned."}})
    result = run_skill_delegate(
        gateway,
        {
            "toolName": "vrcforge_scan_shader_materials",
            "projectPath": "ProjectA",
            "source": "slash-command",
        },
        _idle_event(),
    )

    assert gateway.calls == [
        (
            "vrcforge_scan_shader_materials",
            {"projectPath": "ProjectA"},
            "sub-agent:skill_delegate",
        )
    ]
    assert result["schema"] == "vrcforge.sub_agent.skill_delegate.v1"
    assert result["toolName"] == "vrcforge_scan_shader_materials"
    assert result["summaryText"] == "12 materials scanned."
    assert result["readOnly"] is True


def test_skill_delegate_prefers_explicit_skill_params_and_falls_back_summary():
    gateway = FakeGateway({"ok": True})
    result = run_skill_delegate(
        gateway,
        {
            "toolName": "VRCForge_Scan_Project_Index",
            "projectPath": "ignored-when-explicit",
            "skillParams": {"projectPath": "ProjectB", "maxFiles": 10},
        },
        _idle_event(),
    )

    assert gateway.calls[0][0] == "VRCForge_Scan_Project_Index"
    assert gateway.calls[0][1] == {"projectPath": "ProjectB", "maxFiles": 10}
    assert result["summaryText"].startswith("Delegated skill VRCForge_Scan_Project_Index finished")


def test_skill_delegate_maps_registry_skill_arguments_without_leaking_control_keys():
    gateway = FakeGateway({"ok": True, "arguments": "inspect avatar"})
    run_skill_delegate(
        gateway,
        {
            "toolName": "read-only-avatar-audit",
            "skillArguments": "inspect avatar",
            "projectPath": "ProjectA",
            "task": "visible parent task",
        },
        _idle_event(),
    )

    assert gateway.calls == [
        (
            "read-only-avatar-audit",
            {
                "arguments": "inspect avatar",
                "projectPath": "ProjectA",
                "task": "visible parent task",
            },
            "sub-agent:skill_delegate",
        )
    ]


def test_selected_context_review_never_touches_gateway():
    payload = {"selectedText": "please review this validation output"}
    result = run_selected_context_review(payload, _idle_event())
    assert result["schema"] == "vrcforge.sub_agent.selected_context_review.v1"
    assert result["selectedTextPreview"] == payload["selectedText"]
    assert result["selectedTextCharacters"] == len(payload["selectedText"])
    handlers = build_sub_agent_role_handlers(FakeGateway())
    # selected_context_review 的 handler 不依赖 gateway 调用。
    gateway = FakeGateway()
    handlers_with_probe = build_sub_agent_role_handlers(gateway)
    handlers_with_probe["selected_context_review"]({"selectedText": "abc"}, _idle_event())
    assert gateway.calls == []
    assert set(handlers) == {role.id for role in build_sub_agent_roles()}


def test_build_sub_agent_roles_covers_seven_roles_including_skill_delegate():
    roles = build_sub_agent_roles()
    role_ids = [role.id for role in roles]
    assert role_ids == [
        "project_index_review",
        "outfit_package_inspection",
        "validation_triage",
        "selected_context_review",
        "package_install_diagnosis",
        "outfit_import_plan_review",
        "skill_delegate",
    ]
    delegate = next(role for role in roles if role.id == "skill_delegate")
    assert delegate.tool_profile == "runtime-allowlist"


def test_cancel_event_raises_before_dispatch():
    cancelled = threading.Event()
    cancelled.set()
    gateway = FakeGateway()
    with pytest.raises(CancelledError):
        run_project_index_review(gateway, {"projectPath": "ProjectA"}, cancelled)
    assert gateway.calls == []


def test_non_dict_tool_result_is_wrapped():
    gateway = FakeGateway()
    gateway._result = "plain-text"  # type: ignore[assignment]
    result = run_skill_delegate(
        gateway,
        {"toolName": "vrcforge_read_console_log"},
        _idle_event(),
    )
    assert result["result"] == {"value": "plain-text"}
    assert sub_agent_delegate._DELEGATE_CONTROL_KEYS >= {"toolName", "skillParams", "skillArguments"}
