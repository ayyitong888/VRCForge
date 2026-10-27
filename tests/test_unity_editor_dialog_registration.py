def test_native_dialog_tools_are_exposed_without_a_core_binding():
    import dashboard_server as host
    from agent_gateway import canonical_unity_write_tool_input_schema

    read_name = "vrcforge_inspect_unity_editor_dialogs"
    write_name = "vrcforge_handle_unity_editor_dialog"
    gateway = host.AGENT_GATEWAY
    planning = host._RuntimePlannerCatalog().read("planning")
    execution = host._RuntimePlannerCatalog().read("execution")
    assert read_name in {tool.runtime_name for tool in planning.visible_tools}
    assert write_name not in {tool.runtime_name for tool in planning.visible_tools}
    tools = {tool.runtime_name: tool for tool in execution.visible_tools}
    assert tools[read_name].input_schema["required"] == ["projectPath"]
    schema = canonical_unity_write_tool_input_schema(write_name)
    assert "executionTarget" not in schema.get("required", [])
    assert "sourceApprovalId" in schema["allOf"][0]["else"]["required"]
    assert tools[write_name].input_schema["required"] == schema["required"]
    for name, field in schema["properties"].items():
        assert tools[write_name].input_schema["properties"][name] == field
    external = {tool["name"]: tool for tool in gateway.build_external_mcp_tools("execution", tool_blocks=["project"])}
    assert read_name in external and write_name in external
    assert external[write_name]["inputSchema"]["required"] == schema["required"]
    for name, field in schema["properties"].items():
        assert external[write_name]["inputSchema"]["properties"][name] == field
    assert external[write_name]["_meta"]["inputEnvelopeExtension"]["requiredAtRuntime"] is False


def test_dialog_policy_inherits_the_original_operation_checkpoint():
    import dashboard_server as host

    handler = host.AGENT_GATEWAY._write_handlers["vrcforge_handle_unity_editor_dialog"]
    policy = host.AGENT_GATEWAY.approval_transactions._write_handler_rollback_policy(handler)
    assert handler.pre_write_checkpoint_required is False
    assert handler.requires_approved_execution_context is False
    assert policy["kind"] == "native_dialog_continues_approved_operation"
    assert policy["checkpointScope"] == ["original-approved-operation"]
    assert policy["approvalRequired"] is True
    assert "does not verify asset success" in policy["note"]


def test_registered_dialog_inspector_runs_from_product_agent(monkeypatch, tmp_path):
    import json
    import dashboard_server as host
    import unity_editor_dialog_tools as dialogs

    project = tmp_path / "UnityProject"
    descriptor = project / "Library/VRCForge/mcp-core.json"
    descriptor.parent.mkdir(parents=True)
    descriptor.write_text(json.dumps({"processId": 4242}), encoding="utf-8")
    monkeypatch.setattr(dialogs.native, "_enumerate_process_windows", lambda pid: [])
    result = host.AGENT_GATEWAY.runtime_skills.execute(
        "vrcforge_inspect_unity_editor_dialogs", {"projectPath": str(project)}, "dialog-inspector-regression"
    )
    assert result["ok"] is True, result
    assert result["status"] == "executed", result
    assert result["result"]["available"] is True
    assert result["result"]["dialogs"] == []
