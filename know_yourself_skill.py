from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Mapping


KNOW_YOURSELF_SCHEMA = "vrcforge.know_yourself.v1"
SUPPORTED_UNITY_EDITOR_SERIES = ("2022.3",)

_PREPARATION_CHECKS: tuple[tuple[str, str, str], ...] = (
    ("provider_configured", "provider.configured", "Provider configured"),
    ("project_selected", "unity.project_root", "Unity project selected"),
    ("editor_plugin_present", "unity.plugin", "VRCForge editor plugin present"),
    ("required_sdk_present", "package.vrchat_sdk", "Required avatar SDK present"),
    ("mcp_dependency_present", "unity.mcp.package", "Unity MCP dependency present"),
)

_CAPABILITY_LIST_LIMIT = 32

_PREPARATION_ACTIONS: dict[str, dict[str, Any]] = {
    "provider_configured": {
        "id": "configure_provider",
        "kind": "user_configuration",
        "instruction": (
            "Finish provider setup and run an explicit provider test before asking "
            "the agent to plan project work."
        ),
        "approvalRequired": False,
    },
    "project_selected": {
        "id": "select_unity_project",
        "kind": "user_selection",
        "instruction": "Select the Unity project root that contains Assets, Packages, and ProjectSettings.",
        "approvalRequired": False,
    },
    "supported_editor_version": {
        "id": "use_supported_unity_editor",
        "kind": "user_configuration",
        "instruction": "Open the project with a supported Unity 2022.3 LTS editor before continuing setup.",
        "approvalRequired": False,
    },
    "editor_plugin_present": {
        "id": "install_or_repair_editor_plugin",
        "kind": "reviewed_project_setup",
        "instruction": (
            "Open onboarding Step 3. In Unity, Import All from VRCForge.unitypackage; "
            "keep it open, then select it in VRCForge and recheck."
        ),
        "approvalRequired": True,
    },
    "required_sdk_present": {
        "id": "install_required_sdk",
        "kind": "supervised_dependency_install",
        "instruction": (
            "Install the required avatar SDK through the configured package "
            "workflow and wait for dependency resolution."
        ),
        "approvalRequired": True,
    },
    "mcp_dependency_present": {
        "id": "install_mcp_dependency",
        "kind": "supervised_dependency_install",
        "instruction": (
            "Request the Unity MCP dependency through the supervised package "
            "workflow and wait for installation to finish."
        ),
        "approvalRequired": True,
    },
}

