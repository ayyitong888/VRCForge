"""Project one enabled, verified .vsk Unity tool through approved file imports.

Successful storage reports pending_compile; only the running Core can establish
compilation, discovery, and invocation readiness.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from prepared_file_imports import (
    capture_regular_file,
    cleanup_owned_import,
    copy_approved_file_create_new,
    prepare_project_asset_target,
)
from skill_packages import SkillPackageService

SCHEMA = "vrcforge.user_unity_tools.v1"
RECORD_SCHEMA = "vrcforge.user_unity_tool_install.v1"
PREPARED_SCHEMA = "vrcforge.prepared-user-unity-tools.v1"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class UserUnityToolService:
    """Prepare/apply one installed package using the same canonical projection."""

    def __init__(self, package_service: SkillPackageService | None = None, *, core_tree_identity: Callable[[Path], dict[str, Any]] | None = None) -> None:
        self.package_service = package_service or SkillPackageService()
        self._core_tree_identity = core_tree_identity

    def repair_baseline(self, project_root: str | os.PathLike[str]) -> dict[str, Any]:
        """Expose the same disk compatibility identity used to validate repairs."""
        if self._core_tree_identity is None:
            raise ValueError("repair baseline cannot be verified for this Core.")
        try:
            identity = self._core_tree_identity(Path(project_root).expanduser().resolve() / "Assets" / "VRCForge")
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("current Core source identity is unavailable.") from exc
        actual = str(identity.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", actual):
            raise ValueError("current Core source identity is invalid.")
        return {
            "available": True, "scope": "disk_source_tree", "sourcePath": "Assets/VRCForge",
            "coreSourceTreeSha256": actual.lower(), "loadedImplementationVerified": False,
        }

    def validate_repair_baseline(self, repairs: Any, project_root: str | os.PathLike[str]) -> None:
        if not repairs:
            return
        if not isinstance(repairs, list):
            raise ValueError("repair baseline cannot be verified for this Core.")
        actual = self.repair_baseline(project_root)["coreSourceTreeSha256"]
        for repair in repairs:
            if not isinstance(repair, dict) or not re.fullmatch(r"[0-9a-fA-F]{64}", str(repair.get("coreSourceTreeSha256") or "")):
                raise ValueError("repair baseline coreSourceTreeSha256 is invalid.")
            if str(repair["coreSourceTreeSha256"]).lower() != actual.lower():
                raise ValueError("core source tree baseline mismatch.")

    def _read_verified_entrypoint(self, metadata: dict[str, Any], relative: str) -> bytes:
        current, payload = self.package_service.verified_installed_entrypoint_bytes(metadata["manifest"]["id"], relative)
        if any(current.get(key) != metadata.get(key) for key in ("package_sha256", "lock_sha256")):
            raise ValueError("Installed package changed while reading its verified contents.")
        return payload

    def verified_descriptor(self, package_id: str, project_root: str | os.PathLike[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return one verified package/descriptor snapshot and its disk-source compatibility check."""
        metadata, _ = self.package_service.verified_installed_entrypoint_bytes(package_id, "manifest.json")
        entries = metadata["manifest"]["entrypoints"]
        if not entries.get("unityTools"):
            raise ValueError("Installed package does not declare entrypoints.unityTools.")
        descriptor = json.loads(self._read_verified_entrypoint(metadata, entries["unityTools"]).decode("utf-8"))
        if not isinstance(descriptor, dict) or descriptor.get("schema") != SCHEMA:
            raise ValueError(f"Unity tool descriptor schema must be {SCHEMA}.")
        tools = descriptor.get("tools")
        if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], dict):
            raise ValueError("The first Unity tool package must contain exactly one tool.")
        tool = tools[0]
        if not isinstance(tool.get("toolId"), str) or not _ID_RE.fullmatch(tool["toolId"]):
            raise ValueError("toolId must be a lowercase identifier.")
        if not isinstance(tool.get("typeName"), str) or not tool["typeName"].strip() or not isinstance(tool.get("inputSchema"), dict):
            raise ValueError("typeName and inputSchema are invalid.")
        description = tool.get("description")
        if not isinstance(description, str) or "when-to-use" not in description.lower() or "when-not-to-use" not in description.lower():
            raise ValueError("tool description must contain when-to-use and when-NOT-to-use.")
        source_path = tool.get("source")
        if not isinstance(source_path, str) or source_path not in entries.values() or not source_path.endswith(".cs"):
            raise ValueError("tool source must be a declared C# package entrypoint.")
        source_name = Path(source_path).name
        if source_name.casefold() == "userunitytooldigeststamp.cs":
            raise ValueError("Tool source conflicts with the generated digest stamp.")
        repairs = descriptor.get("repairs") or []
        if not isinstance(repairs, list) or any(
            not isinstance(repair, dict) or any(not isinstance(repair.get(key), str) or not repair[key] for key in ("toolId", "coreVersion", "toolContractVersion", "coreSourceTreeSha256"))
            for repair in repairs
        ):
            raise ValueError("Each repair baseline must identify toolId, coreVersion, toolContractVersion, and coreSourceTreeSha256.")
        self.validate_repair_baseline(repairs, project_root)
        return metadata, descriptor

    def _installed_payloads(self, package_id: str, project_root: str | os.PathLike[str]) -> tuple[dict[str, str], str, list[dict[str, Any]]]:
        metadata, descriptor = self.verified_descriptor(package_id, project_root)
        manifest = metadata["manifest"]
        package_id = manifest["id"]
        digest = metadata["package_sha256"]
        tool = descriptor["tools"][0]
        source_path = tool["source"]
        source_name = Path(source_path).name
        repairs = descriptor.get("repairs") or []
        source_bytes = self._read_verified_entrypoint(metadata, source_path)
        target_folder = f"Assets/VRCForgeUserTools/{package_id}/Editor"
        type_name = f"P_{_sha(package_id.encode())[:12]}"
        record_tool = {key: tool[key] for key in ("toolId", "typeName", "description", "inputSchema")}
        record_tool.update(source=f"{target_folder}/{source_name}", sourceSha256=_sha(source_bytes))
        record = {
            "schema": RECORD_SCHEMA, "packageId": package_id, "packageVersion": manifest["version"],
            "packageDigest": digest, "enabled": True, "tools": [record_tool],
            "stampType": f"VRCForge.UserTools.{type_name}+DigestStamp",
        }
        if repairs:
            record["repairs"] = repairs
        stamp = (
            "// Generated by VRCForge; do not edit.\n"
            f"namespace VRCForge.UserTools {{ public static class {type_name} {{ public static class DigestStamp {{ "
            f"public const string PackageId = {json.dumps(package_id)}; public const string PackageDigest = {json.dumps(digest)}; "
            "} } }\n"
        ).encode()
        files = [
            {"name": name, "sourcePath": source_path if name == source_name else None,
             "sha256": _sha(payload), "payload": base64.b64encode(payload).decode()}
            for name, payload in ((source_name, source_bytes), ("tool-package.json", _json_bytes(record)), ("UserUnityToolDigestStamp.cs", stamp))
        ]
        return {"id": package_id, "sha256": digest}, target_folder, files

    def prepare_installed(self, package_id: str, project_root: str | os.PathLike[str]) -> dict[str, Any]:
        installed, target_folder, files = self._installed_payloads(package_id, project_root)
        project = Path(project_root).expanduser().resolve()
        for item in files:
            item["target"] = prepare_project_asset_target(project, target_folder, item["name"])
        return {
            "schema": PREPARED_SCHEMA, "installed": installed, "project": str(project),
            "targetFolder": target_folder, "files": files, "status": "prepared", "pendingCompile": True,
        }

    def apply(self, plan: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(plan, dict) or plan.get("schema") != PREPARED_SCHEMA or not isinstance(plan.get("installed"), dict):
            raise ValueError("Prepared user Unity tool plan is invalid.")
        package_info, target_folder, expected_files = self._installed_payloads(plan["installed"].get("id"), plan.get("project"))
        if package_info != plan["installed"] or target_folder != plan.get("targetFolder"):
            raise ValueError("Installed package identity drifted after approval.")
        files = plan.get("files")
        if not isinstance(files, list) or len(files) != len(expected_files):
            raise ValueError("Prepared user Unity tool file set drifted.")
        for item, expected in zip(files, expected_files):
            if not isinstance(item, dict) or {key: value for key, value in item.items() if key != "target"} != expected:
                raise ValueError("Prepared user Unity tool payload drifted from the verified package.")
            target = item.get("target") or {}
            if target.get("targetRelativePath") != f"{target_folder}/{expected['name']}" or target.get("project", {}).get("path") != plan.get("project"):
                raise ValueError("Prepared user Unity tool target drifted.")
        created: list[tuple[Path, dict[str, Any]]] = []
        readback_files: list[dict[str, str]] = []
        owned_parent_relatives: set[str] = set()
        owned_parent_identities: dict[str, dict[str, Any]] = {}
        with tempfile.TemporaryDirectory(prefix="vrcforge-user-tool-") as temp:
            try:
                for item in plan.get("files") or []:
                    payload = base64.b64decode(str(item.get("payload") or ""), validate=True)
                    if _sha(payload) != item.get("sha256"):
                        raise ValueError("Prepared user Unity tool payload drifted.")
                    source = Path(temp) / str(item["name"])
                    source.write_bytes(payload)
                    source_identity, digest = capture_regular_file(source, label="Prepared user Unity tool")
                    target = item["target"]
                    target_path, _digest, ownership = copy_approved_file_create_new(source_identity=source_identity, source_sha256=digest, project_identity=target["project"], assets_identity=target["assets"], parent_identities=[*target.get("parentIdentities", []), *owned_parent_identities.values()], absent_parent_relative_paths=[p for p in target.get("absentParentRelativePaths", []) if str(p).replace("\\", "/") not in owned_parent_relatives], target_relative_path=target["targetRelativePath"])
                    created.append((target_path, ownership))
                    readback_files.append({"path": target["targetRelativePath"], "sha256": _digest})
                    project_path = Path(str(target["project"]["path"]))
                    for identity in ownership.get("createdDirectories", []):
                        directory = Path(str(identity["path"]))
                        relative = directory.relative_to(project_path).as_posix()
                        owned_parent_relatives.add(relative)
                        owned_parent_identities[relative] = identity
                return {
                    "ok": True, "status": "pending_compile", "imported": True, "pendingCompile": True,
                    "packageId": package_info["id"], "packageDigest": package_info["sha256"],
                    "copiedFiles": [item["path"] for item in readback_files],
                    "verified": True,
                    "readback": {"verified": True, "scope": "user_tool_files", "files": readback_files},
                }
            except Exception:
                cleanup_errors: list[str] = []
                for path, ownership in reversed(created):
                    error = cleanup_owned_import(path, ownership)
                    if error:
                        cleanup_errors.append(error)
                if cleanup_errors:
                    raise RuntimeError("User Unity tool apply failed and cleanup was incomplete: " + "; ".join(cleanup_errors))
                raise
