"""Adapt the existing package store and approved Unity execution to user tools."""
from __future__ import annotations

from typing import Any, Callable

from skill_packages import SkillPackageError
from user_unity_tool_service import UserUnityToolService


def prepare_install(arguments: dict[str, Any], _preview: Any, service: UserUnityToolService) -> tuple[dict[str, Any], Any]:
    args = dict(arguments or {})
    package_id = str(args.get("packageId") or "").strip()
    project = str(args.get("projectPath") or "").strip()
    if not package_id or not project:
        raise ValueError("packageId and projectPath are required.")
    plan = service.prepare_installed(package_id, project)
    args["preparedPlan"] = plan
    return args, {
        "ok": True, "status": "prepared", "packageId": package_id,
        "projectPath": project, "packageDigest": plan["installed"]["sha256"],
        "files": [{"path": item["target"]["targetRelativePath"], "sha256": item["sha256"]} for item in plan["files"]],
        "notice": "Installing this package allows its Editor code to run when Unity compiles it.",
    }


def apply_install(arguments: dict[str, Any], service: UserUnityToolService) -> dict[str, Any]:
    plan = arguments.get("preparedPlan")
    if not isinstance(plan, dict):
        raise ValueError("Approved user Unity tool install is missing its prepared plan.")
    return service.apply(plan)


def list_user_tools(arguments: dict[str, Any], service: UserUnityToolService,
                    invoke: Callable[[str, dict[str, Any]], Any]) -> dict[str, Any]:
    project = str(arguments.get("projectPath") or "").strip()
    if not project:
        raise ValueError("projectPath is required.")
    catalog = invoke("vrc_list_user_tools", {})
    if not isinstance(catalog, dict) or not isinstance(catalog.get("tools"), list):
        raise ValueError("Unity did not return a user-tool catalog.")
    selected = str(arguments.get("packageId") or "").strip()
    packages = []
    for item in catalog["tools"]:
        package = dict(item)
        package_id = str(package.get("packageId") or "")
        if selected and selected != package_id:
            continue
        try:
            metadata, _ = service.verified_descriptor(package_id, project)
            if metadata.get("package_sha256") != package.get("packageDigest"):
                raise ValueError("The compiled project package differs from the installed package.")
        except (SkillPackageError, ValueError) as exc:
            package.update(available=False, status="unavailable")
            package["reasons"] = [*package.get("reasons", []), str(exc)]
            package["tools"] = [dict(tool, available=False) for tool in package.get("tools", [])]
        packages.append(package)
    try:
        baseline = service.repair_baseline(project)
    except ValueError as exc:
        baseline = {"available": False, "scope": "disk_source_tree", "reason": str(exc)}
    total = len(catalog["tools"])
    summary = (
        f"{len(packages)} of {total} project packages match the exact packageId filter {selected!r}. "
        "Omit packageId to list all project packages."
        if selected else f"Listed all {total} project packages."
    )
    return {
        "ok": True, "schema": catalog.get("schema"), "tools": packages, "count": len(packages),
        "packageIdFilter": selected, "totalPackageCount": total, "summary": summary,
        "repairBaseline": baseline,
    }


def execution_plan(arguments: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    payload = {}
    for key in ("packageId", "packageDigest", "toolId"):
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is required.")
        payload[key] = value
    params = arguments.get("arguments", {})
    if not isinstance(params, dict):
        raise ValueError("arguments must be an object.")
    payload["arguments"] = params
    return [("vrc_invoke_user_tool", payload)]


def invoke_user_tool(arguments: dict[str, Any], service: UserUnityToolService,
                     invoke: Callable[[str, dict[str, Any]], Any]) -> Any:
    name, payload = execution_plan(arguments)[0]
    project = str(arguments.get("projectPath") or "").strip()
    if not project:
        raise ValueError("projectPath is required.")
    metadata, descriptor = service.verified_descriptor(payload["packageId"], project)
    if metadata.get("package_sha256") != payload["packageDigest"]:
        raise ValueError("Installed package digest is stale.")
    if not any(tool.get("toolId") == payload["toolId"] for tool in descriptor.get("tools", [])):
        raise ValueError("User Unity tool is not declared by the verified package.")
    return invoke(name, payload)
