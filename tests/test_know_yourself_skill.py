from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from know_yourself_skill import KNOW_YOURSELF_SCHEMA, build_know_yourself_report


REQUIRED_DOCTOR_IDS = (
    "provider.configured",
    "provider.test",
    "unity.project_root",
    "unity.plugin",
    "package.vrchat_sdk",
    "unity.mcp.package",
)


def _doctor(**statuses: str) -> dict[str, Any]:
    checks = [
        {
            "id": check_id,
            "status": statuses.get(
                check_id,
                "warning" if check_id == "provider.test" else "ok",
            ),
            "detail": {"path": r"C:\private\UnityProject"},
        }
        for check_id in REQUIRED_DOCTOR_IDS
    ]
    return {
        "schema": "vrcforge.doctor.v1",
        "checks": checks,
        "selectedUnityEnvironment": {
            "configured": statuses.get("unity.project_root", "ok") == "ok",
            "label": ".../UnityProject",
        },
        "privatePath": r"C:\private\UnityProject",
    }


def _compile_clean() -> dict[str, Any]:
    return {
        "ok": True,
        "result": {
            "exitCode": 0,
            "payload": {
                "data": {
                    "hasErrors": False,
                    "errorCount": 0,
                    "isCompiling": False,
                }
            },
        },
    }


def _unity(
    *,
    connected: bool = True,
    registered: bool = True,
    matched: bool | None = True,
    tools: bool = True,
    missing: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "connected": connected,
        "unityInstanceRegistered": registered,
        "selectedInstanceMatched": matched,
        "activeInstanceCount": 1 if registered else 0,
        "vrcForgeToolsRegistered": tools,
        "missingRequiredVrcForgeTools": missing or [],
        "projectPath": r"C:\private\UnityProject",
    }


def _tool_registry() -> dict[str, Any]:
    return {
        "schema": "vrcforge.tool_registry.v1",
        "tools": [
            {
                "name": "vrcforge_scan_project_index",
                "category": "project",
                "availableInMcp": True,
                "modelInvocable": True,
                "requiresApproval": False,
                "requiresCheckpoint": False,
                "advanced": False,
            },
            {
                "name": "vrcforge_run_validation_report",
                "category": "validation",
                "availableInMcp": True,
                "modelInvocable": True,
                "requiresApproval": False,
                "requiresCheckpoint": False,
                "advanced": False,
            },
            {
                "name": "vrcforge_apply_example",
                "category": "project",
                "availableInMcp": False,
                "modelInvocable": False,
                "requiresApproval": True,
                "requiresCheckpoint": True,
                "advanced": False,
            },
        ],
    }


def _skill_registry() -> dict[str, Any]:
    return {
        "schema": "vrcforge.skills.v1",
        "userCount": 1,
        "skills": [
            {
                "name": "know-yourself",
                "title": "Know Yourself",
                "description": "Work-start self check.",
                "category": "work-start",
                "source": "builtin",
                "skillType": "group",
                "enabled": True,
                "available": True,
                "permissionMode": "read_only",
                "riskLevel": "low",
                "whenToUse": "before project work",
                "allowedTools": ["vrcforge_know_yourself"],
                "entrypointTool": "vrcforge_know_yourself",
                "validation": {"status": "ok", "reasons": []},
            },
            {
                "name": "blocked-group",
                "title": "Blocked Group",
                "description": "Unavailable fixture.",
                "category": "fixture",
                "source": "builtin",
                "skillType": "group",
                "enabled": True,
                "available": False,
                "permissionMode": "read_only",
                "riskLevel": "low",
                "whenToUse": "fixture",
                "allowedTools": ["missing_tool"],
                "validation": {
                    "status": "error",
                    "reasons": [r"unknown tool from C:\private\UnityProject"],
                },
            },
            {
                "name": "private-user-skill",
                "source": "user",
                "skillType": "package",
                "storagePath": r"C:\private\skills\private-user-skill\SKILL.md",
            },
        ],
    }


def _permission_state(**overrides: Any) -> dict[str, Any]:
    return {
        "executionMode": "approval",
        "perActionApproval": True,
        "autoApprove": False,
        "autoApproveDangerousRequiresApproval": False,
        "fullPermission": False,
        "allowWriteRequests": True,
        **overrides,
    }


