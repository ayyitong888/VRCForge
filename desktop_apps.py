from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class DesktopAppCatalogError(RuntimeError):
    pass


_START_APPS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$items = @(Get-StartApps | ForEach-Object {
    [ordered]@{ name = [string]$_.Name; appId = [string]$_.AppID }
})
$items | ConvertTo-Json -Compress
"""


class WindowsAppCatalog:
    def __init__(self, *, timeout_seconds: float = 15.0, cache_seconds: float = 30.0) -> None:
        self.timeout_seconds = max(2.0, min(float(timeout_seconds), 30.0))
        self.cache_seconds = max(0.0, min(float(cache_seconds), 300.0))
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached_apps: list[dict[str, str]] = []

    def list_apps(self, *, refresh: bool = False) -> list[dict[str, str]]:
        if os.name != "nt":
            raise DesktopAppCatalogError("Application discovery is available only on Windows.")
        with self._lock:
            now = time.monotonic()
            if not refresh and self._cached_apps and now - self._cached_at <= self.cache_seconds:
                return [dict(item) for item in self._cached_apps]
            apps = self._query_start_apps()
            self._cached_apps = apps
            self._cached_at = now
            return [dict(item) for item in apps]

    def resolve_app(self, selector: str) -> dict[str, str]:
        normalized = str(selector or "").strip()
        if not normalized:
            raise DesktopAppCatalogError("launch_app requires an app name or AppID from list_apps.")
        if len(normalized) > 512 or any(character in normalized for character in "\r\n\0"):
            raise DesktopAppCatalogError("The app selector is invalid.")
        executable = Path(normalized)
        if executable.is_absolute() and executable.suffix.casefold() == ".exe":
            try:
                resolved = executable.resolve(strict=True)
            except OSError as exc:
                raise DesktopAppCatalogError(f"The executable does not exist: {executable}") from exc
            if not resolved.is_file():
                raise DesktopAppCatalogError(f"The executable is not a file: {resolved}")
            return {"name": resolved.stem, "appId": str(resolved), "launchKind": "executable"}
        apps = self.list_apps()
        app_id_matches = [item for item in apps if item["appId"].casefold() == normalized.casefold()]
        if len(app_id_matches) == 1:
            return app_id_matches[0]
        name_matches = [item for item in apps if item["name"].casefold() == normalized.casefold()]
        if not name_matches:
            raise DesktopAppCatalogError("The app was not found. Call list_apps and use an exact name or AppID.")
        if len(name_matches) > 1:
            app_ids = ", ".join(item["appId"] for item in name_matches[:5])
            raise DesktopAppCatalogError(f"The app name is ambiguous; use one of these AppIDs: {app_ids}")
        return name_matches[0]

    def launch_app(self, selector: str) -> dict[str, str]:
        app = self.resolve_app(selector)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess,
            "CREATE_BREAKAWAY_FROM_JOB",
            0,
        )
        command = [app["appId"]] if app.get("launchKind") == "executable" else ["explorer.exe", f"shell:AppsFolder\\{app['appId']}"]
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env=self._launch_environment(),
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise DesktopAppCatalogError(f"Windows could not launch {app['name']}: {exc}") from exc
        return dict(app)

    @staticmethod
    def _launch_environment() -> dict[str, str]:
        environment: dict[str, str] = {}
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
            value = str(os.environ.get(key) or "").strip()
            if value:
                environment[key] = value
        system_root = environment.get("SystemRoot") or environment.get("WINDIR") or r"C:\Windows"
        environment["PATH"] = os.pathsep.join(
            [str(Path(system_root) / "System32"), str(Path(system_root))]
        )
        return environment

    def _query_start_apps(self) -> list[dict[str, str]]:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _START_APPS_SCRIPT],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                creationflags=creation_flags,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DesktopAppCatalogError(f"Windows application discovery failed: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr.strip() or completed.stdout.strip() or "unknown error")[-1000:]
            raise DesktopAppCatalogError(f"Windows application discovery failed: {detail}")
        try:
            payload: Any = json.loads(completed.stdout.strip() or "[]")
        except json.JSONDecodeError as exc:
            raise DesktopAppCatalogError("Windows application discovery returned invalid JSON.") from exc
        rows = payload if isinstance(payload, list) else [payload]
        apps: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            app_id = str(row.get("appId") or "").strip()
            key = app_id.casefold()
            if not name or not app_id or key in seen:
                continue
            seen.add(key)
            apps.append({"name": name[:300], "appId": app_id[:512]})
        apps.sort(key=lambda item: (item["name"].casefold(), item["appId"].casefold()))
        return apps
