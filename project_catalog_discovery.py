from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ProjectCatalogDiscoveryPorts:
    appdata_path: Callable[[], Path]
    local_appdata_path: Callable[[], Path]
    path_exists: Callable[[Path], bool]
    read_text: Callable[[Path, str | None, str | None], str]
    list_children: Callable[[Path], tuple[Path, ...]]
    path_is_dir: Callable[[Path], bool]
    normalize_path_string: Callable[[str], str]
    is_unity_project_path: Callable[[Path], bool]
    parse_editor_version: Callable[[Path], str]
    read_litedb_projects: Callable[[Path], dict[str, Any]] | None = None


class ProjectCatalogDiscovery:
    """Read only known VCC, ALCOM, and Unity Hub catalogue locations."""

    __slots__ = ("_ports",)

    def __init__(self, ports: ProjectCatalogDiscoveryPorts) -> None:
        self._ports = ports

    def discover_vcc_projects(self) -> list[str]:
        candidates = [
            self._ports.local_appdata_path() / "VRChatCreatorCompanion" / "settings.json",
            self._ports.local_appdata_path() / "VRChatCreatorCompanion" / "vrc-get-settings.json",
            self._ports.appdata_path() / "VRChatCreatorCompanion" / "settings.json",
            self._ports.appdata_path() / "VRChatCreatorCompanion" / "vrc-get-settings.json",
        ]
        return self.discover_projects_from_settings_files(candidates)

    def discover_alcom_projects(self) -> list[str]:
        candidates = [
            self._ports.local_appdata_path() / "VRChatCreatorCompanion" / "vrc-get-settings.json",
            self._ports.appdata_path() / "VRChatCreatorCompanion" / "vrc-get-settings.json",
            self._ports.local_appdata_path() / "ALCOM" / "settings.json",
            self._ports.appdata_path() / "ALCOM" / "settings.json",
            self._ports.local_appdata_path() / "Alcom" / "settings.json",
            self._ports.appdata_path() / "Alcom" / "settings.json",
            self._ports.local_appdata_path() / "vrc-get" / "settings.json",
            self._ports.appdata_path() / "vrc-get" / "settings.json",
        ]
        return self.discover_projects_from_settings_files(candidates)

    def discover_alcom_database_paths(self) -> list[Path]:
        """Locate the official ALCOM/vrc-get LiteDB without opening it."""
        return [
            self._ports.local_appdata_path() / "VRChatCreatorCompanion" / "vcc.liteDb",
            self._ports.appdata_path() / "VRChatCreatorCompanion" / "vcc.liteDb",
        ]

    def discover_projects_from_settings_files(self, candidates: list[Path]) -> list[str]:
        projects: list[str] = []
        for settings_path in candidates:
            if not self._ports.path_exists(settings_path):
                continue
            raw_text = ""
            try:
                raw_text = self._ports.read_text(settings_path, "utf-8-sig", None)
                payload = json.loads(raw_text)
            except Exception:  # noqa: BLE001
                projects.extend(
                    self.extract_windows_paths_from_text(
                        raw_text or self._ports.read_text(settings_path, None, "ignore")
                    )
                )
                continue
            projects.extend(self.extract_project_paths_from_json(payload))
        return sorted(
            {
                self._ports.normalize_path_string(project)
                for project in projects
                if project
                and self._ports.is_unity_project_path(
                    Path(self._ports.normalize_path_string(project))
                )
            },
            key=str.casefold,
        )

    def scan_settings_files(self, candidates: list[Path]) -> dict[str, Any]:
        """Return projects plus honest read/empty/error state for one catalogue."""
        projects: list[str] = []
        checked: list[str] = []
        errors: list[dict[str, str]] = []
        existing = 0
        for settings_path in candidates:
            raw_text = ""
            try:
                present = self._ports.path_exists(settings_path)
            except Exception as exc:  # noqa: BLE001 - retain the failing source.
                errors.append({"path": str(settings_path), "error": str(exc)})
                continue
            if not present:
                continue
            existing += 1
            checked.append(str(settings_path))
            try:
                raw_text = self._ports.read_text(settings_path, "utf-8-sig", None)
                payload = json.loads(raw_text)
                projects.extend(self.extract_project_paths_from_json(payload))
            except Exception as exc:  # noqa: BLE001 - one broken catalogue must not hide others.
                try:
                    fallback = raw_text or self._ports.read_text(settings_path, None, "ignore")
                    projects.extend(self.extract_windows_paths_from_text(fallback))
                except Exception as fallback_exc:  # noqa: BLE001
                    errors.append({"path": str(settings_path), "error": str(fallback_exc)})
                else:
                    errors.append({"path": str(settings_path), "error": str(exc)})
        normalized = sorted(
            {
                self._ports.normalize_path_string(project)
                for project in projects
                if project and self._ports.is_unity_project_path(
                    Path(self._ports.normalize_path_string(project))
                )
            },
            key=str.casefold,
        )
        return {
            "status": "error" if errors and not normalized else ("ready" if normalized else ("empty" if existing else "unavailable")),
            "projects": normalized,
            "checkedPaths": checked,
            "errorCount": len(errors),
            "partial": bool(errors and normalized),
            "errors": errors,
            "cataloguePresent": existing > 0,
        }

    def extract_project_paths_from_json(self, payload: Any) -> list[str]:
        paths: list[str] = []

        def visit(value: Any, key_hint: str = "") -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    lowered = str(key).casefold()
                    if lowered in {"userprojects", "projects", "recentprojects", "knownprojects"}:
                        visit(item, lowered)
                    elif lowered in {"path", "projectpath", "project", "directorypath"}:
                        if isinstance(item, str) and item.strip():
                            paths.append(self._ports.normalize_path_string(item))
                    elif key_hint in {"userprojects", "projects", "recentprojects", "knownprojects"}:
                        visit(item, key_hint)
            elif isinstance(value, list):
                for item in value:
                    visit(item, key_hint)
            elif isinstance(value, str) and key_hint in {
                "userprojects",
                "projects",
                "recentprojects",
                "knownprojects",
            }:
                paths.append(self._ports.normalize_path_string(value))

        visit(payload)
        return paths

    def extract_windows_paths_from_text(self, value: str) -> list[str]:
        paths: list[str] = []
        for match in re.finditer(r"[A-Za-z]:[\\/]+[^\"\r\n,]+", value):
            candidate = match.group(0).replace("\\\\", "\\").strip().rstrip("\\/")
            paths.append(self._ports.normalize_path_string(candidate))
        return paths

    def discover_unity_hub_projects(self) -> list[dict[str, str]]:
        projects: list[dict[str, str]] = []
        seen: set[str] = set()
        for hub_projects in [
            self._ports.appdata_path() / "UnityHub" / "projects-v1.json",
            self._ports.local_appdata_path() / "UnityHub" / "projects-v1.json",
        ]:
            if not self._ports.path_exists(hub_projects):
                continue
            try:
                payload = json.loads(self._ports.read_text(hub_projects, "utf-8-sig", None))
            except Exception:  # noqa: BLE001
                continue
            data = payload.get("data") if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                if not isinstance(value, dict):
                    continue
                path = self._ports.normalize_path_string(str(value.get("path") or key or "").strip())
                if not path or not self._ports.is_unity_project_path(Path(path)):
                    continue
                key_text = path.casefold()
                if key_text in seen:
                    continue
                seen.add(key_text)
                projects.append(
                    {
                        "name": str(value.get("title") or value.get("name") or Path(path).name),
                        "path": path,
                        "editorVersion": str(value.get("version") or value.get("unityVersion") or "Unknown"),
                    }
                )

        for project_root in self.discover_unity_hub_project_roots():
            if not self._ports.path_exists(project_root):
                continue
            if self._ports.path_is_dir(project_root) and self._ports.is_unity_project_path(project_root):
                path = self._ports.normalize_path_string(str(project_root))
                key_text = path.casefold()
                if key_text not in seen:
                    seen.add(key_text)
                    projects.append(
                        {
                            "name": project_root.name,
                            "path": path,
                            "editorVersion": self._ports.parse_editor_version(
                                project_root / "ProjectSettings" / "ProjectVersion.txt"
                            ),
                        }
                    )
            for child in sorted(self._ports.list_children(project_root), key=lambda item: item.name.casefold()):
                if not self._ports.path_is_dir(child) or not self._ports.is_unity_project_path(child):
                    continue
                path = self._ports.normalize_path_string(str(child))
                key_text = path.casefold()
                if key_text in seen:
                    continue
                seen.add(key_text)
                projects.append(
                    {
                        "name": child.name,
                        "path": path,
                        "editorVersion": self._ports.parse_editor_version(
                            child / "ProjectSettings" / "ProjectVersion.txt"
                        ),
                    }
                )
        return projects

    def scan_unity_hub(self) -> dict[str, Any]:
        """Scan Hub while preserving valid empty state and per-file failures."""
        paths = [
            self._ports.appdata_path() / "UnityHub" / "projects-v1.json",
            self._ports.local_appdata_path() / "UnityHub" / "projects-v1.json",
            self._ports.appdata_path() / "UnityHub" / "projectDir.json",
            self._ports.local_appdata_path() / "UnityHub" / "projectDir.json",
        ]
        present = []
        errors: list[dict[str, str]] = []
        for path in paths:
            try:
                if self._ports.path_exists(path):
                    present.append(str(path))
            except Exception as exc:  # noqa: BLE001
                errors.append({"path": str(path), "error": str(exc)})
        for path in present:
            try:
                json.loads(self._ports.read_text(Path(path), "utf-8-sig", None))
            except Exception as exc:  # noqa: BLE001 - malformed Hub state is a scan error.
                errors.append({"path": path, "error": str(exc)})
        try:
            projects = self.discover_unity_hub_projects()
        except Exception as exc:  # noqa: BLE001 - report Hub failure without hiding other catalogues.
            projects = []
            errors.append({"path": "UnityHub", "error": str(exc)})
        return {
            "status": "error" if errors and not projects else ("ready" if projects else ("empty" if present else "unavailable")),
            "projects": projects,
            "checkedPaths": present,
            "errorCount": len(errors),
            "partial": bool(errors and projects),
            "errors": errors,
            "cataloguePresent": bool(present),
        }

    def scan_catalogues(self) -> dict[str, Any]:
        vcc = self.scan_settings_files([
            self._ports.local_appdata_path() / "VRChatCreatorCompanion" / "settings.json",
            self._ports.local_appdata_path() / "VRChatCreatorCompanion" / "vrc-get-settings.json",
            self._ports.appdata_path() / "VRChatCreatorCompanion" / "settings.json",
            self._ports.appdata_path() / "VRChatCreatorCompanion" / "vrc-get-settings.json",
        ])
        alcom = self.scan_settings_files([
            self._ports.local_appdata_path() / "VRChatCreatorCompanion" / "vrc-get-settings.json",
            self._ports.appdata_path() / "VRChatCreatorCompanion" / "vrc-get-settings.json",
            self._ports.local_appdata_path() / "ALCOM" / "settings.json",
            self._ports.appdata_path() / "ALCOM" / "settings.json",
            self._ports.local_appdata_path() / "Alcom" / "settings.json",
            self._ports.appdata_path() / "Alcom" / "settings.json",
            self._ports.local_appdata_path() / "vrc-get" / "settings.json",
            self._ports.appdata_path() / "vrc-get" / "settings.json",
        ])
        database_paths: list[str] = []
        for database_path in self.discover_alcom_database_paths():
            try:
                if self._ports.path_exists(database_path):
                    database_paths.append(str(database_path))
            except Exception as exc:  # noqa: BLE001
                alcom.setdefault("errors", []).append({"path": str(database_path), "error": str(exc)})
                alcom["errorCount"] = int(alcom.get("errorCount") or 0) + 1
        if database_paths:
            alcom["databasePaths"] = database_paths
            reader = self._ports.read_litedb_projects
            if reader is None:
                alcom["databaseStatus"] = "unsupported"
                alcom["databaseReason"] = "ALCOM LiteDB is present but no bundled read-only LiteDB reader is available."
                alcom.setdefault("errors", []).append({"path": database_paths[0], "error": "reader_unsupported"})
                alcom["errorCount"] = int(alcom.get("errorCount") or 0) + 1
                if not alcom["projects"] and alcom["status"] in {"empty", "unavailable"}:
                    alcom["status"] = "unsupported"
            else:
                database_projects: list[str] = []
                reader_errors = 0
                for database_path in database_paths:
                    try:
                        result = reader(Path(database_path))
                    except Exception as exc:  # noqa: BLE001 - one DB must not hide other sources.
                        result = {"status": "error", "projects": [], "error": str(exc)}
                    if not isinstance(result, dict):
                        result = {"status": "error", "projects": [], "error": "invalid_reader_result"}
                    database_projects.extend(str(item) for item in result.get("projects") or [] if isinstance(item, str))
                    if str(result.get("status") or "") == "error":
                        alcom.setdefault("errors", []).append({"path": database_path, "error": str(result.get("error") or "reader_failed")})
                        alcom["errorCount"] = int(alcom.get("errorCount") or 0) + 1
                        reader_errors += 1
                normalized_database_projects = sorted({
                    self._ports.normalize_path_string(item)
                    for item in database_projects
                    if item and self._ports.is_unity_project_path(Path(self._ports.normalize_path_string(item)))
                }, key=str.casefold)
                alcom["projects"] = sorted({*alcom["projects"], *normalized_database_projects}, key=str.casefold)
                alcom["databaseStatus"] = "error" if reader_errors and not normalized_database_projects else ("ready" if normalized_database_projects else "empty")
                alcom["databaseReason"] = "Read-only LiteDB project collection."
                alcom["partial"] = bool(alcom["errorCount"] and alcom["projects"])
                if alcom["errorCount"] and not alcom["projects"]:
                    alcom["status"] = "error"
                elif alcom["projects"]:
                    alcom["status"] = "ready"
                else:
                    alcom["status"] = "empty"
        hub = self.scan_unity_hub()
        sources = {"vcc": vcc, "alcom": alcom, "unityHub": hub}
        all_projects = set(vcc["projects"]) | set(alcom["projects"]) | {
            str(item.get("path") or "") for item in hub["projects"]
        }
        has_error = any(item["status"] == "error" or int(item.get("errorCount") or 0) > 0 for item in sources.values())
        return {
            "status": "error" if has_error and not all_projects else ("ready" if all_projects else ("empty" if any(item["status"] == "empty" for item in sources.values()) else "unavailable")),
            "sources": sources,
            "projectCount": len({path.casefold() for path in all_projects if path}),
        }

    def discover_unity_hub_project_roots(self) -> list[Path]:
        roots: list[Path] = []
        for project_dir in [
            self._ports.appdata_path() / "UnityHub" / "projectDir.json",
            self._ports.local_appdata_path() / "UnityHub" / "projectDir.json",
        ]:
            if not self._ports.path_exists(project_dir):
                continue
            try:
                payload = json.loads(self._ports.read_text(project_dir, "utf-8-sig", None))
            except Exception:  # noqa: BLE001
                continue
            directory = payload.get("directoryPath") if isinstance(payload, dict) else ""
            if isinstance(directory, str) and directory.strip():
                roots.append(Path(self._ports.normalize_path_string(directory)))
        return roots
