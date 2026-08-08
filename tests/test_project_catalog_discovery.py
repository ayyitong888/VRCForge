from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import dashboard_server
from project_catalog_discovery import ProjectCatalogDiscovery, ProjectCatalogDiscoveryPorts


ROOT = Path(__file__).parents[1]
METHODS = {
    "discover_vcc_projects",
    "discover_alcom_projects",
    "discover_projects_from_settings_files",
    "extract_project_paths_from_json",
    "extract_windows_paths_from_text",
    "discover_unity_hub_projects",
    "discover_unity_hub_project_roots",
}


def _service() -> ProjectCatalogDiscovery:
    return ProjectCatalogDiscovery(
        ProjectCatalogDiscoveryPorts(
            appdata_path=lambda: Path(os.environ.get("APPDATA", "")),
            local_appdata_path=lambda: Path(os.environ.get("LOCALAPPDATA", "")),
            path_exists=lambda path: path.exists(),
            read_text=lambda path, encoding, errors: path.read_text(
                encoding=encoding,
                errors=errors,
            ),
            list_children=lambda path: tuple(path.iterdir()),
            path_is_dir=lambda path: path.is_dir(),
            normalize_path_string=dashboard_server.normalize_path_string,
            is_unity_project_path=dashboard_server.is_unity_project_path,
            parse_editor_version=dashboard_server.parse_editor_version,
        )
    )


def _project(path: Path, version: str = "2022.3.22f1") -> Path:
    (path / "Assets").mkdir(parents=True)
    (path / "Packages").mkdir()
    (path / "ProjectSettings").mkdir()
    (path / "ProjectSettings" / "ProjectVersion.txt").write_text(
        f"m_EditorVersion: {version}\n",
        encoding="utf-8",
    )
    return path


def test_project_catalog_is_typed_read_owner_without_host_proxy() -> None:
    service_source = (ROOT / "project_catalog_discovery.py").read_text(encoding="utf-8")
    dashboard_source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    service_tree = ast.parse(service_source)
    dashboard_tree = ast.parse(dashboard_source)
    service = next(
        node
        for node in service_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProjectCatalogDiscovery"
    )
    methods = {
        node.name
        for node in service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    extract_windows = next(
        node
        for node in service.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "extract_windows_paths_from_text"
    )
    regex_patterns = [
        call.args[0].value
        for call in ast.walk(extract_windows)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "finditer"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    ]
    dashboard_bindings = {
        node.name
        for node in dashboard_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }

    assert METHODS <= methods
    assert regex_patterns == [r"[A-Za-z]:\\\\[^\"\\r\\n,]+(?:\\\\[^\"\\r\\n,]+)*"]
    assert ProjectCatalogDiscovery.__slots__ == ("_ports",)
    assert set(ProjectCatalogDiscoveryPorts.__dataclass_fields__) == {
        "appdata_path",
        "local_appdata_path",
        "path_exists",
        "read_text",
        "list_children",
        "path_is_dir",
        "normalize_path_string",
        "is_unity_project_path",
        "parse_editor_version",
    }
    for forbidden in (
        "_host",
        "_impl_",
        "__getattr__",
        "sys.modules",
        "dashboard_server import",
        "AGENT_GATEWAY",
        "DASHBOARD_STATE",
        "CURRENT_UNITY_STATUS",
        "discover_running_unity_projects",
        "write_text(",
        ".unlink(",
        ".mkdir(",
        "subprocess",
        "invoke_unity",
        "register_tool",
    ):
        assert forbidden not in service_source
    assert METHODS.isdisjoint(dashboard_bindings)
    assert "_PROJECT_CATALOG_DISCOVERY" not in dashboard_source
    assert "PROJECT_CATALOG_DISCOVERY = ProjectCatalogDiscovery(" in dashboard_source
    assert "PROJECT_CATALOG_DISCOVERY.discover_vcc_projects()" in dashboard_source
    assert "PROJECT_CATALOG_DISCOVERY.discover_alcom_projects()" in dashboard_source
    assert "PROJECT_CATALOG_DISCOVERY.discover_unity_hub_projects()" in dashboard_source
    registered_names = {
        *dashboard_server.AGENT_GATEWAY._tools,
        *dashboard_server.AGENT_GATEWAY._write_handlers,
    }
    assert not {
        "vrcforge_discover_vcc_projects",
        "vrcforge_discover_alcom_projects",
        "vrcforge_discover_unity_hub_projects",
        "vrcforge_project_catalog",
    } & registered_names


def test_vcc_and_alcom_use_only_known_environment_catalogues(monkeypatch, tmp_path: Path) -> None:
    local = tmp_path / "local"
    roaming = tmp_path / "roaming"
    project = _project(tmp_path / "Avatar Project")
    vcc = local / "VRChatCreatorCompanion" / "settings.json"
    alcom = roaming / "ALCOM" / "settings.json"
    vcc.parent.mkdir(parents=True)
    alcom.parent.mkdir(parents=True)
    vcc.write_text(json.dumps({"userProjects": [str(project)]}), encoding="utf-8")
    alcom.write_text(json.dumps({"projects": [{"path": str(project)}]}), encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))

    service = _service()
    expected = dashboard_server.normalize_path_string(str(project))
    assert service.discover_vcc_projects() == [expected]
    assert service.discover_alcom_projects() == [expected]


