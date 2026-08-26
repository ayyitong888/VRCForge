from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from unity_mcp_core_client import UnityMcpCoreClient, UnityMcpCoreError
from unity_editor_window_probe import probe_unity_reload_dialog


@dataclass(frozen=True)
class UnityStatusPorts:
    """Read-only Dashboard dependencies for Unity Core readiness snapshots."""

    load_settings: Callable[[], Any]
    selected_project_path: Callable[[], str]
    normalize_path: Callable[[str], str]
    core_installed: Callable[[Path], bool]
    required_tools: tuple[str, ...]


class UnityStatusService:
    """Build stateless project-scoped VRCForge MCP Core readiness snapshots.

    The caller owns Dashboard state, monitoring, routes, caches and lifecycle.
    Each snapshot creates only the existing request-scoped Core client; this
    service owns no threads, locks, tasks, files or external listener.
    """

    __slots__ = ("_ports",)

    def __init__(self, ports: UnityStatusPorts) -> None:
        self._ports = ports

    def build_unity_status_snapshot(
        self,
        settings: Any | None = None,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        settings = settings or self._ports.load_settings()
        settings.unity_mcp_timeout_seconds = min(settings.unity_mcp_timeout_seconds, 10)
        requested_project = (
            str(project_root)
            if project_root is not None
            else str(getattr(settings, "unity_project_path", "") or self._ports.selected_project_path())
        )
        selected_project = self._ports.normalize_path(requested_project)
        selected_project_path = Path(selected_project) if selected_project else None
        if selected_project_path is None:
            return self.build_vrcforge_mcp_core_unavailable_status(
                None,
                "No Unity project is selected.",
                cause_code="unity_project_not_selected",
            )
        if not self._ports.core_installed(selected_project_path):
            return self.build_vrcforge_mcp_core_unavailable_status(
                selected_project_path,
                "The selected project does not contain the VRCForge MCP2 unitypackage.",
                cause_code="unity_core_package_incomplete",
            )
        return self.build_vrcforge_mcp_core_status(selected_project_path, settings)

    def build_vrcforge_mcp_core_unavailable_status(
        self,
        project_root: Path | None,
        error: str,
        *,
        cause_code: str = "unity_core_unavailable",
    ) -> dict[str, Any]:
        project_path = self._ports.normalize_path(str(project_root)) if project_root is not None else ""
        missing = list(self._ports.required_tools)
        return {
            "connected": False,
            "executionReady": False,
            "blockerCode": cause_code,
            "mcpServerReachable": False,
            "unityInstanceRegistered": False,
            "selectedInstanceMatched": False,
            "host": "127.0.0.1",
            "port": 0,
            "instance": "project-scoped",
            "projectPath": project_path,
            "activeInstance": None,
            "instances": [],
            "activeInstanceCount": 0,
            "tools": {
                "ok": False,
                "reachable": False,
                "connected": False,
                "totalTools": 0,
                "vrcForgeToolsCount": 0,
                "toolNames": [],
                "vrcForgeToolNames": [],
                "missingRequiredVrcForgeTools": missing,
                "error": error,
            },
            "mcpHealth": {"ok": False, "protocolVersion": "2026-07-28", "transport": "vrcforge-mcp-core"},
            "unityMcpPackageVersion": "",
            "vrcForgeToolsRegistered": False,
            "missingRequiredVrcForgeTools": missing,
            "output": "",
            "parsed": None,
            "error": error,
            "causeCode": cause_code,
        }

    def build_vrcforge_mcp_core_status(self, project_root: Path, settings: Any) -> dict[str, Any]:
        try:
            tool_items = UnityMcpCoreClient(
                project_root,
                timeout_seconds=max(1, min(int(settings.unity_mcp_timeout_seconds or 10), 30)),
            ).list_tools(exposure_layer="execution")
            names = sorted(
                str(item.get("name") or "")
                for item in tool_items
                if isinstance(item, dict) and str(item.get("name") or "")
            )
            missing = [name for name in self._ports.required_tools if name not in set(names)]
            tools = {
                "ok": True,
                "reachable": True,
                "connected": True,
                "host": "127.0.0.1",
                "port": 0,
                "instance": "project-scoped",
                "totalTools": len(names),
                "defaultToolsCount": 0,
                "vrcForgeToolsCount": len(names),
                "toolNames": names,
                "vrcForgeToolNames": names,
                "missingRequiredVrcForgeTools": missing,
                "onlyDefaultTools": False,
                "output": "",
                "parsed": None,
                "error": "",
            }
            active_instance = {
                "projectPath": str(project_root.resolve()),
                "transport": "vrcforge-mcp-core",
                "cliSelectorStable": True,
                "cliInstanceId": "project-scoped",
            }
            status = {
                "connected": True,
                "executionReady": True,
                "blockerCode": "",
                "mcpServerReachable": True,
                "unityInstanceRegistered": True,
                "selectedInstanceMatched": True,
                "host": "127.0.0.1",
                "port": 0,
                "instance": "project-scoped",
                "projectPath": self._ports.normalize_path(str(project_root)),
                "activeInstance": active_instance,
                "instances": [active_instance],
                "activeInstanceCount": 1,
                "tools": tools,
                "mcpHealth": {"ok": True, "protocolVersion": "2026-07-28", "transport": "vrcforge-mcp-core"},
                "unityMcpPackageVersion": "vrcforge-core-2026-07-28",
                "vrcForgeToolsRegistered": bool(names),
                "missingRequiredVrcForgeTools": missing,
                "output": "",
                "parsed": None,
                "error": "",
            }
            return _apply_editor_readiness(status, project_root)
        except UnityMcpCoreError as exc:
            return {
                "connected": False,
                "executionReady": False,
                "blockerCode": getattr(exc, "cause_code", "unity_core_contract_invalid"),
                "mcpServerReachable": False,
                "unityInstanceRegistered": False,
                "selectedInstanceMatched": False,
                "host": "127.0.0.1",
                "port": 0,
                "instance": "project-scoped",
                "projectPath": self._ports.normalize_path(str(project_root)),
                "activeInstance": None,
                "instances": [],
                "activeInstanceCount": 0,
                "tools": {
                    "ok": False,
                    "reachable": False,
                    "totalTools": 0,
                    "vrcForgeToolsCount": 0,
                    "missingRequiredVrcForgeTools": list(self._ports.required_tools),
                    "error": str(exc),
                },
                "mcpHealth": {"ok": False, "transport": "vrcforge-mcp-core"},
                "unityMcpPackageVersion": "vrcforge-core-2026-07-28",
                "vrcForgeToolsRegistered": False,
                "missingRequiredVrcForgeTools": list(self._ports.required_tools),
                "output": "",
                "parsed": None,
                "error": str(exc),
                "causeCode": getattr(exc, "cause_code", "unity_core_contract_invalid"),
            }



def _apply_editor_readiness(status: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Inspect, but never act on, the exact project's native Reload dialog."""

    blocker = probe_unity_reload_dialog(project_root)
    if blocker.get("blocked") is True:
        status["executionReady"] = False
        status["blockerCode"] = str(blocker.get("blockerCode") or "unity_editor_reload_dialog")
        status["editorBlocker"] = blocker
    elif isinstance(blocker.get("probeError"), dict):
        status["executionReady"] = False
        status["blockerCode"] = str(
            blocker["probeError"].get("code") or "unity_editor_window_probe_failed"
        )
        status["editorBlocker"] = blocker
    return status
