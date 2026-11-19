"""Fixture-provider acceptance for the isolated read-only child runtime."""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import nullcontext

import pytest

import dashboard_server as server
from background_goal_runtime import RuntimeLaneBudget
from general_agent_tools import read_text_file
from outfit_package_inspector import inspect_outfit_package
from sub_agent_collaboration_service import SubAgentCollaborationPorts, SubAgentCollaborationService
from sub_agent_delegate import build_sub_agent_role_handlers, build_sub_agent_roles, run_outfit_package_inspection
from tests.test_native_runtime_gateway import call, setup_gateway
from tests.test_native_permission_modes import _set_mode
from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata


class RecordingTurn:
    def __init__(self):
        self.requests = []

    def bind(self, request):
        self.requests.append(dict(request))
        return nullcontext(PlannerTurnMetadata(native_binding="fixture-provider-model-route"))


def _finish(request):
    evidence = []
    for message in request["messages"]:
        if message.get("role") == "tool":
            for observation in json.loads(message["content"]).get("observations", []):
                if observation.get("actionId") and observation.get("outcome", {}).get("status") == "ok":
                    evidence.append(observation["actionId"])
    return call("final", "vrcforge_runtime_action", {"action": "reply", "reply": "Two checks agree.",
        "completion_claim": {"satisfied": True, "evidence_action_ids": evidence}})


def _child_gateway(tmp_path, replies):
    gateway, model, invoked = setup_gateway(tmp_path, replies)
    gateway.runtime_planner._turn = RecordingTurn()
    return gateway, model, invoked


def _run(gateway, tmp_path, **overrides):
    cancel = overrides.pop("cancel_event", threading.Event())
    request = dict(
        task="Check whether the two fixture reads support a conclusion.",
        child_session_id="child-session-1", model="fixture-child-model",
        system_prompt="Use evidence from both reads.", max_steps=4,
        project_path=str(tmp_path), selected_materials=[], cancel_event=cancel,
    )
    request.update(overrides)
    return gateway.run_sub_agent_llm(**request)


def test_child_uses_real_runtime_loop_for_two_readonly_tools_then_final(tmp_path):
    gateway, model, invoked = _child_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("read-b", "vrcforge_read_text_file", {"path": "b.txt"}),
        _finish,
    ])
    result = _run(gateway, tmp_path)
    assert gateway.runtime_planner._turn.requests[0]["model"] == "fixture-child-model"
    assert [item.get("path") for item in invoked] == ["a.txt", "b.txt"]
    assert len(model.requests) == 3
    assert result["ok"] is True
    assert isinstance(result["finalReport"], str) and result["finalReport"]
    assert result["rawResult"]["planMode"] is False
    assert result["stepCount"] <= 4


@pytest.mark.parametrize("source", ["parent_agent", "user", "retry"])
def test_child_input_credits_its_author_and_keeps_selected_materials_separate(tmp_path, source):
    gateway, model, _ = _child_gateway(tmp_path, [{"role": "assistant", "content": "Reviewed."}])
    task = "Review the reference from the selected folder."
    reference = "Example excerpt from the project."
    result = _run(gateway, tmp_path, task=task, task_source=source,
                  selected_materials=[{"text": reference}])
    assert result["ok"]
    messages = model.requests[0]["messages"]
    task_message = next(item for item in messages if item["role"] == "user" and task in item["content"])
    with gateway.runtime_planner.bind_turn({"model": "fixture-child-model", "projectRoot": str(tmp_path)}):
        binding = gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    snapshot = gateway._runtime_session_state.native_conversation("child-session-1", binding=binding)
    human = [snapshot["messages"][index]["content"] for index in snapshot["humanUserMessageIndices"]]
    assert human == ([task] if source == "user" else [])
    if source == "user":
        assert task_message["content"] == task
    else:
        assert json.loads(task_message["content"].split(": ", 1)[1]) == {"source": source, "text": task}
    assert reference not in task_message["content"]
    assert any(item["role"] == "user" and reference in item["content"] for item in messages)


