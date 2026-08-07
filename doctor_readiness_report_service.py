from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class DoctorReadinessReportPorts:
    """Frozen read/report dependencies owned by the Dashboard composition root."""

    build_health: Callable[[], dict[str, Any]]
    serialize_api_config: Callable[[], dict[str, Any]]
    safe_agent_health: Callable[[], dict[str, Any]]
    safe_agent_manifest: Callable[[], dict[str, Any]]
    safe_permission_state: Callable[[], dict[str, Any]]
    selected_project_path_from_health: Callable[[dict[str, Any]], str]
    doctor_check: Callable[..., dict[str, Any]]
    doctor_check_from_component: Callable[..., dict[str, Any]]
    package_doctor_check: Callable[..., dict[str, Any]]
    status_from_counts: Callable[[int, int], str]
    check_skill_registry: Callable[[], dict[str, Any]]
    list_checkpoints: Callable[[dict[str, Any]], dict[str, Any]]
    checkpoint_paths: Callable[[], tuple[str, str]]
    package_manager_status: Callable[[dict[str, Any]], dict[str, Any]]
    merge_registered_checks: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    doctor_summary: Callable[[list[dict[str, Any]]], dict[str, int]]
    doctor_sections: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    redact_local_path: Callable[[str], str]
    version: Callable[[], str]


