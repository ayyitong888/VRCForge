"""Optional, read-only adapter for the Unity Core scan-scope capability."""
from __future__ import annotations

import re
import hashlib
import uuid
from pathlib import Path
from typing import Any

from unity_mcp_core_client import UnityMcpCoreClient
from agent_scan_scope_budget import capture_admission_reason, scope_request
from execution_target import validate_runtime_execution_target


SCAN_SCOPE_SCHEMA = "vrcforge.scan_scope.v1"
CAPTURE_SCAN_SCOPE_TOOL = "vrc_capture_scan_scope"
VALIDATE_SCAN_SCOPE_TOOL = "vrc_validate_scan_scope"
SCAN_SCOPE_TOOLS = (CAPTURE_SCAN_SCOPE_TOOL, VALIDATE_SCAN_SCOPE_TOOL)
SCOPED_SCAN_TOOLS = frozenset({
    "vrcforge_scan_fx_animator",
    "vrcforge_scan_animation_bindings",
    "vrcforge_scan_wardrobe",
    "vrcforge_scan_avatar_controls",
    "vrcforge_scan_avatar_parameters",
    "vrcforge_scan_avatar_materials",
    "vrcforge_scan_materials",
    "vrcforge_scan_avatar_items",
    "vrcforge_read_avatar_descriptor",
})
# Scope measurement includes dependency hashes, not just a bootstrap probe.
# Keep one bounded read deadline; failed captures remain suppressed by the store.
CORE_TIMEOUT_SECONDS = 30.0
MAX_SCOPE_PATHS = 256
_MATERIAL_SCAN_ALIAS = "vrcforge_scan_materials"
_TOKEN = re.compile(r"scope_[0-9a-f]{32}\Z")


def _reason(value: Any, fallback: str) -> str:
    return value[:160] if isinstance(value, str) and value.strip() else fallback


