from copy import deepcopy
import json
from unittest.mock import Mock
import pytest
from skill_packages import PackageSecurityError
from tests.test_user_unity_tool_service import _installed
from user_unity_tool_gateway import list_user_tools, invoke_user_tool


def fixture(tmp_path):
    service, project = _installed(tmp_path)
    plan = service.prepare_installed("com.example.tool", project)
    service.apply(plan)
    package = {"packageId": "com.example.tool", "packageDigest": plan["installed"]["sha256"],
        "enabled": True, "available": True, "status": "ready", "reasons": [],
        "tools": [{"toolId": "example.tool", "available": True, "reason": "ready"}]}
    service.package_service.set_enabled(package["packageId"], False)
    return service, project, package


def test_disabled_store_projects_effective_fields_preserving_core_and_reenable(tmp_path):
    service, project, package = fixture(tmp_path)
    original = deepcopy(package)
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    def catalog():
        return list_user_tools({"projectPath": str(project), "packageId": package["packageId"]}, service,
            Mock(return_value={"tools": [package]}))
    result = catalog()
    assert result["count"] == 1 and result["totalPackageCount"] == 1
    row = result["tools"][0]
    assert row["available"] is False and row["status"] == "unavailable"
    assert row["enabled"] is False and row["disabledBy"] == "vrcforge_package_store"
    assert row["coreEnabled"] is True and row["reasons"] == ["disabled"]
    assert row["tools"][0]["reason"] == "disabled" and row["tools"][0]["coreReason"] == "ready"
    assert row["tools"][0]["available"] is False
    assert package == original
    service.package_service.set_enabled(package["packageId"], True)
    assert catalog()["tools"][0] == original
    assert {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()} == before


def test_disabled_failure_is_typed_but_dispatch_was_already_blocked(tmp_path):
    service, project, package = fixture(tmp_path)
    invoke = Mock()
    with pytest.raises(PackageSecurityError) as caught:
        invoke_user_tool({"projectPath": str(project), "packageId": package["packageId"],
            "packageDigest": package["packageDigest"], "toolId": "example.tool", "arguments": {}}, service, invoke)
    invoke.assert_not_called()
    assert getattr(caught.value, "code", None) == "disabled"
    assert getattr(caught.value, "package_id", None) == package["packageId"]


def test_missing_package_is_not_reported_as_disabled(tmp_path):
    service, project, _ = fixture(tmp_path)
    with pytest.raises(PackageSecurityError) as caught:
        service.verified_descriptor("com.missing.tool", project)
    assert "not installed" in str(caught.value)
    assert getattr(caught.value, "code", None) != "disabled"


@pytest.mark.parametrize("native", [True, False])
def test_disabled_effective_fields_survive_model_observation(tmp_path, native):
    from runtime_planner_service import RuntimePlannerService
    service, project, package = fixture(tmp_path)
    result = list_user_tools({"projectPath": str(project)}, service, Mock(return_value={"tools": [package]}))
    step = {"tool": "vrcforge_list_user_unity_tools", "result": result, "status": "executed", "outcome": {"status": "ok"}}
    planner = RuntimePlannerService(catalog=None, desktop=None)
    text = planner.native_result_observation(step)["observation"] if native else planner._llm_loop_step_observation(step)
    data, _ = json.JSONDecoder().raw_decode(text.split("structuredEvidence=", 1)[1])
    row = data["data"]["tools"][0]
    assert row["enabled"] is False and row["coreEnabled"] is True
    assert row["disabledBy"] == "vrcforge_package_store"
    assert row["tools"][0]["reason"] == "disabled" and row["tools"][0]["coreReason"] == "ready"
