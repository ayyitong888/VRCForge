"""Store-owned suppression of unavailable scope captures, never validity proof.

Memory is bounded to 128 hashed scope keys and lasts only as long as its store.
Keys include the exact project and fresh Core instance; no shared global state,
external interface, process, asset write or retained credential is introduced.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading


def scope_request(tool, arguments):
    args = deepcopy(arguments)
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
    return {"scanTool": tool, "arguments": args}


class ScopeCaptureBudget:
    def __init__(self):
        self._lock = threading.RLock()
        self._unavailable = {}

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
            if key in self._unavailable:
                return {"schema": "vrcforge.scan_scope.v1", "verified": False,
                        "reason": "capture_suppressed:" + self._unavailable[key]}
            result = operation()
            if result.get("verified") is not True:
                if len(self._unavailable) >= 128:
                    self._unavailable.pop(next(iter(self._unavailable)))
                self._unavailable[key] = str(result.get("reason", "scope_unavailable"))[:120]
            return result