@pytest.mark.parametrize("has_project", [True, False])
def test_native_child_project_read_rejects_foreign_alias_or_missing_parent_project(tmp_path, has_project):
    from agent_gateway import AgentGatewayError

    selected_project = tmp_path / "owning-project"
    selected_project.mkdir()
    tool_name = "vrcforge_scan_modular_avatar"
    arguments = {"project_path": str(tmp_path / "ungranted-project")} if has_project else {}
    gateway, _model, _ = _child_gateway(tmp_path, [
        call("scan", tool_name, arguments),
        {"role": "assistant", "content": "Finished the fixture read."},
    ])
    scans = []
    gateway.register_tool(tool_name, "Inspect a fixture project.", "read/debug",
                          lambda params: scans.append(params) or {"ok": True})

    with pytest.raises(AgentGatewayError, match="runtime-bound projectRoot|parent project"):
        _run(gateway, tmp_path, project_path=str(selected_project) if has_project else "")
    assert not scans


@pytest.mark.parametrize("tool_name,has_project", [
    ("vrcforge_scan_modular_avatar", True),
    ("vrcforge_project_catalog_registration_status", False),
])
def test_native_child_can_inspect_own_project_or_global_catalog(tmp_path, tool_name, has_project):
    arguments = {"project_path": str(tmp_path)} if has_project else {}
    gateway, _model, _ = _child_gateway(tmp_path, [
        call("scan", tool_name, arguments),
        {"role": "assistant", "content": "Finished the fixture read."},
    ])
    scans = []
    gateway.register_tool(tool_name, "Inspect project facts.", "read/debug",
                          lambda params: scans.append(params) or {"ok": True})
    assert _run(gateway, tmp_path, project_path=str(tmp_path) if has_project else "")["ok"]
    assert len(scans) == 1
    assert "project_path" not in scans[0]
    assert scans[0].get("projectPath") == (str(tmp_path) if has_project else None)


@pytest.mark.skipif(os.name != "nt", reason="explicit path grants use Windows paths")
@pytest.mark.parametrize("granted", [True, False])
def test_delegated_child_uses_parent_general_read_scope_not_model_text(tmp_path, monkeypatch, granted):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "source.txt"
    target.write_text("source bytes", encoding="utf-8")
    delegated_task = "Read the reference file." if granted else f"Read {target}"
    child, _model, reads = _child_gateway(tmp_path / "child", [
        call("read", "vrcforge_read_text_file", {"path": str(target)}),
        _finish,
    ])
    _set_mode(child, "roslyn_full_auto")
    assert child.approval_transactions.permission_audit_context()["fullPermission"]

    def scoped_read(arguments):
        result = read_text_file(arguments["path"], allowed_roots=arguments["_generalAllowedRoots"])
        reads.append(result)
        return result

    child.register_tool("vrcforge_read_text_file", "Read scoped file.", "plan/preview", scoped_read)
    service = SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=tmp_path / "children", gateway=child, lane_budget=RuntimeLaneBudget(),
        build_roles=build_sub_agent_roles, build_handlers=build_sub_agent_role_handlers,
    ))
    gateway, _model, _ = _child_gateway(tmp_path / "parent", [
        call("delegate", "vrcforge_delegate_subagent", {
            "role": "llm_readonly", "task": delegated_task,
            "projectPath": str(project if granted else outside),
            "selectedMaterials": [] if granted else [{"text": str(target)}],
            "params": {
                "projectPath": str(outside),
                "_generalReadRoots": [str(outside)],
                "_taskSeed": {"_generalReadRoots": [str(outside)]},
            },
        }),
        _finish,
    ])
    _set_mode(gateway, "auto")
    monkeypatch.setattr(server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    gateway.register_tool(
        "vrcforge_delegate_subagent", "Delegate scoped read.", "plan/preview", server.delegate_sub_agent_runtime,
    )
    try:
        import asyncio
        from dashboard_api_models import AgentRuntimeMessageRequest
        asyncio.run(server._execute_app_agent_runtime_message(AgentRuntimeMessageRequest(**{
            "message": f"Delegate reading files under {outside}" if granted else "Delegate a bounded read.",
            "sessionId": "parent-session", "projectRoot": str(project),
        }), human_user_message=True))
        task_id = service.list_tasks()["tasks"][0]["id"]
        service._registry._threads[task_id].join(5)
        assert not service._registry._threads[task_id].is_alive()
        task = service.get_task(task_id)["task"]
        assert task["projectPath"] == str(project)
        assert child.runtime_planner._turn.requests[0]["projectRoot"] == str(project)
        assert "generalReadRoots" not in task
        if granted:
            assert task["status"] == "completed" and reads[0]["text"] == "source bytes"
        else:
            assert not reads
    finally:
        service.shutdown()


@pytest.mark.parametrize("plan_mode", [False, True])
def test_parent_omitting_role_runs_general_readonly_child_without_project(tmp_path, monkeypatch, plan_mode):
    from dashboard_api_models import SubAgentCreateRequest

    url = "https://modular-avatar.nadena.dev/docs/reference/merge-armature"
    source_excerpt = "Merge Armature remaps clothing bones and updates skinned mesh references."
    request = {"task": f"Read the Merge Armature documentation at {url} and summarize it."}
    child, _child_model, _ = _child_gateway(tmp_path / "child", [
        call("fetch", "vrcforge_web_fetch", {"url": url}),
        {"role": "assistant", "content": source_excerpt},
    ])
    fetches = []

    def fetch(arguments):
        fetches.append(arguments["url"])
        return {"ok": True, "url": url, "text": source_excerpt}

    child.register_tool("vrcforge_web_fetch", "Read public documentation.", "read/debug", fetch)
    service = SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=tmp_path / "children", gateway=child, lane_budget=RuntimeLaneBudget(),
        build_roles=build_sub_agent_roles, build_handlers=build_sub_agent_role_handlers,
    ))
    parent, _parent_model, _ = _child_gateway(tmp_path / "parent", [
        call("delegate", "vrcforge_delegate_subagent", request),
        _finish,
    ])
    monkeypatch.setattr(server, "AGENT_GATEWAY", parent)
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    parent.register_tool(
        "vrcforge_delegate_subagent", "Delegate a read.", "plan/preview", server.delegate_sub_agent_runtime,
    )
    try:
        parent_result = parent.runtime_message({
            "message": request["task"], "sessionId": "general-parent", "planMode": plan_mode,
        })
        assert parent_result["planMode"] is plan_mode
        task = service.list_tasks()["tasks"][0]
        service._registry._threads[task["id"]].join(5)
        detail = service.get_task(task["id"])
        assert detail["task"]["taskSource"] == task["taskSource"] == "parent_agent"
        assert detail["task"]["role"] == SubAgentCreateRequest(**request).role == "llm_readonly"
        assert detail["task"]["projectPath"] == ""
        assert detail["task"]["status"] == "completed", detail["task"]
        assert fetches == [url]
        assert source_excerpt in detail["task"]["summary"]
    finally:
        service.shutdown()


