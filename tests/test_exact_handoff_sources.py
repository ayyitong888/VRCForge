import json
import re

import pytest
from fastapi.testclient import TestClient

import dashboard_server as dashboard
from agent_gateway import AgentGateway
from agent_handoff_graph import parse_handoff_graph
from agent_runtime_input import RuntimeInputSource
from context_compaction import MODEL_HANDOFF_PREFIX, compact_context
from runtime_planner_service import PlannerTurnMetadata, RuntimePlannerService
from tests.test_native_context_guard import _Catalog, _Desktop, _NativeModel, _Turn


@pytest.mark.parametrize("first_source", [RuntimeInputSource.USER, RuntimeInputSource.PARENT_AGENT])
def test_manual_native_endpoint_keeps_exact_instruction_and_todo_sources(tmp_path, monkeypatch, first_source):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    project = tmp_path / "project"
    project.mkdir()
    global_text = "\ufeff\r\n  Global scope 中文  \r\n"
    project_text = "\ufeff\r\n\tProject scope 🐾\r\n\r\n"
    gateway.user_constraints_path.write_bytes(global_text.encode())
    (project / "AGENTS.md").write_bytes(project_text.encode())
    prompts = []
    requests = []

    class Compactor:
        def compact(self, history, metadata):
            def summarize(prompt):
                prompts.append(prompt)
                if metadata["_priorHandoff"] is None:
                    digest = re.search(r"recovery sourceDigest ([0-9a-f]{64})", prompt)[1]
                    rows = json.loads(prompt.split("\nREDACTED_ENTRIES=", 1)[1])
                    record = next(row for row in rows if "Long historical detail" in row["text"])
                    return {"goal": "Continue the selected check", "operations": [
                        {"op": "add", "section": "completed", "id": "old-inspection", "fields": {
                            "action": "Read the historical material", "result": "Observation recorded",
                            "evidence": f"{digest}:{record['recordIndex']}",
                        }},
                        {"op": "add", "section": "nextSteps", "id": "inspect", "fields": {
                            "action": "Inspect the saved observation", "phase": 0, "dependsOn": [],
                        }},
                    ]}
                return {"goal": None, "operations": [
                    {"op": "update", "section": "nextSteps", "id": "inspect", "fields": {
                        "action": "Inspect the newer observation",
                    }},
                ]}
            metadata["_recordUsage"]("fixture", {"exact": True, "inputTokens": 12, "outputTokens": 8})
            return compact_context(history, summarizer=summarize, handoff=True, has_todo=metadata["hasTodo"],
                                   input_mode="native", target_tokens=metadata["targetTokens"],
                                   prior_handoff=metadata["_priorHandoff"], recovery_history=metadata["_recoveryHistory"],
                                   handoff_archive_indices=metadata["_handoffArchiveIndices"])

    class Planner(RuntimePlannerService):
        def maybe_compact_native_context(self, request, *args, **kwargs):
            requests.append(request)
            return super().maybe_compact_native_context(request, *args, **kwargs)

    planner = Planner(catalog=_Catalog(), desktop=_Desktop(), model=_NativeModel(), compactor=Compactor(),
                      turn=_Turn(PlannerTurnMetadata(verified_context_limit=12000, native_binding="fixture",
                                                     provider="openai", model="requested")),
                      global_instructions=lambda: gateway.read_user_constraints().content)
    gateway.bind_runtime_planner(planner)
    monkeypatch.setattr(dashboard, "RUNTIME_PLANNER", planner)
    monkeypatch.setattr(dashboard, "AGENT_GATEWAY", gateway)
    todo = [{"title": "  TODO one 中文\r\n  ", "summary": "Exact source", "status": "in_progress", "order": 1},
            {"title": "TODO two 🐾", "summary": "second", "status": "pending", "order": 2}]
    monkeypatch.setattr(gateway, "list_agent_progress", lambda **scope: {"items": todo} if scope["session_id"] == "owner" else {"items": []})
    state = gateway.runtime_sessions
    with planner.bind_turn({}):
        binding = planner.native_conversation_binding(str(project))
    for index, message in enumerate(("  Exact first request\r\n", "Older material", "Earlier reply", "Recent pair", "Current approval request")):
        state.begin_native_turn("owner", binding=binding, turn_id=f"turn-{index}", message=message,
                                input_source=first_source if index == 0 else RuntimeInputSource.USER)
        if index < 4:
            state.append_native_assistant("owner", binding=binding, message={"role": "assistant", "content": (
                "Long historical detail " * 850 if index == 1 else f"settled-{index}"
            )})
    call = {"id": "pending-call", "type": "function", "function": {"name": "call_tool", "arguments": '{"tool":"write","arguments":{}}'}}
    state.append_native_assistant("owner", binding=binding, message={"role": "assistant", "tool_calls": [call]})
    state.mark_native_dispatch("owner", binding=binding, call_id="pending-call")

    client = TestClient(dashboard.app)
    compact_body = {
        "sessionId": "owner", "projectRoot": str(project), "provider": "openai", "model": "requested",
        "trigger": "manual", "phase": "standalone", "requireNative": True,
    }
    reply = client.post("/api/app/agent/compact", json=compact_body)
    assert reply.status_code == 200, reply.text
    assert reply.json().get("nativeApplied") is True, reply.text[:800]
    assert reply.json()["providerUsage"]["model"] == "requested"
    assert prompts and all("Global scope" not in prompt and "Project scope" not in prompt and "TODO one" not in prompt for prompt in prompts)
    request = requests[0]
    for tag, exact in (("global_user_instructions", global_text), ("project_instructions", project_text)):
        original = request["instructions"].split(f"<{tag}>\n", 1)[1].rsplit(f"\n</{tag}>", 1)[0]
        assert original.encode() == exact.encode()
    saved = state.native_conversation("owner", binding=binding)
    contexts = [json.loads(message["content"].split(": ", 1)[1]) for message in saved["messages"]
                if isinstance(message.get("content"), str) and '"activeTodo"' in message["content"]]
    assert contexts[-1]["activeTodo"] == todo
    assert state._native_pending(saved) == {"pending-call"}
    assert any(message.get("content") == first_source.model_text("  Exact first request\r\n") for message in saved["messages"])
    original_graph = parse_handoff_graph(saved["messages"][0]["content"].removeprefix(MODEL_HANDOFF_PREFIX))
    assert original_graph["nextSteps"][0]["action"] == "Inspect the saved observation"

    state.settle_native_call("owner", binding=binding, call_id="pending-call", content='{"status":"approved"}')
    for index in range(3):
        state.begin_native_turn("owner", binding=binding, turn_id=f"next-{index}", message=f"New task {index}")
        state.append_native_assistant("owner", binding=binding, message={"role": "assistant", "content": f"New observation {index}"})
    second = client.post("/api/app/agent/compact", json=compact_body)
    assert second.status_code == 200, second.text
    assert second.json().get("nativeApplied") is True, second.text[:800]
    saved_again = state.native_conversation("owner", binding=binding)
    graph = parse_handoff_graph(saved_again["messages"][0]["content"].removeprefix(MODEL_HANDOFF_PREFIX))
    assert graph["goal"] == original_graph["goal"]
    assert graph["nextSteps"] == [{**original_graph["nextSteps"][0], "action": "Inspect the newer observation"}]
    assert json.loads(prompts[1].split("\nPRIOR_HANDOFF=", 1)[1].split("\nRETAINED_STATUS=", 1)[0]) == original_graph
    assert "Inspect the saved observation" not in prompts[1].split("\nREDACTED_ENTRIES=", 1)[1]
    assert saved_again["compactionRecovery"][0] == saved["compactionRecovery"][0]
    assert graph["completed"] == original_graph["completed"]
    digest, record_index = graph["completed"][0]["evidence"].split(":")
    archive = next(archive for archive in saved_again["compactionRecovery"] if archive["sourceDigest"] == digest)
    assert archive["sourceEntries"][int(record_index)]["text"] == ("Long historical detail " * 850).strip()

    state.begin_native_turn("short", binding=binding, turn_id="first", message="Only the first request")
    response = client.post("/api/app/agent/compact", json={
        "sessionId": "short", "projectRoot": str(project), "provider": "openai", "model": "requested",
        "trigger": "manual", "phase": "standalone", "history": [],
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "no_content"
    assert len(prompts) == 2
