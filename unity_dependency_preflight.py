from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


# Avatar readiness requires both official SDK packages. The Avatar package
# normally declares Base as a VPM dependency, but both identities are required
# here so partial or stale project metadata cannot produce a false green.
VRCHAT_SDK_PACKAGE_IDS = ["com.vrchat.avatars", "com.vrchat.base"]
NEWTONSOFT_PACKAGE_IDS = ["com.unity.nuget.newtonsoft-json"]


def _unity_package_metadata_is_readable(project_path: Path) -> bool:
    """Return whether package metadata is present and parseable."""

    packages_dir = project_path / "Packages"
    if not packages_dir.is_dir():
        return False
    readable = False
    for manifest_name, sections in (
        ("manifest.json", ("dependencies",)),
        ("packages-lock.json", ("dependencies",)),
        ("vpm-manifest.json", ("locked", "dependencies")),
    ):
        manifest_path = packages_dir / manifest_name
        if not manifest_path.exists():
            continue
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return False
        if isinstance(payload, dict) and any(isinstance(payload.get(section), dict) for section in sections):
            readable = True
    return readable


def _detect_required_packages(
    project_path: Path,
    package_ids: list[str],
    detect_package: Callable[[Path, list[str]], dict[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    """Detect every required package while preserving one representative result."""

    infos = [detect_package(project_path, [package_id]) for package_id in package_ids]
    installed = bool(infos) and all(bool(info.get("installed")) for info in infos)
    representative = next(
        (info for info in infos if info.get("installed")),
        infos[0] if infos else {},
    )
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
            "Install or repair the official VRChat Avatar SDK through VCC or ALCOM, then wait for Unity to finish compiling.",
        ),
        (
            "newtonsoft",
            "VRChat Avatar SDK",
            NEWTONSOFT_PACKAGE_IDS,
            "unity_newtonsoft_missing",
            "Install or repair the official VRChat Avatar SDK through VCC or ALCOM, then wait for Unity to finish compiling.",
        ),
    )
    metadata_readable = _unity_package_metadata_is_readable(project_path)
    for key, title, package_ids, _blocker_code, guidance in specs:
        installed, info = _detect_required_packages(project_path, list(package_ids), detect_package)
        package_status = "installed" if installed else "missing" if metadata_readable else "unknown"
        check = {
            "status": package_status,
            "title": title,
            "packageIds": list(package_ids),
            "installed": installed,
            "packageId": str(info.get("packageId") or ""),
            "version": str(info.get("version") or ""),
            "source": str(info.get("source") or ""),
            "guidance": guidance,
        }
        if package_status == "unknown":
            check["message"] = "Package metadata is missing or unreadable; dependency absence was not asserted."
        elif package_status == "missing":
            check["message"] = f"{title} was not detected in the selected project."
        else:
            suffix = f" {check['version']}" if check["version"] else ""
            check["message"] = f"{title} is detected{suffix}."
        checks[key] = check

    missing_keys = [key for key, check in checks.items() if check["status"] == "missing"]
    unknown_keys = [key for key, check in checks.items() if check["status"] == "unknown"]
    missing = ["vrchat_sdk" if key == "vrchatSdk" else "newtonsoft" for key in missing_keys]
    unknown = ["vrchat_sdk" if key == "vrchatSdk" else "newtonsoft" for key in unknown_keys]
    blocker_key = next(
        (key for key in ("vrchatSdk", "newtonsoft") if checks[key]["status"] == "missing"),
        "",
    )
    blocker_codes = {key: code for key, _title, _ids, code, _guidance in specs}
    blocker_code = blocker_codes.get(blocker_key, "")
    if blocker_code:
        message = "VRChat Avatar SDK setup is incomplete. Install or repair the official Avatar SDK through VCC or ALCOM, then wait for Unity to finish compiling."
        status = "error"
        ready: bool | None = False
    elif unknown:
        message = "Unity project dependency status is unknown because package metadata is missing or unreadable."
        status = "unknown"
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
        "checks": checks,
    }
