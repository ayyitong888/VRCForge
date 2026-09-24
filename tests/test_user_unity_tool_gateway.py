from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import Mock
import json

import pytest
import dashboard_server as dashboard
from skill_packages import PackageSecurityError
from agent_gateway import AgentGateway
from user_unity_tool_gateway import execution_plan, invoke_user_tool, list_user_tools
from vrchat_blendshape_agent import McpResult
from approved_unity_execution import current_approved_unity_execution
from user_unity_tool_service import UserUnityToolService


def _verified_store():
    manifest = {"id": "user.test", "version": "1.0.0", "entrypoints": {"unityTools": "descriptor.json", "source": "Tool.cs"}}
    descriptor = {"schema": "vrcforge.user_unity_tools.v1", "tools": [{
        "toolId": "user.echo", "typeName": "Example.Tool", "source": "Tool.cs",
        "description": "when-to-use: test. when-NOT-to-use: production.", "inputSchema": {},
    }]}
    metadata = {"package_sha256": "digest", "lock_sha256": "lock", "manifest": manifest}
    payloads = {"manifest.json": json.dumps(manifest).encode(), "descriptor.json": json.dumps(descriptor).encode()}
    return SimpleNamespace(verified_installed_entrypoint_bytes=Mock(side_effect=lambda _package, relative: (metadata, payloads[relative])))


def test_install_registration_uses_application_store_and_requires_manual_approval(monkeypatch):
    package_store = object()
    factory = Mock(return_value=SimpleNamespace(prepare_installed=Mock(return_value={
        "installed": {"id": "user.test", "sha256": "digest"},
        "files": [{"target": {"targetRelativePath": "Assets/VRCForgeUserTools/user.test/Editor/Tool.cs"}, "sha256": "source-digest"}],
    })))
    monkeypatch.setattr(dashboard, "skill_package_service", lambda: package_store)
    monkeypatch.setattr(dashboard, "UserUnityToolService", factory)
    handler = dashboard.AGENT_GATEWAY._write_handlers["vrcforge_install_user_unity_tools"]
    args, preview = handler.request_preparer({"packageId": "user.test", "projectPath": "project", "preparedPlan": {"forged": True}}, None)
    factory.assert_called_once_with(package_store, core_tree_identity=dashboard._unity_core_tree_identity)
    assert args["preparedPlan"].get("forged") is None
    assert preview["files"][0]["sha256"] == "source-digest"
    assert handler.manual_approval_resolver(args, preview)
    assert handler.pre_write_checkpoint_required is True
    assert handler.checkpoint_prepare_handler is dashboard.prepare_authoritative_unity_checkpoint_sync


def test_catalog_uses_compiled_core_state_and_current_governance():
    packages = _verified_store()
    service = UserUnityToolService(packages)
    core = Mock(return_value={"schema": "vrcforge.user-tools.v1", "tools": [{
        "packageId": "user.test", "packageDigest": "digest", "available": False,
        "status": "unavailable", "tools": [{"toolId": "user.test", "available": False, "reason": "pending_compile"}],
    }]})
    result = list_user_tools({"projectPath": "project"}, service, core)
    core.assert_called_once_with("vrc_list_user_tools", {})
    assert result["tools"][0]["available"] is False
    packages.verified_installed_entrypoint_bytes.side_effect = PackageSecurityError("disabled")
    core.return_value["tools"][0]["available"] = True
    result = list_user_tools({"projectPath": "project"}, service, core)
    assert result["tools"][0]["available"] is False
    assert "disabled" in result["tools"][0]["reasons"]


def test_unknown_package_filter_does_not_claim_the_project_catalog_is_empty():
    core = Mock(return_value={"schema": "vrcforge.user-tools.v1", "tools": [
        {"packageId": "user.test", "packageDigest": "digest", "available": True, "tools": []},
    ]})
    service = UserUnityToolService(_verified_store())
    result = list_user_tools({"projectPath": "project", "packageId": "project-scoped"}, service, core)
    assert result["count"] == 0
    assert result["tools"] == []
    assert result["totalPackageCount"] == 1
    assert result["packageIdFilter"] == "project-scoped"
    assert "Omit packageId" in result["summary"]
    observation = dashboard.AGENT_GATEWAY.runtime_planner.native_result_observation({
        "tool": "vrcforge_list_user_unity_tools", "status": "executed",
        "result": result, "outcome": {"status": "ok"},
    })["observation"]
    assert "0 of 1 project packages" in observation
    assert "project-scoped" in observation
    assert "Omit packageId" in observation
    unfiltered = list_user_tools({"projectPath": "project"}, service, core)
    assert unfiltered["count"] == unfiltered["totalPackageCount"] == 1
    assert unfiltered["packageIdFilter"] == ""
    matched = list_user_tools({"projectPath": "project", "packageId": "user.test"}, service, core)
    assert matched["count"] == 1
    assert matched["tools"][0]["packageId"] == "user.test"
    from unity_tool_schema_projection import canonical_unity_read_tool_input_schema
    schema = canonical_unity_read_tool_input_schema("vrcforge_list_user_unity_tools")
    assert "exact" in schema["properties"]["packageId"]["description"]


