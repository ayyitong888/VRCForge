"""Published descriptions must match the current component checkpoint lanes."""


def test_lazy_component_tool_discovery_explains_safe_add_paths():
    import dashboard_server as server

    gateway = server.AGENT_GATEWAY
    # Exercise the same block-scoped registry used for external Agent discovery.
    generic = gateway.build_external_mcp_tools("execution", ["avatar"])
    modular_avatar = gateway.build_external_mcp_tools(
        "execution", ["integrations/modular-avatar"]
    )
    tool_registry = gateway.build_tool_registry(exposure_layer="execution")["tools"]
    definitions = {
        tool["name"]: tool
        for tool in (*generic, *modular_avatar)
        if tool["name"] in {
            "vrcforge_add_component",
            "vrcforge_remove_component",
            "vrcforge_preview_add_modular_avatar_component",
            "vrcforge_add_modular_avatar_component",
        }
    }
    registry_definitions = {
        tool["name"]: tool
        for tool in tool_registry
        if tool["name"] in definitions
    }
    assert set(registry_definitions) == set(definitions)
    assert all(
        registry_definitions[name]["description"] == definition["description"]
        for name, definition in definitions.items()
    )

    add = definitions["vrcforge_add_component"]["description"]
    remove = definitions["vrcforge_remove_component"]["description"]
    ma_preview = definitions["vrcforge_preview_add_modular_avatar_component"]["description"]
    ma_write = definitions["vrcforge_add_modular_avatar_component"]["description"]

    assert "UnityEngine.CanvasGroup" in add
    assert "custom MonoBehaviour" in add
    assert "full-project checkpoint" in add
    assert "callbacks may affect assets" in add
    assert "vrcforge_preview_add_modular_avatar_component" in add
    assert "full-project checkpoint" in remove
    assert "When NOT to use:" in remove and "Negative example:" in remove
    for description in (ma_preview, ma_write):
        assert "fully-qualified nadena.dev.modular_avatar.core type" in description
        assert "When NOT to use:" in description and "Negative example:" in description
    assert "generic add_component" in ma_preview.lower()
    assert "generic add_component" in ma_write.lower()
