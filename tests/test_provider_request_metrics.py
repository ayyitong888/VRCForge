import json
from types import SimpleNamespace

import pytest

from provider_request_metrics import compare_prefix, payload_metrics
from responses_native_protocol import stream_response


def test_payload_metrics_has_exact_bounded_serialization_and_nonoverlapping_shape():
    secret = "Bearer secret-marker"
    payload = {
        "model": "fixture",
        "input": [
            {"role": "developer", "type": "message", "content": "中文指令"},
            {"role": "user", "type": "message", "content": [
                {"type": "input_text", "text": "image note"},
                {"type": "input_image", "image_url": "data:image/png;base64,secret-image"},
            ]},
        ],
        "tools": [{"type": "namespace", "namespace": "mcp", "functions": [
            {"name": "tool_alpha", "parameters": {}},
            {"name": "tool_beta", "parameters": {}},
        ]}],
        "stream": True,
        "extra_headers": {"Authorization": secret, "session-id": "session"},
    }
    metrics = payload_metrics(payload)

    serialized = metrics["serializedRequest"]
    top_level = metrics["topLevel"]
    assert serialized["exact"] is True
    assert serialized["chars"] == top_level["serializedChars"]
    assert serialized["bytes"] == top_level["serializedBytes"]
    assert serialized["chars"] == top_level["fieldChars"] + top_level["overhead"]["chars"]
    assert serialized["bytes"] == top_level["fieldBytes"] + top_level["overhead"]["bytes"]

    assert metrics["inputShape"]["itemCount"] == 2
    assert metrics["inputShape"]["roleBuckets"]["developer"]["count"] == 1
    assert metrics["inputShape"]["roleBuckets"]["user"]["count"] == 1
    assert metrics["inputShape"]["typeBuckets"]["message"]["count"] == 2
    assert metrics["inputShape"]["roleBuckets"]["user"]["bytes"] > 0
    assert metrics["inputShape"]["typeBuckets"]["message"]["chars"] > 0
    assert metrics["toolsShape"]["leafDefinitionCount"] == 2
    assert metrics["toolsShape"]["nameList"]["count"] == 2
    assert len(metrics["toolsShape"]["nameList"]["sha256"]) == 64
    assert secret not in json.dumps(metrics)
    assert "secret-image" not in json.dumps(metrics)
    assert "tool_alpha" not in json.dumps(metrics)


def test_payload_metrics_malformed_non_json_values_are_bounded_and_private():
    class NotJson:
        def __repr__(self):
            return "private-path-marker"

    metrics = payload_metrics({
        "input": [NotJson()],
        "tools": {"namespace": "mcp", "functions": "not-a-list"},
        "extra_headers": "not-a-header-map",
        "private": "C:/private-path-marker",
    })

    assert metrics["serializedRequest"]["exact"] is False
    assert metrics["toolsShape"]["leafDefinitionCount"] == 0
    rendered = json.dumps(metrics)
    assert "private-path-marker" not in rendered
    assert "not-a-header-map" not in rendered


def _stream_with_terminal_response(response, payload=None):
    callbacks = []

    def create(**_payload):
        return iter([{"type": "response.completed", "response": response}])

    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None)
    stream_response(
        client,
        payload or {"model": "fixture", "input": []},
        None,
        None,
        callbacks.append,
        error_factory=lambda message, _response: RuntimeError(message),
        request_observer=callbacks.append,
    )
    return [event for event in callbacks if event.get("kind") == "provider_request_metrics"][-1]


def test_sdk_prefix_and_privacy():
    secret = "credential-marker-123"
    original = {"model": "fixture", "input": [{"role": "developer", "content": "private instructions"},
                  {"role": "user", "content": "private history"}, {"role": "developer", "content": "state"}],
                "tools": [], "extra_headers": {"Authorization": secret, "session-id": "session"}}
    a = payload_metrics(original)
    b = payload_metrics({**original, "input": original["input"][:2]+[{"role": "assistant", "content": "delta"}]+original["input"][2:]})
    assert compare_prefix(a, b)["commonItems"] == 2
    assert compare_prefix(a, payload_metrics(original))["commonItems"] == 3
    assert compare_prefix(a, payload_metrics({**original, "tools": [{"type": "function"}]}))["resetReason"] == "requestShape"
    assert compare_prefix(a, payload_metrics({**original, "extra_headers": {"session-id": "other"}}))["resetReason"] == "identity"
    assert secret not in json.dumps(a)
    assert "private history" not in json.dumps(a)
    assert "private instructions" not in json.dumps(a)