def _report(
    *,
    doctor: dict[str, Any] | None = None,
    unity: dict[str, Any] | None = None,
    compile_diagnostics: dict[str, Any] | None = None,
    editor_focus_confirmed: bool = False,
    editor_focus_scope: str | None = None,
) -> dict[str, Any]:
    doctor_report = doctor or _doctor()
    project_check = next(
        (
            item
            for item in doctor_report.get("checks", [])
            if item.get("id") == "unity.project_root"
        ),
        {},
    )
    return build_know_yourself_report(
        doctor_report=doctor_report,
        unity_status=unity or _unity(),
        tool_registry=_tool_registry(),
        skill_registry=_skill_registry(),
        project_context={
            "projectSelected": project_check.get("status") == "ok",
            "editorVersion": "2022.3.22f1",
            "editorLaunchConfigured": True,
            "selectedProjectRunning": True,
            "editorFocusScope": "focus-scope-1",
        },
        compile_diagnostics=compile_diagnostics or _compile_clean(),
        permission_state=_permission_state(),
        editor_focus_confirmed=editor_focus_confirmed,
        editor_focus_scope=(
            editor_focus_scope
            if editor_focus_scope is not None
            else "focus-scope-1" if editor_focus_confirmed else ""
        ),
    )


def test_api_only_without_project_stops_at_project_selection() -> None:
    report = _report(
        doctor=_doctor(**{"unity.project_root": "warning", "unity.plugin": "unknown", "unity.mcp.package": "unknown"}),
        unity=_unity(connected=False, registered=False, matched=None, tools=False),
    )

    assert report["schema"] == KNOW_YOURSELF_SCHEMA
    assert report["readyForUnityWork"] is False
    assert report["stage"] == "select_unity_project"
    assert report["editorFocusGate"]["status"] == "blocked"
    assert report["nextAction"]["approvalRequired"] is False
    assert report["capabilities"]["unityDependentWorkStartEligible"] is False


def test_missing_required_sdk_blocks_before_editor_focus() -> None:
    report = _report(
        doctor=_doctor(**{"package.vrchat_sdk": "error"}),
        unity=_unity(connected=False, registered=False, tools=False),
    )

    assert report["stage"] == "install_required_sdk"
    assert report["nextAction"]["kind"] == "supervised_dependency_install"
    assert report["nextAction"]["approvalRequired"] is True
    assert "required_sdk_present" in report["gaps"]
    assert "unity_editor_activation" not in report["gaps"]


def test_missing_plugin_and_mcp_dependency_follow_setup_order() -> None:
    missing_plugin = _report(
        doctor=_doctor(**{"unity.plugin": "error", "unity.mcp.package": "error"}),
        unity=_unity(connected=False, registered=False, tools=False),
    )
    assert missing_plugin["stage"] == "install_or_repair_editor_plugin"
    assert missing_plugin["nextAction"]["approvalRequired"] is True

    missing_mcp = _report(
        doctor=_doctor(**{"unity.mcp.package": "error"}),
        unity=_unity(connected=False, registered=False, tools=False),
    )
    assert missing_mcp["stage"] == "install_mcp_dependency"
    assert missing_mcp["nextAction"]["kind"] == "supervised_dependency_install"


def test_unsupported_unity_editor_version_blocks_before_dependency_setup() -> None:
    report = build_know_yourself_report(
        doctor_report=_doctor(),
        unity_status=_unity(connected=False, registered=False, tools=False),
        tool_registry=_tool_registry(),
        skill_registry=_skill_registry(),
        project_context={"editorVersion": "2021.3.44f1", "editorLaunchConfigured": True},
    )

    assert report["stage"] == "use_supported_unity_editor"
    assert report["nextAction"]["approvalRequired"] is False
    version_step = next(item for item in report["preparation"] if item["id"] == "supported_editor_version")
    assert version_step["status"] == "error"
    assert version_step["supportedSeries"] == ["2022.3"]


