"""Sub-agent skill delegation domain module for the ROADMAP 1.2.0 runtime.

Every delegated skill runs through AgentGateway's existing allowlist and
runtime dispatch path. The legacy role result envelopes remain compatible,
selected-context review stays local and read-only, and blocked or unknown
skills fail visibly through the durable task registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from sub_agent_tasks import CancelledError, SubAgentRole
from sub_agent_roles import load_sub_agent_roles


class RuntimeSkillGateway(Protocol):
    """子代理分发所需的最小 runtime Skill 执行面。"""

    def execute(
        self,
        tool_name: str,
        params: dict[str, Any],
        agent_name: str,
    ) -> dict[str, Any]: ...

    def run_sub_agent_llm(self, *, task: str, child_session_id: str, provider: str, model: str,
                          system_prompt: str, max_steps: int, project_path: str,
                          selected_materials: list[dict[str, Any]], client_turn_id: str,
                          is_resume: bool, allowed_tools: list[str] | tuple[str, ...] | None,
                          cancel_event: Any) -> dict[str, Any]: ...


def _checkpoint(cancel_event: Any) -> None:
    if cancel_event.is_set():
        raise CancelledError("Sub-agent task was cancelled.")


def _dispatch(
    gateway: RuntimeSkillExecutor,
    tool_name: str,
    params: dict[str, Any],
    agent_name: str,
) -> dict[str, Any]:
    """经 runtime allowlist 执行一个技能并取回工具结果本体。

    blocked / unknown / failed 都转成 RuntimeError，由注册表落成
    failed 状态；这里绝不绕过 gateway 的策略闸另起炉灶。
    """
    envelope = gateway.execute(tool_name, dict(params or {}), agent_name)
    status = str(envelope.get("status") or "")
    if status == "blocked" or not envelope.get("ok"):
        raise RuntimeError(str(envelope.get("error") or f"Skill was not executable: {tool_name}"))
    result = envelope.get("result")
    return result if isinstance(result, dict) else {"value": result}


def run_project_index_review(
    gateway: RuntimeSkillGateway,
    payload: dict[str, Any],
    cancel_event: Any,
) -> dict[str, Any]:
    _checkpoint(cancel_event)
    project_path = str(payload.get("projectPath") or "").strip()
    if not project_path:
        raise RuntimeError("project_index_review requires projectPath (or projectRoot/project_path).")
    result = _dispatch(
        gateway,
        "vrcforge_scan_project_index",
        {"projectPath": project_path, "maxFiles": payload.get("maxFiles") or 100000},
        "sub-agent:project_index_review",
    )
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    changed = bool(summary.get("changed"))
    scanner_families = result.get("summary", {}).get("scannerFamilies") if isinstance(result.get("summary"), dict) else []
    summary_text = (
        f"Project index {'changed' if changed else 'is clean'}: "
        f"+{summary.get('addedFiles', 0)} / ~{summary.get('modifiedFiles', 0)} / -{summary.get('deletedFiles', 0)}; "
        f"scanner families: {', '.join(scanner_families or []) or 'none'}."
    )
    _checkpoint(cancel_event)
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.project_index_review.v1",
        "role": "project_index_review",
        "readOnly": True,
        "summaryText": summary_text,
        "plannerEvidence": {
            "role": "project_index_review",
            "changed": changed,
            "addedFiles": int(summary.get("addedFiles") or 0),
            "modifiedFiles": int(summary.get("modifiedFiles") or 0),
            "deletedFiles": int(summary.get("deletedFiles") or 0),
            "scannerFamilies": [str(item)[:120] for item in (scanner_families or [])[:12]],
        },
        "projectIndex": result,
        "proposedNextAction": "Run targeted scanners for the affected families before planning writes." if changed else "No project-index-triggered scanner rerun is needed.",
    }


def run_outfit_package_inspection(
    gateway: RuntimeSkillGateway,
    payload: dict[str, Any],
    cancel_event: Any,
) -> dict[str, Any]:
    _checkpoint(cancel_event)
    package_path = str(payload.get("packagePath") or payload.get("package_path") or "").strip()
    if not package_path:
        raise RuntimeError("outfit_package_inspection requires params.packagePath.")
    result = _dispatch(
        gateway,
        "vrcforge_inspect_outfit_package",
        {"packagePath": package_path, "maxEntries": payload.get("maxEntries") or 5000},
        "sub-agent:outfit_package_inspection",
    )
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    summary_text = (
        "Outfit package inspected: "
        f"{summary.get('unityPackageCount', 0)} UnityPackage(s), "
        f"{summary.get('prefabCandidateCount', 0)} prefab candidate(s), "
        f"{summary.get('textureCount', 0)} texture(s)."
    )
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.outfit_package_inspection.v1",
        "role": "outfit_package_inspection",
        "readOnly": True,
        "summaryText": summary_text,
        "plannerEvidence": {
            "role": "outfit_package_inspection",
            "unityPackageCount": int(summary.get("unityPackageCount") or 0),
            "prefabCandidateCount": int(summary.get("prefabCandidateCount") or 0),
            "textureCount": int(summary.get("textureCount") or 0),
        },
        "inspection": result,
        "proposedNextAction": "Create a supervised import plan if the package has a UnityPackage or prefab candidate.",
    }


def run_validation_triage(
    gateway: RuntimeSkillGateway,
    payload: dict[str, Any],
    cancel_event: Any,
) -> dict[str, Any]:
    _checkpoint(cancel_event)
    result = _dispatch(
        gateway,
        "vrcforge_run_validation_report",
        {
            "avatarPath": payload.get("avatarPath") or payload.get("avatar_path") or "",
            "projectPath": payload.get("projectPath") or payload.get("project_path") or "",
            "includeQuest": payload.get("includeQuest", True),
            "maxErrors": payload.get("maxErrors") or 50,
        },
        "sub-agent:validation_triage",
    )
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    severity_counts = summary.get("severityCounts") if isinstance(summary.get("severityCounts"), dict) else {}
    summary_text = (
        "Validation triage finished: "
        f"{severity_counts.get('Error', 0)} error(s), "
        f"{severity_counts.get('Warning', 0)} warning(s), "
        f"{severity_counts.get('Suggestion', 0)} suggestion(s)."
    )
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.validation_triage.v1",
        "role": "validation_triage",
        "readOnly": True,
        "summaryText": summary_text,
        "plannerEvidence": {
            "role": "validation_triage",
            "severityCounts": {
                "Error": int(severity_counts.get("Error") or 0),
                "Warning": int(severity_counts.get("Warning") or 0),
                "Suggestion": int(severity_counts.get("Suggestion") or 0),
            },
        },
        "validation": result,
        "proposedNextAction": "Convert selected validation findings into separate supervised fix plans.",
    }


def run_selected_context_review(payload: dict[str, Any], cancel_event: Any) -> dict[str, Any]:
    """纯本地文本处理，无外部工具，不走 gateway。"""
    _checkpoint(cancel_event)
    selected_text = str(payload.get("selectedText") or payload.get("selected_text") or "").strip()
    if not selected_text:
        selected_text = str(payload.get("task") or "").strip()
    preview = selected_text[:1600]
    omitted = max(0, len(selected_text) - len(preview))
    summary_text = (
        f"Selected context opened in a sub-agent thread: {len(selected_text)} character(s)"
        + (f", preview truncated by {omitted} character(s)." if omitted else ".")
    )
    _checkpoint(cancel_event)
    return {
        "ok": True,
        "schema": "vrcforge.sub_agent.selected_context_review.v1",
        "role": "selected_context_review",
        "readOnly": True,
        "summaryText": summary_text,
        "plannerEvidence": {
            "role": "selected_context_review",
            "selectedTextPreview": preview,
            "selectedTextCharacters": len(selected_text),
        },
        "selectedTextPreview": preview,
        "selectedTextCharacters": len(selected_text),
        "proposedNextAction": "Use this scoped sub-agent thread for follow-up review without branching the main chat history.",
    }


def run_package_install_diagnosis(
    gateway: RuntimeSkillGateway,
    payload: dict[str, Any],
    cancel_event: Any,
) -> dict[str, Any]:
    _checkpoint(cancel_event)
    result = _dispatch(
        gateway,
        "vrcforge_diagnose_package_install_errors",
        dict(payload or {}),
        "sub-agent:package_install_diagnosis",
    )
    symptoms = result.get("symptoms") if isinstance(result.get("symptoms"), list) else []
    titles = [str(item.get("title") or item.get("code") or "") for item in symptoms if isinstance(item, dict)]
    symptom_codes = [str(item.get("code") or "") for item in symptoms if isinstance(item, dict)]
    summary_text = f"Package install diagnosis found {len(symptoms)} symptom(s): {', '.join(titles[:4]) or 'none'}."
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.package_install_diagnosis.v1",
        "role": "package_install_diagnosis",
        "readOnly": True,
        "summaryText": summary_text,
        "plannerEvidence": {
            "role": "package_install_diagnosis",
            "symptomCount": len(symptoms),
            "symptomCodes": [item[:120] for item in symptom_codes[:8] if item],
            "symptomTitles": [item[:240] for item in titles[:8] if item],
        },
        "diagnostics": result,
        "proposedNextAction": "Create a separate supervised repair plan for any selected symptom.",
    }


def run_outfit_import_plan_review(
    gateway: RuntimeSkillGateway,
    payload: dict[str, Any],
    cancel_event: Any,
) -> dict[str, Any]:
    _checkpoint(cancel_event)
    package_path = str(payload.get("packagePath") or "").strip()
    if not package_path:
        raise RuntimeError("outfit_import_plan_review requires params.packagePath.")
    result = _dispatch(
        gateway,
        "vrcforge_plan_outfit_import",
        dict(payload or {}),
        "sub-agent:outfit_import_plan_review",
    )
    plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
    ready = bool(plan.get("readyToApply"))
    summary_text = (
        f"Outfit import plan {'ready' if ready else 'needs review'}: "
        f"kind={plan.get('kind') or 'unknown'}, writeTarget={plan.get('writeTarget') or 'none'}."
    )
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.outfit_import_plan_review.v1",
        "role": "outfit_import_plan_review",
        "readOnly": True,
        "planOnly": True,
        "summaryText": summary_text,
        "plannerEvidence": {
            "role": "outfit_import_plan_review",
            "readyToApply": ready,
            "kind": str(plan.get("kind") or "")[:120],
            "writeTarget": str(plan.get("writeTarget") or "")[:240],
            "blockers": [
                str(item)[:240]
                for item in (plan.get("blockers") if isinstance(plan.get("blockers"), list) else [])[:8]
            ],
        },
        "importPlan": result,
        "proposedNextAction": "Queue the normal VRCForge approval from the parent thread if the user accepts this plan." if ready else "Resolve package ambiguity before requesting a write.",
    }


# skill_delegate 的控制键：这些不作为工具参数下发。
_DELEGATE_CONTROL_KEYS = {
    "toolName",
    "tool_name",
    "skillParams",
    "skill_params",
    "skillArguments",
    "skill_arguments",
    "source",
}


def run_skill_delegate(
    gateway: RuntimeSkillGateway,
    payload: dict[str, Any],
    cancel_event: Any,
) -> dict[str, Any]:
    """/delegate 任意技能分发：allowlist 内任何只读/预览技能都可被委派。

    参数约定：payload.toolName 指定技能名；工具参数优先取
    payload.skillParams（显式），否则回落为 payload 去掉控制键
    （让 projectPath/packagePath 之类常用键自然透传）。
    """
    _checkpoint(cancel_event)
    tool_name = str(payload.get("toolName") or payload.get("tool_name") or "").strip()
    if not tool_name:
        raise RuntimeError("skill_delegate requires params.toolName (or params.tool_name).")
    explicit_params = payload.get("skillParams") if isinstance(payload.get("skillParams"), dict) else None
    tool_params = dict(explicit_params) if explicit_params is not None else {
        key: value for key, value in (payload or {}).items() if key not in _DELEGATE_CONTROL_KEYS
    }
    skill_arguments = str(payload.get("skillArguments") or payload.get("skill_arguments") or "").strip()
    if skill_arguments:
        # Registry skills resolve $ARGUMENTS from this semantic field. Direct
        # tools only receive it when an API caller explicitly supplied it.
        tool_params.setdefault("arguments", skill_arguments)
    result = _dispatch(gateway, tool_name, tool_params, "sub-agent:skill_delegate")
    _checkpoint(cancel_event)
    ok = bool(result.get("ok")) if "ok" in result else True
    summary_source = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    summary_hint = str(result.get("summaryText") or summary_source.get("summaryText") or "").strip()
    summary_text = summary_hint or f"Delegated skill {tool_name} {'finished' if ok else 'reported a problem'}."
    return {
        "ok": ok,
        "schema": "vrcforge.sub_agent.skill_delegate.v1",
        "role": "skill_delegate",
        "readOnly": True,
        "toolName": tool_name,
        "summaryText": summary_text,
        "plannerEvidence": {
            "role": "skill_delegate",
            "toolName": tool_name[:160],
            "summary": summary_text[:600],
        },
        "result": result,
        "proposedNextAction": "Review the delegated skill output in the parent thread before planning any write.",
    }


def run_readonly_llm_agent(gateway: RuntimeSkillGateway, payload: dict[str, Any], cancel_event: Any) -> dict[str, Any]:
    """Run one independent child session through AgentGateway's native runtime loop."""
    _checkpoint(cancel_event)
    task = str(payload.get("task") or "").strip()
    child_session_id = str(payload.get("childSessionId") or "").strip()
    if not task or not child_session_id:
        raise RuntimeError("llm_readonly requires task and childSessionId.")
    raw_materials = payload.get("selectedMaterials") or payload.get("selected_materials") or []
    if not isinstance(raw_materials, list) or any(not isinstance(item, dict) for item in raw_materials):
        raise RuntimeError("selectedMaterials must be a list of objects.")
    # Deliberately never forward parent native history, task seed, or arbitrary params.
    materials = [dict(item) for item in raw_materials]
    response = gateway.run_sub_agent_llm(
        task=task,
        child_session_id=child_session_id,
        provider=str(payload.get("provider") or ""),
        model=str(payload.get("model") or "").strip(),
        system_prompt=str(payload.get("systemPrompt") or payload.get("system_prompt") or "").strip(),
        max_steps=payload["maxSteps"],
        project_path=str(payload.get("projectPath") or "").strip(),
        selected_materials=materials,
        client_turn_id=str(payload.get("taskId") or ""),
        is_resume=bool(payload.get("isResume")),
        allowed_tools=payload["tools"],
        cancel_event=cancel_event,
    )
    _checkpoint(cancel_event)
    if not isinstance(response, dict) or not isinstance(response.get("finalReport"), str):
        raise RuntimeError("Native child runtime did not return a final report.")
    role_id = str(payload.get("roleId") or "llm_readonly")
    raw_route = response.get("providerRoute")
    provider_route = {
        key: raw_route[key]
        for key in ("provider", "model")
        if isinstance(raw_route, dict) and isinstance(raw_route.get(key), str)
    }
    return {
        "ok": bool(response.get("ok", True)),
        "schema": "vrcforge.sub_agent.llm_readonly.v1",
        "role": role_id,
        "readOnly": True,
        "childSessionId": child_session_id,
        "summaryText": response["finalReport"][:1000],
        "finalReport": response["finalReport"],
        "plannerEvidence": {"role": role_id, "childSessionId": child_session_id,
                            "stepCount": int(response.get("stepCount") or 0)},
        "rawResult": response.get("rawResult", response),
        "providerRoute": provider_route,
        "resumable": response.get("resumable") is True,
    }


