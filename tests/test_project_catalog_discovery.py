from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

import dashboard_server
from project_catalog_discovery import ProjectCatalogDiscovery


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
PRE_EXTRACTION_AST_SHA256 = {
    "discover_vcc_projects": "93cdb45894963cd03bd5a18d3a11e9ba0b25152169b34ba85f433ade4df80879",
    "discover_alcom_projects": "b56e7780774c6a5ad6ca3977ca620cd7f1bd09dc4fac346032cca562e4a2d390",
    "discover_projects_from_settings_files": "0b6e682306fc3c6574039c3b5da367bdabe228fc47075181953086f76681d7e5",
    "extract_project_paths_from_json": "c57c80121598b463dfd0674fb5e233d018fc3bc5db04f1807889d8e75cab580a",
    "extract_windows_paths_from_text": "29e9bd43d5e4c917dee6f2026370d8fd722ee5ef6ee060efa3b77107b5276150",
    "discover_unity_hub_projects": "c553d9ef54394fb9f1cc9168792e202a28b667263acfdd732bf9746f047aa8f2",
    "discover_unity_hub_project_roots": "047b612f58874d728468ebeb10b3548309ad10b19dc4c5576a11355f58fe715f",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


class _HostFacadeUnwrapper(ast.NodeTransformer):
    """Normalize the transitional host proxy back to the root function AST."""

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        node = self.generic_visit(node)
        if (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "_host"
        ):
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return node


def _normalized_pre_extraction_ast(node: ast.FunctionDef, name: str) -> str:
    normalized = copy.deepcopy(node)
    normalized = ast.fix_missing_locations(_HostFacadeUnwrapper().visit(normalized))
    assert isinstance(normalized, ast.FunctionDef)
    normalized.name = name
    assert normalized.args.args[0].arg == "self"
    normalized.args.args = normalized.args.args[1:]
    return ast.dump(normalized, include_attributes=False)


def test_project_catalog_keeps_exact_root_facades_static_import_and_narrow_host() -> None:
    dashboard_path = ROOT / "dashboard_server.py"
    service_path = ROOT / "project_catalog_discovery.py"
    dashboard_source = dashboard_path.read_text(encoding="utf-8")
    service_source = service_path.read_text(encoding="utf-8")
    dashboard_functions = {
        node.name: node
        for node in ast.parse(dashboard_source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    service = _class(service_path, "ProjectCatalogDiscovery")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_impl_")
    }

    assert set(implementations) == METHODS
    assert "from project_catalog_discovery import ProjectCatalogDiscovery" in dashboard_source
    assert "_PROJECT_CATALOG_DISCOVERY = ProjectCatalogDiscovery" in dashboard_source
    assert ProjectCatalogDiscovery.__slots__ == ("_host",)
    assert len(dashboard_source.encode("utf-8")) < 1_175_000
    assert len(service_source.encode("utf-8")) > 7_000
    for forbidden in (
        "PROJECT_SNAPSHOT_",
        "PROJECT_SELECTION_LOCK",
        "CURRENT_UNITY_STATUS",
        "discover_running_unity_projects",
        "DASHBOARD_STATE",
        "EVENT_BUS",
        "AGENT_GATEWAY",
        "Doctor",
        "Thread",
        "dashboard_server import",
    ):
        assert forbidden not in service_source

    for name, implementation in implementations.items():
        facade = dashboard_functions[name]
        implementation_args = copy.deepcopy(implementation.args)
        assert implementation_args.args[0].arg == "self"
        implementation_args.args = implementation_args.args[1:]
        assert ast.dump(facade.args, include_attributes=False) == ast.dump(
            implementation_args,
            include_attributes=False,
        )
        assert len(facade.body) == 1
        statement = facade.body[0]
        assert isinstance(statement, ast.Return)
        assert f"_impl_{name}" in ast.unparse(statement)


def test_project_catalog_preserves_pre_extraction_method_ast() -> None:
    service = _class(ROOT / "project_catalog_discovery.py", "ProjectCatalogDiscovery")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_impl_")
    }

    assert set(implementations) == METHODS
    for name, implementation in implementations.items():
        actual = _normalized_pre_extraction_ast(implementation, name)
        assert hashlib.sha256(actual.encode("utf-8")).hexdigest() == PRE_EXTRACTION_AST_SHA256[name]


def test_project_catalog_late_binds_settings_and_hub_helpers(monkeypatch, tmp_path: Path) -> None:
    settings_calls: list[list[Path]] = []
    original_settings_loader = dashboard_server.discover_projects_from_settings_files

    def fake_settings(candidates: list[Path]) -> list[str]:
        settings_calls.append(candidates)
        return ["late"]

    monkeypatch.setattr(dashboard_server, "discover_projects_from_settings_files", fake_settings)
    assert dashboard_server.discover_vcc_projects() == ["late"]
    assert dashboard_server.discover_alcom_projects() == ["late"]
    assert len(settings_calls) == 2
    assert any(path.name == "settings.json" for path in settings_calls[0])
    monkeypatch.setattr(dashboard_server, "discover_projects_from_settings_files", original_settings_loader)

    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"projects": ["late-project"]}', encoding="utf-8")
    monkeypatch.setattr(dashboard_server, "extract_project_paths_from_json", lambda _payload: ["late-project"])
    monkeypatch.setattr(dashboard_server, "normalize_path_string", lambda value: str(value))
    monkeypatch.setattr(dashboard_server, "is_unity_project_path", lambda path: str(path) == "late-project")
    assert dashboard_server.discover_projects_from_settings_files([settings_path]) == ["late-project"]

    hub_calls: list[bool] = []
    monkeypatch.setattr(
        dashboard_server,
        "discover_unity_hub_project_roots",
        lambda: hub_calls.append(True) or [],
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    assert dashboard_server.discover_unity_hub_projects() == []
    assert hub_calls == [True]


def test_project_catalog_leaves_cache_process_selection_and_doctor_at_root() -> None:
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
