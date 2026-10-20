import base64
import io
import json
from copy import deepcopy

from PIL import Image
from responses_native_protocol import build_responses_input
from runtime_planner_service import RuntimePlannerService, PlannerTurnMetadata
from tests.native_planner_fixture import NativePlannerFixture
from test_native_context_guard import _Catalog, _Desktop, _NativeModel, _Turn


def test_tool_pixels_reach_native_model_and_responses_without_entering_history():
    output = io.BytesIO()
    Image.new("RGB", (16, 8), (9, 120, 240)).save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode()
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
