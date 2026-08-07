from __future__ import annotations

import ast
from pathlib import Path

import dashboard_server
from skill_package_projection import SkillPackageProjectionService


ROOT = Path(__file__).parents[1]
DASHBOARD_SERVER_MAX_BYTES = 1_183_300
DASHBOARD_SERVER_MAX_LF_LINES = 26_353
METHODS = {
    "_skill_projection_path_is_link_like", "_resolve_skill_projection_source", "_copy_projected_skill_file",
    "_write_projected_skill_state", "_capture_projected_skill_state", "_restore_projected_skill_state",
    "_set_projected_skills_enabled", "_project_installed_skill", "_projected_skill_name",
    "_set_projected_skill_enabled", "_delete_projected_skill_transaction",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(node for node in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(node, ast.ClassDef) and node.name == name)


def test_projection_service_exact_facades_and_static_collection() -> None:
    dashboard = ROOT / "dashboard_server.py"
    service = ROOT / "skill_package_projection.py"
    source = dashboard.read_text(encoding="utf-8")
    assert "from skill_package_projection import SkillPackageProjectionService" in source
    assert "_SKILL_PACKAGE_PROJECTION = SkillPackageProjectionService" in source
    service_class = _class(service, "SkillPackageProjectionService")
    implementations = {node.name.removeprefix("_impl_") for node in service_class.body if isinstance(node, ast.FunctionDef) and node.name.startswith("_impl_")}
    assert implementations == {name.lstrip("_") for name in METHODS}
    assert SkillPackageProjectionService.__slots__ == ("_host",)
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for name in METHODS:
        facade = functions[name]
        assert len(facade.body) == 1
        assert isinstance(facade.body[0], ast.Return)
        assert f"_impl_{name.lstrip('_')}" in ast.unparse(facade.body[0])
    assert "def import_skill_package_sync" not in service.read_text(encoding="utf-8")
    assert "capture_path_to_skill_sync" not in service.read_text(encoding="utf-8")


def test_projection_service_internal_callbacks_remain_dashboard_late_bound(monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(dashboard_server, "_projected_skill_name", lambda _manifest: called.append("name") or "")
    assert dashboard_server._set_projected_skill_enabled({}, False)["skipped"] is True
    assert called == ["name"]


def test_delete_projection_facade_returns_context_manager() -> None:
    context = dashboard_server._delete_projected_skill_transaction({})
    assert hasattr(context, "__enter__") and hasattr(context, "__exit__")
    with context as result:
        assert result["skipped"] is True


def test_dashboard_projection_facade_respects_size_budget() -> None:
    source = (ROOT / "dashboard_server.py").read_bytes()

    assert len(source) <= DASHBOARD_SERVER_MAX_BYTES
    assert source.count(b"\n") <= DASHBOARD_SERVER_MAX_LF_LINES
