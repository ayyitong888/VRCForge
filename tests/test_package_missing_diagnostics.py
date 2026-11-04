from dataclasses import replace
import pytest

from package_install_workflow_service import PackageInstallWorkflowService


@pytest.fixture
def empty_project_service(tmp_path):
    import dashboard_server as app

    project = tmp_path / "Empty Unity Project"
    for directory in ("Assets", "Packages", "ProjectSettings"):
        (project / directory).mkdir(parents=True)
    reads = []

    def detect(path, package_ids):
        reads.append(tuple(package_ids))
        return app.PACKAGE_DETECTION.detect(path, package_ids)

    def unavailable(_params):
        raise RuntimeError("Unity Core is unavailable; compile errors cannot be read")

    service = PackageInstallWorkflowService(replace(
        app.PACKAGE_INSTALL_WORKFLOWS._ports,
        selected_project_path=lambda: str(project),
        locate_managers=lambda: [],
        detect_package=detect,
        read_compile_errors=unavailable,
        redact_support=lambda value: value,
    ))
    return service, project, reads


def test_empty_project_reports_sdk_facts_without_requiring_avatar_packages(empty_project_service):
    service, _project, _reads = empty_project_service
    status = service.package_manager_status({})
    assert status["ok"] is True
    for package in ("com.vrchat.avatars", "com.vrchat.base"):
        assert status["packages"][package]["installed"] is False
    diagnosis = service.diagnose_install({})
    assert not any(item["code"] == "missing_package" for item in diagnosis["symptoms"])
    assert not any(item.get("tool") == "vrcforge_package_install_plan" for item in diagnosis["suggestedFixPlans"])


@pytest.mark.parametrize("package_id", ["com.vrchat.avatars", "com.example.requested", "nadena.dev.modular-avatar"])
def test_requested_missing_package_explains_unavailable_core_with_exact_plan(empty_project_service, package_id):
    service, project, reads = empty_project_service
    result = service.diagnose_install({"packageId": package_id})
    missing = next(item for item in result["symptoms"] if item["code"] == "missing_package")
    assert missing["packageId"] == package_id
    plan = next(item for item in result["suggestedFixPlans"] if item.get("tool") == "vrcforge_package_install_plan")
    assert plan["arguments"] == {"projectPath": str(project), "packageId": package_id}
    assert "Unity Core is unavailable" in result["compileErrors"]["error"]
    assert any(item["code"] == "compile_reader_unavailable" for item in result["symptoms"])
    assert not any(item.get("tool") == "vrcforge_get_compile_errors" for item in result["suggestedFixPlans"])
    assert reads.count((package_id,)) == 1


def test_installed_requested_package_does_not_become_missing_because_core_is_down(empty_project_service):
    service, project, _reads = empty_project_service
    package = project / "Packages" / "com.vrchat.avatars"
    package.mkdir()
    (package / "package.json").write_text('{"name":"com.vrchat.avatars","version":"3.10.6"}', encoding="utf-8")
    result = service.diagnose_install({"packageId": "com.vrchat.avatars"})
    assert result["packageManager"]["packages"]["com.vrchat.avatars"]["installed"] is True
    assert not any(item["code"] == "missing_package" for item in result["symptoms"])


def test_shared_diagnosis_facts_survive_external_mcp_projection(empty_project_service, monkeypatch):
    import dashboard_server as app
    from agent_mcp_2026 import Mcp2026Router, PROTOCOL_VERSION
    from external_mcp_tool_blocks import EXTERNAL_MCP_READ_TOOL_BLOCKS

    service, project, _reads = empty_project_service
    arguments = {"packageId": "com.vrchat.avatars"}
    name = "vrcforge_diagnose_package_install_errors"
    assert name in EXTERNAL_MCP_READ_TOOL_BLOCKS["project"]
    handler = app.AGENT_GATEWAY._tools[name].handler
    assert handler.__self__ is app.PACKAGE_INSTALL_WORKFLOWS
    monkeypatch.setattr(app.PACKAGE_INSTALL_WORKFLOWS, "_ports", service._ports)
    internal = handler(arguments)
    router = Mcp2026Router(
        lambda _params: [{"name": name}],
        lambda _name, params: handler(params),
    )
    response, status = router.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                             "io.modelcontextprotocol/clientCapabilities": {}},
                   "name": "vrcforge_diagnose_package_install_errors", "arguments": arguments},
    })
    assert status == 200
    external = response["result"]["structuredContent"]
    for key in ("symptoms", "suggestedFixPlans", "packageManager", "compileErrors"):
        assert external[key] == internal[key]
    assert any(item["code"] == "missing_package" for item in external["symptoms"])
    assert any(item.get("arguments", {}).get("projectPath") == str(project)
               for item in external["suggestedFixPlans"])


