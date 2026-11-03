import json
from dataclasses import replace
import pytest

from automatic_memory_pipeline import AutomaticMemoryPipeline
from memory_consolidation import MemoryConsolidationError, RevisionConflictError
from memory_consolidation_sources import MemoryScope, admit_memory_source, project_scope_key
from memory_review_inputs import collect_project_chat_records


def source(project, text="Keep current progress", revision="r1"):
    scope = MemoryScope("project", project_scope_key(str(project)), str(project))
    value = admit_memory_source({"sourceType": "project_chat", "sourceId": "chat-one",
        "sourceRevision": revision, "projectRoot": str(project), "status": "completed",
        "text": json.dumps([{"role": "user", "text": text}]), "observedAt": "2026-11-03"}, scope=scope)
    assert value is not None
    return value


def run(pipeline, sources, callback, *, complete=True):
    scope = sources[0].scope
    types = {"project_chat"} if complete else set()
    plan = pipeline.prepare(scope, sources, types)
    staged = pipeline.generate(plan, callback, lambda: True)
    return pipeline.commit(staged, sources, types)


def test_two_phases_update_same_source_and_recall_without_repeating(tmp_path):
    item = source(tmp_path)
    pipeline = AutomaticMemoryPipeline(tmp_path / "automatic.json")
    calls = []

    def invoke(payload):
        calls.append(payload["phase"])
        if payload["phase"] == "extract":
            return {"summary": payload["input"]["evidence"], "usage": {"input_tokens": 10}}
        return {"summary": payload["input"]["sources"][0]["summary"], "sourceIds": ["chat-one"], "usage": {"output_tokens": 5}}

    run(pipeline, [item], invoke)
    assert calls == ["extract", "consolidate"]
    run(pipeline, [item], invoke)
    assert len(calls) == 2
    corrected = source(tmp_path, "Outfit A changes chest only while worn; other outfits restore original", "r2")
    run(pipeline, [corrected], invoke)
    fresh = AutomaticMemoryPipeline(pipeline.path).read(item.scope)
    assert "Outfit A" in fresh["summary"]
    assert "Keep current progress" not in fresh["summary"]
    assert len(fresh["sources"]) == 1
    assert fresh["sources"][0]["sourceRevision"] == "r2"
    unrelated = MemoryScope("project", "project:another", str(tmp_path / "other"))
    assert not pipeline.read(unrelated)["summary"]


def test_phase_two_failure_and_inflight_change_preserve_last_valid_snapshot(tmp_path):
    item = source(tmp_path)
    pipeline = AutomaticMemoryPipeline(tmp_path / "automatic.json")
    provider = lambda payload: {"summary": "saved", **({"sourceIds": ["chat-one"]} if payload["phase"] == "consolidate" else {})}
    run(pipeline, [item], provider)
    before = pipeline.path.read_bytes()
    changed = source(tmp_path, "new correction", "r2")
    plan = pipeline.prepare(item.scope, [changed], {"project_chat"})

    def fail(payload):
        if payload["phase"] == "consolidate":
            raise RuntimeError("provider unavailable")
        return {"summary": "new"}

    with pytest.raises(RuntimeError):
        pipeline.generate(plan, fail, lambda: True)
    assert pipeline.path.read_bytes() == before
    staged = pipeline.generate(plan, provider, lambda: True)
    with pytest.raises(RevisionConflictError):
        pipeline.commit(staged, [source(tmp_path, "even newer", "r3")], {"project_chat"})
    assert pipeline.path.read_bytes() == before


def test_deleted_source_and_empty_extraction_remove_claims_only_with_complete_inventory(tmp_path):
    item = source(tmp_path)
    pipeline = AutomaticMemoryPipeline(tmp_path / "automatic.json")
    provider = lambda payload: {"summary": "saved", **({"sourceIds": ["chat-one"]} if payload["phase"] == "consolidate" else {})}
    run(pipeline, [item], provider)
    partial = pipeline.prepare(item.scope, [], set())
    assert partial["changed"] is False
    deletion = pipeline.prepare(item.scope, [], {"project_chat"})
    assert deletion["providerCallCount"] == 0
    staged = pipeline.generate(deletion, lambda _: pytest.fail("No provider for empty scope"), lambda: True)
    pipeline.commit(staged, [], {"project_chat"})
    assert not pipeline.read(item.scope)["summary"]
    assert pipeline.read(item.scope)["sources"] == []
    empty = pipeline.prepare(item.scope, [item], {"project_chat"})
    staged = pipeline.generate(empty, lambda _: {"summary": ""}, lambda: True)
    pipeline.commit(staged, [item], {"project_chat"})
    assert not pipeline.prepare(item.scope, [item], {"project_chat"})["changed"]


def test_unknown_source_and_revocation_cannot_publish(tmp_path):
    item = source(tmp_path)
    pipeline = AutomaticMemoryPipeline(tmp_path / "automatic.json")
    plan = pipeline.prepare(item.scope, [item], {"project_chat"})
    with pytest.raises(MemoryConsolidationError):
        pipeline.generate(plan, lambda _: pytest.fail("revoked"), lambda: False)
    with pytest.raises(MemoryConsolidationError):
        pipeline.generate(plan, lambda payload: {"summary": "x", "sourceIds": ["invented"]}, lambda: True)
    assert not pipeline.path.exists()


def test_project_chat_source_preserves_original_corrections_and_failed_outcome(tmp_path):
    chats = [{"id": "actual-shape", "projectPath": str(tmp_path), "items": [
        {"type": "user", "text": "Rebuild the wardrobe"},
        {"type": "agent", "response": {"plan": {"nextStep": "tool_failed", "summary": "Visual check failed"}}},
        {"type": "user", "text": "Preserve current progress; do not reinstall"},
    ]}]
    records = collect_project_chat_records(chats, project_root=str(tmp_path))
    assert len(records) == 1
    assert "Visual check failed" in records[0]["text"]
    assert "Preserve current progress" in records[0]["text"]


def test_multi_chat_budget_excerpts_keep_full_source_summaries_readable(tmp_path):
    first = source(tmp_path)
    sources = [replace(first, source_id=f"chat-{index}") for index in range(4)]
    pipeline = AutomaticMemoryPipeline(tmp_path / "automatic.json")
    requests = []
    def provider(payload):
        requests.append(payload)
        if payload["phase"] == "extract":
            return {"summary": "A" * 5000}
        return {"summary": "Four task histories; read sources for details.",
                "sourceIds": [row["sourceId"] for row in payload["input"]["sources"]]}
    run(pipeline, sources, provider)
    assert all(len(json.dumps(payload, ensure_ascii=False)) <= 12000 for payload in requests)
    assert any(row.get("excerpted") for row in requests[-1]["input"]["sources"])
    assert all(len(row["summary"]) == 5000 for row in pipeline.read(first.scope)["sources"])


def test_structured_conversation_redaction_preserves_json_roles(tmp_path):
    item = source(tmp_path, r'Files ["Editor\\C:\Users\Someone\Private\asset.prefab"]')
    rows = json.loads(item.text)
    assert rows[0]["role"] == "user"
    assert isinstance(rows[0]["text"], str)
