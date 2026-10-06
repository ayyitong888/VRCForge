import pytest

from runtime_planner_service import PlannerCatalogSnapshot, PlannerTool
from tests.test_native_runtime_gateway import call, run, setup_gateway
from tests.test_runtime_planner_service import FakeCatalog


@pytest.mark.parametrize("project_active", [False, True])
def test_registry_uses_runtime_profile_and_layer_despite_model_arguments(tmp_path, project_active):
    gateway, _, _ = setup_gateway(tmp_path, [call("registry", "tool_registry", {
        "exposureLayer": "execution", "projectContextActive": not project_active,
    })])
    received = []
    gateway.register_tool("vrcforge_tool_registry", "Inspect registry.", "read/debug",
                          lambda params: received.append(dict(params)) or {"ok": True, "tools": []})
    tool = PlannerTool("tool_registry", "Inspect registry.", "read/debug",
                       runtime_name="vrcforge_tool_registry")
    gateway.runtime_planner._catalog = FakeCatalog(planning=PlannerCatalogSnapshot(
        visible_tools=(tool,), routable_tools=(tool,),
    ))
    run(gateway, tmp_path, planMode=True, _projectContextActive=project_active, maxAgenticTurns=1)
    assert len(received) == 1
    assert received[0]["exposureLayer"] == "planning"
    assert received[0]["projectContextActive"] is project_active
