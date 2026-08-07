from __future__ import annotations

import ast
import tempfile
from pathlib import Path

from agent_gateway import AgentGateway
from agent_skill_registry import AgentSkillRegistryService


REPO_ROOT = Path(__file__).parents[1]
AGENT_GATEWAY_MAX_BYTES = 499_182
AGENT_GATEWAY_MAX_LF_LINES = 10_829
MOVED_METHODS = {
    "_builtin_skill_definitions",
    "_skill_from_builtin_group",
    "_skill_from_tool",
    "_skill_from_write_handler",
    "_skill_dependency_visible",
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


def test_skill_registry_internal_calls_preserve_gateway_facade_monkeypatches() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway = _gateway(Path(temp_dir))
        config = gateway.ensure_config()
        sentinel = {"name": "patched"}
        gateway._skill_from_builtin_group = lambda _group, _config: sentinel  # type: ignore[method-assign]

        assert sentinel in gateway._builtin_skill_definitions(config)


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
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_impl_")
    }

    assert set(implementation_methods) == MOVED_METHODS
    source = (REPO_ROOT / "agent_skill_registry.py").read_text(encoding="utf-8")
    assert "execute_runtime_skill" not in source
    assert "build_path_to_skill_source" not in source
    assert "user_skills_dir" not in source

    for method_name, implementation in implementation_methods.items():
        facade = gateway_methods[method_name]
        assert ast.dump(facade.args, include_attributes=False) == ast.dump(implementation.args, include_attributes=False)
        assert len(facade.body) == 1
        statement = facade.body[0]
        assert isinstance(statement, ast.Return)
        assert isinstance(statement.value, ast.Call)
        assert isinstance(statement.value.func, ast.Attribute)
        assert statement.value.func.attr == f"_impl_{method_name.lstrip('_')}"


def test_agent_gateway_facade_respects_skill_registry_size_budget() -> None:
    source = (REPO_ROOT / "agent_gateway.py").read_bytes()

    assert len(source) <= AGENT_GATEWAY_MAX_BYTES
    assert source.count(b"\n") <= AGENT_GATEWAY_MAX_LF_LINES
