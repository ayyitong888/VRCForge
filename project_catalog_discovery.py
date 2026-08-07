from __future__ import annotations

from typing import Any


class ProjectCatalogDiscovery:
    """Discover external Unity project catalogues through Dashboard-owned helpers."""

    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl_discover_vcc_projects(self) -> list[str]:
        candidates = [
            self._host.Path(self._host.os.environ.get("LOCALAPPDATA", "")) / "VRChatCreatorCompanion" / "settings.json",
            self._host.Path(self._host.os.environ.get("LOCALAPPDATA", "")) / "VRChatCreatorCompanion" / "vrc-get-settings.json",
            self._host.Path(self._host.os.environ.get("APPDATA", "")) / "VRChatCreatorCompanion" / "settings.json",
            self._host.Path(self._host.os.environ.get("APPDATA", "")) / "VRChatCreatorCompanion" / "vrc-get-settings.json",
        ]
        return self._host.discover_projects_from_settings_files(candidates)

    def _impl_discover_alcom_projects(self) -> list[str]:
        candidates = [
            self._host.Path(self._host.os.environ.get("LOCALAPPDATA", "")) / "VRChatCreatorCompanion" / "vrc-get-settings.json",
            self._host.Path(self._host.os.environ.get("APPDATA", "")) / "VRChatCreatorCompanion" / "vrc-get-settings.json",
            self._host.Path(self._host.os.environ.get("LOCALAPPDATA", "")) / "ALCOM" / "settings.json",
            self._host.Path(self._host.os.environ.get("APPDATA", "")) / "ALCOM" / "settings.json",
            self._host.Path(self._host.os.environ.get("LOCALAPPDATA", "")) / "Alcom" / "settings.json",
            self._host.Path(self._host.os.environ.get("APPDATA", "")) / "Alcom" / "settings.json",
            self._host.Path(self._host.os.environ.get("LOCALAPPDATA", "")) / "vrc-get" / "settings.json",
            self._host.Path(self._host.os.environ.get("APPDATA", "")) / "vrc-get" / "settings.json",
        ]
        return self._host.discover_projects_from_settings_files(candidates)

    def _impl_discover_projects_from_settings_files(self, candidates: list[Path]) -> list[str]:
        projects: list[str] = []
        for settings_path in candidates:
            if not settings_path.exists():
                continue
            raw_text = ""
            try:
                raw_text = settings_path.read_text(encoding="utf-8-sig")
                payload = self._host.json.loads(raw_text)
            except Exception:  # noqa: BLE001
                projects.extend(self._host.extract_windows_paths_from_text(raw_text or settings_path.read_text(errors="ignore")))
                continue
            projects.extend(self._host.extract_project_paths_from_json(payload))
        return sorted(
            {
                self._host.normalize_path_string(project)
                for project in projects
                if project and self._host.is_unity_project_path(self._host.Path(self._host.normalize_path_string(project)))
            },
            key=str.casefold,
        )

    def _impl_extract_project_paths_from_json(self, payload: Any) -> list[str]:
        paths: list[str] = []

        def visit(value: Any, key_hint: str = "") -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    lowered = str(key).casefold()
                    if lowered in {"userprojects", "projects", "recentprojects", "knownprojects"}:
                        visit(item, lowered)
                    elif lowered in {"path", "projectpath", "project", "directorypath"}:
                        if isinstance(item, str) and item.strip():
                            paths.append(self._host.normalize_path_string(item))
                    elif key_hint in {"userprojects", "projects", "recentprojects", "knownprojects"}:
                        visit(item, key_hint)
            elif isinstance(value, list):
                for item in value:
                    visit(item, key_hint)
            elif isinstance(value, str) and key_hint in {"userprojects", "projects", "recentprojects", "knownprojects"}:
                paths.append(self._host.normalize_path_string(value))

        visit(payload)
        return paths

    def _impl_extract_windows_paths_from_text(self, value: str) -> list[str]:
        import re

        paths: list[str] = []
        for match in re.finditer(r"[A-Za-z]:\\\\[^\"\\r\\n,]+(?:\\\\[^\"\\r\\n,]+)*", value):
            candidate = match.group(0).replace("\\\\", "\\").strip()
            if "\\unity" in candidate.casefold() or "\\projects" in candidate.casefold():
                paths.append(self._host.normalize_path_string(candidate))
        return paths

    def _impl_discover_unity_hub_projects(self) -> list[dict[str, str]]:
        projects: list[dict[str, str]] = []
        seen: set[str] = set()
        for hub_projects in [
            self._host.Path(self._host.os.environ.get("APPDATA", "")) / "UnityHub" / "projects-v1.json",
            self._host.Path(self._host.os.environ.get("LOCALAPPDATA", "")) / "UnityHub" / "projects-v1.json",
        ]:
            if not hub_projects.exists():
                continue
            try:
                payload = self._host.json.loads(hub_projects.read_text(encoding="utf-8-sig"))
            except Exception:  # noqa: BLE001
                continue
            data = payload.get("data") if isinstance(payload, dict) else {}
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                if not isinstance(value, dict):
                    continue
                path = self._host.normalize_path_string(str(value.get("path") or key or "").strip())
                if not path or not self._host.is_unity_project_path(self._host.Path(path)):
                    continue
                key_text = path.casefold()
                if key_text in seen:
                    continue
                seen.add(key_text)
                projects.append(
                    {
                        "name": str(value.get("title") or value.get("name") or self._host.Path(path).name),
                        "path": path,
                        "editorVersion": str(value.get("version") or value.get("unityVersion") or "Unknown"),
                    }
                )

        for project_root in self._host.discover_unity_hub_project_roots():
            if not project_root.exists():
                continue
            for child in sorted(project_root.iterdir(), key=lambda item: item.name.casefold()):
                if not child.is_dir() or not self._host.is_unity_project_path(child):
                    continue
                path = self._host.normalize_path_string(str(child))
                key_text = path.casefold()
                if key_text in seen:
                    continue
                seen.add(key_text)
                projects.append(
                    {
                        "name": child.name,
                        "path": path,
                        "editorVersion": self._host.parse_editor_version(child / "ProjectSettings" / "ProjectVersion.txt"),
                    }
                )
        return projects

    def _impl_discover_unity_hub_project_roots(self) -> list[Path]:
        roots: list[Path] = []
        for project_dir in [
            self._host.Path(self._host.os.environ.get("APPDATA", "")) / "UnityHub" / "projectDir.json",
            self._host.Path(self._host.os.environ.get("LOCALAPPDATA", "")) / "UnityHub" / "projectDir.json",
        ]:
            if not project_dir.exists():
                continue
            try:
                payload = self._host.json.loads(project_dir.read_text(encoding="utf-8-sig"))
            except Exception:  # noqa: BLE001
                continue
            directory = payload.get("directoryPath") if isinstance(payload, dict) else ""
            if isinstance(directory, str) and directory.strip():
                roots.append(self._host.Path(self._host.normalize_path_string(directory)))
        return roots