def test_vcc_and_alcom_candidate_order_is_exact_and_read_only() -> None:
    checked: list[Path] = []
    service = ProjectCatalogDiscovery(
        ProjectCatalogDiscoveryPorts(
            appdata_path=lambda: Path("roaming"),
            local_appdata_path=lambda: Path("local"),
            path_exists=lambda path: checked.append(path) or False,
            read_text=lambda _path, _encoding, _errors: (_ for _ in ()).throw(
                AssertionError("missing catalogues must not be read")
            ),
            list_children=lambda _path: (),
            path_is_dir=lambda _path: False,
            normalize_path_string=str,
            is_unity_project_path=lambda _path: False,
            parse_editor_version=lambda _path: "Unknown",
        )
    )

    assert service.discover_vcc_projects() == []
    assert checked == [
        Path("local/VRChatCreatorCompanion/settings.json"),
        Path("local/VRChatCreatorCompanion/vrc-get-settings.json"),
        Path("roaming/VRChatCreatorCompanion/settings.json"),
        Path("roaming/VRChatCreatorCompanion/vrc-get-settings.json"),
    ]
    checked.clear()
    assert service.discover_alcom_projects() == []
    assert checked == [
        Path("local/VRChatCreatorCompanion/vrc-get-settings.json"),
        Path("roaming/VRChatCreatorCompanion/vrc-get-settings.json"),
        Path("local/ALCOM/settings.json"),
        Path("roaming/ALCOM/settings.json"),
        Path("local/Alcom/settings.json"),
        Path("roaming/Alcom/settings.json"),
        Path("local/vrc-get/settings.json"),
        Path("roaming/vrc-get/settings.json"),
    ]


def test_settings_json_and_bounded_text_fallback_preserve_path_rules(tmp_path: Path) -> None:
    project = _project(tmp_path / "Unity Projects" / "Avatar")
    valid = tmp_path / "settings.json"
    valid.write_text(
        json.dumps(
            {
                "projects": [
                    str(project),
                    {"projectPath": str(project)},
                    {"ignored": str(tmp_path / "not-a-project")},
                ]
            }
        ),
        encoding="utf-8-sig",
    )
    service = _service()
    assert service.discover_projects_from_settings_files([valid]) == [
        dashboard_server.normalize_path_string(str(project))
    ]

    normalize_only = ProjectCatalogDiscovery(
        ProjectCatalogDiscoveryPorts(
            appdata_path=lambda: Path(),
            local_appdata_path=lambda: Path(),
            path_exists=lambda path: path.exists(),
            read_text=lambda path, encoding, errors: path.read_text(
                encoding=encoding,
                errors=errors,
            ),
            list_children=lambda path: tuple(path.iterdir()),
            path_is_dir=lambda path: path.is_dir(),
            normalize_path_string=lambda value: str(value).replace("\\", "/"),
            is_unity_project_path=lambda _path: True,
            parse_editor_version=lambda _path: "Unknown",
        )
    )
    malformed = tmp_path / "malformed.json"
    malformed.write_text(r'broken "C:\\Unity\\Projects\\Avatar"', encoding="utf-8")
    # Preserve the existing escaped-text matcher exactly; this double-escaped
    # input is read safely but does not pass its legacy Unity/path filter.
    assert normalize_only.discover_projects_from_settings_files([malformed]) == []


