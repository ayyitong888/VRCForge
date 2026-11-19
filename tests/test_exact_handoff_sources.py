import json

from fastapi.testclient import TestClient

import dashboard_server as dashboard
from agent_gateway import AgentGateway
from context_compaction import compact_context
from runtime_planner_service import PlannerTurnMetadata, RuntimePlannerService
from tests.test_native_context_guard import _Catalog, _Desktop, _NativeModel, _Turn


def test_manual_native_endpoint_keeps_exact_instruction_and_todo_sources(tmp_path, monkeypatch):
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
                return json.dumps({"intent": "Continue the selected check", "judgment": ["Older source inspected"],
                                   "nextStep": "Wait for the saved approval"})
            metadata["_recordUsage"]("fixture", {"exact": True, "inputTokens": 12, "outputTokens": 8})
            return compact_context(history, summarizer=summarize, handoff=True, has_todo=metadata["hasTodo"],
                                   input_mode="native", target_tokens=metadata["targetTokens"])

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
        state.begin_native_turn("owner", binding=binding, turn_id=f"turn-{index}", message=message)
        if index < 4:
            state.append_native_assistant("owner", binding=binding, message={"role": "assistant", "content": (
                "Long historical detail " * 850 if index == 1 else f"settled-{index}"
            )})
    call = {"id": "pending-call", "type": "function", "function": {"name": "call_tool", "arguments": '{"tool":"write","arguments":{}}'}}
    state.append_native_assistant("owner", binding=binding, message={"role": "assistant", "tool_calls": [call]})
    state.mark_native_dispatch("owner", binding=binding, call_id="pending-call")

    reply = TestClient(dashboard.app).post("/api/app/agent/compact", json={
        "sessionId": "owner", "projectRoot": str(project), "provider": "openai", "model": "requested",
        "trigger": "manual", "phase": "standalone", "requireNative": True,
    })
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
    assert any(message.get("content") == "  Exact first request\r\n" for message in saved["messages"])

    state.begin_native_turn("short", binding=binding, turn_id="first", message="Only the first request")
    response = TestClient(dashboard.app).post("/api/app/agent/compact", json={
        "sessionId": "short", "projectRoot": str(project), "provider": "openai", "model": "requested",
        "trigger": "manual", "phase": "standalone", "history": [],
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "no_content"
    assert len(prompts) == 1
