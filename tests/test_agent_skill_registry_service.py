from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from agent_gateway import AgentGateway
from agent_skill_registry import AgentSkillRegistryService


REPO_ROOT = Path(__file__).parents[1]
AGENT_GATEWAY_MAX_BYTES = 480_756
AGENT_GATEWAY_MAX_LF_LINES = 10_502
MOVED_METHODS = {
    "_builtin_skill_definitions",
    "_skill_from_builtin_group",
    "_skill_from_tool",
    "_skill_from_write_handler",
    "_skill_dependency_visible",
    "_load_user_skills",
    "_load_projected_skill_state",
    "_find_user_skill",
    "_save_user_skills",
    "_save_user_skill",
    "_normalize_user_skill",
    "_ensure_user_skill_can_use_id",
    "_decorate_skill_validation",
    "_validate_skill",
    "_load_runtime_skill_support_files",
}


def _gateway(root: Path) -> AgentGateway:
    return AgentGateway(root / "config.json", root / "audit")


def _class_definition(path: Path, class_name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)


def test_skill_registry_service_keeps_host_owned_state_and_late_binding() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        service = gateway._skill_registry

        assert isinstance(service, AgentSkillRegistryService)
        assert service._host is gateway
        assert AgentSkillRegistryService.__slots__ == ("_host",)
        assert service._tools is gateway._tools
        assert service._write_handlers is gateway._write_handlers
        assert service.user_skill_lock is gateway.user_skill_lock


def test_skill_registry_internal_calls_preserve_gateway_facade_monkeypatches() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        config = gateway.ensure_config()
        sentinel = {"name": "patched"}
        gateway._skill_from_builtin_group = lambda _group, _config: sentinel  # type: ignore[method-assign]

        assert sentinel in gateway._builtin_skill_definitions(config)

        gateway._load_user_skills = lambda: [sentinel]  # type: ignore[method-assign]
        assert gateway._find_user_skill("patched") is sentinel

        validation = {"status": "warning", "reasons": ["patched"]}
        gateway._validate_skill = lambda _skill, _config: validation  # type: ignore[method-assign]
        assert gateway._decorate_skill_validation({"name": "patched"}, config)["validation"] is validation

    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        config = gateway.ensure_config()
        support_calls: list[dict[str, object]] = []
        gateway._load_runtime_skill_support_files = lambda skill: support_calls.append(skill)  # type: ignore[method-assign]

        assert gateway._validate_skill({"name": "patched"}, config)["status"] == "ok"
        assert support_calls == [{"name": "patched"}]


def test_skill_registry_facades_are_exact_delegate_only_and_keep_domain_boundary() -> None:
    gateway_class = _class_definition(REPO_ROOT / "agent_gateway.py", "AgentGateway")
    service_class = _class_definition(REPO_ROOT / "agent_skill_registry.py", "AgentSkillRegistryService")
    gateway_methods = {
        node.name: node
        for node in gateway_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    implementation_methods = {
        f"_{node.name.removeprefix('_impl_')}": node
        for node in service_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("_impl_")
        and node.name != "_impl_user_skills_dir"
    }

    assert set(implementation_methods) == MOVED_METHODS
    source = (REPO_ROOT / "agent_skill_registry.py").read_text(encoding="utf-8")
    assert "execute_runtime_skill" not in source
    assert "build_path_to_skill_source" not in source
    assert "_impl_create_user_skill" not in source
    assert "_impl_update_user_skill" not in source
    assert "_impl_delete_user_skill" not in source
    assert "_match_package_skill_route" not in source

    for method_name, implementation in implementation_methods.items():
        facade = gateway_methods[method_name]
        assert ast.dump(facade.args, include_attributes=False) == ast.dump(implementation.args, include_attributes=False)
        assert len(facade.body) == 1
        statement = facade.body[0]
        assert isinstance(statement, ast.Return)
        assert isinstance(statement.value, ast.Call)
        assert isinstance(statement.value.func, ast.Attribute)
        assert statement.value.func.attr == f"_impl_{method_name.lstrip('_')}"

    user_skills_dir = gateway_methods["user_skills_dir"]
    assert any(isinstance(decorator, ast.Name) and decorator.id == "property" for decorator in user_skills_dir.decorator_list)
    assert len(user_skills_dir.body) == 1
    statement = user_skills_dir.body[0]
    assert isinstance(statement, ast.Return)
    assert isinstance(statement.value, ast.Call)
    assert isinstance(statement.value.func, ast.Attribute)
    assert statement.value.func.attr == "_impl_user_skills_dir"
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "_impl_user_skills_dir"
        for node in service_class.body
    )


def test_agent_gateway_facade_respects_skill_registry_size_budget() -> None:
    source = (REPO_ROOT / "agent_gateway.py").read_bytes()

    assert len(source) <= AGENT_GATEWAY_MAX_BYTES
    assert source.count(b"\n") <= AGENT_GATEWAY_MAX_LF_LINES
