"""Sub-agent skill delegation domain module for the ROADMAP 1.2.0 runtime.

Every delegated skill runs through AgentGateway's existing allowlist and
runtime dispatch path. The legacy role result envelopes remain compatible,
selected-context review stays local and read-only, and blocked or unknown
skills fail visibly through the durable task registry.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from sub_agent_tasks import CancelledError, SubAgentRole


class RuntimeSkillGateway(Protocol):
    """子代理分发所需的最小 gateway 面。"""

    def execute_runtime_skill(
        self,
        tool_name: str,
        params: dict[str, Any],
        agent_name: str,
    ) -> dict[str, Any]: ...


def _checkpoint(cancel_event: Any) -> None:
    if cancel_event.is_set():
        raise CancelledError("Sub-agent task was cancelled.")


def _dispatch(
    gateway: RuntimeSkillGateway,
    tool_name: str,
    params: dict[str, Any],
    agent_name: str,
) -> dict[str, Any]:
    """经 runtime allowlist 执行一个技能并取回工具结果本体。

    blocked / unknown / failed 都转成 RuntimeError，由注册表落成
    failed 状态；这里绝不绕过 gateway 的策略闸另起炉灶。
    """
    envelope = gateway.execute_runtime_skill(tool_name, dict(params or {}), agent_name)
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
    summary_text = f"Package install diagnosis found {len(symptoms)} symptom(s): {', '.join(titles[:4]) or 'none'}."
    return {
        "ok": bool(result.get("ok")),
        "schema": "vrcforge.sub_agent.package_install_diagnosis.v1",
        "role": "package_install_diagnosis",
        "readOnly": True,
        "summaryText": summary_text,
        "diagnostics": result,
        "proposedNextAction": "Create a separate supervised repair plan for any selected symptom.",
    }


def run_outfit_import_plan_review(
    gateway: RuntimeSkillGateway,
    payload: dict[str, Any],
    cancel_event: Any,
) -> dict[str, Any]:
    _checkpoint(cancel_event)
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
        raise RuntimeError("skill_delegate requires params.toolName.")
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
        "result": result,
        "proposedNextAction": "Review the delegated skill output in the parent thread before planning any write.",
    }


def build_sub_agent_roles() -> list[SubAgentRole]:
    """组合根用的完整角色清单（六个既有角色 + skill_delegate）。"""
    return [
        SubAgentRole(
            id="project_index_review",
            title="Project index review",
            description="Scan the local Unity project index and summarize changed scanner families.",
            tool_profile="local-index-only",
        ),
        SubAgentRole(
            id="outfit_package_inspection",
            title="Outfit package inspection",
            description="Inspect a UnityPackage, Booth ZIP/folder, or loose prefab folder without reading asset payload bytes.",
            tool_profile="read-only",
        ),
        SubAgentRole(
            id="validation_triage",
            title="Validation triage",
            description="Run the read-only validation report and summarize errors, warnings, and likely next plans.",
            tool_profile="read-only",
        ),
        SubAgentRole(
            id="selected_context_review",
            title="Selected context review",
            description="Open a scoped read-only sub-agent thread from selected chat text.",
            tool_profile="read-only",
        ),
        SubAgentRole(
            id="package_install_diagnosis",
            title="Package install diagnosis",
            description="Classify package install output and Unity compile errors without repairing automatically.",
            tool_profile="read-only",
        ),
        SubAgentRole(
            id="outfit_import_plan_review",
            title="Outfit import plan review",
            description="Inspect a package and build a supervised import plan without writing to Unity.",
            tool_profile="plan-only",
        ),
        SubAgentRole(
            id="skill_delegate",
            title="Skill delegate",
            description="Run one allowlisted read-only or plan-only runtime skill by name and report its output.",
            tool_profile="runtime-allowlist",
        ),
    ]


def build_sub_agent_role_handlers(
    gateway: RuntimeSkillGateway,
) -> dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]]:
    """组合根用的 role -> handler 映射；gateway 在这里一次性绑定。"""
    return {
        "project_index_review": lambda payload, cancel_event: run_project_index_review(gateway, payload, cancel_event),
        "outfit_package_inspection": lambda payload, cancel_event: run_outfit_package_inspection(gateway, payload, cancel_event),
        "validation_triage": lambda payload, cancel_event: run_validation_triage(gateway, payload, cancel_event),
        "selected_context_review": lambda payload, cancel_event: run_selected_context_review(payload, cancel_event),
        "package_install_diagnosis": lambda payload, cancel_event: run_package_install_diagnosis(gateway, payload, cancel_event),
        "outfit_import_plan_review": lambda payload, cancel_event: run_outfit_import_plan_review(gateway, payload, cancel_event),
        "skill_delegate": lambda payload, cancel_event: run_skill_delegate(gateway, payload, cancel_event),
    }