@pytest.mark.parametrize("request_case", ["verified", "no_scan", "scan_failed", "cancelled"])
def test_outfit_inspection_delegates_native_child_and_delivers_exact_scanner_receipt(tmp_path, monkeypatch, request_case):
    project = tmp_path / "project"
    project.mkdir()
    materials = project / "素材"
    materials.mkdir()
    for extension, count in (("unitypackage", 14), ("prefab", 86), ("png", 232)):
        for index in range(count):
            (materials / f"fixture{index}.{extension}").touch()
    inspect_tool = "vrcforge_inspect_outfit_package"
    arguments = {"packagePath": str(materials), "maxEntries": 50000}
    final_report = "共 14 个 UnityPackage、86 个 Prefab 候选、232 张纹理。"
    reply = {"role": "assistant", "content": final_report}
    child_calls = [] if request_case == "no_scan" else [call("scan", inspect_tool, arguments)]
    followup_replies = [reply]
    if request_case == "verified":
        unrelated = tmp_path / "other"
        unrelated.mkdir()
        child_calls.insert(0, call("wrong-bound", inspect_tool, {**arguments, "maxEntries": 5000}))
        child_calls.append(call("duplicate", inspect_tool, arguments))
        followup_replies.extend([
            call("other-scan", inspect_tool, {**arguments, "packagePath": str(unrelated)}),
            reply,
        ])
    child, child_model, _ = _child_gateway(tmp_path / "child", [*child_calls, reply, *followup_replies])
    inspected = []
    scan_started = threading.Event()
    release_scan = threading.Event()

    def inspect(arguments):
        inspected.append(arguments)
        scan_started.set()
        if request_case == "cancelled":
            assert release_scan.wait(5)
        if request_case == "scan_failed":
            return {"ok": False, "error": "Unreadable fixture package."}
        return inspect_outfit_package(arguments["packagePath"], max_entries=arguments["maxEntries"])

    child.register_tool(inspect_tool, "When to use: inspect. When NOT to use: import.", "read/debug", inspect)
    request = {
        "role": "outfit_package_inspection",
        "displayName": "素材复核",
        "task": "只读检查指定素材目录，统计 UnityPackage、Prefab 候选和纹理数量。",
        "model": "fixture-child-model",
        "params": {**arguments, "_generalReadRoots": ["must-not-be-used"]},
    }
    expected = {"unityPackageCount": 14, "prefabCandidateCount": 86, "textureCount": 232}
    parent_deliveries = []
    parent_observations = []
    parent_ready = threading.Event()

    def finish_parent(request):
        observation_text = "\n".join(message["content"] for message in request["messages"]
                                     if message["role"] == "tool")
        parent_observations.append(observation_text)
        return {"role": "assistant", "content": final_report}

    parent, _model, _ = _child_gateway(tmp_path / "parent", [
        call("delegate", "vrcforge_delegate_subagent", request),
        finish_parent,
    ])

    def handoff(event):
        try:
            parent_deliveries.append(
                parent.resume_runtime_task_after_sub_agent(event) if request_case == "verified" else event
            )
        finally:
            parent_ready.set()

    service = SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=tmp_path / "children", gateway=child, lane_budget=RuntimeLaneBudget(),
        build_roles=build_sub_agent_roles, build_handlers=build_sub_agent_role_handlers, task_finished=handoff,
    ))
    monkeypatch.setattr(server, "AGENT_GATEWAY", parent)
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    parent.register_tool(
        "vrcforge_delegate_subagent", "Delegate a read.", "plan/preview", server.delegate_sub_agent_runtime,
    )
    try:
        parent.runtime_message({
            "message": request["task"], "sessionId": "outfit-parent-session",
            "projectRoot": str(project), "model": "fixture-parent-model",
        })
        task_id = service.list_tasks()["tasks"][0]["id"]
        if request_case == "cancelled":
            assert scan_started.wait(5)
            service.cancel_task(task_id)
            release_scan.set()
        service._registry._threads[task_id].join(8)
        detail = service.get_task(task_id)["task"]
        assert detail["handler"] == "llm_readonly"
        assert detail["childSessionId"] and detail["childSessionId"] != "outfit-parent-session"
        assert child.runtime_planner._turn.requests[0]["model"] == "fixture-child-model"
        first_child_request = child_model.requests[0]
        visible = _native_available_tool_names(first_child_request)
        assert inspect_tool in visible and "vrcforge_read_text_file" not in visible
        assert len(inspected) == (0 if request_case == "no_scan" else 1)
        if inspected:
            assert inspected == [arguments]
        assert parent_ready.wait(8)
        assert len(parent_deliveries) == 1
        if request_case == "verified":
            assert detail["status"] == "completed", detail
            assert detail["result"]["finalReport"] == final_report
            assert detail["model"] == "fixture-child-model"
            assert detail["result"]["providerRoute"]["model"] == "fixture-child-model"
            assert {key: detail["result"]["plannerEvidence"][key] for key in expected} == expected
            assert detail["result"]["rawResult"]["steps"][1]["result"]["summary"]["textureCount"] == 232
            assert parent_deliveries[0]["plan"]["reply"] == final_report
            assert parent_observations and all(key in parent_observations[0] for key in expected), ascii(parent_observations)
            assert final_report in parent_observations[0], ascii(parent_observations)
        elif request_case == "cancelled":
            assert detail["status"] == "cancelled", detail
            assert parent_deliveries[0]["status"] == "cancelled"
            assert parent_deliveries[0]["result"] is None
        else:
            assert detail["status"] == "failed", detail
            assert parent_deliveries[0]["status"] == "failed"
            assert final_report not in detail["summary"]
            assert "finalReport" not in detail["result"]
            assert ("Unreadable fixture package" if request_case == "scan_failed" else
                    "did not obtain a successful package inspection receipt") in detail["error"]

        if request_case in {"verified", "no_scan"}:
            resumed = service._registry.resume_task(
                task_id, task="Explain the count from the previous inspection.",
                general_read_roots=() if request_case == "verified" else (str(project),),
            )
            assert resumed["ok"], resumed
            followup_id = resumed["task"]["id"]
            service._registry._threads[followup_id].join(8)
            followup = service.get_task(followup_id)["task"]
            assert followup["childSessionId"] == detail["childSessionId"]
            if request_case == "verified":
                assert followup["status"] == "completed", followup
                assert {key: followup["result"]["plannerEvidence"][key] for key in expected} == expected
                assert any(message.get("tool_call_id") == "scan"
                           for message in child_model.requests[-1]["messages"])
                assert inspected == [arguments]
                rejected = service._registry.resume_task(
                    followup_id, task="Review the counts.", general_read_roots=(str(unrelated),),
                )
                assert rejected["ok"], rejected
                rejected_id = rejected["task"]["id"]
                service._registry._threads[rejected_id].join(8)
                rejected_task = service.get_task(rejected_id)["task"]
                assert rejected_task["status"] == "failed", rejected_task
                assert inspected == [arguments]
            else:
                assert followup["status"] == "failed", followup
                assert "did not obtain a successful package inspection receipt" in followup["error"]
                assert not inspected
    finally:
        release_scan.set()
        service.shutdown()


