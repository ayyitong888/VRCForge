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
