import base64
import io
import json

import anthropic
from google import genai
import httpx
from PIL import Image
import pytest

from test_vrchat_blendshape_agent import make_llm_settings
from vrchat_blendshape_agent import _request_llm_protocol_with_metadata


@pytest.mark.parametrize("provider, protocol", [("anthropic", "messages"), ("gemini", "generate_content")])
def test_native_image_history_reaches_sdk_wire_as_images(monkeypatch, provider, protocol):
    pngs = []
    history = []
    for label, color in zip(("upload", "steer", "tool screenshot"), ("red", "green", "blue")):
        out = io.BytesIO()
        Image.new("RGB", (12, 8), color).save(out, format="PNG")
        pngs.append(out.getvalue())
        data_url = "data:image/png;base64," + base64.b64encode(pngs[-1]).decode("ascii")
        history.append({"role": "user", "content": [
            {"type": "text", "text": label},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]})

    payloads = []

    def transport(request):
        payloads.append(json.loads(request.content))
        if provider == "anthropic":
            return httpx.Response(200, json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
                "content": [{"type": "text", "text": "seen"}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 1},
            })
        return httpx.Response(200, json={"candidates": [
            {"content": {"role": "model", "parts": [{"text": "seen"}]}, "finishReason": "STOP"}
        ]})

    http_client = httpx.Client(transport=httpx.MockTransport(transport))
    if provider == "anthropic":
        sdk = anthropic.Anthropic

        def client(**kwargs):
            kwargs["http_client"].close()
            return sdk(**{**kwargs, "http_client": http_client})

        monkeypatch.setattr(anthropic, "Anthropic", client)
    else:
        sdk = genai.Client
        monkeypatch.setattr(genai, "Client", lambda **kwargs: sdk(
            **kwargs, http_options={"httpx_client": http_client}
        ))

    settings = make_llm_settings(provider, model="test-model", base_url="")
    receipt = _request_llm_protocol_with_metadata(
        settings, protocol, "current", [], None, native_messages=history
    )
    assert receipt.text == "seen"
    assert len(payloads) == 1
    if provider == "anthropic":
        parts = [item for msg in payloads[0]["messages"] for item in msg["content"]]
        texts = [part["text"] for part in parts if part["type"] == "text"]
        images = [part["source"] for part in parts if part["type"] == "image"]
        assert all(image["type"] == "base64" and image["media_type"] == "image/png" for image in images)
    else:
        parts = [item for msg in payloads[0]["contents"] for item in msg["parts"]]
        texts = [part["text"] for part in parts if "text" in part]
        images = [part["inlineData"] for part in parts if "inlineData" in part]
        assert all(image["mimeType"] == "image/png" for image in images)

    assert texts == ["upload", "steer", "tool screenshot"]
    altchars = b"-_" if provider == "gemini" else None
    assert [base64.b64decode(img["data"], altchars=altchars, validate=True) for img in images] == pngs