def test_plain_text_reply_is_a_successful_terminal_analysis_without_tool_completion_claim(tmp_path):
    gateway, model, invoked = _child_gateway(tmp_path, [
        {"role": "assistant", "content": "The supplied notes support the conclusion because both state the same date."},
    ])
    result = _run(gateway, tmp_path, task="Compare the supplied notes.")
    assert not invoked
    assert len(model.requests) == 1
    assert result["finalReport"].startswith("The supplied notes support")
    assert result["rawResult"]["plan"]["nextStep"] == "done"
    assert result["rawResult"]["task"]["status"] == "planning"
    assert result["ok"] is True
    assert result["status"] == "completed"


def test_child_history_system_prompt_and_model_are_bound_without_parent_history(tmp_path):
    gateway, model, _ = _child_gateway(tmp_path, [_finish])
    with gateway.runtime_planner.bind_turn({"model": "fixture-child-model", "projectRoot": str(tmp_path)}):
        binding = gateway.runtime_planner.native_conversation_binding(str(tmp_path))
    gateway._runtime_session_state.begin_native_turn(
        "parent-session", binding=binding, turn_id="parent-turn",
        message="PARENT PRIVATE HISTORY SENTINEL")
    parent_instructions = gateway.runtime_planner._read_global_instructions()
    result = _run(gateway, tmp_path)
    request = model.requests[0]
    messages = request["messages"] if isinstance(request, dict) else request.messages
    assert "PARENT PRIVATE HISTORY SENTINEL" not in json.dumps(messages)
    instructions = request["instructions"] if isinstance(request, dict) else request.instructions
    assert "Use evidence from both reads." in instructions
    assert gateway.runtime_planner._read_global_instructions() == parent_instructions
    bound = gateway.runtime_planner._turn.requests[1]
    assert bound.get("sessionId") == "child-session-1" or bound.get("session_id") == "child-session-1"
    assert gateway._runtime_session_state.native_conversation(
        "child-session-1", binding=binding) is not None
    assert gateway._runtime_session_state.native_conversation(
        "parent-session", binding=binding)["messages"][-1]["content"] == "PARENT PRIVATE HISTORY SENTINEL"