@pytest.mark.parametrize("relative", [
    "Packages/manifest.json", "Packages/vpm-manifest.json", "Packages/packages-lock.json",
    "Packages/com.vrchat.avatars/package.json", "Assets/UnknownAddon/package.json",
])
def test_unreadable_package_state_is_unknown_and_points_to_file_not_install(empty_project_service, relative):
    service, project, _reads = empty_project_service
    broken = project / relative
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text('{"dependencies":', encoding="utf-8")
    result = service.diagnose_install({"packageId": "com.vrchat.avatars"})
    state = result["packageManager"]["packages"]["com.vrchat.avatars"]
    assert state["installed"] is None
    assert state["readErrors"][0]["path"] == str(broken)
    assert state["readErrors"][0]["error"]
    assert any(item["code"] == "package_state_unreadable" for item in result["symptoms"])
    assert not any(item["code"] == "missing_package" for item in result["symptoms"])
    assert not any(item.get("tool") in {"vrcforge_package_install_plan", "vrcforge_install_vpm_package"}
                   for item in result["suggestedFixPlans"])
    read = next(item for item in result["suggestedFixPlans"] if item.get("tool") == "vrcforge_read_text_file")
    assert read["arguments"] == {"projectPath": str(project), "path": str(broken)}
    plan = service.plan_install({"packageId": "com.vrchat.avatars"})
    assert plan["compatibilityAction"] == "inspect_package_state"
    assert plan["canCreateInstallRequest"] is False


def test_valid_package_evidence_survives_another_broken_manifest(empty_project_service):
    service, project, _reads = empty_project_service
    (project / "Packages/vpm-manifest.json").write_text("{broken", encoding="utf-8")
    (project / "Packages/manifest.json").write_text('{"dependencies":{"com.vrchat.avatars":"3.10.6"}}', encoding="utf-8")
    result = service.diagnose_install({"packageId": "com.vrchat.avatars"})
    state = result["packageManager"]["packages"]["com.vrchat.avatars"]
    assert state["installed"] is True and state["version"] == "3.10.6"
    assert state["readErrors"]
    assert not any(item["code"] == "missing_package" for item in result["symptoms"])


def test_unrelated_invalid_package_does_not_block_confirmed_installed_target(empty_project_service):
    service, project, _reads = empty_project_service
    broken = project / "Assets/UnrelatedTool/package.json"
    broken.parent.mkdir()
    broken.write_text("{broken", encoding="utf-8")
    (project / "Packages/manifest.json").write_text('{"dependencies":{"com.example.target":"1.0.0"}}', encoding="utf-8")
    plan = service.plan_install({"packageId": "com.example.target"})
    assert plan["packageState"]["readErrors"]
    assert plan["packageState"]["installed"] is True
    assert plan["compatibilityAction"] == "use_installed"
    result = service.request_install({"packageId": "com.example.target"})
    assert result["status"] == "use_installed"
    assert result["approvalCreated"] is False


@pytest.mark.parametrize("contents", ["[]", '{"dependencies":[]}'])
def test_invalid_json_shape_is_not_package_absence(empty_project_service, contents):
    service, project, _reads = empty_project_service
    (project / "Packages/manifest.json").write_text(contents, encoding="utf-8")
    result = service.diagnose_install({"packageId": "com.vrchat.avatars"})
    assert result["packageManager"]["packages"]["com.vrchat.avatars"]["installed"] is None
    assert not any(item["code"] == "missing_package" for item in result["symptoms"])
