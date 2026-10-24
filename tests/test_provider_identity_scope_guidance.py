"""Replay real tool projections through both provider wire formats, offline."""
from copy import deepcopy
import json
import sys
from types import SimpleNamespace

import pytest

import dashboard_server as host
from agent_gateway import AgentGatewayConfig
from chatgpt_integration.adapter import ChatGPTResponsesAdapter
from runtime_planner_service import PlannerCatalogSnapshot, RuntimePlannerService
from tests.test_provider_protocol_clients import _native_chat_settings
from tests.test_runtime_planner_service import FakeCatalog
from vrchat_blendshape_agent import request_openai_compatible_plan_with_metadata


TOOL_NAMES = ("vrcforge_toggle_scene_object", "vrcforge_add_modular_avatar_component")


@pytest.fixture
def projected_tools(monkeypatch):
    # Use the actual registered handlers and shared descriptor without reading
    # machine configuration or invoking handlers/Core.
    monkeypatch.setattr(host.AGENT_GATEWAY, "ensure_config", lambda: AgentGatewayConfig())
    projections = host._RUNTIME_PROFILED_TOOL_REGISTRY.project(host.CapabilityProfile.UNITY_PROJECT)
    tools = []
    for name in TOOL_NAMES:
        projection = next(item for item in projections if item.internal_name == name)
        tools.append(host._runtime_planner_write_tool(host.AGENT_GATEWAY._write_handlers[name], projection))
    snapshot = PlannerCatalogSnapshot(visible_tools=tuple(tools), routable_tools=tuple(tools))
    planner = RuntimePlannerService(catalog=FakeCatalog(planning=snapshot, execution=snapshot), desktop=None)
    return planner, tools


def _loaded_request(planner, tools):
    observations = []
    for tool in tools:
        observations.append(planner.native_result_observation({
            "tool": "vrcforge_load_internal_tool_block", "status": "executed",
            "outcome": {"status": "ok"},
            "result": {"ok": True, "status": "loaded", "block": tool.block,
                       "selectedTools": [tool.name], "exposureLayer": "execution"},
        }, project_context_active=True))
    messages = [
        {"role": "user", "content": "Adjust the existing outfit object."},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "load-1", "type": "function", "function": {
                "name": "load_internal_tool_block", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "load-1",
         "content": json.dumps({"observations": observations})},
    ]
    request, _ = planner._build_native_plan_request(
        messages, observe={}, exposure_layer="execution", project_context_active=True,
        project_path="D:/Fixture", internal_tool_blocks=[tool.block for tool in tools],
        global_instructions="", project_instructions="",
    )
    return request


def _assert_object_guidance(text):
    for term in ("minimum scope: object", "avatar reference is insufficient", "existing host object"):
        assert term in text


def test_object_scope_survives_gpt_responses_wire_payload(projected_tools):
    planner, tools = projected_tools
    request = _loaded_request(planner, tools)
    wire = []

    def create(**payload):
        wire.append(deepcopy(payload))
        return iter([{"type": "response.completed", "response": {
            "status": "completed", "output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "Ready."}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }}])

    adapter = ChatGPTResponsesAdapter(
        api_key="offline-account", base_url="https://api.openai.com/v1",
        access_token_provider=lambda _: "offline-token",
        client_factory=lambda **_: SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None),
    )
    adapter.send_native_request(model="account-model", messages=request["messages"], tools=request["tools"])
    result = next(item["output"] for item in wire[0]["input"] if item.get("type") == "function_call_output")
    definitions = [definition for observation in json.loads(result)["observations"]
                   for definition in observation["loadedToolDefinitions"]]
    assert {item["name"] for item in definitions} == {tool.name for tool in tools}
    for definition in definitions:
        _assert_object_guidance(definition["parameters"]["properties"]["executionTarget"]["description"])


def test_object_scope_survives_glm_chat_wire_payload_and_legacy_prompt(projected_tools, monkeypatch):
    planner, tools = projected_tools
    request = _loaded_request(planner, tools)
    wire = []

    def create(**payload):
        wire.append(deepcopy(payload))
        return {"choices": [{"message": {"role": "assistant", "content": "Ready."},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **_: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None)))
    settings = _native_chat_settings()
    settings.llm_model = "glm-4.7"
    request_openai_compatible_plan_with_metadata(settings, request["instructions"],
        native_messages=request["messages"], native_tools=request["tools"])
    result = next(item["content"] for item in wire[0]["messages"] if item.get("role") == "tool")
    definitions = [definition for observation in json.loads(result)["observations"]
                   for definition in observation["loadedToolDefinitions"]]
    assert {item["name"] for item in definitions} == {tool.name for tool in tools}
    for definition in definitions:
        _assert_object_guidance(definition["parameters"]["properties"]["executionTarget"]["description"])

    prompt = planner._build_llm_plan_prompt("Adjust outfit", [], exposure_layer="execution",
        project_context_active=True, internal_tool_blocks=[tool.block for tool in tools])
    # Legacy planners carry definitions as user text, so inspect the final
    # provider request as well as the native history lane above.
    request_openai_compatible_plan_with_metadata(settings, prompt)
    legacy_text = wire[1]["messages"][-1]["content"]
    shared_line = next(line for line in legacy_text.splitlines() if line.startswith("shared_schema_definitions="))
    shared_defs = json.loads(shared_line.split("=", 1)[1])["$defs"]

    def resolve(schema):
        while "$ref" in schema:
            schema = shared_defs[schema["$ref"].removeprefix("#/$defs/")]
        return schema

    for tool in tools:
        line = next(line for line in legacy_text.splitlines() if line.startswith("- " + tool.name))
        schema, _ = json.JSONDecoder().raw_decode(line.split(" schema=", 1)[1])
        identity = resolve(resolve(schema)["properties"]["executionTarget"])
        _assert_object_guidance(identity["description"])