def test_plan_mode_blocks_proposed_write_before_handler_dispatch(tmp_path):
    from tests.test_native_permission_modes import _write_gateway_for_mode
    gateway, model, invoked = _write_gateway_for_mode(tmp_path, [
        call("write-attempt", "fixture_write", {"value": "must-not-run"}),
        {"role": "assistant", "content": "Write was blocked."},
    ])
    result = _run(gateway, tmp_path)
    assert not invoked
    assert len(model.requests) <= 2
    assert result["ok"] is False


def test_readonly_child_cannot_start_another_worker_even_with_full_permission(tmp_path):
    tool = "vrcforge_delegate_subagent"
    gateway, model, _ = _child_gateway(tmp_path, [
        call("recursive", tool, {"task": "Start another worker."}),
        {"role": "assistant", "content": "I cannot delegate another child."},
    ])
    invoked = []
    _set_mode(gateway, "roslyn_full_auto")
    gateway.register_tool(tool, "Delegate read-only work.", "plan/preview", lambda args: invoked.append(args))

    result = _run(gateway, tmp_path, allowed_tools=(tool,))
    assert not invoked
    assert tool not in _native_available_tool_names(model.requests[0])
    assert result["rawResult"]["planMode"] is False
    assert "tool_not_visible" in json.dumps(result)


