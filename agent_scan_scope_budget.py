"""Store-owned suppression of unavailable scope captures, never validity proof.

Memory and private timeout records are bounded to 128 hashed scope keys.
Keys include the exact project and fresh Core instance. Connections are owned
by one budget operation; no external interface, asset write or credential.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading


_SCAN_TOOL_ALIASES = {
    "vrcforge_scan_materials": "vrcforge_scan_avatar_materials",
}


def scope_request(tool, arguments, *, host_object_ids=None):
    args = deepcopy(arguments)
    canonical_tool = _SCAN_TOOL_ALIASES.get(tool, tool)
    if tool == "vrcforge_scan_materials":
        # Material roots come only from the host's validated ExecutionTarget.
        # Model-supplied roots are never forwarded to Core.
        for name in ("objectIds", "object_ids", "assetPaths", "asset_paths", "assetPath", "asset_path"):
            args.pop(name, None)
    avatar = (args.get("avatar_path") or args.get("avatarPath") or "") if tool == "vrcforge_scan_avatar_controls" else (
        args.get("source_avatar_path") or args.get("sourceAvatarPath")
        or args.get("avatar_path") or args.get("avatarPath") or "")
    if any(name in args for name in ("avatar_path", "avatarPath", "source_avatar_path", "sourceAvatarPath")):
        args["avatarPath"] = str(avatar).strip()
    for snake, camel in (("controller_path", "controllerPath"), ("clip_paths", "clipPaths")):
        if snake in args or camel in args:
            args[camel] = args.get(snake) or args.get(camel)
    if "include_all_project_clips" in args or "includeAllProjectClips" in args:
        args["includeAllProjectClips"] = bool(args.get("include_all_project_clips") or args.get("includeAllProjectClips"))
    request = {"scanTool": canonical_tool, "arguments": args}
    if host_object_ids is not None:
        request["objectIds"] = list(host_object_ids)
    return request


_GLOBAL_OBJECT_ROOT = re.compile(r"global:GlobalObjectId_V1-[^\x00\\/:]{1,500}\Z")


def capture_admission_reason(request, *, trusted_object_root=False):
    """Avoid unbounded scene capture before it can enter Core's uncancellable queue."""
    args = request["arguments"]
    asset_roots = any(args.get(name) for name in ("assetPath", "controllerPath", "clipPath", "clipPaths"))
    object_ids = request.get("objectIds")
    bounded_material_object_root = (
        trusted_object_root is True
        and request.get("scanTool") == "vrcforge_scan_avatar_materials"
        and isinstance(object_ids, list)
        and len(object_ids) == 1
        and isinstance(object_ids[0], str)
        and _GLOBAL_OBJECT_ROOT.fullmatch(object_ids[0]) is not None
    )
    if args.get("includeAllProjectClips") or (not asset_roots and not bounded_material_object_root) or (
            request["scanTool"] == "vrcforge_scan_animation_bindings" and args.get("avatarPath")):
        return "scope_capture_unbounded"
    return None


class ScopeCaptureBudget:
    def __init__(self, path=None):
        self._lock = threading.RLock()
        self._unavailable = {}
        self._path = Path(path) if path is not None else None

    @contextmanager
    def _connection(self, create=False):
        if create:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=0.1)
        try:
            if create:
                connection.execute("CREATE TABLE IF NOT EXISTS capture_failures (cache_key TEXT PRIMARY KEY, reason TEXT NOT NULL)")
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _cache_key(key):
        return hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()

    def _stored_timeout(self, key):
        try:
            if self._path is not None and self._path.is_file():
                with self._connection() as connection:
                    row = connection.execute("SELECT reason FROM capture_failures WHERE cache_key=?", (self._cache_key(key),)).fetchone()
                    return "unity_core_timeout" if row and row[0] == "unity_core_timeout" else None
        except (OSError, sqlite3.Error):
            pass
        return None

    def _remember_timeout(self, key, reason):
        if self._path is None or reason != "unity_core_timeout":
            return
        try:
            with self._connection(create=True) as connection:
                connection.execute("INSERT OR REPLACE INTO capture_failures VALUES (?, ?)", (self._cache_key(key), reason))
                connection.execute("DELETE FROM capture_failures WHERE cache_key NOT IN (SELECT cache_key FROM capture_failures ORDER BY rowid DESC LIMIT 128)")
        except (OSError, sqlite3.Error):
            pass

    @staticmethod
    def _key(project, info, request):
        instance = info.get("instanceId") if isinstance(info, dict) else None
        if not isinstance(instance, str) or not instance:
            return None
        args = request["arguments"]
        paths = [args[name] for name in ("assetPath", "controllerPath", "clipPath") if args.get(name)]
        if isinstance(args.get("clipPaths"), list):
            paths.extend(args["clipPaths"])
        avatar = args.get("avatarPath", "") if not paths or request["scanTool"] == "vrcforge_scan_animation_bindings" else ""
        roots = {"paths": sorted(set(paths)), "avatar": avatar,
                 "allProjectClips": args.get("includeAllProjectClips", False),
                 "identity": info.get("captureRootIdentity")}
        if not roots["identity"]:
            return None
        digest = hashlib.sha256(json.dumps(roots, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return str(Path(project).resolve()).casefold(), instance, digest

    def capture(self, project, info, request, operation):
        key = self._key(project, info, request)
        if key is None:
            return {"schema": "vrcforge.scan_scope.v1", "verified": False,
                    "reason": "scope_capture_identity_unavailable"}
        with self._lock:
            reason = self._unavailable.get(key) or self._stored_timeout(key)
            if reason:
                return {"schema": "vrcforge.scan_scope.v1", "verified": False,
                        "reason": "capture_suppressed:" + reason}
            result = operation()
            if result.get("verified") is not True:
                if len(self._unavailable) >= 128:
                    self._unavailable.pop(next(iter(self._unavailable)))
                self._unavailable[key] = str(result.get("reason", "scope_unavailable"))[:120]
                self._remember_timeout(key, self._unavailable[key])
            return result
