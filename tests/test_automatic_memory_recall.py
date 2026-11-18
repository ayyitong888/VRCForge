import json

import pytest

from agent_gateway import AgentGateway
from agent_memory_tools import register_memory_tools
from automatic_memory_pipeline import AutomaticMemoryPipeline
from runtime_planner_service import PlannerModelResult
from tests.test_automatic_memory_pipeline import source, run
from tests.test_native_runtime_gateway import call, setup_gateway
from tests.test_runtime_planner_service import FakeModel, service
from memory_consolidation_sources import MemoryScope, admit_memory_sources
from memory_review_inputs import collect_user_chat_records


def test_fresh_session_recalls_derived_summary_without_old_history_or_other_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    enabled = {"memoryEnabled": True, "crossSessionEnabled": True}
    gateway.set_memory_preferences_provider(lambda: enabled)
    pipeline = AutomaticMemoryPipeline(gateway.audit_dir / "memory-review" / "automatic-memory.json")
    evidence = source(project)
    def invoke(payload):
        if payload["phase"] == "extract":
            return {"summary": "Source detail", "usage": {}}
        return {"summary": "Keep the fitted wardrobe; validation remains unfinished.",
                "sourceIds": [evidence.source_id], "usage": {}}
    run(pipeline, [evidence], invoke)
    observed = gateway.runtime_observe("fresh-independent-session", str(project))
    assert observed["session"]["turnCount"] == 0
    assert observed["session"]["restoredFromTranscript"] is False
    assert observed["automaticMemory"]["sources"] == [{"chatId": evidence.source_id}]
    prompt = service()._message_with_runtime_context("Continue", observed)
    assert "validation remains unfinished" in prompt
    assert "Source chatId: " + evidence.source_id in prompt
    assert "Source detail" not in prompt
    assert gateway.runtime_observe("other", str(tmp_path / "other"))["automaticMemory"]["summary"] == ""
    enabled["crossSessionEnabled"] = False
    assert gateway.runtime_observe("disabled", str(project))["automaticMemory"]["summary"] == ""


