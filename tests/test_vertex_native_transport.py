import json

from google import genai
from google.auth.credentials import AnonymousCredentials
import httpx
import pytest

from test_vrchat_blendshape_agent import make_llm_settings
from vrchat_blendshape_agent import _request_llm_protocol_with_metadata


@pytest.mark.parametrize("streaming", [False, True])
def test_vertex_native_tool_round_trip_uses_sdk_wire(monkeypatch, streaming):
    requests = []
    outputs = [
        {"candidates": [{"content": {"role": "model", "parts": [{
            "functionCall": {"name": "read_file", "args": {"path": "manual.txt"}},
            "thoughtSignature": "AAECAw==",
        }]}, "finishReason": "STOP"}], "modelVersion": "gemini-test-build"},
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "File read."}]},
                         "finishReason": "STOP"}], "modelVersion": "gemini-test-build"},
    ]

    def transport(request):
        assert "/projects/test-project/locations/asia-northeast1/" in request.url.path
        requests.append(json.loads(request.content))
        response = outputs[len(requests) - 1]
        if streaming:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  text="data: " + json.dumps(response) + "\n\n")
        return httpx.Response(200, json=response)

    sdk_client = genai.Client
    credentials = AnonymousCredentials()
    credentials.token = "fixture-token"
    monkeypatch.setattr(genai, "Client", lambda **kwargs: sdk_client(
        **kwargs, credentials=credentials,
        http_options={"httpx_client": httpx.Client(transport=httpx.MockTransport(transport))},
    ))
    settings = make_llm_settings("vertexai", model="gemini-test", base_url="test-project/asia-northeast1")
    history = [{"role": "system", "content": "Use available tools."},
               {"role": "user", "content": "Read the instructions."}]
    tools = [{"type": "function", "function": {"name": "read_file", "description": "Read a file",
              "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    deltas = []
    callback = deltas.append if streaming else None
    first = _request_llm_protocol_with_metadata(
        settings, "generate_content", "fallback", [], callback, native_messages=history, native_tools=tools,
    )
    declaration = requests[0]["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "read_file"
    assert requests[0]["systemInstruction"]["parts"][0]["text"] == "Use available tools."
    assert requests[0]["contents"] == [{"role": "user", "parts": [{"text": "Read the instructions."}]}]
    assert first.finish_reason == "tool_calls"
    assert first.model == "gemini-test-build"
    call = first.assistant_message["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"path": "manual.txt"}

    persisted_message = json.loads(json.dumps(first.assistant_message))
    history += [persisted_message, {"role": "tool", "tool_call_id": call["id"],
                                   "content": '{"text": "expected manual"}'}]
    second = _request_llm_protocol_with_metadata(
        settings, "generate_content", "fallback", [], callback, native_messages=history, native_tools=tools,
    )
    replayed_call, tool_reply = requests[1]["contents"][1:]
    assert replayed_call["parts"][0]["function_call"]["name"] == "read_file"
    assert replayed_call["parts"][0]["thought_signature"] == "AAECAw=="
    assert tool_reply["parts"][0]["function_response"] == {
        "name": "read_file", "response": {"result": {"text": "expected manual"}},
    }
    assert second.text == "File read."
    assert second.finish_reason == "stop"
    if streaming:
        assert deltas == ["File read."]


@pytest.mark.parametrize("provider", ["vertexai", "gemini"])
@pytest.mark.parametrize("with_ids", [False, True])
def test_google_native_stream_keeps_ordered_parallel_calls_and_wire_ids(monkeypatch, provider, with_ids):
    sdk_client = genai.Client
    requests = []
    paths = ["manual.txt", "index.txt"]
    ids = ["google-call-one", "google-call-two"]

    def tool_part(index):
        call = {"name": "read_file", "args": {"path": paths[index]}}
        if with_ids:
            call["id"] = ids[index]
        part = {"functionCall": call}
        if index == 0:
            part["thoughtSignature"] = "AAECAw=="
        return part

    def event(part, finish=None):
        candidate = {"content": {"role": "model", "parts": [part]}}
        if finish is not None:
            candidate["finishReason"] = finish
        return {"candidates": [candidate], "modelVersion": "gemini-test-build"}

    def transport(request):
        requests.append(json.loads(request.content))
        events = [
            event({"text": "First. "}), event(tool_part(0)),
            event({"text": "Next. "}), event(tool_part(1), "STOP"),
        ] if len(requests) == 1 else [event({"text": "Both read."}, "STOP")]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text="".join("data: " + json.dumps(item) + "\n\n" for item in events))

    def create_client(**kwargs):
        if provider == "vertexai":
            credentials = AnonymousCredentials()
            credentials.token = "fixture-token"
            kwargs["credentials"] = credentials
        return sdk_client(**kwargs, http_options={"httpx_client": httpx.Client(transport=httpx.MockTransport(transport))})

    monkeypatch.setattr(genai, "Client", create_client)
    settings = make_llm_settings(provider, model="gemini-test", base_url="test-project/asia-northeast1")
    history = [{"role": "user", "content": "Read the manual and index."}]
    tools = [{"type": "function", "function": {"name": "read_file", "description": "Read a file",
              "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    deltas = []
    first = _request_llm_protocol_with_metadata(
        settings, "generate_content", "fallback", [], deltas.append, native_messages=history, native_tools=tools,
    )
    assert first.finish_reason == "tool_calls"
    assert first.text == "First. Next. "
    assert deltas == ["First. ", "Next. "]
    calls = first.assistant_message["tool_calls"]
    assert [json.loads(call["function"]["arguments"])["path"] for call in calls] == paths
    assert len({call["id"] for call in calls}) == 2

    history.append(json.loads(json.dumps(first.assistant_message)))
    history.extend({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"path": path})}
                   for call, path in zip(calls, paths))
    second = _request_llm_protocol_with_metadata(
        settings, "generate_content", "fallback", [], deltas.append, native_messages=history, native_tools=tools,
    )
    replayed, replies = requests[1]["contents"][1:]
    parts = replayed["parts"]
    signature_key, call_key, response_key = (
        ("thought_signature", "function_call", "function_response") if provider == "vertexai"
        else ("thoughtSignature", "functionCall", "functionResponse")
    )
    assert [parts[0]["text"], parts[2]["text"]] == ["First. ", "Next. "]
    assert parts[1][signature_key] == "AAECAw=="
    assert [parts[index][call_key].get("id") for index in (1, 3)] == (ids if with_ids else [None, None])
    responses = [part[response_key] for part in replies["parts"]]
    assert [response.get("id") for response in responses] == (ids if with_ids else [None, None])
    assert [response["response"]["result"]["path"] for response in responses] == paths
    assert second.text == "Both read."
