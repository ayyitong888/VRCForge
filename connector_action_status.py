"""Short-lived connector self-test summaries, scoped to exact config context.

Owned by one backend process; no persistence or authentication material. Config
files are read only to hash their current bytes and each handle closes at once.
Installation facts continue to come from the installer, not this history.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
from threading import RLock
import time
from typing import Any


class ConnectorActionStatusStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: OrderedDict[tuple[str, str, str], tuple[str, dict[str, Any]]] = OrderedDict()

    @staticmethod
    def _path(value: str) -> str:
        try:
            return os.path.normcase(str(Path(value).expanduser().resolve())) if value else ""
        except (OSError, ValueError):
            return ""

    @staticmethod
    def _digest(path: str) -> str:
        try:
            with open(path, "rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            return "unavailable"

    def record(self, profile: str, project: str, action: dict[str, Any]) -> dict[str, Any]:
        # Do not retain bridge command output, environment, transcript, or error
        # strings that may contain credentials supplied by another process.
        summary = {
            key: action[key] for key in (
                "ok", "client", "action", "configPath", "changed", "installed",
                "removed", "restartRequired", "restartInstruction", "backupPath",
            ) if key in action
        }
        stage = str(action.get("stage") or "")
        if re.fullmatch(r"[a-zA-Z0-9_./-]{1,64}", stage):
            summary["stage"] = stage
        if not summary.get("ok"):
            summary["error"] = "Connector action failed. Check the configuration and retry the connection test."
        handshake = action.get("handshake")
        if isinstance(handshake, dict):
            summary["handshake"] = {
                key: handshake[key] for key in (
                    "ok", "connected", "ready", "preflightOk", "preflightRuntimeOnline",
                    "toolCount", "hasBridgePreflight", "hasRequestApply",
                ) if isinstance(handshake.get(key), (bool, int))
            }
        now = time.time()
        summary["observedAt"] = now
        summary["verificationExpiresAt"] = now + 120
        summary["verificationScope"] = "installation_self_test"
        path = self._path(str(summary.get("configPath") or ""))
        if path:
            key = (self._path(profile), self._path(project), path)
            with self._lock:
                # App/CLI aliases (and custom clients sharing one file) must not
                # keep a successful test after any mutation of that same file.
                for previous in list(self._entries):
                    if previous[0] == key[0] and previous[2] == path:
                        del self._entries[previous]
                self._entries[key] = (self._digest(path), deepcopy(summary))
                while len(self._entries) > 64:
                    self._entries.popitem(last=False)
        return summary

    def current(self, profile: str, project: str, clients: dict[str, Any]) -> dict[str, Any]:
        scope = (self._path(profile), self._path(project))
        result = {}
        with self._lock:
            for client, state in clients.items():
                path = self._path(str(state.get("configPath") or ""))
                stored = self._entries.get((*scope, path))
                if not stored or stored[0] != self._digest(path):
                    continue
                action = deepcopy(stored[1])
                action["client"] = client
                if (
                    not state.get("installed")
                    or action.get("action") != "install"
                    or time.time() >= action["verificationExpiresAt"]
                ):
                    action.pop("handshake", None)
                result[client] = action
        return result
