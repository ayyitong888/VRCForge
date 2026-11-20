"""Request-bound routing and protocol conversion on a continuing native turn."""
from dataclasses import replace
import json

import dashboard_server as host
from agent_runtime_session_state import NATIVE_RUNTIME_CONTEXT_PREFIX, native_model_history
from responses_native_protocol import build_responses_input
from tests.test_native_context_compaction import Compactor, fixture
from tests.test_runtime_provider_session_override import configured_owner
from vrchat_blendshape_agent import _native_messages_to_anthropic, _native_messages_to_gemini


def test_mid_turn_change_measures_smaller_model_before_sending(tmp_path, monkeypatch):
    owner, original = configured_owner(tmp_path)
    monkeypatch.setattr(host, "PROVIDER_CONFIGURATION", owner)
    monkeypatch.setattr(host.PROVIDER_MODEL_CATALOG, "resolve_context_limit", lambda config: config.context_window)
    owner.save_api_config(replace(original, context_window=300_000))
    binding = host._RuntimePlannerProviderTurnBinding()
    compactor = Compactor()
    _, planner, model, turn = fixture(compactor)
    planner._turn = binding
    with planner.bind_turn({}):
        first = planner.plan_agent_turn("Continue checking", {}, {}, native_turn=turn)
        assert first["nextStep"] == "done" and not compactor.calls
        assert turn.snapshot()["messages"][-1]["providerRoute"] == replace(original, thinking_level="").native_binding()
        owner.save_api_config(replace(original, model="next", context_window=8_000, thinking_level="high"))
        second = planner.plan_agent_turn("Continue checking", {}, {}, native_turn=turn)
        assert second["nextStep"] == "done"
        assert binding.current_config().thinking_level == "high"
    assert len(compactor.calls) == 1 and len(model.requests) == 2
    assert turn.compaction["contextLimit"] == 8_000 and turn.compaction["applied"]
    assert "old context old context" not in str(model.requests[-1]["messages"])
    assert "Continue checking" in str(model.requests[-1]["messages"])


def test_cross_model_history_keeps_calls_and_results_in_each_target_protocol():
    call = {"id": "call-old", "type": "function", "function": {"name": "read", "arguments": '{"page":2}'}}
    assistant = {"role": "assistant", "content": "public", "tool_calls": [call],
                 "reasoning_content": "private", "anthropic_content": [{"signature": "private"}],
                 "gemini_parts": [{"thoughtSignature": "private"}], "responses_output": [{"encrypted_content": "private"}],
                 "provider_protocol": "responses", "providerRoute": "a" * 64}
    result = {"role": "tool", "tool_call_id": call["id"], "content": "receipt"}
    history = [assistant, result]
    assert native_model_history(history, provider_route="a"*64)[0]["responses_output"] == assistant["responses_output"]
    projected = native_model_history(history, provider_route="b"*64)
    assert projected == [{"role": "assistant", "content": "public", "tool_calls": [call]}, result]
    for namespace in (None, "vrcforge"):
        _, request, reply = build_responses_input(projected, namespace=namespace)
        assert request.get("namespace") == namespace
        assert request["call_id"] == reply["call_id"] == call["id"] and reply["output"] == "receipt"
    request, reply = _native_messages_to_anthropic(projected)
    assert request["content"][-1]["id"] == reply["content"][0]["tool_use_id"] == call["id"]
    (request, reply), _ = _native_messages_to_gemini(projected)
    assert request["parts"][-1]["functionCall"]["id"] == reply["parts"][0]["functionResponse"]["id"] == call["id"]
    assert request["parts"][-1]["thoughtSignature"] == "skip_thought_signature_validator"

