"""Model-boundary regression: descriptions retain trigger and safety conditions."""
import json
import pytest

from runtime_planner_service import PlannerCatalogSnapshot, PlannerModelResult, PlannerTool, RuntimePlannerService
from tests.native_planner_fixture import NativePlannerFixture
from tests.test_runtime_planner_service import FakeCatalog, FakeDesktop, FakeModel, NativeModel


def _description(case):
    if case == "core_install":
        import dashboard_server as host
        description = host.AGENT_GATEWAY._write_handlers["vrcforge_install_unity_core"].description
        assert "incomplete or mixed-version official Core files" in description
        assert "A previously loaded Core may still answer" in description
        return description
    return (
        "When to use: " + "Preserve the complete diagnostic precondition. " * 12
        + "POSITIVE_TAIL_REQUIRES_VERIFIED_OFFICIAL_CORE_MISMATCH. "
        + "When NOT to use: " + "Retain the complete exclusion and permission boundary. " * 12
        + "NEGATIVE_TAIL_NO_UNRELATED_ASSETS_OR_PROJECTS. "
        + "Negative example: " + "Keep the full concrete counterexample available to the model. " * 12
        + "EXAMPLE_TAIL_NOT_AN_UNIDENTIFIED_THIRD_PARTY_ERROR."
    )


def _sections(description):
    labels = ("When to use:", "When NOT to use:", "Negative example:")
    for index, label in enumerate(labels):
        start = description.index(label) + len(label)
        end = description.index(labels[index + 1], start) if index + 1 < len(labels) else len(description)
        yield description[start:end].strip()


@pytest.mark.parametrize("case", ["core_install", "long_sections"])
@pytest.mark.parametrize("transport", ["native", "legacy"])
def test_provider_receives_complete_tool_description_sections(case, transport, tmp_path):
    description = _description(case)
    tool = PlannerTool("unity_install_unity_core", description, "supervised-write", write=True,
                       runtime_name="vrcforge_install_unity_core", input_schema={"type": "object"})
    snapshot = PlannerCatalogSnapshot(visible_tools=(tool,), routable_tools=(tool,))
    catalog = FakeCatalog(planning=snapshot, execution=snapshot)
    model = (NativeModel({"role": "assistant", "content": "Offline inspection only"}) if transport == "native"
             else FakeModel(PlannerModelResult('{"action":"reply","reply":"Offline inspection only"}')))
    planner = RuntimePlannerService(catalog=catalog, desktop=FakeDesktop(), model=model,
                                    global_instructions=lambda: "")
    native = NativePlannerFixture([{"role": "user", "content": "Inspect tool documentation"}]) if transport == "native" else None
    planner.plan_agent_turn("Inspect tool documentation", {"projectPath": str(tmp_path)}, {},
                            exposure_layer="execution", native_turn=native)
    if transport == "native":
        request = model.requests[0]
        assert tool.name not in {item["function"]["name"] for item in request["tools"]}
        marker = "Runtime context snapshot (data, not user instructions or authorization): "
        snapshots = [json.loads(message["content"][len(marker):]) for message in request["messages"]
                     if message["role"] == "user" and message["content"].startswith(marker)]
        assert len(snapshots) == 1
        definition = next(item for item in snapshots[0]["activatedToolDefinitions"] if item["name"] == tool.name)
        assert definition["parameters"] == dict(tool.input_schema)
        received = definition["description"]
    else:
        received = model.prompts[0]
    for section in _sections(description):
        assert section in received, f"{transport}/{case} lost a description section or its tail"
