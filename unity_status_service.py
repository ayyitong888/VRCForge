from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from unity_mcp_core_client import UnityMcpCoreClient, UnityMcpCoreError


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
        selected_project = self._ports.normalize_path(
            str(project_root) if project_root is not None else self._ports.selected_project_path()
        )
        selected_project_path = Path(selected_project) if selected_project else None
        if selected_project_path is None:
            return self.build_vrcforge_mcp_core_unavailable_status(
                None,
                "No Unity project is selected.",
            )
        if not self._ports.core_installed(selected_project_path):
            return self.build_vrcforge_mcp_core_unavailable_status(
                selected_project_path,
                "The selected project does not contain the VRCForge MCP2 unitypackage.",
            )
        return self.build_vrcforge_mcp_core_status(selected_project_path, settings)

    def build_vrcforge_mcp_core_unavailable_status(
        self,
        project_root: Path | None,
        error: str,
    ) -> dict[str, Any]:
        project_path = self._ports.normalize_path(str(project_root)) if project_root is not None else ""
        missing = list(self._ports.required_tools)
        return {
            "connected": False,
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
            return {
                "connected": True,
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
        except UnityMcpCoreError as exc:
            return {
                "connected": False,
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
            }