def build_sub_agent_roles(*, data_dir: str | Path | None = None,
                          builtin_dir: str | Path | None = None) -> list[SubAgentRole]:
    """Load packaged role declarations and user additions from DATA_DIR."""
    return load_sub_agent_roles(data_dir=data_dir, builtin_dir=builtin_dir)


def build_sub_agent_input_schema(roles: list[SubAgentRole] | None = None) -> dict[str, Any]:
    """Build the public delegate schema from the executable role catalogue."""

    roles = roles if roles is not None else build_sub_agent_roles()
    role_ids = [role.id for role in roles]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "role": {
                "type": "string",
                "enum": role_ids,
                "description": "Optional; omit to use project_index_review. Choose a role ID from the catalogue: " + "; ".join(
                    f"{role.id} ({role.title}): {role.description}" for role in roles
                ),
            },
            "task": {"type": "string", "description": "Bounded task for the selected role."},
            "model": {"type": "string", "description": "Optional provider model override; defaults to the configured provider model."},
            "systemPrompt": {"type": "string", "description": "Optional role instructions for this child session."},
            "maxSteps": {"type": "integer", "minimum": 1, "maximum": 12,
                         "description": "Maximum provider model requests; queued tool calls in one response do not add model turns."},
            "selectedMaterials": {"type": "array", "items": {"type": "object"}},
            "prompt": {"type": "string", "description": "Alias for task."},
            "displayName": {"type": "string"},
            "display_name": {"type": "string"},
            "parentChatId": {"type": "string"},
            "parent_chat_id": {"type": "string"},
            "parentSessionId": {"type": "string"},
            "parent_session_id": {"type": "string"},
            "projectPath": {"type": "string"},
            "projectRoot": {"type": "string"},
            "project_path": {"type": "string"},
            "params": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "packagePath": {"type": "string", "description": "Required by outfit_package_inspection and outfit_import_plan_review."},
                    "package_path": {"type": "string", "description": "Existing alias accepted by outfit_package_inspection."},
                    "maxEntries": {"type": "integer", "minimum": 1, "maximum": 50000},
                    "toolName": {"type": "string", "description": "Required by skill_delegate; exact allowlisted tool or Skill name."},
                    "tool_name": {"type": "string", "description": "Existing alias of toolName."},
                    "skillParams": {"type": "object", "additionalProperties": True},
                    "skill_params": {"type": "object", "additionalProperties": True},
                    "skillArguments": {"type": "string"},
                    "skill_arguments": {"type": "string"},
                },
            },
        },
    }


