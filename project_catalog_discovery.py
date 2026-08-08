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
        for match in re.finditer(r"[A-Za-z]:\\\\[^\"\\r\\n,]+(?:\\\\[^\"\\r\\n,]+)*", value):
            candidate = match.group(0).replace("\\\\", "\\").strip()
            if "\\unity" in candidate.casefold() or "\\projects" in candidate.casefold():
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