def test_dependencies_ready_require_explicit_unity_editor_activation() -> None:
    report = _report(unity=_unity(connected=False, registered=False, matched=None, tools=False))

    assert report["stage"] == "activate_unity_editor"
    assert report["editorFocusGate"]["status"] == "action_required"
    assert report["nextAction"]["requiresUnityWindowFocus"] is True
    assert report["nextAction"]["readbackRequired"] is True
    assert "click once inside the Unity editor window" in report["nextAction"]["instruction"]


def test_closed_selected_project_is_opened_before_editor_activation() -> None:
    report = build_know_yourself_report(
        doctor_report=_doctor(),
        unity_status=_unity(connected=False, registered=False, matched=None, tools=False),
        tool_registry=_tool_registry(),
        skill_registry=_skill_registry(),
        project_context={
            "editorVersion": "2022.3.22f1",
            "editorLaunchConfigured": False,
            "selectedProjectRunning": False,
        },
    )

    assert report["stage"] == "open_selected_unity_project"
    assert report["editorFocusGate"]["status"] == "blocked_by_project_not_open"
    assert "open the selected project manually" in report["nextAction"]["instruction"].lower()
    assert "selected_unity_project_not_running" in report["gaps"]


def test_closed_selected_project_cannot_reuse_stale_green_live_readback() -> None:
    report = build_know_yourself_report(
        doctor_report=_doctor(),
        unity_status=_unity(connected=True, registered=True, matched=True, tools=True),
        tool_registry=_tool_registry(),
        skill_registry=_skill_registry(),
        project_context={
            "editorVersion": "2022.3.22f1",
            "editorLaunchConfigured": True,
            "selectedProjectRunning": False,
        },
        editor_focus_confirmed=True,
    )

    assert report["readyForUnityWork"] is False
    assert report["stage"] == "open_selected_unity_project"
    assert report["editorFocusGate"]["status"] == "blocked_by_project_not_open"
    assert report["capabilities"]["unityEnvironmentReady"] is False
    assert report["gaps"] == ["selected_unity_project_not_running"]


def test_unknown_selected_project_process_never_reuses_green_live_readback() -> None:
    report = build_know_yourself_report(
        doctor_report=_doctor(),
        unity_status=_unity(connected=True, registered=True, matched=True, tools=True),
        tool_registry=_tool_registry(),
        skill_registry=_skill_registry(),
        project_context={
            "projectSelected": True,
            "editorVersion": "2022.3.22f1",
            "editorLaunchConfigured": True,
            "selectedProjectRunning": None,
        },
        compile_diagnostics=_compile_clean(),
        permission_state=_permission_state(),
    )

    assert report["readyForUnityWork"] is False
    assert report["capabilities"]["unityEnvironmentReady"] is False
    assert report["stage"] == "recheck_selected_unity_project"
    assert report["editorFocusGate"]["status"] == "blocked_by_project_process_unknown"
    assert report["gaps"] == ["selected_unity_project_process_unknown"]
    assert report["blockingGaps"] == [
        {
            "id": "selected_unity_project_process_unknown",
            "source": "process_discovery",
            "observedStatus": "unknown",
            "reasonCode": "selected_project_process_unavailable",
            "evidenceStatus": "unavailable",
            "nextActionId": "recheck_selected_unity_project",
        }
    ]


def test_focus_acknowledgement_never_substitutes_for_live_readback() -> None:
    report = _report(
        unity=_unity(connected=False, registered=False, matched=None, tools=False),
        editor_focus_confirmed=True,
    )

    assert report["readyForUnityWork"] is False
    assert report["stage"] == "retry_unity_bridge"
    assert report["editorFocusGate"]["status"] == "acknowledged_pending_live_readback"
    assert report["editorFocusGate"]["claimedActionNeverProvesReadiness"] is True


def test_stale_focus_acknowledgement_is_rejected_after_scope_changes() -> None:
    report = _report(
        unity=_unity(connected=False, registered=False, matched=None, tools=False),
        editor_focus_confirmed=True,
        editor_focus_scope="focus-scope-from-prior-project",
    )

    assert report["stage"] == "activate_unity_editor"
    assert report["editorFocusGate"]["status"] == "stale_acknowledgement"
    assert report["editorFocusGate"]["acknowledgementValid"] is False
    assert report["editorFocusGate"]["acknowledgementStale"] is True
    assert "unity_editor_activation_stale" in report["gaps"]
    stale_gap = next(
        item
        for item in report["blockingGaps"]
        if item["id"] == "unity_editor_activation_stale"
    )
    assert stale_gap["reasonCode"] == "editor_focus_scope_stale"