class DoctorReadinessReportService:
    """Assemble the existing read-only Doctor report from frozen Dashboard ports.

    Health, registered Doctor rules, repair paths, routes, status monitoring and
    all filesystem probes remain owned by the Dashboard root. This service only
    shapes their already-produced observations into the public Doctor schema.
    """

    __slots__ = ("_ports",)

    def __init__(self, ports: DoctorReadinessReportPorts) -> None:
        self._ports = ports

    def build_app_doctor_report(self) -> dict[str, Any]:
        health = self._ports.build_health()
        components = health.get("components") if isinstance(health.get("components"), dict) else {}
        api_config = self._ports.serialize_api_config()
        agent_health = self._ports.safe_agent_health()
        agent_manifest = self._ports.safe_agent_manifest()
        permission = self._ports.safe_permission_state()
        selected_project_value = self._ports.selected_project_path_from_health(health)
        selected_project = Path(selected_project_value) if selected_project_value else None

        checks: list[dict[str, Any]] = [
            self._ports.doctor_check(
                "desktop.runtime",
                "Desktop runtime connection",
                "ok",
                "Desktop can reach the local VRCForge runtime.",
                "The desktop UI needs the loopback runtime for chat, tools, approvals, checkpoints, and diagnostics.",
                "Restart VRCForge or use Retry if this check ever disappears.",
                {"endpoint": "http://127.0.0.1:8757"},
            ),
            self._ports.doctor_check_from_component(
                "backend.online", "Backend online", components.get("backend"),
                "All avatar workflows depend on the local FastAPI runtime.",
                "Restart the backend from the desktop app; if it still fails, open logs and export a support bundle.",
            ),
            self._ports.doctor_check_from_component(
                "unity.project_root", "Unity environment root", components.get("selectedUnityProject"),
                "Unity bridge, plugin, and SDK dependency-version checks need the configured Unity root; Doctor does not inspect avatar assets or scene content.",
                "Select the Unity root folder used by the editor bridge. Project content checks happen later as normal agent tasks.",
            ),
            self._ports.doctor_check_from_component(
                "unity.plugin", "VRCForge Unity plugin", components.get("unityPluginInstalled"),
                "The editor plugin provides the predefined Unity tools used for scans, previews, writes, and rollback validation.",
                "Install or repair the VRCForge Unity plugin for the selected project.",
            ),
            self._ports.doctor_check_from_component(
                "unity.mcp.package", "VRCForge MCP Core", components.get("mcpPackageConfigured"),
                "VRCForge reaches Unity through its project-scoped MCP Core bundled with the editor plugin.",
                "Repair the VRCForge plugin install; no separate MCP package is required.",
            ),
            self._ports.doctor_check_from_component(
                "unity.mcp.bridge", "Unity MCP bridge", components.get("unityMcpBridgeReachable"),
                "Live scans and writes require the Unity editor bridge to be reachable.",
                "Open the selected Unity project, confirm the MCP server is running, then Retry.",
                actions=["repair_unity_bridge", "retry", "open_logs", "copy_diagnostic_summary"], fixable=True,
            ),
            self._ports.doctor_check_from_component(
                "unity.mcp.instance", "Unity instance registration", components.get("unityMcpInstance"),
                "The runtime must target the correct Unity editor instance before tool calls are reliable.",
                "Focus the Unity project, check MCP instance selection, or restart the bridge.",
                actions=["repair_unity_bridge", "retry", "open_logs", "copy_diagnostic_summary"], fixable=True,
            ),
            self._ports.doctor_check_from_component(
                "unity.tools", "VRCForge Unity tools", components.get("vrcForgeUnityTools"),
                "VRCForge uses predefined Unity tools for live editor access; Doctor only checks that the tool surface is registered.",
                "Repair the VRCForge plugin and wait for Unity compile to finish.",
            ),
            self._ports.package_doctor_check(
                "package.vrchat_sdk", "VRChat SDK", selected_project,
                ["com.vrchat.avatars", "com.vrchat.base"],
                "Avatar validation, expression menus, parameters, and VRChat build checks need the SDK packages.",
                "Install the VRChat Avatar SDK through VCC, ALCOM, or vrc-get.",
            ),
            self._ports.package_doctor_check(
                "package.modular_avatar", "Modular Avatar", selected_project,
                ["nadena.dev.modular-avatar"],
                "Outfit and menu workflows prefer Modular Avatar because it keeps edits non-destructive.",
                "Install Modular Avatar if the avatar/outfit workflow needs MA components.", optional=True,
            ),
            self._ports.package_doctor_check(
                "package.vrcfury", "VRCFury", selected_project,
                ["com.vrcfury.vrcfury"],
                "VRCFury components can affect generated controllers and conflict analysis.",
                "Install VRCFury only when the avatar uses it; otherwise this warning is informational.", optional=True,
            ),
            self._ports.doctor_check_from_component(
                "provider.configured", "Provider configured", components.get("providerConfigPresent"),
                "Model planning needs a configured cloud, local, or fallback provider; manual tools still work without one.",
                "Set a BYOK provider, choose Ollama/local, or continue in manual/read-only mode.",
            ),
        ]

        provider = str(api_config.get("provider") or "")
        provider_requires_key = bool(api_config.get("apiKeyRequired"))
        provider_has_key = bool(api_config.get("apiKeyPresent"))
        provider_status = "warning" if provider_requires_key and not provider_has_key else "unknown"
        if provider == "ollama":
            provider_status = "unknown"
        checks.append(self._ports.doctor_check(
            "provider.test", "Provider test call", provider_status,
            "Provider test has not been run automatically.",
            "Automatic first-run diagnostics must not spend API credits or send project data without an explicit action.",
            "Use Settings > Providers to run text, vision, or structured-output tests when needed.",
            {"provider": provider, "model": api_config.get("model"), "apiKeyPresent": provider_has_key},
            ["retry", "open_settings", "copy_diagnostic_summary"],
        ))
        checks.append(self._ports.doctor_check(
            "provider.local_ollama", "Ollama local provider", "unknown" if provider == "ollama" else "ok",
            "Ollama reachability is checked only when explicitly testing the provider." if provider == "ollama" else "Ollama is not the selected provider.",
            "Local fallback keeps the app usable when cloud providers are unavailable or privacy mode is required.",
            "Select Ollama in provider settings and run a provider test when using local/offline mode.",
            {"provider": provider, "baseUrl": api_config.get("base_url")},
            ["retry", "open_settings", "copy_diagnostic_summary"],
        ))

        gateway_enabled = bool(agent_health.get("enabled"))
        checks.append(self._ports.doctor_check(
            "agent.gateway", "External Agent Gateway", "ok" if gateway_enabled else "warning",
            "Agent Gateway is enabled." if gateway_enabled else "Agent Gateway is disabled.",
            "External Codex, Claude Code, and MCP clients can only use VRCForge through this supervised bridge.",
            "Enable the gateway only when connecting an external agent; keep it disabled otherwise.",
            {"enabled": gateway_enabled, "requiresToken": agent_health.get("requiresToken"), "mcpUrl": agent_health.get("mcpUrl"), "pendingApprovalCount": agent_health.get("pendingApprovalCount"), "allowWriteRequests": agent_health.get("allowWriteRequests")},
            ["retry", "open_settings", "copy_diagnostic_summary"],
        ))

        try:
            skill_check = self._ports.check_skill_registry()
            skill_status = self._ports.status_from_counts(int(skill_check.get("errorCount") or 0), int(skill_check.get("warningCount") or 0))
            checks.append(self._ports.doctor_check(
                "skills.registry", "Skill registry", skill_status,
                "Skill registry is healthy." if skill_status == "ok" else "Skill registry has warnings or errors.",
                "Slash commands, community skills, and external-agent skill lists all depend on registry health.",
                "Open Skill Manager, inspect broken skills, disable unsafe packages, or repair manifests.",
                {"schema": skill_check.get("schema"), "count": skill_check.get("count"), "errorCount": skill_check.get("errorCount"), "warningCount": skill_check.get("warningCount")},
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(self._ports.doctor_check(
                "skills.registry", "Skill registry", "error", f"Skill registry check failed: {exc}",
                "Broken skill registry state can hide capabilities or break startup surfaces.",
                "Open logs, remove the broken skill package, or restart with user skills disabled.", {"error": str(exc)},
            ))

        try:
            checkpoint_payload = self._ports.list_checkpoints({"projectRoot": selected_project_value, "limit": 1})
            checkpoint_log_path, checkpoint_store_dir = self._ports.checkpoint_paths()
            checks.append(self._ports.doctor_check(
                "checkpoint.backend", "Checkpoint backend", "ok" if checkpoint_payload.get("ok") else "warning",
                "Checkpoint timeline is readable." if checkpoint_payload.get("ok") else "Checkpoint timeline could not be read.",
                "Every real write must create a pre-write checkpoint so restore can prove rollback.",
                "Check logs and the checkpoint storage path before approving any write.",
                {"checkpointLogPath": checkpoint_log_path, "checkpointStoreDir": checkpoint_store_dir, "count": checkpoint_payload.get("count")},
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(self._ports.doctor_check(
                "checkpoint.backend", "Checkpoint backend", "error", f"Checkpoint backend failed: {exc}",
                "Writes must be blocked when VRCForge cannot create or read rollback checkpoints.",
                "Open logs and repair checkpoint storage before approving writes.", {"error": str(exc)},
            ))

        try:
            package_manager = self._ports.package_manager_status({"projectPath": selected_project_value})
            preferred_cli = package_manager.get("preferredCli")
            checks.append(self._ports.doctor_check(
                "package.manager", "vrc-get / ALCOM / VPM", "ok" if preferred_cli else "warning",
                f"Preferred package CLI detected: {preferred_cli.get('name')}." if isinstance(preferred_cli, dict) else "No vrc-get or VCC vpm CLI was detected.",
                "Dependency diagnostics and repair flows are clearer when VPM tooling is installed.",
                "Install vrc-get or use VCC/ALCOM UI for dependency changes.",
                {"managers": package_manager.get("managers"), "preferredCli": preferred_cli},
            ))
        except Exception as exc:  # noqa: BLE001
            checks.append(self._ports.doctor_check(
                "package.manager", "vrc-get / ALCOM / VPM", "warning", f"Package manager diagnostics failed: {exc}",
                "Dependency diagnostics help explain missing MA/VRCFury/VRC SDK packages.",
                "Open logs or verify vrc-get/VCC/ALCOM manually.", {"error": str(exc)},
            ))

        external_writes_blocked = not bool(permission.get("allowWriteRequests", True))
        if external_writes_blocked:
            checks.append(self._ports.doctor_check(
                "external.security_contract", "External agent write contract", "warning",
                "External write requests are disabled by permission state.",
                "External agents should request writes; VRCForge must own approval, checkpoint, apply, validation, and restore.",
                "Enable write requests only when a trusted local agent needs supervised writes.", {"permission": permission},
            ))
        else:
            checks.append(self._ports.doctor_check(
                "external.security_contract", "External agent write contract", "ok",
                "External agents can request supervised writes; direct approval still belongs to VRCForge.",
                "This prevents Codex, Claude Code, and other MCP clients from bypassing approval/checkpoint policy.",
                "Keep gateway tokens private and revoke the gateway when external work is finished.",
                {"permission": permission, "writeTargets": len(agent_manifest.get("writeTargets") or [])},
            ))

        checks = self._ports.merge_registered_checks(checks)
        summary = self._ports.doctor_summary(checks)
        return {
            "ok": summary["errorCount"] == 0,
            "schema": "vrcforge.doctor.v1",
            "scope": "vrcforge.environment.v1",
            "projectContentInspected": False,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "version": self._ports.version(),
            "summary": summary,
            "sections": self._ports.doctor_sections(checks),
            "selectedUnityEnvironment": {"configured": bool(selected_project_value), "label": self._ports.redact_local_path(selected_project_value) if selected_project_value else ""},
            "checks": checks,
        }
