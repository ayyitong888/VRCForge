from __future__ import annotations

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import dashboard_server
from path_to_skill_controller import PathToSkillDashboardController


ROOT = Path(__file__).parents[1]
METHODS = {
    "_path_to_skill_kwargs",
    "_path_to_skill_file_list",
    "_path_to_skill_vsk_filename",
    "capture_path_to_skill_sync",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def test_path_to_skill_controller_keeps_exact_dashboard_facades_and_small_host() -> None:
    dashboard_path = ROOT / "dashboard_server.py"
    controller_path = ROOT / "path_to_skill_controller.py"
    dashboard_source = dashboard_path.read_text(encoding="utf-8")
    controller_source = controller_path.read_text(encoding="utf-8")
    dashboard_functions = {
        node.name: node
        for node in ast.parse(dashboard_source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    controller = _class(controller_path, "PathToSkillDashboardController")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in controller.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_impl_")
    }

    assert set(implementations) == {name.lstrip("_") for name in METHODS}
    assert "from path_to_skill_controller import PathToSkillDashboardController" in dashboard_source
    assert "_PATH_TO_SKILL_CONTROLLER = PathToSkillDashboardController" in dashboard_source
    assert PathToSkillDashboardController.__slots__ == ("_host",)
    assert len(dashboard_source.encode("utf-8")) < 1_180_000
    assert len(controller_source.encode("utf-8")) > 5_000
    assert "SkillPackageController" not in controller_source
    assert "SkillPackageGovernanceService" not in controller_source
    assert "SKILL_PACKAGE_WRITE_LOCK" not in controller_source
    assert "agent_gateway" not in controller_source

    for name in METHODS:
        facade = dashboard_functions[name]
        implementation = implementations[name.lstrip("_")]
        implementation_args = copy.deepcopy(implementation.args)
        assert implementation_args.args[0].arg == "self"
        implementation_args.args = implementation_args.args[1:]
        assert ast.dump(facade.args, include_attributes=False) == ast.dump(
            implementation_args,
            include_attributes=False,
        )
        assert len(facade.body) == 1
        assert isinstance(facade.body[0], ast.Return)
        assert f"_impl_{name.lstrip('_')}" in ast.unparse(facade.body[0])


def test_path_to_skill_controller_late_binds_dashboard_capture_helpers(monkeypatch) -> None:
    controller = dashboard_server._PATH_TO_SKILL_CONTROLLER
    assert controller.Path is dashboard_server.Path

    captured = SimpleNamespace(
        manifest={"skill_name": "late bound"},
        workflow={"schema": "workflow"},
        skill_markdown="skill",
        source_files={"SKILL.md": "late-bound"},
    )
    called: list[dict[str, object]] = []

    def build(summary: dict[str, object], **kwargs: object) -> SimpleNamespace:
        called.append({"summary": summary, **kwargs})
        return captured

    monkeypatch.setattr(dashboard_server, "build_path_to_skill_source", build)
    monkeypatch.setattr(dashboard_server, "_path_to_skill_kwargs", lambda _params: {"title": "Late bound"})
    monkeypatch.setattr(
        dashboard_server,
        "_path_to_skill_file_list",
        lambda source_files: [{"lateBound": sorted(source_files)}],
    )

    result = dashboard_server.capture_path_to_skill_sync({"summary": {"workflow": "capture"}})

    assert called == [{"summary": {"workflow": "capture"}, "title": "Late bound"}]
    assert result["files"] == [{"lateBound": ["SKILL.md"]}]