def test_selected_instance_mismatch_and_missing_tools_remain_distinct() -> None:
    mismatch = _report(
        unity=_unity(connected=True, registered=True, matched=False, tools=True),
        editor_focus_confirmed=True,
    )
    assert mismatch["stage"] == "register_selected_unity_instance"
    assert mismatch["liveReadback"]["instance"]["registered"] is True
    assert mismatch["liveReadback"]["instance"]["ready"] is False

    unknown_match = _report(
        unity=_unity(connected=True, registered=True, matched=None, tools=True),
        editor_focus_confirmed=True,
    )
    assert unknown_match["stage"] == "register_selected_unity_instance"
    assert unknown_match["readyForUnityWork"] is False

    missing_tools = _report(
        unity=_unity(connected=True, registered=True, matched=True, tools=True, missing=["vrc_missing_tool"]),
        editor_focus_confirmed=True,
    )
    assert missing_tools["stage"] == "wait_for_unity_tools"
    assert missing_tools["liveReadback"]["tools"]["missingRequiredTools"] == ["vrc_missing_tool"]


def test_compile_errors_are_reported_before_waiting_for_missing_tools() -> None:
    report = build_know_yourself_report(
        doctor_report=_doctor(),
        unity_status=_unity(
            connected=True,
            registered=True,
            matched=True,
            tools=True,
            missing=["vrc_missing_tool"],
        ),
        tool_registry=_tool_registry(),
        skill_registry=_skill_registry(),
        project_context={
            "editorVersion": "2022.3.22f1",
            "editorLaunchConfigured": True,
            "selectedProjectRunning": True,
        },
        compile_diagnostics={
            "ok": True,
            "result": {
                "exitCode": 0,
                "stdout": "hasErrors: True\nerrorCount: 2\n",
                "stderr": r"C:\private\compile.log",
            },
        },
        editor_focus_confirmed=True,
    )

    assert report["stage"] == "resolve_unity_compile_errors"
    assert report["liveReadback"]["compile"] == {
        "status": "errors",
        "checked": True,
        "hasErrors": True,
        "errorCount": 2,
    }
    assert r"C:\private" not in json.dumps(report)


def test_registered_tools_never_bypass_compile_errors_or_missing_evidence() -> None:
    compile_error = _report(
        compile_diagnostics={
            "ok": True,
            "result": {
                "exitCode": 0,
                "payload": {
                    "data": {
                        "hasErrors": True,
                        "errorCount": 1,
                        "isCompiling": False,
                    }
                },
            },
        }
    )
    assert compile_error["capabilities"]["unityEnvironmentReady"] is True
    assert compile_error["readyForUnityWork"] is False
    assert compile_error["stage"] == "resolve_unity_compile_errors"
    assert "compile" in compile_error["gaps"]

    unavailable = _report(compile_diagnostics={"ok": False})
    assert unavailable["readyForUnityWork"] is False
    assert unavailable["stage"] == "check_unity_compile_status"
    compile_gap = next(item for item in unavailable["blockingGaps"] if item["id"] == "compile")
    assert compile_gap == {
        "id": "compile",
        "source": "compile_diagnostics",
        "observedStatus": "unavailable",
        "reasonCode": "compile_unavailable",
        "evidenceStatus": "unavailable",
        "nextActionId": "check_unity_compile_status",
    }


def test_missing_sources_are_not_claimed_as_observed_self_knowledge() -> None:
    report = build_know_yourself_report(
        doctor_report={},
        unity_status={},
        tool_registry={},
        skill_registry={},
    )

    assert report["selfKnowledge"]["factsFrom"] == []
    assert report["selfKnowledge"]["evidenceSources"] == [
        {"id": "doctor", "status": "unavailable"},
        {"id": "selected_project", "status": "not_checked"},
        {"id": "unity_live_readback", "status": "unavailable"},
        {"id": "compile_diagnostics", "status": "not_checked"},
        {"id": "tool_registry", "status": "unavailable"},
        {"id": "skill_registry", "status": "unavailable"},
        {"id": "permission_state", "status": "unavailable"},
    ]


