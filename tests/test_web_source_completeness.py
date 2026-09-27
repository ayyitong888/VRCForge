import pytest

from general_agent_tools import web_fetch
from test_general_agent_web_tools import FakeClient, FakeResponse


def test_web_fetch_does_not_parse_a_cut_utf8_response_as_complete_source():
    response = FakeResponse("head 中文 tail".encode("utf-8"), headers={"content-type": "text/plain"})
    with pytest.raises(ValueError, match="resource limit"):
        web_fetch("https://example.test/page", client=FakeClient(response), max_bytes=7)


def test_web_fetch_preserves_long_admitted_source_tail():
    body = "readable fact " * 3000 + "WEB_SOURCE_LAST_FACT"
    response = FakeResponse(body.encode(), headers={"content-type": "text/plain"})
    result = web_fetch("https://example.test/page", client=FakeClient(response))
    assert result["text"] == body
    assert result["truncated"] is False
