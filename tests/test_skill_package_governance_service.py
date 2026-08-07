from __future__ import annotations

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import dashboard_server
from skill_package_governance import SkillPackageGovernanceService


ROOT = Path(__file__).parents[1]
METHODS = {
    "_disable_projected_skills_for_packages",
    "set_skill_package_safe_mode_sync",
    "trust_skill_package_signer_sync",
    "revoke_skill_package_signer_sync",
    "block_skill_package_sync",
}


def _class(path: Path, name: str) -> ast.ClassDef:
    return next(
        node
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def test_governance_service_keeps_exact_dashboard_facades() -> None:
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")
    assert "from skill_package_governance import SkillPackageGovernanceService" in source
    assert "_SKILL_PACKAGE_GOVERNANCE = SkillPackageGovernanceService" in source
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    service_path = ROOT / "skill_package_governance.py"
    service_source = service_path.read_text(encoding="utf-8")
    service = _class(service_path, "SkillPackageGovernanceService")
    implementations = {
        node.name.removeprefix("_impl_"): node
        for node in service.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_impl_")
    }
    assert set(implementations) == {name.lstrip("_") for name in METHODS}
    assert SkillPackageGovernanceService.__slots__ == ("_host",)
    for name in METHODS:
        facade = functions[name]
        implementation = implementations[name.lstrip("_")]
        implementation_args = copy.deepcopy(implementation.args)
        assert implementation_args.args[0].arg == "self"
        implementation_args.args = implementation_args.args[1:]
        assert ast.dump(facade.args, include_attributes=False) == ast.dump(
            implementation_args,
            include_attributes=False,
        )
        assert len(facade.body) == 1 and isinstance(facade.body[0], ast.Return)
        assert f"_impl_{name.lstrip('_')}" in ast.unparse(facade.body[0])
    assert "import_skill_package_sync" not in service_source
    assert "set_skill_package_enabled_sync" not in service_source
    assert "uninstall_skill_package_sync" not in service_source
    assert "capture_path_to_skill_sync" not in service_source
    assert "export_skill_package_sync" not in service_source


def test_governance_service_late_binds_lock_service_and_projection(monkeypatch) -> None:
    calls: list[str] = []

    class Lock:
        def __enter__(self):
            calls.append("lock")

        def __exit__(self, *_args):
            pass

    class Tx:
        def __enter__(self):
            calls.append("transaction")

        def __exit__(self, *_args):
            pass

    service = SimpleNamespace(
        state_transaction=lambda: Tx(),
        set_safe_mode=lambda *_args, **_kwargs: {"disabledSkillIds": ["one"]},
    )
    monkeypatch.setattr(dashboard_server, "SKILL_PACKAGE_WRITE_LOCK", Lock())
    monkeypatch.setattr(dashboard_server, "skill_package_service", lambda: service)
    monkeypatch.setattr(
        dashboard_server,
        "_disable_projected_skills_for_packages",
        lambda received, ids: calls.append(
            f"projection:{received is service}:{ids}"
        )
        or [],
    )
    assert dashboard_server.set_skill_package_safe_mode_sync({"enabled": True})["ok"] is True
    assert calls == ["lock", "transaction", "projection:True:['one']"]