def test_empty_catalog_exposes_real_disk_baseline_for_authoring(tmp_path):
    root = tmp_path / "Assets/VRCForge"
    root.mkdir(parents=True)
    source = root / "Official.cs"
    source.write_text("// original", encoding="utf-8")
    service = UserUnityToolService(_verified_store(), core_tree_identity=dashboard._unity_core_tree_identity)
    core = Mock(return_value={"tools": []})
    first = list_user_tools({"projectPath": str(tmp_path)}, service, core)
    baseline = first["repairBaseline"]
    assert baseline == {
        "available": True, "scope": "disk_source_tree", "sourcePath": "Assets/VRCForge",
        "coreSourceTreeSha256": dashboard._unity_core_tree_identity(root)["sha256"],
        "loadedImplementationVerified": False,
    }
    service.validate_repair_baseline([baseline], tmp_path)
    source.write_text("// changed implementation", encoding="utf-8")
    second = list_user_tools({"projectPath": str(tmp_path)}, service, core)
    assert second["repairBaseline"]["coreSourceTreeSha256"] != baseline["coreSourceTreeSha256"]
    with pytest.raises(ValueError, match="baseline mismatch"):
        service.validate_repair_baseline([baseline], tmp_path)


def test_unavailable_disk_baseline_does_not_hide_normal_catalog():
    identity = Mock(side_effect=OSError("source unreadable"))
    service = UserUnityToolService(_verified_store(), core_tree_identity=identity)
    core = Mock(return_value={"tools": [{"packageId": "user.test", "packageDigest": "digest", "available": True, "tools": []}]})
    result = list_user_tools({"projectPath": "project"}, service, core)
    assert result["tools"][0]["available"] is True
    assert result["repairBaseline"]["available"] is False
    assert "coreSourceTreeSha256" not in result["repairBaseline"]
    assert "unavailable" in result["repairBaseline"]["reason"]


def test_disabled_package_cannot_invoke_and_dispatch_plan_is_exact():
    packages = SimpleNamespace(verified_installed_entrypoint_bytes=Mock(side_effect=PackageSecurityError("disabled")))
    invoke = Mock()
    arguments = {"packageId": "user.test", "packageDigest": "digest", "toolId": "echo", "arguments": {"value": "hello"}, "projectPath": "project"}
    with pytest.raises(PackageSecurityError):
        invoke_user_tool(arguments, UserUnityToolService(packages), invoke)
    invoke.assert_not_called()
    assert execution_plan(arguments) == [("vrc_invoke_user_tool", {k: arguments[k] for k in ("packageId", "packageDigest", "toolId", "arguments")})]


def test_install_route_only_queues_supervised_write(monkeypatch):
    queue = Mock(return_value={"ok": True, "status": "pending_approval"})
    monkeypatch.setattr(dashboard, "request_supervised_unity_write", queue)
    result = dashboard.app_install_unity_user_tools("user.test", {"projectPath": "project", "packageId": "wrong"})
    assert result["status"] == "pending_approval"
    assert queue.call_args.args == ("vrcforge_install_user_unity_tools", {"projectPath": "project", "packageId": "user.test"})


@pytest.mark.parametrize("structured", [
    {"success": True, "message": "User tool completed.", "data": {"echo": "accepted"}},
    {"success": False, "code": "acceptance_failure", "error": "expected failure", "data": {"retryable": False}},
    {"success": True, "_mcp_status": "pending", "_mcp_poll_interval": 2.5, "data": {"jobId": "user-job"}},
])
def test_registered_user_tool_invocation_serializes_real_mcp_result_losslessly(monkeypatch, structured):
    package_store = _verified_store()
    monkeypatch.setattr(dashboard, "skill_package_service", lambda: package_store)
    envelope = {"structuredContent": structured, "isError": structured["success"] is False, "content": []}
    core_result = McpResult(exit_code=0, stdout="", stderr="", payload=envelope)
    core = Mock(return_value=core_result)
    monkeypatch.setattr(dashboard, "invoke_unity_mcp", core)
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _request: object())
    handler = dashboard.AGENT_GATEWAY._write_handlers["vrcforge_invoke_user_unity_tool"]
    result = handler.handler({"packageId": "user.test", "packageDigest": "digest", "toolId": "user.echo", "arguments": {}, "projectPath": "project"})
    persisted = json.loads(json.dumps(result))
    assert persisted == envelope
    assert core.call_args.args[1] == "vrc_invoke_user_tool"