def _register_second_read_tool(gateway, invoked):
    gateway.register_tool(
        "vrcforge_search_text", "When to use: search selected text. When NOT to use: write.",
        "plan/preview", lambda params: invoked.append(("search", params)) or {"matches": []},
    )


def _native_function_names(request):
    names = []
    for item in request.get("tools", []):
        function = item.get("function") if isinstance(item, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            names.append(name)
    return names


def _native_available_tool_names(request):
    for message in request.get("messages", []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.startswith("Current runtime state (data): "):
            return json.loads(content.partition(": ")[2]).get("availableToolNames", [])
    raise AssertionError("native request did not include runtime catalog state")


def test_scoped_child_can_follow_its_scan_receipt_without_reopening_the_package(tmp_path):
    tool = "vrcforge_inspect_outfit_package"
    folder = tmp_path / "materials"
    folder.mkdir()
    (folder / "sample.prefab").touch()
    arguments = {"packagePath": str(folder), "maxEntries": 5000}
    scans = []

    def inspect(params):
        scans.append(params)
        return inspect_outfit_package(params["packagePath"], max_entries=params["maxEntries"])

    def read_receipt(request):
        message = next(item for item in request["messages"] if item.get("tool_call_id") == "scan")
        observation = json.loads(message["content"])["observations"][0]["observation"]
        continuation, _ = json.JSONDecoder().raw_decode(observation.split("; resultContinuation=", 1)[1])
        return call("read-result", "call_tool", continuation["nextRequest"])

    gateway, model, _ = _child_gateway(tmp_path, [
        call("scan", tool, arguments), read_receipt, _finish,
    ])
    gateway.register_tool(tool, "When to use: inspect. When NOT to use: import.", "read/debug", inspect)
    result = run_outfit_package_inspection(gateway, {
        **arguments, "roleId": "outfit_package_inspection", "task": "Inspect the selected package.",
        "childSessionId": "inspection-owner", "model": "fixture-child-model", "maxSteps": 4,
        "projectPath": str(tmp_path), "tools": [tool], "_generalReadRoots": (str(folder),),
    }, threading.Event())
    steps = result["rawResult"]["steps"]
    page = steps[1].get("result") or {}
    assert page.get("sourceTool") == tool, steps[1].get("outcome")
    assert page["resultRef"] == steps[0]["resultRead"]["resultRef"]
    assert scans == [arguments]
    assert result["ok"]
    assert "load_internal_tool_block" not in _native_function_names(model.requests[0])


def test_role_allowlist_filters_native_and_legacy_tool_catalogs_including_empty_list(tmp_path):
    reply = {"role": "assistant", "content": "I reviewed the supplied context."}
    gateway, model, invoked = _child_gateway(tmp_path, [reply])
    _register_second_read_tool(gateway, invoked)
    result = _run(gateway, tmp_path, allowed_tools=("vrcforge_read_text_file",))
    assert result["ok"] is True
    exposed = _native_available_tool_names(model.requests[0])
    assert "vrcforge_read_text_file" in exposed
    assert "vrcforge_read_tool_result" in exposed
    assert "vrcforge_search_text" not in exposed

    with gateway.runtime_planner.bind_role_instructions(
        "Role instructions", allowed_tools=("vrcforge_read_text_file",),
    ):
        legacy_allowed_prompt = gateway.runtime_planner._build_llm_plan_prompt(
            "Read selected project files.", [], observe={"planMode": True},
            exposure_layer="planning", project_context_active=True,
            project_path=str(tmp_path), global_instructions="", project_instructions="",
        )
    assert "vrcforge_read_text_file" in legacy_allowed_prompt
    assert "vrcforge_search_text" not in legacy_allowed_prompt

    with gateway.runtime_planner.bind_role_instructions("Role instructions", allowed_tools=()):
        legacy_prompt = gateway.runtime_planner._build_llm_plan_prompt(
            "Read selected project files.", [], observe={"planMode": True},
            exposure_layer="planning", project_context_active=True,
            project_path=str(tmp_path), global_instructions="", project_instructions="",
        )
    assert "vrcforge_read_text_file" not in legacy_prompt
    assert "vrcforge_search_text" not in legacy_prompt

    empty_path = tmp_path / "empty-role"
    empty_path.mkdir()
    empty_gateway, empty_model, _ = _child_gateway(empty_path, [reply])
    empty_invoked = []
    _register_second_read_tool(empty_gateway, empty_invoked)
    empty_result = _run(empty_gateway, empty_path, allowed_tools=())
    assert empty_result["ok"] is True
    empty_exposed = _native_available_tool_names(empty_model.requests[0])
    assert "vrcforge_read_text_file" not in empty_exposed
    assert "vrcforge_read_tool_result" not in empty_exposed
    assert "vrcforge_search_text" not in empty_exposed


def test_native_child_cannot_invoke_a_read_tool_not_in_the_declared_role_allowlist(tmp_path):
    gateway, model, invoked = _child_gateway(tmp_path, [
        call("guessed-search", "vrcforge_search_text", {"query": "must not execute"}),
        {"role": "assistant", "content": "The selected role cannot run that tool."},
    ])
    _register_second_read_tool(gateway, invoked)
    result = _run(gateway, tmp_path, allowed_tools=("vrcforge_read_text_file",))
    assert "vrcforge_search_text" not in _native_available_tool_names(model.requests[0])
    assert not invoked
    assert "tool_not_visible" in json.dumps(result["rawResult"])


def test_child_cancel_event_stops_runtime_before_later_tool_dispatch(tmp_path):
    cancel = threading.Event()
    gateway, model, invoked = _child_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        lambda _request: (cancel.set() or call("read-b", "vrcforge_read_text_file", {"path": "b.txt"})),
        {"role": "assistant", "content": "Stopped."},
    ])
    result = _run(gateway, tmp_path, cancel_event=cancel)
    assert [item.get("path") for item in invoked] == ["a.txt"]
    assert result["rawResult"]["plan"]["nextStep"] == "cancelled"
    assert result["ok"] is False and result["status"] == "cancelled"