_READY_PROBES: tuple[tuple[str, str], ...] = (
    ("vrcforge_scan_project_index", "Refresh the structural project index before planning changes."),
    ("vrcforge_unity_status", "Confirm the selected live Unity instance."),
    ("vrcforge_unity_tools", "Confirm the required predefined Unity tools."),
    ("vrcforge_list_avatars", "Discover avatars in the active project."),
    ("vrcforge_get_compile_errors", "Read current compile errors before any write."),
    ("vrcforge_run_validation_report", "Establish a read-only project and avatar baseline."),
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _normalized_status(value: Any) -> str:
    status = str(value or "unknown").strip().lower()
    return status if status in {"ok", "warning", "error", "unknown"} else "unknown"


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _doctor_checks(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    checks: dict[str, Mapping[str, Any]] = {}
    for item in _as_list(report.get("checks")):
        check = _as_mapping(item)
        check_id = str(check.get("id") or "").strip()
        if check_id:
            checks[check_id] = check
    return checks


def _provider_projection(report: Mapping[str, Any]) -> dict[str, Any]:
    checks = _doctor_checks(report)
    configured_status = _normalized_status(
        _as_mapping(checks.get("provider.configured")).get("status")
    )
    test_status = _normalized_status(_as_mapping(checks.get("provider.test")).get("status"))
    if configured_status != "ok":
        connectivity = "configuration_incomplete"
    elif test_status == "ok":
        connectivity = "tested_ok"
    elif test_status == "error":
        connectivity = "tested_failed"
    else:
        connectivity = "not_tested"
    return {
        "configured": configured_status == "ok",
        "configurationStatus": configured_status,
        "connectivity": connectivity,
        "automaticTestCallMade": False,
    }


def _preparation_steps(
    report: Mapping[str, Any],
    project_context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    checks = _doctor_checks(report)
    steps: list[dict[str, Any]] = []
    for step_id, check_id, title in _PREPARATION_CHECKS:
        check = checks.get(check_id, {})
        status = _normalized_status(check.get("status"))
        steps.append(
            {
                "id": step_id,
                "title": title,
                "source": "doctor",
                "sourceCheck": check_id,
                "status": status,
                "ready": status == "ok",
            }
        )
        if step_id == "project_selected":
            editor_version = str(project_context.get("editorVersion") or "").strip()
            project_ready = status == "ok"
            supported = project_ready and any(
                editor_version == series or editor_version.startswith(f"{series}.")
                for series in SUPPORTED_UNITY_EDITOR_SERIES
            )
            version_status = "ok" if supported else "unknown" if not project_ready or not editor_version else "error"
            steps.append(
                {
                    "id": "supported_editor_version",
                    "title": "Supported Unity editor version",
                    "source": "selected_project",
                    "status": version_status,
                    "ready": supported,
                    "editorVersion": editor_version,
                    "supportedSeries": list(SUPPORTED_UNITY_EDITOR_SERIES),
                }
            )
    return steps


def _bounded_named_value(value: Any, names: set[str]) -> Any:
    pending = [value]
    visited = 0
    while pending and visited < 128:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            for key, child in current.items():
                if str(key).strip().lower() in names:
                    return child
                if isinstance(child, (Mapping, list, tuple)):
                    pending.append(child)
        elif isinstance(current, (list, tuple)):
            pending.extend(current[:64])
    return None


def _compile_readback(compile_diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    if not compile_diagnostics:
        return {"status": "not_checked", "checked": False, "hasErrors": None, "errorCount": None}
    raw_result = _as_mapping(compile_diagnostics.get("result"))
    try:
        exit_code = int(raw_result.get("exitCode"))
    except (TypeError, ValueError):
        exit_code = -1
    if compile_diagnostics.get("ok") is False or exit_code != 0:
        return {"status": "unavailable", "checked": True, "hasErrors": None, "errorCount": None}

    stdout = str(raw_result.get("stdout") or "")
    error_count_value = _bounded_named_value(raw_result, {"errorcount"})
    has_errors_value = _bounded_named_value(raw_result, {"haserrors"})
    compiling_value = _bounded_named_value(raw_result, {"iscompiling", "compiling"})
    if error_count_value is None:
        match = re.search(r"\berrorCount\s*:\s*(\d+)", stdout, flags=re.IGNORECASE)
        error_count_value = match.group(1) if match else 0
    else:
        match = None
    has_structured_evidence = any(
        value is not None
        for value in (has_errors_value, compiling_value, _bounded_named_value(raw_result, {"errorcount"}))
    ) or match is not None or bool(re.search(r"\bhasErrors\s*:", stdout, flags=re.IGNORECASE))
    if not has_structured_evidence:
        return {"status": "unavailable", "checked": True, "hasErrors": None, "errorCount": None}
    error_count = _nonnegative_int(error_count_value)
    has_errors = (
        has_errors_value is True
        or str(has_errors_value or "").strip().lower() == "true"
        or bool(re.search(r"\bhasErrors\s*:\s*True\b", stdout, flags=re.IGNORECASE))
        or error_count > 0
    )
    compiling = (
        compiling_value is True
        or str(compiling_value or "").strip().lower() == "true"
        or bool(re.search(r"\bisCompiling\s*:\s*True\b", stdout, flags=re.IGNORECASE))
    )
    return {
        "status": "errors" if has_errors else "compiling" if compiling else "clean",
        "checked": True,
        "hasErrors": has_errors,
        "errorCount": error_count,
    }


def _live_readback(
    unity_status: Mapping[str, Any],
    compile_diagnostics: Mapping[str, Any],
) -> dict[str, Any]:
    connected = unity_status.get("connected") is True
    instance_registered = unity_status.get("unityInstanceRegistered") is True
    selected_match = unity_status.get("selectedInstanceMatched")
    instance_ready = instance_registered and selected_match is True
    missing_tools = sorted(
        {
            str(item).strip()
            for item in _as_list(unity_status.get("missingRequiredVrcForgeTools"))
            if str(item).strip()
        }
    )
    tools_registered = unity_status.get("vrcForgeToolsRegistered") is True
    tools_ready = tools_registered and not missing_tools
    return {
        "bridge": {
            "status": "ok" if connected else "warning",
            "ready": connected,
        },
        "instance": {
            "status": "ok" if instance_ready else "warning",
            "ready": instance_ready,
            "registered": instance_registered,
            "selectedInstanceMatched": selected_match if isinstance(selected_match, bool) else None,
            "activeInstanceCount": _nonnegative_int(unity_status.get("activeInstanceCount")),
        },
        "tools": {
            "status": "ok" if tools_ready else "warning",
            "ready": tools_ready,
            "registered": tools_registered,
            "missingRequiredTools": missing_tools,
        },
        "compile": _compile_readback(compile_diagnostics),
    }


def _validation_reason_codes(skill: Mapping[str, Any]) -> list[str]:
    validation = _as_mapping(skill.get("validation"))
    codes: set[str] = set()
    for reason in _as_list(validation.get("reasons")):
        text = str(reason or "").strip().lower()
        if not text:
            continue
        if "missing env" in text:
            codes.add("environment_requirement_missing")
        elif "missing binar" in text:
            codes.add("binary_requirement_missing")
        elif "unsupported os" in text:
            codes.add("unsupported_os")
        elif "unknown" in text and "tool" in text:
            codes.add("tool_not_registered")
        elif "unavailable" in text or "not available" in text:
            codes.add("tool_unavailable")
        elif "disabled" in text:
            codes.add("disabled")
        else:
            codes.add("validation_failed")
    if not skill.get("enabled", True):
        codes.add("disabled")
    if not skill.get("available") and not codes:
        codes.add("tool_unavailable")
    return sorted(codes)


def _capability_projection(
    tool_registry: Mapping[str, Any],
    skill_registry: Mapping[str, Any],
) -> dict[str, Any]:
    category_rows: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "total": 0,
            "availableInMcp": 0,
            "modelInvocable": 0,
            "approvalRequired": 0,
            "checkpointRequired": 0,
        }
    )
    available_tools: set[str] = set()
    available_read_or_plan_tools: set[str] = set()
    available_guarded_write_tools: set[str] = set()
    advanced_available = False
    for item in _as_list(tool_registry.get("tools")):
        tool = _as_mapping(item)
        name = str(tool.get("name") or "").strip()
        category = str(tool.get("category") or "misc").strip() or "misc"
        row = category_rows[category]
        row["total"] += 1
        available = tool.get("availableInMcp") is True
        model_invocable = (
            available
            and tool.get("modelInvocable") is not False
            and tool.get("directTool") is not False
        )
        if available:
            row["availableInMcp"] += 1
            if name:
                available_tools.add(name)
                if tool.get("requiresCheckpoint") is True:
                    available_guarded_write_tools.add(name)
                elif tool.get("requiresApproval") is not True:
                    available_read_or_plan_tools.add(name)
        if model_invocable:
            row["modelInvocable"] += 1
        if tool.get("requiresApproval") is True:
            row["approvalRequired"] += 1
        if tool.get("requiresCheckpoint") is True:
            row["checkpointRequired"] += 1
        if available and tool.get("advanced") is True:
            advanced_available = True

    groups: list[dict[str, Any]] = []
    unavailable_groups: list[dict[str, Any]] = []
    for item in _as_list(skill_registry.get("skills")):
        skill = _as_mapping(item)
        if str(skill.get("source") or "") != "builtin" or str(skill.get("skillType") or "") != "group":
            continue
        group = {
            "name": str(skill.get("name") or ""),
            "title": str(skill.get("title") or ""),
            "category": str(skill.get("category") or "builtin"),
            "description": str(skill.get("description") or ""),
            "available": bool(skill.get("available") and skill.get("enabled", True)),
            "permissionMode": str(skill.get("permissionMode") or "read_only"),
            "riskLevel": str(skill.get("riskLevel") or "low"),
            "whenToUse": str(skill.get("whenToUse") or ""),
            "allowedTools": sorted(
                {
                    str(name).strip()
                    for name in _as_list(skill.get("allowedTools") or skill.get("tools"))
                    if str(name).strip()
                }
            ),
        }
        groups.append(group)
        if not group["available"]:
            unavailable_groups.append(
                {
                    "name": group["name"],
                    "title": group["title"],
                    "reasonCodes": _validation_reason_codes(skill),
                }
            )

    ready_probes = [
        {"tool": tool_name, "purpose": purpose}
        for tool_name, purpose in _READY_PROBES
        if tool_name in available_tools
    ]
    categories = [
        {"category": category, **category_rows[category]}
        for category in sorted(category_rows)
    ]
    return {
        "availabilityScope": "registry_and_permission_only",
        "availabilityNote": (
            "A registered capability can still require a ready Unity environment "
            "and task-specific project dependencies."
        ),
        "summary": {
            "toolCount": sum(row["total"] for row in category_rows.values()),
            "availableInMcpCount": len(available_tools),
            "availableReadOrPlanToolCount": len(available_read_or_plan_tools),
            "availableGuardedWriteToolCount": len(available_guarded_write_tools),
            "capabilityGroupCount": len(groups),
            "availableCapabilityGroupCount": sum(1 for group in groups if group["available"]),
            "unavailableCapabilityGroupCount": len(unavailable_groups),
            "advancedPowerAvailable": advanced_available,
            "userSkillCount": int(skill_registry.get("userCount") or 0),
        },
        "categories": categories,
        "groups": sorted(groups, key=lambda group: (group["category"], group["name"])),
        "unavailableGroups": sorted(unavailable_groups, key=lambda group: group["name"]),
        "availableReadOrPlanTools": sorted(available_read_or_plan_tools)[:_CAPABILITY_LIST_LIMIT],
        "availableReadOrPlanToolsTruncated": len(available_read_or_plan_tools) > _CAPABILITY_LIST_LIMIT,
        "availableGuardedWriteTools": sorted(available_guarded_write_tools)[:_CAPABILITY_LIST_LIMIT],
        "availableGuardedWriteToolsTruncated": len(available_guarded_write_tools) > _CAPABILITY_LIST_LIMIT,
        "recommendedReadyProbes": ready_probes,
    }


def _authorization_projection(permission_state: Mapping[str, Any]) -> dict[str, Any]:
    if permission_state.get("fullPermission") is True:
        mode = "full_permission"
    elif permission_state.get("autoApprove") is True:
        mode = "automatic_approval"
    elif permission_state.get("perActionApproval") is True:
        mode = "step_approval"
    else:
        mode = "unknown"
    return {
        "mode": mode,
        "writeRequestsEnabled": permission_state.get("allowWriteRequests") is True,
        "perActionApproval": permission_state.get("perActionApproval") is True,
        "automaticApproval": permission_state.get("autoApprove") is True,
        "dangerousActionsRequireExplicitApproval": permission_state.get("fullPermission") is not True,
    }


def _ability(
    ability_id: str,
    available: bool,
    *,
    blocked_by: list[str] | None = None,
    available_after_read_only_baseline: bool | None = None,
) -> dict[str, Any]:
    result = {
        "id": ability_id,
        "availableNow": available,
        "blockedBy": [] if available else list(dict.fromkeys(blocked_by or [])),
    }
    if available_after_read_only_baseline is not None:
        result["availableAfterReadOnlyBaseline"] = available_after_read_only_baseline
    return result


def _evidence_sources(
    *,
    doctor_report: Mapping[str, Any],
    project_context: Mapping[str, Any],
    unity_status: Mapping[str, Any],
    tool_registry: Mapping[str, Any],
    skill_registry: Mapping[str, Any],
    permission_state: Mapping[str, Any],
    compile_status: str,
) -> list[dict[str, str]]:
    compile_evidence = (
        "observed"
        if compile_status in {"clean", "compiling", "errors"}
        else "unavailable"
        if compile_status == "unavailable"
        else "not_checked"
    )
    return [
        {"id": "doctor", "status": "observed" if _doctor_checks(doctor_report) else "unavailable"},
        {
            "id": "selected_project",
            "status": (
                "observed"
                if isinstance(project_context.get("projectSelected"), bool)
                else "not_checked"
            ),
        },
        {
            "id": "unity_live_readback",
            "status": "observed" if unity_status else "unavailable",
        },
        {"id": "compile_diagnostics", "status": compile_evidence},
        {
            "id": "tool_registry",
            "status": "observed" if isinstance(tool_registry.get("tools"), (list, tuple)) else "unavailable",
        },
        {
            "id": "skill_registry",
            "status": "observed" if isinstance(skill_registry.get("skills"), (list, tuple)) else "unavailable",
        },
        {
            "id": "permission_state",
            "status": "observed" if permission_state else "unavailable",
        },
    ]


def _self_knowledge_projection(
    *,
    ready: bool,
    gaps: list[str],
    capabilities: Mapping[str, Any],
    authorization: Mapping[str, Any],
    evidence_sources: list[dict[str, str]],
) -> dict[str, Any]:
    summary = _as_mapping(capabilities.get("summary"))
    read_tool_count = _nonnegative_int(summary.get("availableReadOrPlanToolCount"))
    write_tool_count = _nonnegative_int(summary.get("availableGuardedWriteToolCount"))

    read_blockers = list(gaps)
    if read_tool_count == 0:
        read_blockers.append("no_registered_read_or_plan_capability")

    write_blockers = list(gaps)
    if authorization.get("writeRequestsEnabled") is not True:
        write_blockers.append("write_requests_disabled")
    if write_tool_count == 0:
        write_blockers.append("no_registered_guarded_write_capability")
    write_blockers.append("read_only_baseline_required")

    write_available_after_baseline = bool(
        ready
        and authorization.get("writeRequestsEnabled") is True
        and write_tool_count > 0
    )

    advanced_available = summary.get("advancedPowerAvailable") is True
    advanced_blockers = list(gaps)
    if not advanced_available:
        advanced_blockers.append("advanced_capabilities_unavailable")
    return {
        "scope": "current_selected_project_and_runtime_snapshot",
        "evidenceSources": evidence_sources,
        "factsFrom": [item["id"] for item in evidence_sources if item["status"] == "observed"],
        "abilities": [
            _ability("explain_current_readiness", True),
            _ability(
                "run_unity_read_only_baseline",
                ready and read_tool_count > 0,
                blocked_by=read_blockers,
            ),
            _ability(
                "request_guarded_unity_write",
                False,
                blocked_by=write_blockers,
                available_after_read_only_baseline=write_available_after_baseline,
            ),
            _ability(
                "use_advanced_capabilities",
                ready and advanced_available,
                blocked_by=advanced_blockers,
            ),
        ],
        "mustRecheckAfter": [
            "selected_project_changed",
            "unity_editor_restarted_or_closed",
            "project_dependencies_changed",
            "unity_bridge_or_instance_changed",
            "permission_or_skill_registry_changed",
        ],
        "neverAssume": [
            "editor_focus_acknowledgement_proves_readiness",
            "provider_configuration_proves_connectivity",
            "registry_availability_proves_task_dependencies",
            "environment_readiness_proves_project_content_validity",
        ],
    }


def _compile_next_action_id(status: str) -> str:
    if status == "errors":
        return "resolve_unity_compile_errors"
    if status == "compiling":
        return "wait_for_unity_compile"
    return "check_unity_compile_status"


def _blocking_gap_projection(
    gaps: list[str],
    *,
    preparation: list[dict[str, Any]],
    live: Mapping[str, Any],
    focus_status: str,
) -> list[dict[str, Any]]:
    preparation_by_id = {str(item.get("id")): item for item in preparation}
    result: list[dict[str, Any]] = []
    for gap_id in dict.fromkeys(gaps):
        if gap_id in preparation_by_id:
            step = preparation_by_id[gap_id]
            observed = str(step.get("status") or "unknown")
            result.append(
                {
                    "id": gap_id,
                    "source": str(step.get("source") or "doctor"),
                    "observedStatus": observed,
                    "reasonCode": f"{gap_id}_{observed}",
                    "evidenceStatus": "unavailable" if observed == "unknown" else "observed",
                    "nextActionId": _PREPARATION_ACTIONS[gap_id]["id"],
                }
            )
            continue
        if gap_id == "selected_unity_project_not_running":
            fields = (
                "selected_project",
                "not_running",
                "selected_project_not_running",
                "observed",
                "open_selected_unity_project",
            )
        elif gap_id == "selected_unity_project_process_unknown":
            fields = (
                "process_discovery",
                "unknown",
                "selected_project_process_unavailable",
                "unavailable",
                "recheck_selected_unity_project",
            )
        elif gap_id in {"unity_editor_activation", "unity_editor_activation_stale"}:
            fields = (
                "user_action",
                focus_status,
                "editor_focus_scope_stale" if gap_id.endswith("stale") else "editor_focus_required",
                "observed",
                "activate_unity_editor",
            )
        elif gap_id == "bridge":
            fields = (
                "unity_live_readback",
                "unreachable",
                "unity_bridge_unreachable",
                "observed",
                "retry_unity_bridge",
            )
        elif gap_id == "instance":
            instance = _as_mapping(live.get("instance"))
            observed = "mismatch" if instance.get("registered") is True else "not_registered"
            fields = (
                "unity_live_readback",
                observed,
                "selected_instance_not_ready",
                "observed",
                "register_selected_unity_instance",
            )
        elif gap_id == "tools":
            fields = (
                "unity_live_readback",
                "incomplete",
                "required_tools_incomplete",
                "observed",
                "wait_for_unity_tools",
            )
        elif gap_id == "compile":
            compile_status = str(_as_mapping(live.get("compile")).get("status") or "not_checked")
            evidence = "unavailable" if compile_status in {"not_checked", "unavailable"} else "observed"
            fields = (
                "compile_diagnostics",
                compile_status,
                f"compile_{compile_status}",
                evidence,
                _compile_next_action_id(compile_status),
            )
        else:
            fields = ("self_check", "unknown", "unclassified_gap", "unavailable", "run_know_yourself")
        source, observed, reason, evidence, action = fields
        result.append(
            {
                "id": gap_id,
                "source": source,
                "observedStatus": observed,
                "reasonCode": reason,
                "evidenceStatus": evidence,
                "nextActionId": action,
            }
        )
    return result


def _readiness_sequence(
    preparation: list[dict[str, Any]],
    *,
    project_context: Mapping[str, Any],
    focus_status: str,
    focus_acknowledged: bool,
    live: Mapping[str, Any],
    ready_for_baseline: bool,
) -> list[dict[str, Any]]:
    sequence = [
        {
            **step,
            "reasonCode": None if step["ready"] else f"{step['id']}_{step['status']}",
            "nextActionId": _PREPARATION_ACTIONS[step["id"]]["id"],
        }
        for step in preparation
    ]
    project_running = project_context.get("selectedProjectRunning")
    sequence.append(
        {
            "id": "selected_unity_project_open",
            "title": "Selected Unity project open",
            "source": "process_discovery",
            "status": "ok" if project_running is True else "error" if project_running is False else "unknown",
            "ready": project_running is True,
            "reasonCode": (
                None
                if project_running is True
                else "selected_project_not_running"
                if project_running is False
                else "selected_project_process_unavailable"
            ),
            "nextActionId": (
                "open_selected_unity_project"
                if project_running is False
                else "recheck_selected_unity_project"
            ),
        }
    )
    sequence.append(
        {
            "id": "unity_editor_activation",
            "title": "Unity editor activated after dependency setup",
            "source": "user_action_and_live_readback",
            "status": focus_status,
            "ready": focus_acknowledged or focus_status == "satisfied_by_live_readback",
            "reasonCode": (
                None
                if focus_acknowledged or focus_status == "satisfied_by_live_readback"
                else "editor_focus_scope_stale"
                if focus_status == "stale_acknowledgement"
                else "selected_project_not_running"
                if focus_status == "blocked_by_project_not_open"
                else "selected_project_process_unavailable"
                if focus_status == "blocked_by_project_process_unknown"
                else "prerequisites_not_ready"
                if focus_status == "blocked"
                else "editor_focus_required"
            ),
            "nextActionId": "activate_unity_editor",
        }
    )
    for item_id, title in (
        ("bridge", "Unity bridge reachable"),
        ("instance", "Selected Unity instance matched"),
        ("compile", "Unity compile status clean"),
        ("tools", "Required VRCForge Unity tools registered"),
    ):
        item = _as_mapping(live.get(item_id))
        status = str(item.get("status") or "unknown")
        item_ready = item.get("ready") is True if item_id != "compile" else status == "clean"
        sequence.append(
            {
                "id": item_id,
                "title": title,
                "source": "compile_diagnostics" if item_id == "compile" else "unity_live_readback",
                "status": status,
                "ready": item_ready,
                "reasonCode": None if item_ready else f"{item_id}_{status}",
                "nextActionId": _compile_next_action_id(status) if item_id == "compile" else {
                    "bridge": "retry_unity_bridge",
                    "instance": "register_selected_unity_instance",
                    "tools": "wait_for_unity_tools",
                }[item_id],
            }
        )
    sequence.append(
        {
            "id": "read_only_baseline",
            "title": "Read-only project baseline completed",
            "source": "recommended_read_only_probes",
            "status": "required" if ready_for_baseline else "blocked",
            "ready": False,
            "reasonCode": "read_only_baseline_required" if ready_for_baseline else "environment_not_ready",
            "nextActionId": "run_project_baseline",
        }
    )
    return sequence


def _next_action(
    preparation: list[dict[str, Any]],
    live: Mapping[str, Any],
    project_context: Mapping[str, Any],
    *,
    editor_focus_acknowledged: bool,
) -> dict[str, Any]:
    for step in preparation:
        if not step["ready"]:
            return {**_PREPARATION_ACTIONS[step["id"]], "blockedBy": step["id"]}

    selected_project_running = project_context.get("selectedProjectRunning")
    if selected_project_running is not True:
        if selected_project_running is False:
            launch_configured = project_context.get("editorLaunchConfigured") is True
            return {
                "id": "open_selected_unity_project",
                "kind": "explicit_user_action",
                "instruction": (
                    "Open the selected Unity project from VRCForge or open it manually, "
                    "then wait for the editor window."
                    if launch_configured
                    else (
                        "The Unity editor launch path is not configured. Open the "
                        "selected project manually, or configure the editor path first."
                    )
                ),
                "approvalRequired": False,
                "requiresUnityWindowFocus": True,
                "readbackRequired": True,
            }
        return {
            "id": "recheck_selected_unity_project",
            "kind": "read_only_retry",
            "instruction": (
                "VRCForge could not prove that the selected Unity project process is "
                "running. Retry process discovery before using any live instance state."
            ),
            "approvalRequired": False,
            "readbackRequired": True,
        }

    bridge_ready = _as_mapping(live.get("bridge")).get("ready") is True
    instance_ready = _as_mapping(live.get("instance")).get("ready") is True
    tools_ready = _as_mapping(live.get("tools")).get("ready") is True
    if not bridge_ready and not editor_focus_acknowledged:
        return {
            "id": "activate_unity_editor",
            "kind": "explicit_user_action",
            "instruction": (
                "Open the selected project in Unity if needed. After dependencies finish "
                "installing, click once inside the Unity editor window so package "
                "resolution and MCP loading can start, then run Know Yourself again."
            ),
            "approvalRequired": False,
            "requiresUnityWindowFocus": True,
            "readbackRequired": True,
        }

    if not bridge_ready:
        return {
            "id": "retry_unity_bridge",
            "kind": "read_only_retry_or_guided_repair",
            "instruction": (
                "The editor-focus step was acknowledged, but the bridge is still not "
                "reachable. Retry the bridge check and use the guided repair surface "
                "if it remains unavailable."
            ),
            "approvalRequired": False,
            "readbackRequired": True,
        }
    if not instance_ready:
        return {
            "id": "register_selected_unity_instance",
            "kind": "read_only_retry_or_guided_repair",
            "instruction": (
                "The bridge is reachable, but the selected Unity instance is not "
                "registered. Keep the intended editor active and retry instance discovery."
            ),
            "approvalRequired": False,
            "requiresUnityWindowFocus": True,
            "readbackRequired": True,
        }
    compile_status = str(_as_mapping(live.get("compile")).get("status") or "not_checked")
    if compile_status == "errors":
        return {
            "id": "resolve_unity_compile_errors",
            "kind": "read_only_diagnosis_then_supervised_fix",
            "instruction": (
                "Unity reports compile errors. Inspect the compile diagnostics and plan "
                "a separate supervised fix before starting project work."
            ),
            "approvalRequired": False,
            "readbackRequired": True,
        }
    if not tools_ready:
        return {
            "id": "wait_for_unity_tools",
            "kind": "wait_and_readback",
            "instruction": (
                "Wait for Unity package resolution and compilation, then recheck the "
                "predefined VRCForge Unity tools. Repair the editor plugin only if the "
                "tools remain incomplete."
            ),
            "approvalRequired": False,
            "readbackRequired": True,
        }
    if compile_status == "compiling":
        return {
            "id": "wait_for_unity_compile",
            "kind": "wait_and_readback",
            "instruction": (
                "Unity is still compiling. Wait for compilation to finish, then run "
                "this self-check again before planning work."
            ),
            "approvalRequired": False,
            "readbackRequired": True,
        }
    if compile_status != "clean":
        return {
            "id": "check_unity_compile_status",
            "kind": "read_only_retry_or_guided_repair",
            "instruction": (
                "The selected Unity instance is ready, but compile status has no usable "
                "readback. Retry the compile check before planning work."
            ),
            "approvalRequired": False,
            "readbackRequired": True,
        }
    return {
        "id": "run_project_baseline",
        "kind": "read_only_baseline",
        "instruction": (
            "Unity work-start readiness is green. Run the recommended read-only "
            "probes before planning the first change."
        ),
        "approvalRequired": False,
    }


def build_know_yourself_report(
    *,
    doctor_report: Mapping[str, Any],
    unity_status: Mapping[str, Any],
    tool_registry: Mapping[str, Any],
    skill_registry: Mapping[str, Any],
    project_context: Mapping[str, Any] | None = None,
    compile_diagnostics: Mapping[str, Any] | None = None,
    permission_state: Mapping[str, Any] | None = None,
    editor_focus_confirmed: bool = False,
    editor_focus_scope: str = "",
) -> dict[str, Any]:
    project_context = _as_mapping(project_context)
    permission_state = _as_mapping(permission_state)
    preparation = _preparation_steps(doctor_report, project_context)
    live = _live_readback(unity_status, _as_mapping(compile_diagnostics))
    prerequisites_ready = all(step["ready"] for step in preparation)
    live_environment_ready = all(
        _as_mapping(live[name]).get("ready") is True
        for name in ("bridge", "instance", "tools")
    )
    compile_status = str(_as_mapping(live.get("compile")).get("status") or "not_checked")
    selected_project_running = project_context.get("selectedProjectRunning")
    project_runtime_ready = selected_project_running is True
    unity_environment_ready = prerequisites_ready and project_runtime_ready and live_environment_ready
    ready_for_baseline = unity_environment_ready and compile_status == "clean"

    expected_focus_scope = str(project_context.get("editorFocusScope") or "").strip()
    supplied_focus_scope = str(editor_focus_scope or "").strip()
    focus_acknowledged = bool(
        editor_focus_confirmed
        and expected_focus_scope
        and supplied_focus_scope == expected_focus_scope
    )
    focus_acknowledgement_stale = editor_focus_confirmed and not focus_acknowledged
    bridge_ready = _as_mapping(live.get("bridge")).get("ready") is True
    if not prerequisites_ready:
        focus_status = "blocked"
    elif project_context.get("selectedProjectRunning") is False:
        focus_status = "blocked_by_project_not_open"
    elif project_context.get("selectedProjectRunning") is not True:
        focus_status = "blocked_by_project_process_unknown"
    elif bridge_ready:
        focus_status = "satisfied_by_live_readback"
    elif focus_acknowledged:
        focus_status = "acknowledged_pending_live_readback"
    elif focus_acknowledgement_stale:
        focus_status = "stale_acknowledgement"
    else:
        focus_status = "action_required"

    next_action = _next_action(
        preparation,
        live,
        project_context,
        editor_focus_acknowledged=focus_acknowledged,
    )
    capabilities = _capability_projection(tool_registry, skill_registry)
    capabilities["unityEnvironmentReady"] = unity_environment_ready
    capabilities["unityDependentWorkStartEligible"] = ready_for_baseline
    gaps = [step["id"] for step in preparation if not step["ready"]]
    if prerequisites_ready:
        if not project_runtime_ready:
            gaps.append(
                "selected_unity_project_not_running"
                if selected_project_running is False
                else "selected_unity_project_process_unknown"
            )
        else:
            if not bridge_ready and not focus_acknowledged:
                gaps.append(
                    "unity_editor_activation_stale"
                    if focus_acknowledgement_stale
                    else "unity_editor_activation"
                )
            if not live_environment_ready:
                gaps.extend(
                    name
                    for name in ("bridge", "instance", "tools")
                    if not _as_mapping(live[name]).get("ready")
                )
            instance_ready = _as_mapping(live.get("instance")).get("ready") is True
            if bridge_ready and instance_ready and compile_status != "clean":
                gaps.append("compile")
    gaps = list(dict.fromkeys(gaps))

    authorization = _authorization_projection(permission_state)
    evidence_sources = _evidence_sources(
        doctor_report=doctor_report,
        project_context=project_context,
        unity_status=unity_status,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        permission_state=permission_state,
        compile_status=compile_status,
    )
    self_knowledge = _self_knowledge_projection(
        ready=ready_for_baseline,
        gaps=gaps,
        capabilities=capabilities,
        authorization=authorization,
        evidence_sources=evidence_sources,
    )
    blocking_gaps = _blocking_gap_projection(
        gaps,
        preparation=preparation,
        live=live,
        focus_status=focus_status,
    )
    readiness_sequence = _readiness_sequence(
        preparation,
        project_context=project_context,
        focus_status=focus_status,
        focus_acknowledged=focus_acknowledged,
        live=live,
        ready_for_baseline=ready_for_baseline,
    )

    selected_environment = _as_mapping(doctor_report.get("selectedUnityEnvironment"))
    return {
        "ok": True,
        "schema": KNOW_YOURSELF_SCHEMA,
        "notice": (
            "This readiness report is authoritative for work-start. Reply to the user now "
            "from this result; do not inspect project files or run setup actions."
        ),
        "summary": (
            f"readyForUnityWork={'true' if ready_for_baseline else 'false'}; "
            f"nextSafeAction={next_action['id']}; "
            f"blockingGaps={','.join(gaps[:6]) or 'none'}"
        ),
        "message": str(next_action.get("instruction") or ""),
        "readyForUnityWork": ready_for_baseline,
        "readiness": {
            "unityEnvironmentReady": unity_environment_ready,
            "readyForReadOnlyBaseline": ready_for_baseline,
            "baselineComplete": False,
            "readyForTaskPlanning": False,
            "taskPlanningRequiresReadOnlyBaseline": True,
        },
        "stage": next_action["id"],
        "gaps": gaps,
        "blockingGaps": blocking_gaps,
        "preparation": preparation,
        "readinessSequence": readiness_sequence,
        "editorFocusGate": {
            "status": focus_status,
            "explicitUserAction": True,
            "claimedActionNeverProvesReadiness": True,
            "liveReadbackRequired": True,
            "scope": expected_focus_scope,
            "scopeRequiredForAcknowledgement": True,
            "acknowledgementValid": focus_acknowledged,
            "acknowledgementStale": focus_acknowledgement_stale,
        },
        "liveReadback": live,
        "nextAction": next_action,
        "provider": _provider_projection(doctor_report),
        "selectedUnityEnvironment": {
            "configured": selected_environment.get("configured") is True,
            "label": str(selected_environment.get("label") or ""),
        },
        "projectContext": {
            "projectSelected": (
                project_context.get("projectSelected")
                if isinstance(project_context.get("projectSelected"), bool)
                else None
            ),
            "editorVersion": str(project_context.get("editorVersion") or ""),
            "editorLaunchConfigured": project_context.get("editorLaunchConfigured") is True,
            "editorLaunchRequiredForReadiness": False,
            "selectedProjectRunning": (
                project_context.get("selectedProjectRunning")
                if isinstance(project_context.get("selectedProjectRunning"), bool)
                else None
            ),
        },
        "capabilities": capabilities,
        "authorization": authorization,
        "selfKnowledge": self_knowledge,
        "operatingBoundaries": {
            "skillMutatesUnityProject": False,
            "skillInstallsDependencies": False,
            "skillLaunchesOrClosesUnity": False,
            "directUnityProjectWrites": False,
            "writesUseApprovalWorkflow": True,
            "writesRequireExplicitApproval": authorization["mode"] in {
                "step_approval",
                "unknown",
            },
            "dangerousActionsRequireExplicitApproval": authorization[
                "dangerousActionsRequireExplicitApproval"
            ],
            "writesRequireCheckpoint": True,
            "writesRequireValidationReadback": True,
            "writesRequireRollbackPath": True,
        },
        "knownLimits": [
            {
                "id": "project_content_not_inspected",
                "meaning": (
                    "This self-check establishes environment readiness; it does not "
                    "inspect the active scene, avatar, or project content."
                ),
                "next": "Run the recommended read-only baseline probes after environment readiness is green.",
            },
            {
                "id": "registry_availability_is_not_project_readiness",
                "meaning": (
                    "Capability registry availability proves exposure and permission only, "
                    "not that every task-specific Unity package is installed."
                ),
                "next": (
                    "Use the relevant read-only scanner or validation report before "
                    "choosing a task-specific workflow."
                ),
            },
            {
                "id": "provider_test_is_explicit",
                "meaning": "Provider configuration can be present without an automatic billable test call.",
                "next": "Run a provider test explicitly when model connectivity must be proven.",
            },
        ],
    }
