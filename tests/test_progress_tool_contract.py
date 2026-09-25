"""Progress contracts reject accidental replacement and preserve explicit clearing."""
import pytest
from agent_gateway import AgentGateway, AgentGatewayError
from runtime_planner_service import validate_planner_tool_arguments
from unity_tool_schema_projection import canonical_unity_read_tool_input_schema

@pytest.mark.parametrize("bad", [{}, {"steps": [{"title": "new"}]}, {"items": "wrong"}, {"plan": None}])
def test_missing_or_wrong_replace_array_preserves_existing_progress(tmp_path, bad):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    scope = {"sessionId": "owner", "projectRoot": "fixture"}
    gateway.replace_agent_progress({**scope, "items": [{"id": "keep", "title": "Keep me"}]})
    before = gateway.agent_progress_log_path.read_bytes()
    with pytest.raises(AgentGatewayError):
        gateway.replace_agent_progress({**scope, **bad})
    assert gateway.agent_progress_log_path.read_bytes() == before
    assert gateway.list_agent_progress(session_id="owner")["items"][0]["title"] == "Keep me"

@pytest.mark.parametrize("alias", ["items", "plan"])
def test_replace_alias_and_explicit_empty_clear(tmp_path, alias):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.replace_agent_progress({alias: [{"step": "Inspect", "id": "one"}], "sessionId": "owner"})
    assert gateway.list_agent_progress(session_id="owner")["count"] == 1
    assert gateway.replace_agent_progress({alias: [], "sessionId": "owner"})["count"] == 0

@pytest.mark.parametrize("name,good,bad", [
    ("replace", {"plan": [{"content": "Inspect"}]}, {"steps": []}),
    ("create", {"step": "Inspect"}, {}),
    ("update", {"id": "one", "status": "completed"}, {}),
    ("delete", {"progressId": "one"}, {}),
])
def test_shared_schema_advertises_and_validates_progress_arguments(name, good, bad):
    schema = canonical_unity_read_tool_input_schema("vrcforge_progress_" + name)
    assert schema["properties"]
    assert validate_planner_tool_arguments(schema, good)["ok"]
    assert not validate_planner_tool_arguments(schema, bad)["ok"]
    if name in {"replace", "create", "update"}:
        invalid = {**good, "status": "invented"} if name != "replace" else {"items": [{"title": "Inspect", "status": "invented"}]}
        assert not validate_planner_tool_arguments(schema, invalid)["ok"]


def test_replace_keeps_alias_precedence_and_scoped_items(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.replace_agent_progress({"items": [{"title": "Other"}], "sessionId": "other"})
    result = gateway.replace_agent_progress({"items": [{"title": "Primary"}], "plan": [{"title": "Alias"}], "sessionId": "owner"})
    assert result["items"][0]["title"] == "Primary"
    result = gateway.replace_agent_progress({"items": [], "plan": [{"title": "Alias"}], "sessionId": "owner"})
    assert result["items"][0]["title"] == "Alias"
    gateway.replace_agent_progress({"items": [], "sessionId": "owner"})
    assert gateway.list_agent_progress(session_id="other")["items"][0]["title"] == "Other"


@pytest.mark.parametrize("layer", ["planning", "execution"])
@pytest.mark.parametrize("project", [False, True])
def test_native_catalog_contains_complete_progress_schemas(layer, project):
    from dashboard_server import _RuntimePlannerCatalog
    from runtime_planner_service import RuntimePlannerService
    catalog = _RuntimePlannerCatalog()
    planner = RuntimePlannerService(catalog=catalog, desktop=None)
    request, tools = planner._build_native_plan_request([], observe={}, exposure_layer=layer,
        project_context_active=project, project_path="", internal_tool_blocks=None,
        global_instructions="", project_instructions="")
    definitions = {entry["function"]["name"]: entry["function"] for entry in request["tools"]}
    for operation in ("replace", "create", "update", "delete"):
        tool = next(tool for tool in tools if tool.runtime_name == "vrcforge_progress_" + operation)
        assert definitions[tool.name]["parameters"]["properties"]
        assert not validate_planner_tool_arguments(definitions[tool.name]["parameters"], {})["ok"]
        assert "When NOT to use:" in definitions[tool.name]["description"]


def test_update_delete_aliases_preserve_scope(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    created = gateway.create_agent_progress({"content": "Inspect", "sessionId": "owner"})
    identity = created["progress"]["progressId"]
    with pytest.raises(AgentGatewayError):
        gateway.update_agent_progress(identity, {"status": "completed", "sessionId": "other"})
    assert gateway.update_agent_progress(identity, {"status": "completed", "sessionId": "owner"})["progress"]["status"] == "completed"
    gateway.delete_agent_progress(identity, {"sessionId": "owner"})
    assert gateway.list_agent_progress(session_id="owner")["count"] == 0


@pytest.mark.parametrize("items", [[{}], ["invalid"], [{"title": " "}]])
def test_nonempty_replacement_without_valid_items_never_clears(tmp_path, items):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.replace_agent_progress({"items": [{"title": "Keep"}]})
    before = gateway.agent_progress_log_path.read_bytes()
    with pytest.raises(AgentGatewayError):
        gateway.replace_agent_progress({"items": items})
    assert gateway.agent_progress_log_path.read_bytes() == before


def test_mixed_valid_replacement_keeps_existing_filtering(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    result = gateway.replace_agent_progress({"items": [{}, {"title": "Keep"}, "invalid"]})
    assert [item["title"] for item in result["items"]] == ["Keep"]


def test_create_schema_does_not_advertise_caller_selected_identity():
    schema = canonical_unity_read_tool_input_schema("vrcforge_progress_create")
    assert "progressId" not in schema["properties"] and "id" not in schema["properties"]
