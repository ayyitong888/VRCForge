"""Offline Gateway regressions for handing a child final report to its parent."""

from copy import deepcopy
from contextlib import nullcontext
import json
import threading
from types import SimpleNamespace

import pytest

from agent_gateway import AgentGateway
from agent_task_loop import TASK_LOOP_SCHEMA, canonical_action_id
from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata, RuntimePlannerService
from sub_agent_delegate import run_readonly_llm_agent
from tests.test_dashboard_server import _TestRuntimePlannerCatalog, _TestRuntimePlannerDesktop
from tests.test_native_runtime_gateway import call


REPORT_MARKER = "CHILD_REPORT_FINDING_AFTER_MULTIPLE_PAGES"
RAW_SCAN_MARKER = "RAW_CHILD_SCAN_PAYLOAD_MUST_NOT_REACH_PARENT"
REPORT_TEXT = ("Verified child finding with source summary.\n" * 650) + REPORT_MARKER
DELEGATE_ARGUMENTS = {
    "role": "project_index_review",
    "task": "find relevant prefabs",
    "displayName": "Manuka",
}
DELEGATE_ACTION_ID = canonical_action_id(
    "skill", "vrcforge_delegate_subagent", DELEGATE_ARGUMENTS,
)


def _embedded_json(text: str):
    decoder = json.JSONDecoder()
    for marker in ("modelInformationPage=", "modelInformationContinuation=", "modelInformationRead=", "retainedResultPage="):
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            payload = text[index + len(marker):].lstrip()
            try:
                value, _ = decoder.raw_decode(payload)
            except json.JSONDecodeError:
                start = index + len(marker)
                continue
            yield value
            start = index + len(marker)


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            for embedded in _embedded_json(value):
                yield from _walk(embedded)
        else:
            yield from _walk(decoded)


def _owner_page_next_request(value) -> dict | None:
    requests = []
    for item in _walk(value):
        request = item.get("nextRequest")
        if not isinstance(request, dict) or request.get("tool") != "vrcforge_read_tool_result":
            continue
        arguments = request.get("arguments")
        if (isinstance(arguments, dict) and arguments.get("source") == "owner_model"
                and arguments.get("jsonPointer") == "/text"):
            requests.append(deepcopy(request))
    if not requests:
        return None
    return max(requests, key=lambda request: int(request.get("arguments", {}).get("textOffset", 0)))


class _ParentModel:
    def __init__(self, *, native: bool):
        self.native = native
        self.requests = []
        self.page_requests = []
        self.first_continuation = None
        self.saw_full_report = False
        self.initial_delegate_sent = False

    def _next_action(self, request_view):
        if REPORT_MARKER in json.dumps(request_view, ensure_ascii=False):
            self.saw_full_report = True
            return {"action": "reply", "reply": "The child report contains the requested finding.",
                    "completion_claim": {"satisfied": True, "evidence_action_ids": [DELEGATE_ACTION_ID]}}
        next_request = _owner_page_next_request(request_view)
        assert next_request is not None, "parent did not receive an owner_model page cursor"
        arguments = next_request["arguments"]
        assert arguments["resultRef"]
        self.page_requests.append(deepcopy(arguments))
        return {"action": "read", "arguments": arguments}

    def plan_native(self, request):
        self.requests.append(deepcopy(request))
        if not self.initial_delegate_sent:
            self.initial_delegate_sent = True
            receipt = call("delegate-child", "vrcforge_delegate_subagent", DELEGATE_ARGUMENTS)
            return PlannerModelResult("", assistant_message=receipt, finish_reason="tool_calls")
        if self.first_continuation is None:
            self.first_continuation = deepcopy(request)
            assert REPORT_MARKER not in json.dumps(request, ensure_ascii=False)
        action = self._next_action(request)
        if action["action"] == "read":
            receipt = call(f"read-child-report-{len(self.page_requests)}", "vrcforge_read_tool_result", action["arguments"])
        else:
            receipt = call("finish-child-report", "vrcforge_runtime_action", {
                "action": "reply", "reply": action["reply"],
                "completion_claim": action["completion_claim"],
            })
        return PlannerModelResult("", assistant_message=receipt,
                                  finish_reason="tool_calls" if receipt.get("tool_calls") else "stop")

    def plan(self, prompt):
        prompt = str(prompt)
        self.requests.append(prompt)
        if not self.initial_delegate_sent:
            self.initial_delegate_sent = True
            return PlannerModelResult(text=json.dumps({
                "action": "skill", "skill_tool": "vrcforge_delegate_subagent",
                "skill_params": DELEGATE_ARGUMENTS,
            }), planner_label="fixture")
        if self.first_continuation is None:
            self.first_continuation = prompt
            assert REPORT_MARKER not in prompt
        action = self._next_action(prompt)
        if action["action"] == "read":
            action = {"action": "skill", "skill_tool": "vrcforge_read_tool_result",
                      "skill_params": action["arguments"]}
        return PlannerModelResult(text=json.dumps(action, ensure_ascii=False), planner_label="fixture")


