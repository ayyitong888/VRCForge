from dataclasses import dataclass

import pytest

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
        web_fetch("https://example.test/file", client=FakeClient(FakeResponse(b"x", headers={"content-type": "image/png"})))


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


def test_web_tool_metadata_has_required_negative_guidance() -> None:
    assert {WEB_FETCH_TOOL_NAME, WEB_SEARCH_TOOL_NAME} <= set(GENERAL_AGENT_WEB_TOOL_METADATA)
    for metadata in GENERAL_AGENT_WEB_TOOL_METADATA.values():
        assert "when-to-use:" in metadata["description"]
        assert "when-NOT-to-use:" in metadata["description"]
        assert metadata["write"] is False
