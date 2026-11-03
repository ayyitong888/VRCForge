from general_agent_tools import web_fetch
from runtime_planner_service import planner_read_output_evidence
from test_general_agent_web_tools import FakeClient, FakeResponse


def test_http_navigation_survives_model_evidence_projection():
    source = b'''<html><a href="/manual">Instructions</a>
        <a href="#">Continue</a><form action="/search"><input name="q"></form></html>'''
    result = web_fetch("https://example.test/page", client=FakeClient(
        FakeResponse(source, headers={"content-type": "text/html"})))
    evidence = planner_read_output_evidence("vrcforge_web_fetch", result)
    assert evidence["links"] == [{"text": "Instructions", "url": "https://example.test/manual"}]
    assert evidence["controls"] == [{"text": "Continue", "tag": "a"}]
    assert evidence["forms"] == result["forms"]
    assert evidence["sourceKind"] == "http_response"
    assert evidence["javascriptExecuted"] is False
    assert evidence["authority"] == "untrusted_tool_output"