def test_registry_cancel_watcher_interrupts_provider_waiting_for_child_cancel(tmp_path):
    gateway, _model, invoked = _child_gateway(tmp_path, [])
    provider_entered = threading.Event()
    provider_cancel_seen = threading.Event()
    cancel = threading.Event()

    class WaitingProvider:
        def plan_native(self, request):
            provider_entered.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if gateway._runtime_session_state.cancel_requested(
                    session_id="child-session-1", client_turn_id="child-session-1:initial"):
                    provider_cancel_seen.set()
                    return PlannerModelResult("", assistant_message={
                        "role": "assistant", "content": "Stopped after cancellation."}, finish_reason="stop")
                time.sleep(.01)
            raise AssertionError("cancel watcher did not reach the blocked provider")

    gateway.runtime_planner._model = WaitingProvider()
    result_holder = []
    errors = []

    def run_child():
        try:
            result_holder.append(_run(gateway, tmp_path, cancel_event=cancel))
        except BaseException as exc:  # surfaced in the test thread after bounded join
            errors.append(exc)

    worker = threading.Thread(target=run_child, name="fixture-child-call")
    worker.start()
    try:
        assert provider_entered.wait(2), "fixture provider was not entered"
        cancel.set()
        worker.join(4)
        assert not worker.is_alive(), "child call did not stop after cancellation"
        assert provider_cancel_seen.is_set(), "watcher did not forward cancellation to the provider owner"
        assert not errors, errors
        assert result_holder and result_holder[0]["status"] == "cancelled"
        assert result_holder[0]["ok"] is False
        assert not invoked
        assert not any(thread.name == "sub-agent-cancel" for thread in threading.enumerate())
    finally:
        cancel.set()
        worker.join(4)
    assert not worker.is_alive(), "fixture child worker leaked"


def test_child_max_step_budget_is_enforced_by_runtime(tmp_path):
    gateway, model, invoked = _child_gateway(tmp_path, [
        call("read-a", "vrcforge_read_text_file", {"path": "a.txt"}),
        call("read-b", "vrcforge_read_text_file", {"path": "b.txt"}),
        call("read-c", "vrcforge_read_text_file", {"path": "c.txt"}),
    ])
    result = _run(gateway, tmp_path, max_steps=1)
    assert len(model.requests) <= 2
    assert len(invoked) <= 1
    assert result.get("stepCount", 0) <= 1
    assert result["ok"] is False
