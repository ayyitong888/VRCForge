from pathlib import Path

import pytest

from agent_memory_store import AgentMemoryStore
from memory_consolidation import MemoryConsolidationError, MemoryConsolidationService, RevisionConflictError
from memory_consolidation_sources import ScopeResolutionError


def _prepared(service):
    rows = [service.accepted_store.create({"scope": "user", "text": f"Saved preference {i}."}) for i in range(5)]
    prepared = service.prepare_dreaming()
    reviewed = {"reviewed": True, "duplicateGroups": [{"keepId": rows[0]["memoryId"], "removeIds": [rows[1]["memoryId"]]}]}
    return rows, prepared, reviewed


def test_manual_secret_rejected_and_legacy_record_excluded_without_erasure(tmp_path: Path):
    store = AgentMemoryStore(tmp_path / "memory.jsonl", tmp_path / "audit.jsonl")
    secret = "API key=" + "sk-" + "fixtureOnly0123456789" * 3
    with pytest.raises(ValueError, match="sensitive"):
        store.create({"scope": "user", "text": secret})
    row = store.create({"scope": "user", "text": "Safe preference."})
    raw = store.log_path.read_text(encoding="utf-8").replace("Safe preference.", secret)
    store.log_path.write_text(raw, encoding="utf-8")
    assert store.list() == []
    assert store.list_active() == []
    assert store.get(row["memoryId"])["text"] == secret
    assert secret in store.log_path.read_text(encoding="utf-8")


def test_dreaming_requires_durable_pending_proposal_and_user_acceptance(tmp_path: Path):
    service = MemoryConsolidationService(tmp_path)
    rows, prepared, reviewed = _prepared(service)
    result = service.commit_dreaming(prepared, reviewed)
    assert result["reason"] == "awaiting_user_approval"
    assert len(service.accepted_store.list_active()) == 5
    restarted = MemoryConsolidationService(tmp_path)
    proposal = restarted.dreaming_proposal()
    assert proposal["proposalId"] == prepared["runId"]
    with pytest.raises(RevisionConflictError):
        restarted.decide_dreaming(proposal["proposalId"], "accept", expected_revision=result["revision"] - 1)
    accepted = restarted.decide_dreaming(proposal["proposalId"], "accept", expected_revision=result["revision"])
    assert accepted["deduplicatedCount"] == 1
    assert restarted.accepted_store.get(rows[1]["memoryId"]) is None
    assert restarted.dreaming_proposal() is None


def test_reject_or_stale_approval_cannot_change_memory(tmp_path: Path):
    service = MemoryConsolidationService(tmp_path)
    _, prepared, reviewed = _prepared(service)
    result = service.commit_dreaming(prepared, reviewed)
    service.accepted_store.create({"scope": "user", "text": "New fact."})
    with pytest.raises(MemoryConsolidationError, match="changed"):
        service.decide_dreaming(prepared["runId"], "accept", expected_revision=result["revision"])
    service.decide_dreaming(prepared["runId"], "reject", expected_revision=result["revision"])
    assert len(service.accepted_store.list_active()) == 6


def test_disabled_memory_rejects_dreaming_result(tmp_path: Path):
    service = MemoryConsolidationService(tmp_path)
    _, prepared, reviewed = _prepared(service)
    revision = service.review_store.snapshot(include_internal=True)["revision"]
    service.update_config({"memoryEnabled": False}, expected_revision=revision)
    with pytest.raises(MemoryConsolidationError, match="disabled"):
        service.commit_dreaming(prepared, reviewed)
    assert len(service.accepted_store.list_active()) == 5


def test_project_proposal_preview_and_approval_require_same_project(tmp_path: Path):
    service = MemoryConsolidationService(tmp_path / "state")
    project = tmp_path / "project"
    project.mkdir()
    rows = [service.accepted_store.create({"scope": "project", "projectRoot": str(project), "text": f"Preference {i}."}) for i in range(5)]
    prepared = service.prepare_dreaming()
    result = service.commit_dreaming(prepared, {"reviewed": True, "duplicateGroups": [{"keepId": rows[0]["memoryId"], "removeIds": [rows[1]["memoryId"]]}]})
    assert service.dreaming_proposal() is None
    assert service.dreaming_proposal(str(tmp_path / "other")) is None
    assert service.dreaming_proposal(str(project))["groups"][0]["keepText"] == "Preference 0."
    with pytest.raises(ScopeResolutionError):
        service.decide_dreaming(result["proposalId"], "accept", expected_revision=result["revision"])
    assert len(service.accepted_store.list_active()) == 5
    service.decide_dreaming(result["proposalId"], "accept", expected_revision=result["revision"], project_root=str(project))
    assert len(service.accepted_store.list_active()) == 4


def test_approval_rechecks_enabled_and_failure_restores_memories(tmp_path: Path, monkeypatch):
    service = MemoryConsolidationService(tmp_path)
    _, prepared, reviewed = _prepared(service)
    result = service.commit_dreaming(prepared, reviewed)
    service.update_config({"memoryEnabled": False}, expected_revision=result["revision"])
    revision = service.review_store.snapshot(include_internal=True)["revision"]
    with pytest.raises(MemoryConsolidationError, match="disabled"):
        service.decide_dreaming(result["proposalId"], "accept", expected_revision=revision)
    service.update_config({"memoryEnabled": True}, expected_revision=revision)
    revision = service.review_store.snapshot(include_internal=True)["revision"]
    def fail(**_kwargs):
        raise OSError("fixture persistence failure")
    monkeypatch.setattr(service.review_store, "record_dreaming_result", fail)
    with pytest.raises(OSError, match="fixture"):
        service.decide_dreaming(result["proposalId"], "accept", expected_revision=revision)
    assert len(service.accepted_store.list_active()) == 5
    assert service.dreaming_proposal() is not None


def test_promotion_rejects_secret_and_legacy_memory_never_reaches_dreaming(tmp_path: Path):
    service = MemoryConsolidationService(tmp_path)
    rows, _, _ = _prepared(service)
    secret = "API key=" + "sk-" + "fixtureOnly0123456789" * 3
    with pytest.raises(ValueError, match="sensitive"):
        service.accepted_store.promote(promotion_id="fixture", candidate_id="fixture", scope="user", project_root="", kind="fact", text=secret)
    path = service.accepted_store.log_path
    path.write_text(path.read_text(encoding="utf-8").replace(rows[0]["text"], secret), encoding="utf-8")
    service.accepted_store.create({"scope": "user", "text": "Another safe memory."})
    prepared = service.prepare_dreaming()
    assert secret not in str(prepared["request"])
    assert service.accepted_store.get(rows[0]["memoryId"])["text"] == secret