def build_sub_agent_role_handlers(
    gateway: RuntimeSkillGateway,
    roles: list[SubAgentRole] | None = None,
) -> dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]]:
    """Bind legacy fixed handlers and the generic read-only LLM executor."""
    handlers = {
        "project_index_review": lambda payload, cancel_event: run_project_index_review(gateway, payload, cancel_event),
        "outfit_package_inspection": lambda payload, cancel_event: run_outfit_package_inspection(gateway, payload, cancel_event),
        "validation_triage": lambda payload, cancel_event: run_validation_triage(gateway, payload, cancel_event),
        "selected_context_review": lambda payload, cancel_event: run_selected_context_review(payload, cancel_event),
        "package_install_diagnosis": lambda payload, cancel_event: run_package_install_diagnosis(gateway, payload, cancel_event),
        "outfit_import_plan_review": lambda payload, cancel_event: run_outfit_import_plan_review(gateway, payload, cancel_event),
        "skill_delegate": lambda payload, cancel_event: run_skill_delegate(gateway, payload, cancel_event),
        "llm_readonly": lambda payload, cancel_event: run_readonly_llm_agent(gateway, payload, cancel_event),
    }
    for role in roles if roles is not None else build_sub_agent_roles():
        if role.handler == "llm_readonly":
            def run_role(payload: dict[str, Any], cancel_event: Any, *, role_id: str = role.id) -> dict[str, Any]:
                role_payload = dict(payload)
                role_payload.setdefault("roleId", role_id)
                return run_readonly_llm_agent(gateway, role_payload, cancel_event)

            handlers[role.id] = run_role
    return handlers
