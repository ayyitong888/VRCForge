"""Real file-read exceptions must remain useful in the next internal model turn."""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from agent_runtime_native_turn import NativeRuntimeTurn
from general_agent_tools import read_text_file
from tests.test_agent_runtime_skill_executor import FakeTool, make_executor
from tests.test_native_runtime_gateway import setup_gateway


@pytest.mark.parametrize("projected", [False, True])
@pytest.mark.parametrize("failure,category,reason,correction", [
    ("missing", "FileNotFoundError", "Path does not exist", "Use find_files or list_directory"),
    ("directory", "IsADirectoryError", "path is a directory", "Use list_directory"),
    ("outside", "PermissionError", "outside every authorized root", "ask the user to authorize"),
])
def test_file_failure_reaches_next_internal_request(tmp_path, projected, failure, category, reason, correction):
    root = tmp_path / "authorized"
    root.mkdir()
    path = {"missing": root / "missing.cs", "directory": root,
            "outside": tmp_path / "outside.cs"}[failure]
    tool = FakeTool(name="vrcforge_read_text_file",
                    handler=lambda _params: read_text_file(path, allowed_roots=[root]))
    snapshot = SimpleNamespace(
        skill={"name": "file-skill", "category": "read/debug", "entrypointTool": tool.name,
               "allowedTools": [tool.name]},
        package_audit_context={}, validation={"reasons": []}, loaded=True, support_files=[])
    executor, _ = make_executor(tools={tool.name: tool}, snapshot=snapshot)
    receipt = executor.execute("file-skill" if projected else tool.name, {}, "internal")
    assert receipt["outcome"]["cause"]["category"] == category

    gateway, model, _ = setup_gateway(tmp_path, [{"role": "assistant", "content": "Recorded."}])
    planner = gateway.runtime_planner
    loop = []
    owner = NativeRuntimeTurn(gateway.runtime_sessions, planner, "file-failure", "turn", "offline",
                              "Read text", loop, None, None, None)
    owner.admit({"role": "assistant", "content": None, "tool_calls": [{
        "id": "file-call", "type": "function", "function": {
            "name": "call_tool", "arguments": json.dumps({"tool": "read_text_file", "arguments": {}})}}]})
    owner.next_receipt()
    loop.append({"kind": "skill", "tool": tool.name, "status": "failed", "actionId": "file-read",
                 "result": receipt, "outcome": receipt["outcome"]})
    owner.settle()
    planner._llm_plan_agent_turn("Read text", observe={}, history=[], loop_state=loop,
        exposure_layer="planning", project_context_active=False, internal_tool_blocks={"core"},
        native_turn=owner, propagate_provider_errors=True)
    tool_messages = [message["content"] for message in model.requests[-1]["messages"]
                     if message.get("role") == "tool"]
    visible = " ".join(tool_messages)
    assert category in visible and reason in visible and correction in visible
    assert "<path redacted>" in visible
    assert str(root) not in visible and str(root).replace("\\", "\\\\") not in visible