@pytest.mark.parametrize("successful", [True, False])
def test_registered_user_tool_result_reaches_actual_internal_approval_outcome(tmp_path, monkeypatch, successful):
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    service = gateway.approval_transactions
    target = "vrcforge_invoke_user_unity_tool"
    gateway._write_handlers[target] = dashboard.AGENT_GATEWAY._write_handlers[target]
    package_store = _verified_store()
    structured = ({"success": True, "data": {
        "schema": "vrcforge.user_tool_asset_write.v1", "ok": True, "verified": True,
        "mutationStarted": True, "mutationApplied": True, "commitState": "committed",
        "readback": {"assetPath": "Assets/fixture.txt", "text": "persisted"},
    }} if successful else {"success": False, "code": "acceptance_failure", "error": "expected acceptance failure"})
    envelope = {"structuredContent": structured, "isError": not successful, "content": []}
    calls = []

    def invoke(_settings, tool, arguments, **_kwargs):
        plan = current_approved_unity_execution()
        assert plan is not None
        plan.claim(tool, arguments, tmp_path).complete()
        calls.append(tool)
        return McpResult(exit_code=0, stdout="", stderr="", payload=envelope)

    monkeypatch.setattr(dashboard, "skill_package_service", lambda: package_store)
    monkeypatch.setattr(dashboard, "load_dashboard_settings", lambda _request: object())
    monkeypatch.setattr(dashboard, "invoke_unity_mcp", invoke)
    monkeypatch.setattr(type(service), "_create_pre_write_checkpoint", lambda *_args: {
        "ok": True, "id": "user-result-checkpoint", "projectRoot": str(tmp_path),
    })
    request = service.create_apply_request({"targetTool": target, "arguments": {
        "projectPath": str(tmp_path), "packageId": "user.test", "packageDigest": "digest",
        "toolId": "user.echo", "arguments": {},
    }})
    service.approve(request["approval"]["id"])
    result = service.apply_approved({"approval_id": request["approval"]["id"]})
    json.dumps(result)
    assert calls == ["vrc_invoke_user_tool"]
    assert result["ok"] is successful
    assert result["outcome"]["success"] is successful
    assert result["status"] == ("applied" if successful else "failed")
    if not successful:
        assert "acceptance_failure" in json.dumps(result)
        assert "expected acceptance failure" in json.dumps(result)


def _installation_gateway(tmp_path, events):
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    config = gateway.ensure_config()
    config.enabled = True
    config.allow_write_requests = True
    config.execution_mode = "roslyn_full_auto"
    config.roslyn_risk_acknowledged = True
    gateway.save_config(config)
    target = "vrcforge_install_user_unity_tools"
    gateway._write_handlers[target] = replace(
        dashboard.AGENT_GATEWAY._write_handlers[target],
        request_preparer=None,
        handler=lambda _args: events.append("handler") or {
            "ok": True, "status": "pending_compile", "pendingCompile": True,
            "verified": True, "readback": {"verified": True, "scope": "user_tool_files"},
        },
    )
    project = tmp_path / "project"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True)
    return gateway.approval_transactions, target, {"projectPath": str(project), "packageId": "user.test"}


@pytest.mark.parametrize("channel", ["internal", "external"])
def test_user_code_install_still_requires_confirmation_in_full_permission(tmp_path, monkeypatch, channel):
    events = []
    service, target, args = _installation_gateway(tmp_path, events)
    auto_execute = Mock(return_value={"status": "executed"})
    monkeypatch.setattr(type(service), "_auto_execute_approval", auto_execute)
    if channel == "internal":
        result = service.create_apply_request({"targetTool": target, "arguments": args})
        assert result["status"] == "pending"
        assert result["approval"]["requiresExplicitApproval"] is True
        auto_execute.assert_not_called()
    else:
        prepared = service.prepare_external_mcp_write(target, args)
        assert prepared["requiresUserConfirmation"] is True
        assert "Editor code" in prepared["confirmationReason"]
    assert events == []


@pytest.mark.parametrize("checkpoint_ok", [True, False])
def test_external_user_code_install_checkpoints_before_handler(tmp_path, monkeypatch, checkpoint_ok):
    events = []
    service, target, args = _installation_gateway(tmp_path, events)
    prepared = service.prepare_external_mcp_write(target, args)
    assert prepared["approvedUnityExecutionPlan"] is None
    monkeypatch.setattr(
        type(service), "_create_pre_write_checkpoint",
        lambda *_args: events.append("checkpoint") or {
            "ok": checkpoint_ok, "id": "user-install-checkpoint", "projectRoot": args["projectPath"],
            "error": "checkpoint failed" if not checkpoint_ok else "",
        },
    )
    result = service.execute_prepared_external_mcp_write(prepared)
    assert events == (["checkpoint", "handler"] if checkpoint_ok else ["checkpoint"])
    assert result["ok"] is checkpoint_ok