def test_settings_first_read_failure_retries_only_with_ignore_errors() -> None:
    reads: list[tuple[str | None, str | None]] = []

    def read_text(_path: Path, encoding: str | None, errors: str | None) -> str:
        reads.append((encoding, errors))
        if errors is None:
            raise UnicodeError("decode failed")
        return r'broken "C:\\Unity\\Projects\\Avatar"'

    service = ProjectCatalogDiscovery(
        ProjectCatalogDiscoveryPorts(
            appdata_path=lambda: Path(),
            local_appdata_path=lambda: Path(),
            path_exists=lambda _path: True,
            read_text=read_text,
            list_children=lambda _path: (),
            path_is_dir=lambda _path: False,
            normalize_path_string=lambda value: value.replace("\\", "/"),
            is_unity_project_path=lambda _path: True,
            parse_editor_version=lambda _path: "Unknown",
        )
    )

    assert service.discover_projects_from_settings_files([Path("settings.json")]) == []
    assert reads == [("utf-8-sig", None), (None, "ignore")]


def test_json_extraction_keeps_original_key_and_nested_list_semantics() -> None:
    service = ProjectCatalogDiscovery(
        ProjectCatalogDiscoveryPorts(
            appdata_path=lambda: Path(),
            local_appdata_path=lambda: Path(),
            path_exists=lambda path: path.exists(),
            read_text=lambda path, encoding, errors: path.read_text(
                encoding=encoding,
                errors=errors,
            ),
            list_children=lambda path: tuple(path.iterdir()),
            path_is_dir=lambda path: path.is_dir(),
            normalize_path_string=lambda value: f"normalized:{value}",
            is_unity_project_path=lambda _path: True,
            parse_editor_version=lambda _path: "Unknown",
        )
    )
    payload = {
        "ignored": {"path": "C:/ignored"},
        "recentProjects": [
            "C:/one",
            {"directoryPath": "C:/two"},
            {"nested": [{"project": "C:/three"}]},
        ],
        "projectPath": "C:/four",
    }

    assert service.extract_project_paths_from_json(payload) == [
        "normalized:C:/one",
        "normalized:C:/two",
        "normalized:C:/three",
        "normalized:C:/four",
    ]


def test_unity_hub_merges_json_and_project_root_without_duplicates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    local = tmp_path / "local"
    roaming = tmp_path / "roaming"
    json_project = _project(tmp_path / "JSON Project", "2022.3.6f1")
    root = tmp_path / "Hub Projects"
    root_project = _project(root / "Root Project", "2022.3.22f1")
    projects_file = roaming / "UnityHub" / "projects-v1.json"
    roots_file = local / "UnityHub" / "projectDir.json"
    projects_file.parent.mkdir(parents=True)
    roots_file.parent.mkdir(parents=True)
    projects_file.write_text(
        json.dumps(
            {
                "data": {
                    str(json_project): {
                        "path": str(json_project),
                        "title": "JSON title",
                        "version": "2022.3.6f1",
                    }
                }
            }
        ),
        encoding="utf-8-sig",
    )
    roots_file.write_text(json.dumps({"directoryPath": str(root)}), encoding="utf-8-sig")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))

    projects = _service().discover_unity_hub_projects()
    by_name = {item["name"]: item for item in projects}
    assert by_name == {
        "JSON title": {
            "name": "JSON title",
            "path": dashboard_server.normalize_path_string(str(json_project)),
            "editorVersion": "2022.3.6f1",
        },
        "Root Project": {
            "name": root_project.name,
            "path": dashboard_server.normalize_path_string(str(root_project)),
            "editorVersion": "2022.3.22f1",
        },
    }


def test_catalog_owner_does_not_absorb_snapshot_process_or_doctor_domains() -> None:
    dashboard_functions = {
        node.name
        for node in ast.parse((ROOT / "dashboard_server.py").read_text(encoding="utf-8")).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in (
        "discover_projects",
        "discover_running_unity_projects",
        "build_project_snapshot_payload",
        "schedule_project_snapshot_refresh",
        "load_persisted_selected_project_path",
        "build_unity_status_snapshot",
        "build_app_doctor_report",
    ):
        assert name in dashboard_functions
