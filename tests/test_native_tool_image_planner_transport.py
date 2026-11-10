import base64
import io
import json
from copy import deepcopy

from PIL import Image
from responses_native_protocol import build_responses_input
from runtime_planner_service import RuntimePlannerService, PlannerTurnMetadata
from tests.native_planner_fixture import NativePlannerFixture
from test_native_context_compaction import Compactor, fixture as compaction_fixture
from test_native_context_guard import _Catalog, _Desktop, _NativeModel, _Turn


def _encoded_png():
    output = io.BytesIO()
    Image.new("RGB", (16, 8), (9, 120, 240)).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode()


def test_tool_pixels_reach_native_model_and_responses_without_entering_history():
    encoded = _encoded_png()
    step = {"tool": "image_reader", "kind": "skill", "status": "executed", "actionId": "image-read",
            "result": {"ok": True, "content": [{"type": "image", "mimeType": "image/png", "data": encoded}]}}
    original = deepcopy(step)
    model = _NativeModel()
    planner = RuntimePlannerService(catalog=_Catalog(), desktop=_Desktop(), model=model,
                                    turn=_Turn(PlannerTurnMetadata(verified_context_limit=100000)))
    native = NativePlannerFixture([{"role": "user", "content": "Describe the tool image."}])
    usage = {}
    with planner.bind_turn({"provider": "fixture", "model": "native"}):
        planner.plan_agent_turn("Describe the tool image.", {}, {}, loop_state=[step],
                                context_usage=usage, native_turn=native)
    assert len(model.requests) == 1
    request = model.requests[0]
    assert request["messages"][-1]["role"] == "system"
    image_messages = [message for message in request["messages"] if isinstance(message.get("content"), list)]
    assert image_messages[0]["content"][1]["image_url"]["url"] == "data:image/png;base64," + encoded
    assert encoded in json.dumps(build_responses_input(request["messages"]))
    assert "input_image" in json.dumps(build_responses_input(request["messages"]))
    assert encoded not in json.dumps(native.messages())
    assert usage["nativeContextGuard"]["imageCount"] == 1
    assert step == original


def test_screenshot_keeps_its_wire_position_after_parallel_and_later_tool_receipts():
    encoded = _encoded_png()
    message = "Inspect the screenshot and verify the avatar."
    screenshot = {"tool": "vrcforge_capture_screenshot", "status": "executed", "actionId": "capture-a",
                  "result": {"ok": True, "content": [{"type": "image", "mimeType": "image/png", "data": encoded}]}}

    def call(call_id):
        return {"id": call_id, "type": "function", "function": {"name": "call_tool", "arguments": "{}"}}

    def result(call_id, action_id):
        return {"role": "tool", "tool_call_id": call_id,
                "content": json.dumps({"observations": [{"actionId": action_id}]})}

    model = _NativeModel()
    planner = RuntimePlannerService(catalog=_Catalog(), desktop=_Desktop(), model=model,
                                    turn=_Turn(PlannerTurnMetadata(verified_context_limit=100000)))
    native = NativePlannerFixture([
        {"role": "user", "content": message},
        {"role": "assistant", "content": None, "tool_calls": [call("capture"), call("parallel-read")]},
        result("capture", "capture-a"), result("parallel-read", "read-a"),
    ])
    usage = {}
    with planner.bind_turn({"provider": "fixture", "model": "native"}):
        planner.plan_agent_turn(message, {}, {}, loop_state=[screenshot], context_usage=usage, native_turn=native)
        native.admit({"role": "assistant", "content": None, "tool_calls": [call("later-read")]})
        native._queued_calls.clear()
        native._messages.append(result("later-read", "read-b"))
        planner.plan_agent_turn(message, {}, {}, loop_state=[screenshot], context_usage=usage, native_turn=native)

    first, second = [build_responses_input(request["messages"]) for request in model.requests]
    assert second[:len(first)] == first
    image_index = next(i for i, item in enumerate(first) if "input_image" in json.dumps(item))
    assert first[image_index - 1]["call_id"] == "parallel-read"
    assert first[image_index]["content"][1]["image_url"] == "data:image/png;base64," + encoded
    assert encoded not in json.dumps(native.messages())


def test_compacted_screenshot_stays_at_the_first_post_compaction_state():
    encoded = _encoded_png()
    step = {"tool": "vrcforge_capture_screenshot", "status": "executed", "actionId": "capture-a",
            "result": {"ok": True, "content": [{"type": "image", "mimeType": "image/png", "data": encoded}]}}
    compactor = Compactor()
    state, planner, model, turn = compaction_fixture(compactor)
    state.append_native_assistant("s", binding="b", message={
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "capture", "type": "function", "function": {"name": "call_tool", "arguments": "{}"},
        }],
    })
    state.settle_native_call("s", binding="b", call_id="capture", content='{"observations":[{"actionId":"capture-a"}]}')
    usage = {}
    with planner.bind_turn({}):
        planner.plan_agent_turn("Continue checking", {}, {}, loop_state=[step], context_usage=usage, native_turn=turn)
        assert turn.compaction["applied"] is True
        state.append_native_user("s", binding="b", message="Now verify the original selection")
        planner.plan_agent_turn("Continue checking", {}, {}, loop_state=[step], context_usage=usage, native_turn=turn)

    first, second = [build_responses_input(request["messages"]) for request in model.requests]
    assert second[:len(first)] == first
    assert first[-2]["content"][1]["image_url"] == "data:image/png;base64," + encoded
    assert encoded not in json.dumps(turn.messages())
    assert encoded not in json.dumps(compactor.calls[0][0])
