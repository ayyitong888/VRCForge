import pytest

from profiled_tool_registry import (
    CapabilityProfile,
    ProfiledToolRegistry,
    ToolSet,
    UNITY_PROJECT_ACCESS,
)


def _handler(params):
    return params


def test_general_and_unity_profiles_are_layered_without_copying_handlers():
    registry = ProfiledToolRegistry()
    registry.register("vrcforge_ask_user", _handler, ToolSet.CORE)
    registry.register("vrcforge_read_text_file", _handler, ToolSet.GENERAL)
    registry.register("vrcforge_scan_materials", _handler, ToolSet.UNITY)

    general = {item.model_name: item for item in registry.project(CapabilityProfile.GENERAL)}
    unity = {item.model_name: item for item in registry.project(CapabilityProfile.UNITY_PROJECT)}

    assert set(general) == {"ask_user", "read_text_file"}
    assert set(general) < set(unity)
    assert "unity_scan_materials" in unity
    assert unity["unity_scan_materials"].capabilities == {UNITY_PROJECT_ACCESS}
    assert unity["read_text_file"].handler is general["read_text_file"].handler is _handler


def test_unity_shell_is_an_extra_projection_of_the_same_implementation():
    registry = ProfiledToolRegistry()
    registered = registry.register("vrcforge_execute_shell", _handler, ToolSet.CORE, model_name="shell")
    unity_shell = registry.add_unity_shell(registered.internal_name)

    assert registry.resolve(CapabilityProfile.GENERAL, "unity_shell") is None
    assert registry.resolve(CapabilityProfile.UNITY_PROJECT, "shell").capabilities == frozenset()
    assert unity_shell.handler is registered.handler
    assert unity_shell.capabilities == {UNITY_PROJECT_ACCESS}


def test_duplicate_internal_or_profile_name_is_rejected():
    registry = ProfiledToolRegistry()
    registry.register("vrcforge_read_text_file", _handler, ToolSet.GENERAL)
    with pytest.raises(ValueError, match="already registered"):
        registry.register("vrcforge_read_text_file", _handler, ToolSet.GENERAL)
    with pytest.raises(ValueError, match="model tool"):
        registry.register("other_read", _handler, ToolSet.CORE, model_name="read_text_file")
