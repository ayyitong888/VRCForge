from dataclasses import dataclass
import ast
import base64
import hashlib
import io
import wave
import zipfile
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from general_agent_tools import (
    GENERAL_AGENT_WEB_TOOL_METADATA,
    WEB_FETCH_TOOL_NAME,
    WEB_SEARCH_TOOL_NAME,
    web_fetch,
    web_search,
)


@dataclass
class FakeResponse:
    content: bytes
    status_code: int = 200
    headers: dict[str, str] | None = None
    url: str = "https://example.test/final"


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_web_fetch_extracts_bounded_html_text_and_metadata() -> None:
    client = FakeClient(
        FakeResponse(
            b"<html><head><title>Example</title></head><body><h1>Hello</h1><script>secret</script></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )

    result = web_fetch("https://example.test/page", client=client, max_bytes=200)

    assert result["title"] == "Example"
    assert result["text"] == "Example Hello"
    assert result["content_type"] == "text/html"
    assert result["truncated"] is False
    assert client.calls[0][1]["timeout"] == 10.0


def test_web_fetch_rejects_http_failure_and_unsupported_type() -> None:
    with pytest.raises(RuntimeError, match="HTTP 404"):
        web_fetch("https://example.test/missing", client=FakeClient(FakeResponse(b"", 404, {})))
    with pytest.raises(ValueError, match="does not support"):
        web_fetch("https://example.test/file", client=FakeClient(FakeResponse(b"x", headers={"content-type": "application/pdf"})))


def test_web_fetch_preserves_complete_long_text_and_response_limits():
    text = "ordinary public source evidence\n" * 6000 + "COMPLETE_TEXT_TAIL"
    source = text.encode("utf-8")
    client = FakeClient(FakeResponse(source, headers={"content-type": "text/plain"}))
    result = web_fetch("https://example.test/long.txt", client=client)
    assert result["text"] == text
    assert result["bytes"] == len(source)
    assert result["truncated"] is False
    assert "content" not in result
    with pytest.raises(ValueError, match="complete web response exceeds"):
        web_fetch("https://example.test/long.txt", client=client, max_bytes=128)


def _binary_text_admission_fixtures():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        entry = zipfile.ZipInfo("notes.txt", date_time=(2020, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_DEFLATED
        zipped.writestr(entry, "ARCHIVE_CONTENT_MARKER")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x01" * 64)
    return [archive.getvalue(), audio.getvalue()]


@pytest.mark.parametrize("mime", [None, "text/plain"])
@pytest.mark.parametrize("source", _binary_text_admission_fixtures(), ids=["valid_zip", "valid_wav"])
def test_web_fetch_rejects_binary_mislabeled_or_missing_text_mime(source, mime):
    headers = {} if mime is None else {"content-type": mime}
    with pytest.raises(ValueError, match="UTF-8 text"):
        web_fetch("https://example.test/source", client=FakeClient(FakeResponse(source, headers=headers)))


def test_web_fetch_rejects_invalid_utf8_instead_of_replacing_source_bytes():
    with pytest.raises(ValueError, match="UTF-8 text"):
        web_fetch("https://example.test/source", client=FakeClient(FakeResponse(b"invalid text \xff\xfe", headers={"content-type": "text/plain"})))


def test_web_fetch_preserves_valid_unicode_text_and_html():
    text = "Complete source: 中文 日本語 😀"
    for body, mime in [(text, "text/plain"), ("<html><p>" + text + "</p></html>", "text/html")]:
        result = web_fetch("https://example.test/source", client=FakeClient(FakeResponse(body.encode("utf-8"), headers={"content-type": mime})))
        assert result["text"] == text
        assert result["truncated"] is False


def test_web_fetch_keeps_url_timeout_and_client_error_contracts():
    for url in ["file:///fixture/image.jpg", "relative/image.jpg"]:
        with pytest.raises(ValueError, match="absolute http"):
            web_fetch(url)
    with pytest.raises(ValueError, match="timeout exceeds"):
        web_fetch("https://example.test/source", timeout=31)
    class FailedClient:
        def get(self, *_args, **_kwargs):
            raise TimeoutError("fixture timeout")
    with pytest.raises(RuntimeError, match="web_fetch request failed: fixture timeout"):
        web_fetch("https://example.test/source", client=FailedClient())


def _jpeg(size=(1536, 768)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (32, 120, 220)).save(output, format="JPEG")
    return output.getvalue()


@pytest.mark.parametrize("content_type", ["image/jpg", "image/jpeg; charset=binary"])
def test_web_image_becomes_validated_native_pixels_without_download(content_type):
    from native_tool_images import bind_tool_images, collect_tool_images
    from responses_native_protocol import build_responses_input

    source = _jpeg()
    client = FakeClient(FakeResponse(source, headers={"content-type": content_type}))
    result = web_fetch("https://example.test/parameters.jpg", client=client)

    assert result["ok"] is True
    assert result["sourceMimeType"] == "image/jpeg"
    assert result["bytes"] == len(source)
    assert "text" not in result
    assert result["summary"]
    image = result["content"][0]
    assert image["mimeType"] == "image/png"
    pixels = base64.b64decode(image["data"], validate=True)
    assert hashlib.sha256(pixels).hexdigest() == image["sha256"]
    with Image.open(io.BytesIO(pixels)) as decoded:
        assert decoded.size == (1024, 512)
        decoded.verify()
    assert len(pixels) <= 1024 * 1024
    step = {"tool": "vrcforge_web_fetch", "actionId": "web-image", "status": "executed", "result": result}
    content = collect_tool_images([step], resource_reader=None, project_path="")
    request = bind_tool_images({"messages": [{"role": "system", "content": "state"}]}, content)
    assert request["messages"][0]["content"][1]["image_url"]["url"] == "data:image/png;base64," + image["data"]
    assert "input_image" in str(build_responses_input(request["messages"]))
    assert len(client.calls) == 1


def test_web_image_keeps_response_limit_and_rejects_invalid_or_animated_pixels():
    with pytest.raises(ValueError, match="complete web response exceeds"):
        web_fetch("https://example.test/large.jpg", client=FakeClient(FakeResponse(_jpeg(), headers={"content-type": "image/jpg"})), max_bytes=32)
    with pytest.raises(ValueError, match="supported still image"):
        web_fetch("https://example.test/fake.jpg", client=FakeClient(FakeResponse(b"<html>not pixels</html>", headers={"content-type": "image/jpg"})))
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, format="GIF", save_all=True, append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0)
    with pytest.raises(ValueError, match="animated"):
        web_fetch("https://example.test/animated.gif", client=FakeClient(FakeResponse(output.getvalue(), headers={"content-type": "image/gif"})))


def test_registered_web_fetch_wrapper_preserves_image_and_text_summaries():
    # Compile only this registered handler, without importing/starting the server.
    source = Path("dashboard_server.py").read_text(encoding="utf-8-sig")
    function = next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef) and node.name == "general_web_fetch_tool")
    namespace = {"Any": Any, "general_params": lambda params: dict(params), "bounded_int": lambda value, *_: int(value)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "dashboard_server.py:web_fetch_handler", "exec"), namespace)
    for response in [FakeResponse(_jpeg(), headers={"content-type": "image/jpg"}), FakeResponse(b"plain original text", headers={"content-type": "text/plain"})]:
        client = FakeClient(response)
        namespace["general_web_fetch"] = lambda url, **kwargs: web_fetch(url, client=client, **kwargs)
        result = namespace["general_web_fetch_tool"]({"url": "https://example.test/source"})
        assert result["summary"] == result.get("text", result["summary"])
        assert result["summary"]