def test_provider_configuration_never_claims_untested_connectivity() -> None:
    not_tested = _report()
    provider_step = next(
        item for item in not_tested["preparation"] if item["id"] == "provider_configured"
    )
    assert provider_step["title"] == "Provider configured"
    assert not_tested["provider"]["configured"] is True
    assert not_tested["provider"]["connectivity"] == "not_tested"

    failed = _report(doctor=_doctor(**{"provider.test": "error"}))
    assert failed["provider"]["configured"] is True
    assert failed["provider"]["connectivity"] == "tested_failed"


def test_blocking_gap_reasons_cover_each_live_readiness_boundary() -> None:
    cases = [
        (
            _report(
                doctor=_doctor(**{"unity.project_root": "unknown"}),
                unity=_unity(connected=False, registered=False, matched=None, tools=False),
            ),
            "project_selected",
            "project_selected_unknown",
            "select_unity_project",
        ),
        (
            _report(unity=_unity(connected=False, registered=False, matched=None, tools=False)),
            "bridge",
            "unity_bridge_unreachable",
            "retry_unity_bridge",
        ),
        (
            _report(unity=_unity(connected=True, registered=True, matched=False, tools=True)),
            "instance",
            "selected_instance_not_ready",
            "register_selected_unity_instance",
        ),
        (
            _report(unity=_unity(connected=True, registered=True, matched=True, tools=False)),
            "tools",
            "required_tools_incomplete",
            "wait_for_unity_tools",
        ),
    ]

    for report, gap_id, reason_code, next_action_id in cases:
        gap = next(item for item in report["blockingGaps"] if item["id"] == gap_id)
        assert gap["reasonCode"] == reason_code
        assert gap["nextActionId"] == next_action_id


def test_capability_tool_lists_are_bounded_and_marked_truncated() -> None:
    tool_registry = {
        "tools": [
            {
                "name": f"vrcforge_fixture_read_{index:02d}",
                "category": "fixture",
                "availableInMcp": True,
                "requiresApproval": False,
                "requiresCheckpoint": False,
            }
            for index in range(40)
        ]
    }
    report = build_know_yourself_report(
        doctor_report=_doctor(),
        unity_status=_unity(),
        tool_registry=tool_registry,
        skill_registry=_skill_registry(),
        project_context={
            "editorVersion": "2022.3.22f1",
            "selectedProjectRunning": True,
        },
        compile_diagnostics=_compile_clean(),
        permission_state=_permission_state(),
    )

    assert len(report["capabilities"]["availableReadOrPlanTools"]) == 32
    assert report["capabilities"]["availableReadOrPlanToolsTruncated"] is True