def test_deepseek_thinking_receives_unsigned_calls_as_quoted_history_but_replays_its_own_trace():
    from runtime_planner_service import PlannerTurnMetadata, RuntimePlannerService

    planner = RuntimePlannerService(catalog=None, desktop=None, model=None)
    def transaction(origin, call_id, reasoning=None):
        call = {"id": call_id, "type":"function", "function":{"name":"read_probe","arguments":'{"page":2}'}}
        assistant = {"role":"assistant","content":"Checking.", "tool_calls":[call], "providerRoute":origin}
        if reasoning:
            assistant["responses_output"] = [{"type":"reasoning","content":[{"type":"reasoning_text","text":reasoning}]}]
        return [assistant, {"role":"tool","tool_call_id":call_id,"content":"Read marker " + call_id}]
    foreign = transaction("other", "call_external", "private foreign")
    native = transaction("dest", "call_native", "owned trace")
    unsigned = transaction("dest", "call_old")
    history = [*foreign, *native, *unsigned]
    metadata = PlannerTurnMetadata(provider="deepseek",provider_route="dest",thinking_level="high")
    planner._turn_metadata.set(metadata)
    quoted_foreign, own, own_result, quoted_unsigned = planner._project_provider_history(history)
    for quoted, source in ((quoted_foreign,foreign), (quoted_unsigned,unsigned)):
        assert quoted["role"] == "user"
        payload=json.loads(quoted["content"].removeprefix(NATIVE_RUNTIME_CONTEXT_PREFIX))
        expected = [{k: source[0][k] for k in ("role","content","tool_calls")}, source[1]]
        assert payload["source"] == "runtime" and payload["priorToolEvents"] == expected
    assert own["responses_output"] == native[0]["responses_output"] and own_result == native[1]
    assert "private foreign" not in str(quoted_foreign) and "owned trace" in str(own)
    planner._turn_metadata.set(replace(metadata, thinking_level="none"))
    assert planner._project_provider_history(history) == native_model_history(history, provider_route="dest")


def test_three_physical_requests_switch_protocols_between_two_tool_receipts(tmp_path, monkeypatch):
    from tests.test_agent_configured_provider_loop import _configured_service, _isolated_gateway, _planner_loopback, _provider_protocol_loopback

    tools = ["ProjectSettings/ProjectVersion.txt", "Packages/manifest.json"]
    reads = [{"action": "skill", "skill_tool": "read_text_file", "skill_params": {"path": path}} for path in tools]
    end = {"assistant_text": "Both reads finished."}
    a_records, b_records = [], []
    with _planner_loopback([reads[0], end], a_records, native=True) as a_url, _provider_protocol_loopback(
            "generate_content", [reads[1]], b_records) as b_url:
        config = _configured_service(tmp_path/"providers.json", a_url)
        a = replace(config.current_api_config(), provider="openai")
        b = replace(a, provider="custom", model="gemini-probe", api_type="generate_content", base_url=b_url)
        config.save_api_config(b)
        config.save_api_config(a)
        observed = []
        def read(_owner, tool, params, agent_name=None, **_options):
            observed.append(tool)
            config.save_api_config(b if len(observed) == 1 else a)
            return {"ok": True, "tool": tool, "status": "executed", "result": {"ok": True, "fixture": len(observed)},
                    "outcome": {"status": "ok", "summary": f"Read marker {len(observed)}", "verification": {"state":"not_required","checks":[]}}}
        with _isolated_gateway(tmp_path) as gateway:
            monkeypatch.setattr(host, "PROVIDER_CONFIGURATION", config)
            monkeypatch.setattr(host.PROVIDER_MODEL_CATALOG, "resolve_context_limit", lambda *_: 128_000)
            monkeypatch.setattr(type(gateway.runtime_skills), "execute", read)
            result = gateway.runtime_message({"message":"Check the two files.", "session_id":"switch-protocol",
                                             "client_turn_id":"one", "maxAgenticTurns":4})
    assert result["plan"]["nextStep"] == "done", (result["plan"].get("plannerFailure"), [r["path"] for r in a_records+b_records])
    assert observed == ["vrcforge_read_text_file"] * 2
    assert len(a_records) == 2 and len(b_records) == 1
    assert b_records[0]["body"]["contents"]
    assert "Read marker 1" in json.dumps(b_records[0]["body"])
    wire = a_records[-1]["body"]["messages"]
    assert "Read marker 2" in json.dumps(wire)
    assert not any("gemini_parts" in item or "providerRoute" in item for item in wire)
    returns = [item["tool_call_id"] for item in wire if item["role"] == "tool"]
    calls = [c["id"] for item in wire for c in item.get("tool_calls", [])]
    assert calls == returns and len(set(calls)) == 2
