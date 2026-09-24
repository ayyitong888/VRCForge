from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from know_yourself_skill import build_know_yourself_report


@dataclass(frozen=True)
class KnowYourselfReadinessPorts:
    """Frozen read-only facts consumed by the Know Yourself report."""

    load_settings_for_params: Callable[[dict[str, Any]], Any]
    build_unity_status: Callable[[Any], dict[str, Any]]
    build_doctor_report: Callable[[], dict[str, Any]]
    selected_project_path: Callable[[], str]
    unity_editor_path: Callable[[], str]
    parse_editor_version: Callable[[Path], str]
    list_running_unity_processes_strict: Callable[[], list[dict[str, Any]]]
    process_matches_project: Callable[[dict[str, Any], Path], bool]
    read_compile_errors: Callable[[dict[str, Any]], dict[str, Any]]
    normalize_path: Callable[[str], str]
    build_tool_registry: Callable[[], dict[str, Any]]
    build_skill_registry: Callable[[], dict[str, Any]]
    permission_state: Callable[[], dict[str, Any]]
    ensure_dict: Callable[[Any], dict[str, Any]]
    normalize_bool: Callable[[Any, bool], bool]
    project_snapshot: Callable[[], dict[str, Any]] = lambda: {}


class KnowYourselfReadinessService:
    """Build the existing read-only work-start report from observed facts.

    The Dashboard composition root owns all state, process discovery, Core
    clients, diagnostics, registries, routes and lifecycle. This service only
    assembles the existing report and does not create files, processes, locks
    or external communications.
    """

    __slots__ = ("_ports",)

    def __init__(self, ports: KnowYourselfReadinessPorts) -> None:
        self._ports = ports

    def know_yourself_sync(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = self._ports.ensure_dict(params or {})
        settings = self._ports.load_settings_for_params(params)
        unity_status = self._ports.build_unity_status(settings)
        doctor_report = self._ports.build_doctor_report()
        selected_project = str(self._ports.selected_project_path() or "").strip()
        editor_path = str(self._ports.unity_editor_path() or "").strip()
        editor_version = (
            self._ports.parse_editor_version(
                Path(selected_project) / "ProjectSettings" / "ProjectVersion.txt"
            )
            if selected_project
            else ""
        )
        selected_project_running: bool | None = None
        matching_process_ids: list[int] = []
        if selected_project:
            try:
                for process in self._ports.list_running_unity_processes_strict():
                    if not self._ports.process_matches_project(process, Path(selected_project)):
                        continue
                    selected_project_running = True
                    try:
                        matching_process_ids.append(
                            int(process.get("processId") or process.get("pid") or 0)
                        )
                    except (TypeError, ValueError):
                        continue
                if selected_project_running is None:
                    selected_project_running = False
            except Exception:  # noqa: BLE001 - retain the existing bounded unknown result.
                selected_project_running = None

        compile_diagnostics: dict[str, Any] = {}
        unity_readiness = unity_status.get("readiness")
        if isinstance(unity_readiness, dict) and unity_readiness.get("ready") is True:
            try:
                compile_diagnostics = self._ports.read_compile_errors(
                    {**params, "maxErrors": 20, "includeConsoleFallback": True}
                )
            except Exception:  # noqa: BLE001 - a missing diagnostic tool remains bounded unavailable.
                compile_diagnostics = {"ok": False}

        focus_scope = ""
        if selected_project:
            doctor_checks = doctor_report.get("checks")
            if not isinstance(doctor_checks, list):
                doctor_checks = []
            dependency_checks = [
                item
                for item in doctor_checks
                if str(self._ports.ensure_dict(item).get("id") or "")
                in {"unity.plugin", "package.vrchat_sdk", "unity.mcp.package"}
            ]
            focus_scope_material = json.dumps(
                {
                    "selectedProject": self._ports.normalize_path(selected_project).casefold(),
                    "editorVersion": editor_version,
                    "matchingProcessIds": sorted(pid for pid in matching_process_ids if pid > 0),
                    "selectedInstance": str(unity_status.get("instance") or ""),
                    "dependencyChecks": dependency_checks,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            focus_scope = f"focus-{hashlib.sha256(focus_scope_material).hexdigest()[:16]}"
        report = build_know_yourself_report(
            doctor_report=doctor_report,
            unity_status=unity_status,
            tool_registry=self._ports.build_tool_registry(),
            skill_registry=self._ports.build_skill_registry(),
            permission_state=self._ports.permission_state(),
            compile_diagnostics=compile_diagnostics,
            project_context={
                "projectSelected": bool(selected_project),
                "editorVersion": editor_version,
                "editorLaunchConfigured": bool(editor_path and Path(editor_path).is_file()),
                "selectedProjectRunning": selected_project_running,
                "editorFocusScope": focus_scope,
            },
            editor_focus_confirmed=self._ports.normalize_bool(
                params.get("editorFocusConfirmed") or params.get("editor_focus_confirmed"),
                False,
            ),
            editor_focus_scope=str(
                params.get("editorFocusScope") or params.get("editor_focus_scope") or ""
            ),
        )
        try:
            snapshot = self._ports.project_snapshot()
        except Exception:
            snapshot = {"scan": {"status": "error"}}
        discovery = project_discovery_guidance(snapshot)
        report["projectDiscovery"] = discovery
        if not selected_project:
            # Discovery does not establish readiness. Enrich the existing project
            # selection action without bypassing provider/Core/compile checks.
            action = report.get("nextAction", {})
            if action.get("id") == "select_unity_project":
                action["instruction"] = discovery["instruction"]
                report["message"] = discovery["instruction"]
        return report


def project_discovery_guidance(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Describe observed discovery without treating an empty cache as a scan."""
    scan = snapshot.get("scan") or {}
    projects = snapshot.get("projects") or []
    count = sum(1 for item in projects if isinstance(item, dict) and item.get("projectType") != "general")
    sources = (snapshot.get("catalogueScan") or {}).get("sources") or {}
    failed_sources = [name for name, item in sources.items() if item.get("status") == "error" or item.get("errorCount", 0)]
    status = str(scan.get("status") or "unknown")
    if scan.get("refreshing") or status == "refreshing":
        state = "scanning"
        instruction = "Project discovery is running. Read its result after completion, or manually add an existing Unity project folder. Do not report an empty cache as no projects found."
    elif scan.get("error") or status == "error":
        state = "error"
        instruction = "Project discovery failed. Inspect the project scan error in VRCForge diagnostics and retry; an empty result here does not prove there are no projects. You can manually add an existing Unity project folder."
    elif status == "ready" and failed_sources:
        state = "partial" if count else "error"
        instruction = "Some project sources could not be scanned: " + ", ".join(failed_sources) + ". Inspect source diagnostics and retry; keep any discovered projects usable. Do not describe this as a verified empty list. Manual addition remains available."
    elif status == "ready":
        state = "found" if count else "empty"
        instruction = (
            "Select the intended Unity project in VRCForge."
            if count else
            "No Unity projects were found by the completed scan. Open the project picker and add your existing project folder manually. If you have no project yet, create a VRChat project in VCC or ALCOM first; do not claim that an existing project was found."
        )
    else:
        state = "not_checked"
        instruction = "Project discovery has not completed. Refresh the project list or manually add an existing Unity project folder."
    return {
        "state": state,
        "observedUnityProjectCount": count,
        "failedSources": failed_sources,
        "cached": scan.get("cached") is True,
        "updatedAt": str(scan.get("updatedAt") or ""),
        "instruction": instruction,
        "entrypoint": "VRCForge project picker",
        "supportedSources": ["Unity Hub", "VCC", "ALCOM", "running Unity", "manual"],
        "normalWorkflow": [
            "Find or manually add the existing project root containing Assets, Packages and ProjectSettings; select it in VRCForge.",
            "Open that exact project in its supported Unity editor. A Hub/VCC/ALCOM catalogue entry does not prove the editor is running.",
            "Import the matching VRCForge Unity package and wait for compilation. Recheck exact project/instance, Core connection, required tools and compile errors.",
            "Configure a provider for the built-in Agent, or connect an external MCP client using its own model provider. A key saved in VRCForge is not proof that a provider request succeeds.",
            "Run know-yourself again, then the recommended read-only avatar baseline. Only proceed to edits when the live checks pass, using the chosen permission mode.",
        ],
        "projectListOperations": "Add or select a project, refresh discovery, edit its recorded path, or remove it from the VRCForge list. Removing a list entry must not delete project files.",
        "discoveryNeverProvesUnityReady": True,
    }