def test_ready_report_explains_capabilities_boundaries_and_safe_probes() -> None:
    report = _report()

    assert report["readyForUnityWork"] is True
    assert report["stage"] == "run_project_baseline"
    assert report["editorFocusGate"]["status"] == "satisfied_by_live_readback"
    capabilities = report["capabilities"]
    assert capabilities["availabilityScope"] == "registry_and_permission_only"
    assert capabilities["unityEnvironmentReady"] is True
    assert capabilities["summary"] == {
        "toolCount": 3,
        "availableInMcpCount": 2,
        "availableReadOrPlanToolCount": 2,
        "availableGuardedWriteToolCount": 0,
        "capabilityGroupCount": 2,
        "availableCapabilityGroupCount": 1,
        "unavailableCapabilityGroupCount": 1,
        "advancedPowerAvailable": False,
        "userSkillCount": 1,
    }
    assert [item["tool"] for item in capabilities["recommendedReadyProbes"]] == [
        "vrcforge_scan_project_index",
        "vrcforge_run_validation_report",
    ]
    assert capabilities["unavailableGroups"] == [
        {
            "name": "blocked-group",
            "title": "Blocked Group",
            "reasonCodes": ["tool_not_registered"],
        }
    ]
    assert capabilities["availableReadOrPlanTools"] == [
        "vrcforge_run_validation_report",
        "vrcforge_scan_project_index",
    ]
    assert capabilities["availableReadOrPlanToolsTruncated"] is False
    assert capabilities["availableGuardedWriteTools"] == []
    assert report["provider"] == {
        "configured": True,
        "configurationStatus": "ok",
        "connectivity": "not_tested",
        "automaticTestCallMade": False,
    }
    assert report["readiness"] == {
        "unityEnvironmentReady": True,
        "readyForReadOnlyBaseline": True,
        "baselineComplete": False,
        "readyForTaskPlanning": False,
        "taskPlanningRequiresReadOnlyBaseline": True,
    }
    assert [item["id"] for item in report["readinessSequence"]] == [
        "provider_configured",
        "project_selected",
        "supported_editor_version",
        "editor_plugin_present",
        "required_sdk_present",
        "mcp_dependency_present",
        "selected_unity_project_open",
        "unity_editor_activation",
        "bridge",
        "instance",
        "compile",
        "tools",
        "read_only_baseline",
    ]
    assert report["operatingBoundaries"]["skillMutatesUnityProject"] is False
    assert report["authorization"] == {
        "mode": "step_approval",
        "writeRequestsEnabled": True,
        "perActionApproval": True,
        "automaticApproval": False,
        "dangerousActionsRequireExplicitApproval": True,
    }
    assert report["operatingBoundaries"]["writesRequireExplicitApproval"] is True
    abilities = {
        item["id"]: item
        for item in report["selfKnowledge"]["abilities"]
    }
    assert abilities["explain_current_readiness"]["availableNow"] is True
    assert abilities["run_unity_read_only_baseline"]["availableNow"] is True
    assert abilities["request_guarded_unity_write"] == {
        "id": "request_guarded_unity_write",
        "availableNow": False,
        "blockedBy": [
            "no_registered_guarded_write_capability",
            "read_only_baseline_required",
        ],
        "availableAfterReadOnlyBaseline": False,
    }
    assert "unity_editor_restarted_or_closed" in report["selfKnowledge"]["mustRecheckAfter"]
    assert "editor_focus_acknowledgement_proves_readiness" in report["selfKnowledge"]["neverAssume"]
    assert {item["id"] for item in report["knownLimits"]} == {
        "project_content_not_inspected",
        "provider_test_is_explicit",
        "registry_availability_is_not_project_readiness",
    }


def test_report_does_not_copy_private_registry_or_doctor_paths() -> None:
    serialized = json.dumps(_report(), ensure_ascii=False)

    assert "C:\\\\private" not in serialized
    assert "private-user-skill" not in serialized
    assert "storagePath" not in serialized


def test_self_knowledge_projects_effective_guarded_write_permission() -> None:
    tool_registry = _tool_registry()
    tool_registry["tools"][2]["availableInMcp"] = True
    report = build_know_yourself_report(
        doctor_report=_doctor(),
        unity_status=_unity(),
        tool_registry=tool_registry,
        skill_registry=_skill_registry(),
        project_context={
            "editorVersion": "2022.3.22f1",
            "editorLaunchConfigured": True,
            "selectedProjectRunning": True,
        },
        compile_diagnostics=_compile_clean(),
        permission_state=_permission_state(
            executionMode="auto",
            perActionApproval=False,
            autoApprove=True,
            autoApproveDangerousRequiresApproval=True,
        ),
    )

    assert report["authorization"]["mode"] == "automatic_approval"
    assert report["operatingBoundaries"]["writesUseApprovalWorkflow"] is True
    assert report["operatingBoundaries"]["writesRequireExplicitApproval"] is False
    assert report["operatingBoundaries"]["dangerousActionsRequireExplicitApproval"] is True
    ability = next(
        item
        for item in report["selfKnowledge"]["abilities"]
        if item["id"] == "request_guarded_unity_write"
    )
    assert ability == {
        "id": "request_guarded_unity_write",
        "availableNow": False,
        "blockedBy": ["read_only_baseline_required"],
        "availableAfterReadOnlyBaseline": True,
    }


