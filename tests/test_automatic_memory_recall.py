from agent_gateway import AgentGateway
from automatic_memory_pipeline import AutomaticMemoryPipeline
from tests.test_automatic_memory_pipeline import source, run
from tests.test_runtime_planner_service import service
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
