"""Real web adapters must preserve useful evidence in the next planner prompt."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from general_agent_tools import web_fetch, web_search
from runtime_planner_service import PlannerCatalogSnapshot, RuntimePlannerService
from runtime_planner_service import planner_read_output_evidence


@pytest.fixture(scope="module")
def web_adapters():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "dashboard_server.py").read_text(encoding="utf-8"))
    names = {"general_web_fetch_tool", "general_web_search_tool"}
    nodes = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(nodes) == 2
    return compile(ast.Module(body=nodes, type_ignores=[]), "actual_dashboard_web_adapters", "exec")


def projected(web_adapters, tool, html, params):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, text=html, headers={"content-type": "text/html"}, request=request,
    )))
    env = {"Any": object, "json": json, "general_params": dict,
           "bounded_int": lambda value, default, maximum: min(int(value), maximum),
           "general_web_fetch": lambda *args, **kwargs: web_fetch(*args, client=client, **kwargs),
           "general_web_search": lambda *args, **kwargs: web_search(*args, client=client, **kwargs)}
    exec(web_adapters, env)
    try:
        result = env[f"general_{tool}_tool"](params)
    finally:
        client.close()
    service = object.__new__(RuntimePlannerService)
    service._catalog = SimpleNamespace(read=lambda *args, **kwargs: PlannerCatalogSnapshot())
    prompt = service._build_llm_plan_prompt("Explain the requested page", [], loop_state=[{
        "tool": f"vrcforge_{tool}", "status": "executed", "actionId": "web-call-1",
        "result": result, "outcome": {"status": "ok", "verification": {"state": "passed"}},
    }])
    observation = service._llm_loop_step_observation({"tool": f"vrcforge_{tool}", "result": result})
    evidence = json.loads(observation.split("; readEvidence=", 1)[1]) if "; readEvidence=" in observation else {}
    return result, prompt, evidence


def test_fetch_real_adapter_preserves_body_title_and_public_url(web_adapters):
    url = "https://example.org/docs/setup?lang=en#install"
    html = "<html><title>Setup instructions</title><p>" + "Introduction. " * 25 + "</p><p>INSTALL_MARKER_452: use the portable archive.</p></html>"
    result, prompt, evidence = projected(web_adapters, "web_fetch", html, {"url": url})
    assert "INSTALL_MARKER_452" in result["text"]
    assert "INSTALL_MARKER_452" in prompt
    assert evidence["title"] == "Setup instructions"
    assert evidence["url"] == url
    assert evidence["truncated"] is False
    assert evidence["authority"] == "untrusted_tool_output"


def test_search_real_adapter_keeps_multiple_clickable_results(web_adapters):
    html = ''.join(f'<a class="result__a" href="https://example.org/docs/{i}">Guide {i}</a><a class="result__snippet">Evidence marker {i}</a>' for i in range(3))
    result, prompt, evidence = projected(web_adapters, "web_search", html, {"query": "setup"})
    assert len(result["results"]) == 3
    assert "https://example.org/docs/2" in prompt
    assert evidence["results"][2] == {"title": "Guide 2", "url": "https://example.org/docs/2", "snippet": "Evidence marker 2"}
    assert evidence["truncated"] is False


def test_web_evidence_bounds_redaction_and_honest_continuation(web_adapters):
    html = '<title>Long page</title><p>api_key=SYNTHETIC_PRIVATE_572 C:\\private\\file.txt /private/file.txt https://example.org/next ' + '"quoted" ' * 4000 + '</p>'
    _, prompt, evidence = projected(web_adapters, "web_fetch", html, {"url": "https://example.org/long"})
    assert "SYNTHETIC_PRIVATE_572" not in prompt
    assert "C:\\private\\file.txt" not in prompt
    assert "/private/file.txt" not in prompt
    assert "https://example.org/next" in evidence["text"]
    assert len(json.dumps(evidence, ensure_ascii=False)) <= 6000
    assert evidence["truncated"] is True
    assert evidence["omittedChars"] > 0
    assert "no offset" in evidence["continuation"]


def test_search_projection_reports_omitted_results(web_adapters):
    html = ''.join(f'<a class="result__a" href="https://example.org/{i}">Guide {i}</a><a class="result__snippet">' + 'detail ' * 200 + '</a>' for i in range(10))
    result, _, evidence = projected(web_adapters, "web_search", html, {"query": "setup", "maxResults": 10})
    assert len(result["results"]) == 10
    assert evidence["truncated"] is True
    assert evidence["returnedItems"] == 10
    assert evidence["omittedItems"] == 10 - len(evidence["results"])
    assert evidence["continuation"]
    assert len(json.dumps(evidence, ensure_ascii=False)) <= 6000


def test_web_projection_never_emits_a_cut_url_or_url_credentials():
    evidence = planner_read_output_evidence("vrcforge_web_fetch", {
        "url": "https://example.org/" + "long" * 500,
        "text": "Visit https://alice:PRIVATE_CREDENTIAL_832@example.org/docs and https://example.org/?api_key=PRIVATE_KEY_921",
    })
    assert evidence["url"] == ""
    assert evidence["truncated"] is True
    serialized = json.dumps(evidence)
    assert "PRIVATE_CREDENTIAL_832" not in serialized
    assert "PRIVATE_KEY_921" not in serialized
    assert "https://example.org/docs" in evidence["text"]


def test_web_source_truncation_remains_visible_even_for_short_projection():
    evidence = planner_read_output_evidence("vrcforge_web_fetch", {
        "url": "https://example.org/", "text": "Short prefix", "truncated": True,
    })
    assert evidence["sourceTruncated"] is True
    assert evidence["truncated"] is True
    assert evidence["continuation"]