class _Turn:
    def __init__(self, native: bool):
        self.native = native

    def bind(self, _request):
        return nullcontext(PlannerTurnMetadata(
            provider="openai", model="fixture",
            native_binding="fixture-sub-agent-parent-native" if self.native else "",
        ))


def _gateway_case(tmp_path, *, native: bool):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    model = _ParentModel(native=native)
    gateway.bind_runtime_planner(RuntimePlannerService(
        catalog=_TestRuntimePlannerCatalog(gateway),
        desktop=_TestRuntimePlannerDesktop(gateway),
        model=model,
        turn=_Turn(native),
    ))
    captured = {}

    def delegate(params):
        copied = dict(params)
        seed, _session = gateway.consume_runtime_task_link(copied)
        assert seed is not None
        captured["seed"] = seed
        return {"ok": True, "status": "running", "taskId": "child-report-task"}

    gateway.register_tool(
        "vrcforge_delegate_subagent",
        "When to use: delegate one bounded read-only review. When NOT to use: delegate writes.",
        "read/debug", delegate,
    )
    gateway._parent_report_seed = captured
    return gateway, model


@pytest.mark.parametrize("native", [False, True], ids=["legacy", "native"])
def test_parent_pages_child_final_report_through_owner_model_reader(tmp_path, native):
    gateway, model = _gateway_case(tmp_path, native=native)

    def child_model(**_kwargs):
        return {"ok": True, "finalReport": REPORT_TEXT, "stepCount": 2}

    try:
        child = run_readonly_llm_agent(
            SimpleNamespace(run_sub_agent_llm=child_model),
            {"task": DELEGATE_ARGUMENTS["task"], "childSessionId": "offline-child-session",
             "maxSteps": 4, "tools": []},
            threading.Event(),
        )
        child["rawScanDiagnostic"] = RAW_SCAN_MARKER
        assert child["finalReport"] == REPORT_TEXT
        initial = gateway.runtime_message({
            "message": "Review the delegated specialist report.",
            "sessionId": "child-report-parent-session",
            "clientTurnId": "child-report-parent-turn",
            "projectRoot": str(tmp_path), "maxAgenticTurns": 4,
        })
        assert gateway._parent_report_seed.get("seed"), initial
        seed = gateway._parent_report_seed["seed"]
        continuation = gateway.resume_runtime_task_after_sub_agent({
            "subAgentTaskId": "child-report-task", "status": "completed",
            "taskSeed": seed, "summary": child["summaryText"], "result": child,
        })
    finally:
        gateway.shutdown_runtime_continuations(0)

    assert continuation is not None
    assert continuation["continuationSource"] == "sub_agent_finished"
    assert continuation["plan"]["nextStep"] == "done", json.dumps(continuation["plan"], ensure_ascii=False)
    assert model.first_continuation is not None
    assert model.saw_full_report
    assert len(model.page_requests) >= 2
    assert all(item["source"] == "owner_model" and item["jsonPointer"] == "/text"
               for item in model.page_requests)
    offsets = [item.get("textOffset", 0) for item in model.page_requests]
    assert offsets == sorted(set(offsets))
    assert REPORT_MARKER not in json.dumps(model.first_continuation, ensure_ascii=False)
    assert RAW_SCAN_MARKER not in json.dumps(model.requests, ensure_ascii=False)
