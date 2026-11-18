from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


# Avatar readiness requires both official SDK packages. The Avatar package
# normally declares Base as a VPM dependency, but both identities are required
# here so partial or stale project metadata cannot produce a false green.
VRCHAT_SDK_PACKAGE_IDS = ["com.vrchat.avatars", "com.vrchat.base"]
NEWTONSOFT_PACKAGE_IDS = ["com.unity.nuget.newtonsoft-json"]


def _detect_required_packages(
    project_path: Path,
    package_ids: list[str],
    detect_package: Callable[[Path, list[str]], dict[str, Any]],
) -> tuple[bool | None, dict[str, Any]]:
    """Detect every required package while preserving one representative result."""

    infos = [detect_package(project_path, [package_id]) for package_id in package_ids]
    installed = (
        True if infos and all(info.get("installed") is True for info in infos)
        else False if any(info.get("installed") is False for info in infos)
        else None
    )
    matching = [info for info in infos if info.get("installed") is installed]
    representative = dict(next(
        (info for info in matching if info.get("resolution") != "pending"), matching[0] if matching else {},
    ))
    read_errors = []
    for info in infos:
        for error in info.get("readErrors") or []:
            if error not in read_errors:
                read_errors.append(error)
    if read_errors:
        representative["readErrors"] = read_errors
    conflicts = [
        {"packageId": info["packageId"], "installedVersion": info["version"], "lockedVersion": info["lockedVersion"]}
        for info in infos if info.get("resolution") == "version_mismatch"
    ]
    if conflicts:
        representative["versionConflicts"] = conflicts
    if installed is False:
        representative["missingPackageIds"] = [
            name for name, info in zip(package_ids, infos) if info.get("installed") is False
        ]
    return installed, representative


def build_unity_dependency_preflight_report(
    project_path: Path,
    *,
    detect_package: Callable[[Path, list[str]], dict[str, Any]],
) -> dict[str, Any]:
    """Report supported project package prerequisites before Core connection."""

    checks: dict[str, dict[str, Any]] = {}
    specs = (
        (
            "vrchatSdk",
            "VRChat Avatar SDK",
            VRCHAT_SDK_PACKAGE_IDS,
            "unity_vrchat_sdk_missing",
            "Check the package plan for the missing SDK package and resolve it in the selected project.",
        ),
        (
            "newtonsoft",
            "Newtonsoft JSON",
            NEWTONSOFT_PACKAGE_IDS,
            "unity_newtonsoft_missing",
            "Open the selected project in its matching Editor and inspect Unity Package Manager resolution.",
        ),
    )
    for key, title, package_ids, _blocker_code, guidance in specs:
        installed, info = _detect_required_packages(project_path, list(package_ids), detect_package)
        package_status = "installed" if installed else "missing" if installed is False else "unknown"
        if info.get("resolution") == "pending":
            package_status = "pending"
        elif (key == "newtonsoft" and package_status == "missing"
              and checks["vrchatSdk"]["status"] in {"installed", "pending"}
              and not (project_path / "Packages" / "packages-lock.json").is_file()):
            # SDK manifests may not be on disk before Unity resolves the graph.
            installed = None
            package_status = "pending"
        check = {
            "status": package_status,
            "title": title,
            "packageIds": list(package_ids),
            "installed": installed,
            "packageId": str(info.get("packageId") or ""),
            "version": str(info.get("version") or ""),
            "source": str(info.get("source") or ""),
        }
        if package_status != "installed":
            check["guidance"] = guidance
        if info.get("declaredBy"):
            check["declaredBy"] = info["declaredBy"]
        if info.get("readErrors"):
            check["readErrors"] = info["readErrors"]
        if package_status == "pending":
            check["message"] = f"Unity has not confirmed resolution of {title} in this project."
        elif info.get("versionConflicts") and installed:
            check["status"] = "unknown"
            check["versionConflicts"] = info["versionConflicts"]
            check["message"] = f"{title} is present but its version differs from the VPM lock."
        elif package_status == "unknown":
            check["message"] = info.get("warning") or "Project metadata does not establish the package state."
        elif package_status == "missing":
            check["missingPackageIds"] = info["missingPackageIds"]
            check["message"] = f"Missing {title} package in the selected project: {', '.join(info['missingPackageIds'])}."
        else:
            suffix = f" {check['version']}" if check["version"] else ""
            check["message"] = f"{title} is detected{suffix}."
        checks[key] = check

    missing_keys = [key for key, check in checks.items() if check["status"] == "missing"]
    unknown_keys = [key for key, check in checks.items() if check["status"] == "unknown"]
    missing = ["vrchat_sdk" if key == "vrchatSdk" else "newtonsoft" for key in missing_keys]
    unknown = ["vrchat_sdk" if key == "vrchatSdk" else "newtonsoft" for key in unknown_keys]
    pending = ["vrchat_sdk" if key == "vrchatSdk" else key for key, check in checks.items() if check["status"] == "pending"]
    blocker_key = next(
        (key for key in ("vrchatSdk", "newtonsoft") if checks[key]["status"] == "missing"),
        "",
    )
    blocker_codes = {key: code for key, _title, _ids, code, _guidance in specs}
    blocker_code = blocker_codes.get(blocker_key, "")
    if blocker_code:
        message = f"{checks[blocker_key]['message']} {checks[blocker_key]['guidance']}"
        status = "error"
        ready: bool | None = False
    elif unknown:
        message = checks[unknown_keys[0]]["message"]
        status = "unknown"
        ready = None
    elif pending:
        message = "Open the selected project in its matching Editor so Unity can resolve the declared packages."
        status = "pending"
        ready = None
    else:
        message = "Required Unity project dependencies are detected."
        status = "ok"
        ready = True
    return {
        "ready": ready,
        "status": status,
        "blockerCode": blocker_code,
        "message": message,
        "missing": missing,
        "unknown": unknown,
        "pending": pending,
        "checks": checks,
    }