def test_web_search_normalizes_public_html_results_without_network() -> None:
    html = b"""
    <html><body>
      <a class='result__a' href='https://example.test/a'>First result</a>
      <a class='result__snippet'>A useful snippet</a>
      <a class='result__a' href='https://example.test/b'>Second result</a>
    </body></html>
    """
    client = FakeClient(FakeResponse(html, headers={"content-type": "text/html"}, url="https://html.duckduckgo.com/html/"))

    result = web_search("vrcforge", client=client, max_results=1)

    assert result["query"] == "vrcforge"
    assert result["results"] == [{"title": "First result", "url": "https://example.test/a", "snippet": "A useful snippet"}]
    assert result["truncated"] is True
    assert "q=vrcforge" in client.calls[0][0]


def test_web_search_unwraps_duckduckgo_tracking_urls_before_snapshotting() -> None:
    html = b"<a class='result__a' href='//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.test%2Fitem&rut=unstable'>Source</a>"
    result = web_search(
        "tracking-url-normalization",
        client=FakeClient(FakeResponse(html, headers={"content-type": "text/html"})),
    )
    assert result["results"][0]["url"] == "https://example.test/item"


def test_web_tool_metadata_has_required_negative_guidance() -> None:
    assert {WEB_FETCH_TOOL_NAME, WEB_SEARCH_TOOL_NAME} <= set(GENERAL_AGENT_WEB_TOOL_METADATA)
    for metadata in GENERAL_AGENT_WEB_TOOL_METADATA.values():
        assert "when-to-use:" in metadata["description"]
        assert "when-NOT-to-use:" in metadata["description"]
        assert metadata["write"] is False