def test_user_summary_recalled_with_real_chat_source_and_project_scope_isolated(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    gateway.set_memory_preferences_provider(lambda: {"memoryEnabled": True, "crossSessionEnabled": True})
    pipeline = AutomaticMemoryPipeline(gateway.audit_dir / "memory-review" / "automatic-memory.json")
    project = tmp_path / "project"
    project.mkdir()
    records = collect_user_chat_records([
        {"id": "general-chat-real-id", "items": [
            {"id": "u1", "type": "user", "text": "Please remember that I prefer concise answers."},
            {"id": "a1", "type": "agent", "text": "Understood."},
        ]},
        {"id": "unrelated-project-chat", "projectPath": str(project), "items": [
            {"id": "u2", "type": "user", "text": "Please remember that this project is private."},
            {"id": "a2", "type": "agent", "text": "Understood."},
        ]},
    ], scope="user")
    user_scope = MemoryScope("user", "user")
    sources, _ = admit_memory_sources(records, scope=user_scope)
    assert len(sources) == 1
    plan = pipeline.prepare(user_scope, sources, {"user_chat"})
    staged = pipeline.generate(plan, lambda payload: {
        "summary": "Prefer concise answers.",
        **({"sourceIds": [sources[0].source_id]} if payload["phase"] == "consolidate" else {}),
    }, lambda: True)
    pipeline.commit(staged, sources, {"user_chat"})
    evidence = source(project, "Project wardrobe remains unfinished")
    run(pipeline, [evidence], lambda payload: {
        "summary": "Project wardrobe remains unfinished.",
        **({"sourceIds": [evidence.source_id]} if payload["phase"] == "consolidate" else {}),
    })
    general = gateway.runtime_observe("new-general-session", "")
    assert "Prefer concise answers" in general["automaticMemory"]["summary"]
    assert "Project wardrobe" not in general["automaticMemory"]["summary"]
    assert general["automaticMemory"]["sources"] == [{"chatId": "general-chat-real-id"}]
    assert general["session"]["turnCount"] == 0
    prompt = service()._message_with_runtime_context("Help", general)
    assert "Source chatId: general-chat-real-id" in prompt
    assert sources[0].source_id not in prompt
    scoped = gateway.runtime_observe("new-project-session", str(project))["automaticMemory"]
    assert "Prefer concise answers" in scoped["summary"]
    assert "Project wardrobe" in scoped["summary"]
    assert scoped["sources"] == [{"chatId": evidence.source_id}]
    other = gateway.runtime_observe("other-project-session", str(tmp_path / "other"))["automaticMemory"]
    assert "Project wardrobe" not in other["summary"]
    assert other["sources"] == []


@pytest.mark.parametrize("scope", ["general", "project"])
def test_native_reply_attributes_only_current_cited_memories(tmp_path, scope):
    project_root = str(tmp_path) if scope == "project" else ""
    selected = {}

    def answer(request):
        wire = json.dumps(request["messages"])
        assert all(row["memoryId"] in wire for row in selected.values())
        ids = [selected["automatic"]["memoryId"], selected["explicit"]["memoryId"]]
        return call("memory-final", "vrcforge_runtime_action", {
            "action": "reply", "reply": "A concise answer.", "memory_ids": [*ids, "unavailable", ids[0]],
        })

    gateway, model, _ = setup_gateway(tmp_path, [
        {"role": "assistant", "content": "No prior context needed."},
        call("invalid-final", "vrcforge_runtime_action", {
            "action": "reply", "reply": "A generic answer.", "memory_ids": ["unavailable"],
        }),
        answer,
        lambda _request: call("disabled-final", "vrcforge_runtime_action", {
            "action": "reply", "reply": "No citation.",
            "memory_ids": [row["memoryId"] for row in selected.values()],
        }),
    ])
    preferences = {"memoryEnabled": True, "crossSessionEnabled": True}
    gateway.set_memory_preferences_provider(lambda: preferences)
    pipeline = AutomaticMemoryPipeline(gateway.audit_dir / "memory-review" / "automatic-memory.json")
    evidence = source(tmp_path)
    user_scope = MemoryScope("user", "user")
    sources, _ = admit_memory_sources(collect_user_chat_records([{
        "id": "general-chat", "items": [
            {"id": "u1", "type": "user", "text": "Please remember that I prefer concise answers."},
            {"id": "a1", "type": "agent", "text": "Understood."},
        ],
    }], scope="user"), scope=user_scope)
    assert len(sources) == 1
    plan = pipeline.prepare(user_scope, sources, {"user_chat"})
    staged = pipeline.generate(plan, lambda payload: {
        "summary": "Use fewer words.",
        **({"sourceIds": [sources[0].source_id]} if payload["phase"] == "consolidate" else {}),
    }, lambda: True)
    pipeline.commit(staged, sources, {"user_chat"})
    run(pipeline, [evidence], lambda payload: {
        "summary": "Verify the wardrobe.",
        **({"sourceIds": [evidence.source_id]} if payload["phase"] == "consolidate" else {}),
    })
    selected["explicit"] = gateway.create_agent_memory({
        "text": "Prefer bullet lists.", "scope": "user",
    })["memory"]
    gateway.create_agent_memory({"text": "Unused preference.", "scope": "user"})
    raw = gateway.runtime_observe("preflight", project_root)["automaticMemory"]["items"]
    selected["automatic"] = raw[-1]
    assert selected["automatic"]["scope"] == ("project" if project_root else "user")

    def turn(number):
        return gateway.runtime_message({"message": "How should I present it?", "projectRoot": project_root,
                                        "sessionId": "memory-session", "clientTurnId": f"memory-{number}"})

    assert "memoryUsage" not in turn(1)
    assert "memoryUsage" not in turn(2)
    result = turn(3)
    expected = [{"id": row["memoryId"], "text": row["text"]} for row in (
        selected["automatic"], selected["explicit"],
    )]
    assert result["memoryUsage"] == expected
    assert result["plan"]["reply"] == "A concise answer."
    assert "memoryUsage" not in result["plan"]
    assert gateway.get_runtime_session("memory-session", client_turn_id="memory-3")["response"]["memoryUsage"] == expected
    preferences["crossSessionEnabled"] = False
    assert "memoryUsage" not in turn(4)
    assert len(model.requests) == 4


def test_native_reply_cites_an_older_memory_from_a_successful_list_receipt(tmp_path, monkeypatch):
    import dashboard_server

    older = {}

    def answer(request):
        receipt = next(item["content"] for item in request["messages"] if item["role"] == "tool")
        assert older["memoryId"] in receipt
        assert older["text"] in receipt
        return call("list-final", "vrcforge_runtime_action", {
            "action": "reply", "reply": "Use the saved color.", "memory_ids": [older["memoryId"]],
        })

    gateway, model, _ = setup_gateway(tmp_path, [
        call("list-memory", "list_memory", {"query": "cerulean"}), answer,
    ])
    register_memory_tools(gateway, lambda _ids: None)
    monkeypatch.setattr(dashboard_server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(gateway.runtime_planner, "_catalog", dashboard_server._RuntimePlannerCatalog())
    older.update(gateway.create_agent_memory({"text": "My palette is cerulean.", "scope": "user"})["memory"])
    for index in range(12):
        gateway.create_agent_memory({"text": f"Newer preference {index}.", "scope": "user"})
    response = gateway.runtime_message({"message": "Recall my palette.", "sessionId": "listed-memory-session"})
    assert len(model.requests) == 2
    assert older["memoryId"] not in json.dumps(model.requests[0]["messages"])
    assert response.get("memoryUsage") == [{"id": older["memoryId"], "text": older["text"]}], response["plan"]


def test_json_reply_cites_only_present_memory_ids():
    model = FakeModel(PlannerModelResult(json.dumps({
        "action": "reply", "reply": "Use a short answer.", "memory_ids": ["mem_real", "mem_guessed"],
    })))
    observe = {"memory": {"items": [{"memoryId": "mem_real", "scope": "user", "text": "Keep it short."}]}}
    result = service(model=model).plan_agent_turn("Answer my question.", {}, observe)
    assert "mem_real" in model.prompts[0] and "Keep it short." in model.prompts[0]
    assert result["reply"] == "Use a short answer."
    assert result["memoryUsage"] == [{"id": "mem_real", "text": "Keep it short."}]
