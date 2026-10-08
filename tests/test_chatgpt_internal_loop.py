"""Exercise the real internal loop with a local fake Responses stream."""

from copy import deepcopy
import json
from types import SimpleNamespace

from agent_gateway import AgentGateway
from chatgpt_responses_adapter import ChatGPTResponsesAdapter
from runtime_planner_service import PlannerModelResult, RuntimePlannerService
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop
from tests.test_native_runtime_gateway import NativeTurn, call, finish, run


def test_subscription_adapter_returns_tool_results_to_existing_loop(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    invoked = []
    gateway.register_tool(
        "vrcforge_read_text_file", "When to use: read. When NOT to use: write.", "plan/preview",
        lambda params: invoked.append(params) or {"text": "fixture-body"},
    )
    requests = []
    wire_requests = []
    closed = []
    replies = iter([call("read-1", "vrcforge_read_text_file", {"path": "fixture.txt"}), finish])

    class Model:
        def plan(self, prompt):
            raise AssertionError("OAuth must not replace the native loop with a JSON planner")

        def plan_native(self, request):
            requests.append(deepcopy(request))
            receipt = next(replies)
            if callable(receipt):
                receipt = receipt(request)
            output = [{"type": "reasoning", "id": f"rs_{len(requests)}", "summary": [], "encrypted_content": "private-reasoning-ciphertext"}]
            for item in receipt.get("tool_calls", []):
                output.append({
                    "type": "function_call", "id": "fc_" + item["id"],
                    "call_id": item["id"], "namespace": "vrcforge",
                    "name": item["function"]["name"], "arguments": item["function"]["arguments"],
                    "status": "completed",
                })

            def create(**payload):
                wire_requests.append(deepcopy(payload))
                return iter([{"type": "response.completed", "response": {
                    "status": "completed", "output": output,
                    "usage": {"input_tokens": 40, "output_tokens": 5, "total_tokens": 45},
                }}])

            adapter = ChatGPTResponsesAdapter(
                api_key="local-account", base_url="https://api.openai.com/v1",
                access_token_provider=lambda _: "fake-oauth-token",
                client_factory=lambda **kwargs: SimpleNamespace(
                    responses=SimpleNamespace(create=create), close=lambda: closed.append(True),
                ),
            )
            response = adapter.send_native_request(
                model="account-model", messages=request["messages"], tools=request["tools"],
            )
            return PlannerModelResult(response.text, assistant_message=response.assistant_message,
                                      finish_reason=response.finish_reason, usage=response.usage)

    gateway.bind_runtime_planner(RuntimePlannerService(
        catalog=_TestRuntimePlannerCatalog(gateway), desktop=_TestRuntimePlannerDesktop(gateway),
        model=Model(), turn=NativeTurn(),
    ))
    result = run(gateway, tmp_path)
    assert len(invoked) == 1
    assert len(wire_requests) == len(closed) == 2
    assert result["plan"]["nextStep"] == "done"
    assert wire_requests[0]["tools"] == wire_requests[1]["tools"]
    second = wire_requests[1]["input"]
    tool_result = next(item for item in second if item.get("type") == "function_call_output")
    assert tool_result["call_id"] == "read-1"
    assert "fixture-body" in tool_result["output"]
    assert any(item.get("namespace") == "vrcforge" and item.get("call_id") == "read-1" for item in second)
    assert any(item.get("encrypted_content") == "private-reasoning-ciphertext" for item in second)
    assert "private-reasoning-ciphertext" not in json.dumps(result)
    assert "private-reasoning-ciphertext" not in json.dumps(gateway._runtime_session_state.get_session("native-session"))
