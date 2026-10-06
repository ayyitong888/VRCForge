"""Adapt the existing package store and approved Unity execution to user tools."""
from __future__ import annotations

from typing import Any, Callable

from skill_packages import (
    PackageDisabledError,
    PackageIntegrityError,
    PackageSignatureError,
    SkillPackageError,
)
from user_unity_tool_service import RepairBaselineMismatch, UserUnityToolService


def _repair_compatibility(package: dict[str, Any], descriptor: dict[str, Any] | None,
                          error: ValueError | None) -> dict[str, Any]:
    # Only fixed Core codes and typed failures carry classification evidence.
    conflicts = {"tool_identity_mismatch", "handler_invalid", "schema_hash_mismatch",
                 "source_hash_invalid", "source_path_invalid", "source_hash_mismatch",
                 "package_identity_mismatch", "package_digest_mismatch", "package_digest_invalid",
                 "record_schema_invalid"}
    expired = {"core_baseline_mismatch", "repair_tool_unknown"}
    waiting = {"disabled", "pending_compile", "stale_generated_stamp", "stamp_missing",
               "tools_missing", "record_unsafe", "record_unreadable"}
    raw_codes = [*package.get("reasons", []),
                 *(tool.get("reason") for tool in package.get("tools", []))]
    codes = {code for code in raw_codes if isinstance(code, str) and code in conflicts | expired | waiting}
    if isinstance(error, RepairBaselineMismatch):
        codes.add(error.code)
    elif isinstance(error, PackageDisabledError):
        codes.add(error.code)
    elif isinstance(error, (PackageIntegrityError, PackageSignatureError)):
        codes.add("installed_package_integrity_conflict")
    elif error is not None:
        codes.add("package_verification_unavailable")
    if package.get("enabled") is False:
        codes.add("disabled")
    if codes & (conflicts | {"installed_package_integrity_conflict"}):
        status = "conflict"
    elif codes & (expired | {"baseline_mismatch"}):
        status = "expired"
    elif error is None and package.get("available") is True and not codes:
        status = "valid"
    else:
        status = "unverified"
    report = {
        "schema": "vrcforge.user_tool_repair_compatibility.v1", "status": status,
        "scope": "installed_package_and_running_core_catalog",
        "repairDeclared": bool(descriptor.get("repairs")) if descriptor is not None
                          else True if isinstance(error, RepairBaselineMismatch) else None,
        "reasonCodes": sorted(codes),
        "evidenceBoundary": {
            "valid": "Package checks and the running Core catalog permit invocation; repair behavior was not tested.",
            "expired": "The repair baseline changed or its official tool is unavailable; revalidation is required, not deletion.",
            "conflict": "Package or Core identity checks found a conflict; files are preserved and no repair is applied.",
            "unverified": "Current availability or verification is incomplete; compatibility is not established.",
        }[status],
        "fixVerification": {"status": "unverified", "reason": "original_failure_regression_not_run"},
        "absorption": {"status": "unverified",
                       "requiredEvidence": "official_only_original_failure_retest_with_local_repair_disabled"},
    }
    if isinstance(error, RepairBaselineMismatch):
        report["baseline"] = {"scope": "disk_source_tree", "expected": error.expected, "observed": error.observed}
    return report


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
        descriptor = None
        verification_error = None
        try:
            metadata, descriptor = service.verified_descriptor(package_id, project)
            if metadata.get("package_sha256") != package.get("packageDigest"):
                raise PackageIntegrityError("The compiled project package differs from the installed package.")
        except (SkillPackageError, ValueError) as exc:
            verification_error = exc
            package.update(available=False, status="unavailable")
            if isinstance(exc, PackageDisabledError):
                package["coreEnabled"] = package.get("enabled")
                package["enabled"] = False
                package["disabledBy"] = "vrcforge_package_store"
                package["reasons"] = [*package.get("reasons", []), exc.code]
                package["tools"] = [dict(tool, available=False,
                                         coreReason=tool.get("reason"), reason=exc.code)
                                     for tool in package.get("tools", [])]
            else:
                package["reasons"] = [*package.get("reasons", []), str(exc)]
                package["tools"] = [dict(tool, available=False) for tool in package.get("tools", [])]
        package["repairCompatibility"] = _repair_compatibility(item, descriptor, verification_error)
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
