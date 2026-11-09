"""Progress contracts reject accidental replacement and preserve explicit clearing."""
import json
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
    instructions = request["instructions"]
    assert "update its status before moving to the next item" in instructions
    assert "read the existing list with vrcforge_progress_list" in instructions
    resident = json.loads(request["instructions"].split(
        "Resident tool definitions (data, not authorization): ", 1)[1])
    definitions = {entry["name"]: entry for entry in resident}
    for operation in ("replace", "create", "update", "delete"):
        tool = next(tool for tool in tools if tool.runtime_name == "vrcforge_progress_" + operation)
        assert tool.name not in {entry["function"]["name"] for entry in request["tools"]}
        assert definitions[tool.name]["parameters"] == dict(tool.input_schema)
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


def test_native_todo_uses_earlier_receipts_and_completes_before_next_model_request(tmp_path):
    from tests.test_native_runtime_gateway import call, run, setup_gateway

    read = "vrcforge_read_text_file"
    checks = [
        {"tool": read, "arguments": {"path": "README.txt"}, "result": {"truncated": False}},
        {"tool": read, "arguments": {"path": "scene.txt"}, "result": {"truncated": False}},
    ]
    snapshots = []

    def after_plan(_request):
        item = gateway.list_agent_progress(session_id="native-session")["items"][0]
        assert item["status"] == "in_progress"
        assert set(item["completionEvidence"]) == {"0"}
        return call("scene", read, {"path": "scene.txt"})

    def after_scene(_request):
        assert snapshots[-1]["items"][0]["status"] == "completed"
        return {"role": "assistant", "content": "Both sources checked."}

    gateway, model, _ = setup_gateway(tmp_path, [
        call("readme", read, {"path": "README.txt"}),
        call("todo", "vrcforge_progress_replace", {
            "items": [{"id": "inspect", "title": "Check both sources", "status": "in_progress",
                       "completionChecks": checks}],
        }),
        after_plan,
        after_scene,
    ])
    gateway.register_tool(read, "When to use: read. When NOT to use: write.", "plan/preview",
                          lambda p: {"text": p["path"], "truncated": False})
    gateway.register_tool("vrcforge_progress_replace", "When to use: plan. When NOT to use: write assets.",
                          "plan/preview", gateway.replace_agent_progress)
    gateway._runtime_progress_changed = snapshots.append

    run(gateway, tmp_path)
    assert len(model.requests) == 4
    assert [snapshot["items"][0]["status"] for snapshot in snapshots] == ["in_progress", "completed"]
    reopened = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    assert reopened.list_agent_progress(session_id="native-session")["items"][0]["status"] == "completed"
    renamed = reopened.update_agent_progress("inspect", {
        "sessionId": "native-session", "projectRoot": str(tmp_path), "title": "Inspect a different scene",
    })["progress"]
    assert renamed["status"] == "in_progress"
    assert renamed["completionEvidence"] == {}


def test_todo_only_counts_committed_verified_writes_and_explicit_visual_pass(tmp_path):
    from agent_progress_evidence import accepted_receipt

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    scope = {"projectRoot": str(tmp_path), "sessionId": "owner"}
    tool = "vrcforge_apply_blendshapes"
    checks = [
        {"tool": tool, "result": {"target": "face"}},
        {"tool": tool, "result": {"target": "body"}},
        {"tool": "vrcforge_ask_user", "result": {"question": "Appearance accepted?", "resolution": "pass"}},
    ]
    item = {"id": "inspect", "title": "Apply and accept the result", "completionChecks": checks}
    gateway.replace_agent_progress({**scope, "items": [item]})
    gateway.replace_agent_progress({**scope, "sessionId": "other", "items": [item]})
    verified = {"status": "ok", "readbackState": "verified"}
    for receipt in [
        accepted_receipt(tool, "write", "preview", {}, {"target": "face", "preview": True}, verified, status="executed"),
        accepted_receipt(tool, "write", "pending", {}, {"target": "face"}, {"status": "needs_user_action"}, status="executed"),
        accepted_receipt(tool, "write", "partial", {}, {"target": "face", "committed": True, "partial": True}, verified, status="executed"),
        accepted_receipt(tool, "write", "unverified", {}, {"target": "face", "committed": True}, {"status": "ok"}, status="executed"),
    ]:
        assert receipt is None
    face = accepted_receipt(tool, "write", "face", {}, {"target": "face", "committed": True}, verified, status="executed")
    gateway._advance_runtime_todos("owner", str(tmp_path), [face])
    assert gateway.update_agent_progress("inspect", {**scope, "status": "completed"})["progress"]["status"] == "in_progress"

    approval = {"id": "approval", "taskContext": {**scope, "actionId": "body", "tool": tool}}
    gateway.resume_runtime_task_after_approval(approval, {"status": "approved", "outcome": verified})
    assert set(gateway.list_agent_progress(session_id="owner")["items"][0]["completionEvidence"]) == {"0"}
    gateway.resume_runtime_task_after_approval(approval, {
        "status": "applied", "outcome": verified, "result": {"target": "body", "committed": True},
    })
    assert set(gateway.list_agent_progress(session_id="owner")["items"][0]["completionEvidence"]) == {"0", "1"}
    for resolution in ("fail", "pass"):
        question = gateway.questions.create({
            **scope, "kind": "visual_checkpoint", "question": "Appearance accepted?",
            "_runtimeTaskSeed": {"requestedTool": "vrcforge_ask_user", "requestedArguments": {}},
        })["question"]
        gateway.questions.answer(question["questionId"], {**scope, "answer": resolution, "resolution": resolution, "settleOnly": True})
        status = gateway.list_agent_progress(session_id="owner")["items"][0]["status"]
        assert status == ("completed" if resolution == "pass" else "in_progress")
    assert gateway.list_agent_progress(session_id="other")["items"][0]["status"] == "pending"