@pytest.mark.parametrize("failure", [False, True])
def test_stream_records_one_start_and_terminal_with_usage(failure):
    callbacks = []
    count = []
    response = {"status": "failed" if failure else "completed", "output": [],
                "usage": {"input_tokens": 100, "output_tokens": 2, "total_tokens": 102,
                          "input_tokens_details": {"cached_tokens": 80}}}
    def create(**payload):
        count.append(payload)
        return iter([{"type": "response.failed" if failure else "response.completed", "response": response}])
    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None)
    def run():
        return stream_response(client, {"model": "fixture", "input": []}, None, None, callbacks.append,
                               error_factory=lambda message, _response: RuntimeError(message), request_observer=callbacks.append)
    if failure:
        with pytest.raises(RuntimeError):
            run()
    else:
        run()
    events = [v for v in callbacks if v["kind"] == "provider_request_metrics"]
    assert len(count) == 1
    assert [v["requestPhase"] for v in events] == ["start", "terminal"]
    assert events[0]["requestId"] == events[1]["requestId"]
    assert events[1]["usage"]["cached_tokens"] == 80
    assert events[1]["usage"]["cache_write_tokens"] is None
    assert events[1]["status"] == ("failed" if failure else "completed")


def test_observer_failure_does_not_retry_or_fail_request():
    count = []
    def create(**payload):
        count.append(payload)
        return iter([{"type": "response.completed", "response": {"output": [], "status": "completed"}}])
    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None)
    def broken(_event):
        raise OSError("fixture")
    result = stream_response(client, {"input": []}, None, None, broken,
                             error_factory=lambda message, _response: RuntimeError(message), request_observer=broken)
    assert result["status"] == "completed"
    assert len(count) == 1


def test_failed_request_without_usage_keeps_unknown():
    events = []
    def create(**_payload):
        raise OSError("fixture")
    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None)
    with pytest.raises(OSError):
        stream_response(client, {"input": []}, None, None, events.append,
                        error_factory=lambda message, _response: RuntimeError(message), request_observer=events.append)
    terminal = events[-1]
    assert terminal["usagePresent"] is False
    assert all(value is None for value in terminal["usage"].values())


def test_terminal_metrics_use_actual_response_service_tier_over_request_priority():
    terminal = _stream_with_terminal_response(
        {"output": [], "status": "completed", "service_tier": "default"},
        {"model": "fixture", "input": [], "service_tier": "priority"},
    )
    assert terminal["serviceTier"] == "default"


@pytest.mark.parametrize("service_tier", [None, "", "not-a-tier", 7])
def test_terminal_metrics_keep_missing_or_invalid_service_tier_unknown(service_tier):
    response = {"output": [], "status": "completed"}
    if service_tier is not None:
        response["service_tier"] = service_tier
    terminal = _stream_with_terminal_response(response)
    assert terminal["serviceTier"] is None


def test_failed_terminal_response_preserves_actual_service_tier():
    callbacks = []
    response = {"status": "failed", "output": [], "service_tier": "flex"}

    def create(**_payload):
        return iter([{"type": "response.failed", "response": response}])

    client = SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None)
    with pytest.raises(RuntimeError):
        stream_response(
            client,
            {"model": "fixture", "input": []},
            None,
            None,
            callbacks.append,
            error_factory=lambda message, _response: RuntimeError(message),
            request_observer=callbacks.append,
        )
    terminal = [event for event in callbacks if event.get("kind") == "provider_request_metrics"][-1]
    assert terminal["status"] == "failed"
    assert terminal["serviceTier"] == "flex"