def _asset_path(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 1024 or "\\" in value or ":" in value or "\x00" in value:
        return None
    parts = value.split("/")
    return value if len(parts) > 1 and parts[0] == "Assets" and all(part not in ("", ".", "..") for part in parts) else None


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return None
    normalized = str(parsed)
    return normalized if value == normalized else None


class AgentScanScopeClient:
    """Calls optional Core tools only after a fresh, project-bound capability check."""

    def __init__(self, project_root: str | Path) -> None:
        self._project_root = Path(project_root)
        self.capture_budget = None
        self._core_info = None

    @staticmethod
    def _has_capability(info: Any) -> bool:
        capability = info.get("scanScope") if isinstance(info, dict) else None
        return (
            isinstance(capability, dict)
            and capability.get("schema") == SCAN_SCOPE_SCHEMA
            and capability.get("implementation") == "scan-scope-v1"
            and isinstance(capability.get("tools"), list)
            and len(capability["tools"]) == 2
            and set(capability["tools"]) == set(SCAN_SCOPE_TOOLS)
        )

    def _client_with_capability(self) -> UnityMcpCoreClient | None:
        client = UnityMcpCoreClient(self._project_root, timeout_seconds=CORE_TIMEOUT_SECONDS)
        self._core_info = client.core_info()
        if not self._has_capability(self._core_info):
            return None
        return client

    def capture(self, scan_tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if not isinstance(scan_tool, str) or not scan_tool or len(scan_tool) > 160 or not isinstance(arguments, dict):
                return {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "invalid_scan_request"}
            if scan_tool not in SCOPED_SCAN_TOOLS:
                return {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "scan_scope_undeclared"}
            is_material_alias = scan_tool == _MATERIAL_SCAN_ALIAS
            request = scope_request(scan_tool, arguments)
            if self.capture_budget is not None and not is_material_alias:
                reason = capture_admission_reason(request)
                if reason:
                    return {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": reason}
            client = self._client_with_capability()
            if client is None:
                return {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "capability_unavailable"}
            material_target = self._material_target(client, arguments) if is_material_alias else None
            if is_material_alias:
                if material_target is None:
                    return {"schema": SCAN_SCOPE_SCHEMA, "verified": False,
                            "reason": "scope_capture_identity_unavailable"}
                avatar = material_target["avatar"]
                object_id = avatar["globalObjectId"]
                request = scope_request(
                    scan_tool,
                    {**arguments, "avatarPath": avatar["exactHierarchyPath"]},
                    host_object_ids=["global:" + object_id],
                )
                if self.capture_budget is not None:
                    reason = capture_admission_reason(request, trusted_object_root=True)
                    if reason:
                        return {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": reason}
            if self.capture_budget is not None:
                capture_identity = (self._target_capture_identity(material_target) if is_material_alias
                                    else self._capture_identity(client, request))
                info = {**self._core_info, "captureRootIdentity": capture_identity}
                return self.capture_budget.capture(self._project_root, info, request,
                                                   lambda: self._capture_request(client, request))
            return self._capture_request(client, request)
        except Exception as exc:
            code = getattr(exc, "cause_code", None)
            return {"schema": SCAN_SCOPE_SCHEMA, "verified": False,
                    "reason": _reason(code, "core_unavailable")}

    def _material_target(self, client, arguments):
        """Resolve one fresh, exact Avatar target; caller roots are not authority."""
        envelope = client.call_tool("vrc_get_execution_targets", {"scope": "avatar"})
        structured = envelope.get("structuredContent") if isinstance(envelope, dict) else None
        if not isinstance(structured, dict) or structured.get("success") is not True \
                or not isinstance(structured.get("data"), dict):
            return None
        data = structured["data"]
        targets = data.get("targets")
        if targets is None:
            if data.get("schema") != "vrcforge.execution_target.v1" \
                    or data.get("ambiguous") is not False \
                    or data.get("resolutionCandidateCount") != 1:
                return None
            targets = [data]
        else:
            candidate_count = data.get("totalCandidateCount", data.get("resolutionCandidateCount"))
            if data.get("ambiguous") is not False or type(candidate_count) is not int \
                    or candidate_count != len(targets):
                return None
        if not isinstance(targets, list) or data.get("ambiguous") is True \
                or data.get("hasMore") is True or data.get("complete") is False:
            return None
        if len(targets) != 1:
            return None
        target = targets[0]
        try:
            verified = validate_runtime_execution_target(
                target, project_root=str(self._project_root), required_scope="avatar", refresh_scene_snapshot=False,
            )
        except Exception:
            return None
        if verified.get("scope") != "avatar":
            return None
        avatar = verified.get("avatar")
        scene = verified.get("scene")
        if not isinstance(avatar, dict) or not isinstance(scene, dict):
            return None
        hierarchy_path = avatar.get("exactHierarchyPath")
        object_id = avatar.get("globalObjectId")
        scene_guid = scene.get("guid")
        if not isinstance(hierarchy_path, str) or not hierarchy_path.strip() \
                or not isinstance(object_id, str) or not object_id.startswith("GlobalObjectId_V1-") \
                or not isinstance(scene_guid, str) or not scene_guid.strip():
            return None
        selector = arguments.get("avatarPath") or arguments.get("avatar_path") or ""
        if selector and selector not in (hierarchy_path, avatar.get("name")):
            return None
        return {"avatar": avatar, "scene": scene}

    @staticmethod
    def _target_capture_identity(target):
        if not isinstance(target, dict):
            return None
        avatar, scene = target.get("avatar"), target.get("scene")
        if not isinstance(avatar, dict) or not isinstance(scene, dict):
            return None
        return [["avatar", scene.get("guid"), avatar.get("globalObjectId")]]

    def _capture_identity(self, client, request):
        args = request["arguments"]
        paths = [args[name] for name in ("assetPath", "controllerPath", "clipPath") if args.get(name)]
        if isinstance(args.get("clipPaths"), list):
            paths.extend(args["clipPaths"])
        identities = []
        root = self._project_root.resolve()
        for path in sorted(set(paths)):
            if _asset_path(path) is None:
                return None
            metadata = (root / (path + ".meta")).resolve()
            if not metadata.is_relative_to(root) or not metadata.is_file() or metadata.stat().st_size > 65536:
                return None
            identities.append(["asset", path, hashlib.sha256(metadata.read_bytes()).hexdigest()])
        if not paths or request["scanTool"] == "vrcforge_scan_animation_bindings" and args.get("avatarPath"):
            envelope = client.call_tool("vrc_get_execution_targets", {"scope": "avatar"})
            structured = envelope.get("structuredContent", {})
            if structured.get("success") is not True or not isinstance(structured.get("data"), dict):
                return None
            data = structured["data"]
            targets = data.get("targets", [data] if isinstance(data.get("avatar"), dict) else [])
            if not isinstance(targets, list):
                return None
            selector = args.get("avatarPath", "")
            matches = [target for target in targets if isinstance(target, dict)
                       and isinstance(target.get("avatar"), dict) and (not selector or selector in
                       (target["avatar"].get("exactHierarchyPath"), target["avatar"].get("name")))]
            if len(matches) != 1:
                return None
            target = matches[0]
            scene = target.get("scene", {}).get("guid")
            avatar = target["avatar"].get("globalObjectId")
            if not isinstance(scene, str) or not scene or not isinstance(avatar, str) or not avatar:
                return None
            identities.append(["avatar", scene, avatar])
        return identities

    def _capture_request(self, client, request):
        try:
            result = client.call_tool(CAPTURE_SCAN_SCOPE_TOOL, request)
        except Exception as exc:
            code = getattr(exc, "cause_code", None)
            return {"schema": SCAN_SCOPE_SCHEMA, "verified": False,
                    "reason": _reason(code, "core_unavailable")}

        structured = result.get("structuredContent") if isinstance(result, dict) else None
        if not isinstance(structured, dict) or structured.get("success") is not True \
                or not isinstance(structured.get("data"), dict):
            return {"schema": SCAN_SCOPE_SCHEMA, "verified": False,
                    "reason": _reason(structured.get("message") if isinstance(structured, dict) else None,
                                      "capture_failed")}
        content = structured["data"]
        if content.get("schema") != SCAN_SCOPE_SCHEMA:
            return {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "invalid_scope_response"}
        if content.get("verified") is not True:
            return {"schema": SCAN_SCOPE_SCHEMA, "verified": False,
                    "reason": _reason(content.get("reason"), "scope_unverified")}
        token = content.get("token")
        editor_session = _uuid(content.get("editorSession"))
        raw_paths = content.get("assetPaths")
        captured_at = content.get("capturedAt")
        paths = [_asset_path(path) for path in raw_paths] if isinstance(raw_paths, list) else None
        object_only_request = (
            isinstance(request.get("objectIds"), list) and bool(request["objectIds"])
            and not request.get("assetPaths")
        )
        tool_resolves_scene_objects = request.get("scanTool", "").startswith("vrcforge_scan_") \
            or request.get("scanTool") == "vrcforge_read_avatar_descriptor"
        if not isinstance(token, str) or not _TOKEN.fullmatch(token) or not editor_session \
                or paths is None or (not paths and not (object_only_request or tool_resolves_scene_objects)) \
                or len(paths) > MAX_SCOPE_PATHS \
                or any(path is None for path in paths) or not isinstance(captured_at, str) or not captured_at.strip() \
                or len(captured_at) > 80 or content.get("dependencyCoverage") != "complete":
            return {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "invalid_scope_response"}
        normalized_paths = sorted(set(paths))
        if len(normalized_paths) != len(paths):
            return {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "invalid_scope_response"}
        metadata_paths = content.get("metadataOnlyAssetPaths", [])
        if not isinstance(metadata_paths, list) or any(not isinstance(path, str) for path in metadata_paths) \
                or len(set(metadata_paths)) != len(metadata_paths) or not set(metadata_paths).issubset(normalized_paths):
            return {"schema": SCAN_SCOPE_SCHEMA, "verified": False, "reason": "invalid_scope_response"}
        return {
            "schema": SCAN_SCOPE_SCHEMA,
            "verified": True,
            "token": token,
            "editorSession": editor_session,
            "assetPaths": normalized_paths,
            "capturedAt": captured_at,
            "dependencyCoverage": "complete",
            **({"metadataOnlyAssetPaths": sorted(metadata_paths)} if metadata_paths else {}),
        }

    def validate(self, token: str) -> dict[str, Any]:
        try:
            if not isinstance(token, str) or not _TOKEN.fullmatch(token):
                return self._invalid("invalid_scope_token")
            client = self._client_with_capability()
            if client is None:
                return self._invalid("capability_unavailable")
            result = client.call_tool(VALIDATE_SCAN_SCOPE_TOOL, {"token": token})
        except Exception as exc:
            code = getattr(exc, "cause_code", None)
            return self._invalid(_reason(code, "core_unavailable"))

        structured = result.get("structuredContent") if isinstance(result, dict) else None
        if not isinstance(structured, dict) or structured.get("success") is not True \
                or not isinstance(structured.get("data"), dict):
            return self._invalid(_reason(structured.get("message") if isinstance(structured, dict) else None,
                                         "validation_failed"))
        content = structured["data"]
        if content.get("schema") != SCAN_SCOPE_SCHEMA \
                or type(content.get("valid")) is not bool:
            return self._invalid("invalid_validation_response")
        changed = content.get("changedAssetPaths")
        if not isinstance(changed, list) or len(changed) > MAX_SCOPE_PATHS \
                or any(_asset_path(path) != path for path in changed):
            return self._invalid("invalid_validation_response")
        session = _uuid(content.get("editorSession"))
        reason = _reason(content.get("reason"), "scope_invalid" if content["valid"] is False else "")
        if content["valid"] and (not session or changed or reason):
            return self._invalid("invalid_validation_response")
        return {
            "schema": SCAN_SCOPE_SCHEMA,
            "valid": content["valid"],
            "reason": "" if content["valid"] else reason,
            "changedAssetPaths": list(changed),
            "editorSession": session or "",
        }

    @staticmethod
    def _invalid(reason: str) -> dict[str, Any]:
        return {"schema": SCAN_SCOPE_SCHEMA, "valid": False, "reason": reason[:160],
                "changedAssetPaths": [], "editorSession": ""}