def test_unity_status_requires_the_selected_project_core_contract(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    import dashboard_server

    project_a = tmp_path / "workspace-a" / "SharedProject"
    project_a.mkdir(parents=True)
    settings = SimpleNamespace(
        unity_mcp_timeout_seconds=5,
    )
    status = dashboard_server.UNITY_STATUS.build_unity_status_snapshot(settings, project_a)

    assert status["selectedInstanceMatched"] is False
    assert status["unityInstanceRegistered"] is False
    assert status["instances"] == []
    assert status["tools"]["toolNames"] == []
    assert "does not contain the VRCForge MCP2 unitypackage" in status["error"]


def test_unity_process_proof_requires_the_exact_selected_project_path(tmp_path: Any) -> None:
    import dashboard_server

    project_a = tmp_path / "workspace-a" / "SharedProject"
    project_b = tmp_path / "workspace-b" / "SharedProject"

    assert dashboard_server.unity_process_exactly_matches_project(
        {"commandLine": f'-projectPath "{project_a}"'},
        project_a,
    )
    assert not dashboard_server.unity_process_exactly_matches_project(
        {"commandLine": f'-projectPath "{project_b}"'},
        project_a,
    )
    assert not dashboard_server.unity_process_exactly_matches_project(
        {"commandLine": "Unity.exe"},
        project_a,
    )


def test_core_transport_has_no_runtime_tool_reregistration_seam(
    monkeypatch: Any,
    tmp_path: Any,
) -> None:
    import dashboard_server

    _ = monkeypatch, tmp_path
    assert not hasattr(dashboard_server, "register_vrcforge_unity_tools_from_project")
    assert not hasattr(dashboard_server, "post_unity_http_json")
    assert dashboard_server.REQUIRED_VRCFORGE_UNITY_TOOLS


def test_dashboard_process_discovery_unavailable_or_failed_stays_blocked(
    monkeypatch: Any,
) -> None:
    import dashboard_server

    def run_case(psutil_value: Any) -> dict[str, Any]:
        with monkeypatch.context() as scoped:
            scoped.setattr(dashboard_server, "psutil", psutil_value)
            scoped.setattr(dashboard_server, "build_agent_connection_request", lambda _params: object())
            scoped.setattr(dashboard_server, "load_dashboard_settings", lambda _request: object())
            scoped.setattr(
                dashboard_server,
                "UNITY_STATUS",
                SimpleNamespace(build_unity_status_snapshot=lambda _settings: _unity()),
            )
            scoped.setattr(
                dashboard_server,
                "DOCTOR_READINESS_REPORT",
                SimpleNamespace(build_app_doctor_report=_doctor),
            )
            scoped.setattr(dashboard_server, "read_agent_compile_errors", lambda _params: _compile_clean())
            scoped.setattr(dashboard_server, "parse_editor_version", lambda _path: "2022.3.22f1")
            scoped.setattr(
                dashboard_server.DASHBOARD_STATE,
                "selected_project_path",
                r"C:\fixture\UnityProject",
            )
            scoped.setattr(dashboard_server.DASHBOARD_STATE, "unity_editor_path", __file__)
            scoped.setattr(dashboard_server.AGENT_GATEWAY, "build_tool_registry", _tool_registry)
            scoped.setattr(
                dashboard_server.KNOW_YOURSELF_READINESS,
                "_ports",
                replace(
                    dashboard_server.KNOW_YOURSELF_READINESS._ports,
                    build_skill_registry=_skill_registry,
                ),
            )
            scoped.setattr(
                type(dashboard_server.AGENT_GATEWAY.approval_transactions),
                "permission_state",
                lambda _owner, _config=None: _permission_state(),
            )
            return dashboard_server.KNOW_YOURSELF_READINESS.know_yourself_sync({})

    missing = run_case(None)

    def fail_discovery(_attrs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("process discovery unavailable")

    failed = run_case(SimpleNamespace(process_iter=fail_discovery))
    unreadable_name = run_case(
        SimpleNamespace(
            process_iter=lambda _attrs: [
                SimpleNamespace(
                    info={"pid": 17, "name": None, "exe": None, "cmdline": []}
                )
            ]
        )
    )
    unreadable_unity_command_line = run_case(
        SimpleNamespace(
            process_iter=lambda _attrs: [
                SimpleNamespace(
                    info={
                        "pid": 18,
                        "name": "Unity.exe",
                        "exe": None,
                        "cmdline": None,
                    }
                )
            ]
        )
    )

    for report in (missing, failed, unreadable_name, unreadable_unity_command_line):
        assert report["readyForUnityWork"] is False
        assert report["stage"] == "recheck_selected_unity_project"
        assert report["gaps"] == ["selected_unity_project_process_unknown"]


def test_dashboard_registers_read_only_know_yourself_skill(monkeypatch: Any) -> None:
    import dashboard_server

    monkeypatch.setattr(dashboard_server, "build_agent_connection_request", lambda _params: object())
    monkeypatch.setattr(dashboard_server, "load_dashboard_settings", lambda _request: object())
    monkeypatch.setattr(
        dashboard_server,
        "UNITY_STATUS",
        SimpleNamespace(build_unity_status_snapshot=lambda _settings: _unity()),
    )
    monkeypatch.setattr(
        dashboard_server,
        "DOCTOR_READINESS_REPORT",
        SimpleNamespace(build_app_doctor_report=_doctor),
    )
    monkeypatch.setattr(dashboard_server, "read_agent_compile_errors", lambda _params: _compile_clean())
    monkeypatch.setattr(dashboard_server, "parse_editor_version", lambda _path: "2022.3.22f1")
    monkeypatch.setattr(
        dashboard_server,
        "list_running_unity_processes",
        lambda **_kwargs: [{"commandLine": '-projectPath "C:\\fixture\\UnityProject"'}],
    )
    monkeypatch.setattr(dashboard_server.DASHBOARD_STATE, "selected_project_path", r"C:\fixture\UnityProject")
    monkeypatch.setattr(dashboard_server.DASHBOARD_STATE, "unity_editor_path", __file__)
    monkeypatch.setattr(dashboard_server.AGENT_GATEWAY, "build_tool_registry", _tool_registry)
    monkeypatch.setattr(
        dashboard_server.KNOW_YOURSELF_READINESS,
        "_ports",
        replace(
            dashboard_server.KNOW_YOURSELF_READINESS._ports,
            build_skill_registry=_skill_registry,
        ),
    )
    monkeypatch.setattr(
        type(dashboard_server.AGENT_GATEWAY.approval_transactions),
        "permission_state",
        lambda _owner, _config=None: _permission_state(),
    )

    report = dashboard_server.KNOW_YOURSELF_READINESS.know_yourself_sync({"editorFocusConfirmed": "true"})
    route = dashboard_server.AGENT_GATEWAY.runtime_planner._match_runtime_skill(
        "API 已经接好了，准备打开 Unity 工程",
        {},
    )
    direct_work_start_route = dashboard_server.AGENT_GATEWAY.runtime_planner._match_runtime_skill(
        "打开这个 Unity 工程开始做头像",
        {},
    )
    short_work_start_route = dashboard_server.AGENT_GATEWAY.runtime_planner._match_runtime_skill(
        "开工程",
        {},
    )
    executed = dashboard_server.AGENT_GATEWAY.runtime_skills.execute(
        "know-yourself",
        {"editorFocusConfirmed": True},
        "know-yourself-contract-test",
    )
    tool = dashboard_server.AGENT_GATEWAY._tools["vrcforge_know_yourself"]
    group = next(
        item
        for item in dashboard_server.AGENT_GATEWAY.skills.build_skill_registry()["skills"]
        if item["name"] == "know-yourself"
    )

    assert report["readyForUnityWork"] is True
    assert route is not None
    assert route["tool"] == "know-yourself"
    assert route["reason"] == "work-start self check"
    assert direct_work_start_route is not None
    assert direct_work_start_route["tool"] == "know-yourself"
    assert short_work_start_route is not None
    assert short_work_start_route["tool"] == "know-yourself"
    assert executed["status"] == "executed"
    assert executed["entrypointTool"] == "vrcforge_know_yourself"
    assert executed["entrypoint"]["result"]["readyForUnityWork"] is True
    assert tool.write is False
    assert tool.category == "read/debug"
    assert tool.handler.__self__ is dashboard_server.KNOW_YOURSELF_READINESS
    assert tool.handler.__func__ is dashboard_server.KNOW_YOURSELF_READINESS.know_yourself_sync.__func__
    assert group["entrypointTool"] == "vrcforge_know_yourself"
    assert group["available"] is True
    assert group["permissionMode"] == "read_only"
    assert "acknowledgement never proves readiness" in group["instructions"]
