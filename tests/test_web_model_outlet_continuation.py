import json

import pytest

from general_agent_tools import web_search
from runtime_planner_service import planner_read_output_evidence


class _Response:
    status_code = 200
    url = "https://html.duckduckgo.com/html/"
    headers = {"content-type": "text/html"}

    def __init__(self, content):
        self.content = content


class _Client:
    def __init__(self, content):
        self.content = content

    def get(self, _url, timeout=None):
        return _Response(self.content)


def _search_html(count=13):
    return "".join(
        f'<a class="result__a" href="https://example.test/{i}">Result {i}</a>'
        for i in range(count)
    ).encode()


def test_model_outlet_preserves_exact_search_cursor_and_safe_metadata():
    result = web_search("sample", client=_Client(_search_html()), max_results=5)
    evidence = planner_read_output_evidence("vrcforge_web_search", result)
    assert evidence["offset"] == 0
    assert evidence["nextOffset"] == 5
    assert evidence["totalCount"] == 13
    assert evidence["hasMore"] is True
    assert evidence["snapshotDigest"] == result["snapshotDigest"]
    assert evidence["nextRequest"] == {
        "tool": "vrcforge_web_search",
        "arguments": result["nextRequest"],
    }


def test_model_outlet_cursor_reconstructs_all_pages_and_changed_digest_rejects():
    client = _Client(_search_html())
    result = web_search("sample", client=client, max_results=5)
    titles = []
    while True:
        titles.extend(row["title"] for row in result["results"])
        evidence = planner_read_output_evidence("vrcforge_web_search", result)
        if not evidence["hasMore"]:
            break
        args = evidence["nextRequest"]["arguments"]
        result = web_search(client=client, max_results=args["maxResults"],
                            offset=args["offset"], snapshot_digest=args["snapshotDigest"],
                            query=args["query"])
    assert titles == [f"Result {i}" for i in range(13)]
    assert "nextRequest" not in evidence

    changed = _Client(_search_html() + b'<a class="result__a" href="https://example.test/new">New</a>')
    first_request = web_search("sample", client=client, max_results=5)["nextRequest"]
    with pytest.raises(ValueError, match="snapshot changed"):
        web_search(client=changed, query=first_request["query"],
                   max_results=first_request["maxResults"], offset=first_request["offset"],
                   snapshot_digest=first_request["snapshotDigest"])


def test_model_outlet_sanitizes_query_and_rejects_untrusted_cursor_fields():
    result = {
        "query": "api_key=SECRET_QUERY",
        "results": [], "offset": 0, "nextOffset": None, "totalCount": 0,
        "hasMore": False, "snapshotDigest": "a" * 64,
        "nextRequest": None,
    }
    evidence = planner_read_output_evidence("vrcforge_web_search", result)
    assert "SECRET_QUERY" not in json.dumps(evidence)
    assert evidence["snapshotDigest"] == "a" * 64
    forged = dict(result)
    forged["nextRequest"] = {"query": "api_key=SECRET_QUERY", "offset": 0,
                              "snapshotDigest": "not-a-digest", "maxResults": 5}
    evidence = planner_read_output_evidence("vrcforge_web_search", forged)
    assert "nextRequest" not in evidence
    assert "SECRET_QUERY" not in json.dumps(evidence)

    private_query = dict(forged)
    private_query["hasMore"] = True
    private_query["snapshotDigest"] = "b" * 64
    private_query["nextRequest"] = {"query": private_query["query"], "offset": 5,
                                     "snapshotDigest": private_query["snapshotDigest"], "maxResults": 5}
    evidence = planner_read_output_evidence("vrcforge_web_search", private_query)
    assert "nextRequest" not in evidence
    assert "SECRET_QUERY" not in json.dumps(evidence)
