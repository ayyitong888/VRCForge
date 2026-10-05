import json
import uuid

from agent_runtime_native_turn import NativeRuntimeTurn
from agent_scan_result_policy import is_scan_result_policy_message
from tests.test_native_scan_result_policy import native_request
from tests.test_native_runtime_gateway import setup_gateway
from tests.test_provider_protocol_clients import _native_chat_settings
from vrchat_blendshape_agent import build_openai_compatible_request_payload


def wire(request):
    return build_openai_compatible_request_payload(
        _native_chat_settings(), "",
        native_messages=[{"role": "system", "content": request["instructions"]}, *request["messages"]],
        native_tools=request["tools"],
    )


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def test_policy_stays_append_only_in_final_provider_payload_and_after_disk_restart(tmp_path):
    gateway, _, _ = setup_gateway(tmp_path, [])
    session, binding = uuid.uuid4().hex, uuid.uuid4().hex
    scope = {"tool": "vrcforge_scan_fx_animator", "scanSnapshot": {
        "persisted": True, "scope": "validated_scope", "freshness": "validated",
        "scanScope": {"schema": "vrcforge.scan_scope.v1", "dependencyCoverage": "complete"}}}
    turn = NativeRuntimeTurn(gateway.runtime_sessions, gateway.runtime_planner,
                             session, uuid.uuid4().hex, binding, uuid.uuid4().hex, [scope], None, None, None)
    before = native_request(gateway.runtime_planner, turn, tmp_path)
    turn.persist_request_context(before["messages"][:-1])
    history = turn.snapshot()["messages"]
    old_wire = wire(before)
    turn.persist_scan_result_policy()
    first = wire(native_request(gateway.runtime_planner, turn, tmp_path))
    prefix_length = len(history) + 1
    assert encoded(first["messages"][:prefix_length]) == encoded(old_wire["messages"][:prefix_length])
    assert encoded(first["tools"]) == encoded(old_wire["tools"])
    assert is_scan_result_policy_message(first["messages"][prefix_length])
    saved = turn.snapshot()["messages"]
    restarted, _, _ = setup_gateway(tmp_path, [])
    resumed = NativeRuntimeTurn(restarted.runtime_sessions, restarted.runtime_planner,
                                session, uuid.uuid4().hex, binding, uuid.uuid4().hex, [], None, None, None)
    resumed.persist_scan_result_policy()
    second = wire(native_request(restarted.runtime_planner, resumed, tmp_path))
    assert encoded(second["messages"][1:len(saved) + 1]) == encoded(saved)
    assert second["messages"][prefix_length] == first["messages"][prefix_length]
    assert sum(is_scan_result_policy_message(message) for message in second["messages"]) == 1
    assert encoded(second["messages"][0]) == encoded(old_wire["messages"][0])
    assert encoded(second["tools"]) == encoded(old_wire["tools"])
