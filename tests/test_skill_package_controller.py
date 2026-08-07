from __future__ import annotations

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import dashboard_server
from skill_package_controller import SkillPackageController


ROOT = Path(__file__).parents[1]
METHODS = {
    "import_skill_package_sync",
    "set_skill_package_enabled_sync",
    "uninstall_skill_package_sync",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def test_skill_package_controller_keeps_exact_facades_and_static_collection() -> None:
    dashboard_path = ROOT / "dashboard_server.py"
    controller_path = ROOT / "skill_package_controller.py"
    dashboard_source = dashboard_path.read_text(encoding="utf-8")
    controller_source = controller_path.read_text(encoding="utf-8")
    dashboard_tree = ast.parse(dashboard_source)
    dashboard_functions = {
        node.name: node
        for node in dashboard_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    controller_class = _class(controller_path, "SkillPackageController")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in controller_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_impl_")
    }

    assert set(implementations) == METHODS
    assert "from skill_package_controller import SkillPackageController" in dashboard_source
    assert "_SKILL_PACKAGE_CONTROLLER = SkillPackageController" in dashboard_source
    assert SkillPackageController.__slots__ == ("_host",)
    assert "set_skill_package_safe_mode_sync" not in controller_source
    assert "trust_skill_package_signer_sync" not in controller_source
    assert "revoke_skill_package_signer_sync" not in controller_source
    assert "block_skill_package_sync" not in controller_source
    assert "export_skill_package_sync" not in controller_source
    assert "capture_path_to_skill_sync" not in controller_source
    assert "user_skill_lock" not in controller_source

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
        assert isinstance(statement.value, ast.Call)
        assert isinstance(statement.value.func, ast.Attribute)
        assert statement.value.func.attr == f"_impl_{name}"


def test_skill_package_controller_uses_dashboard_host_late_bound(monkeypatch) -> None:
    controller = dashboard_server._SKILL_PACKAGE_CONTROLLER
    assert controller.SKILL_PACKAGE_WRITE_LOCK is dashboard_server.SKILL_PACKAGE_WRITE_LOCK
    preview = SimpleNamespace(as_dict=lambda: {"packageId": "late-bound"})
    service = SimpleNamespace(preflight_import=lambda *_args, **_kwargs: preview)
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: service)

    result = dashboard_server.import_skill_package_sync({"packagePath": "example.vsk", "dryRun": True})

    assert result == {"ok": True, "dryRun": True, "preview": {"packageId": "late-bound"}}
