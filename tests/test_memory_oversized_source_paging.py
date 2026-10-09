from __future__ import annotations

import json
from pathlib import Path

import pytest

import memory_consolidation as memory_consolidation_module
from memory_consolidation import MemoryConsolidationError, MemoryConsolidator, MemoryReviewStore, build_provider_request
from memory_consolidation_sources import admit_memory_source, resolve_memory_scope


def _source(project: Path, text: str, source_id: str = "oversized-source"):
    scope = resolve_memory_scope("project", str(project), authorized_project_roots=[str(project)])
    return admit_memory_source({
        "sourceType": "user_chat", "sourceId": source_id, "sourceRevision": "1",
        "role": "user", "status": "completed", "signalKind": "preference",
        "text": text, "projectRoot": str(project),
    }, scope=scope)


def test_oversized_source_is_sent_as_bound_page_and_keeps_logical_binding(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    source = _source(project, "HEAD-" + ("x" * 900) + "-TAIL-SENTINEL")
    payload, selected = build_provider_request([source], source.scope, 1_000)
    assert [item.source_id for item in selected] == ["oversized-source"]
    provider_source = payload["sources"][0]
    assert provider_source["sourceId"] == source.source_id
    assert provider_source["sourceDigest"] == source.source_digest
    assert provider_source["text"].startswith("HEAD-")
    assert provider_source["pageHasMore"] is True
    assert "TAIL-SENTINEL" not in provider_source["text"]
    assert len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) <= 1_000


def test_oversized_source_pages_resume_after_durable_cursor(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    source = _source(project, "HEAD-" + ("y" * 900) + "-TAIL-SENTINEL")
    store = MemoryReviewStore(tmp_path / "review.json", tmp_path / "audit.jsonl")
    consolidator = MemoryConsolidator(store)
    seen: list[dict[str, object]] = []

    def provider(payload: dict[str, object]) -> dict[str, object]:
        item = payload["sources"][0]
        assert isinstance(item, dict)
        seen.append(item)
        return {"candidates": []}

    result = consolidator.run(mode="suggest_only", sources=[source], expected_revision=0,
                              provider=provider, input_char_cap=1_000)
    while "TAIL-SENTINEL" not in seen[-1]["text"]:
        assert len(seen) < 12
        result = consolidator.run(mode="suggest_only", sources=[source], expected_revision=result["revision"],
                                  provider=provider, input_char_cap=1_000)
    assert len(seen) > 1
    assert seen[0]["sourceId"] == seen[1]["sourceId"] == source.source_id
    assert seen[0]["sourceDigest"] == seen[1]["sourceDigest"] == source.source_digest
    assert seen[0]["pageOffset"] < seen[1]["pageOffset"]
    assert seen[0]["text"] != seen[1]["text"]
    assert result["selection"]["page"]["hasMore"] is False
    assert all(seen[index]["pageOffset"] < seen[index + 1]["pageOffset"] for index in range(len(seen) - 1))
    assert "TAIL-SENTINEL" in seen[-1]["text"]
    assert store.source_cursor_record(source.scope.scope_key)["pageOffset"] == 0


def test_oversized_source_does_not_replace_already_selected_small_sources(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    scope = resolve_memory_scope("project", str(project), authorized_project_roots=[str(project)])
    small = admit_memory_source({
        "sourceType": "user_chat", "sourceId": "small", "sourceRevision": "1",
        "role": "user", "status": "completed", "signalKind": "preference",
        "text": "small source", "projectRoot": str(project),
    }, scope=scope)
    oversized = _source(project, "HEAD-" + ("z" * 1500) + "-TAIL", "zzz-oversized")
    payload, selected = build_provider_request([small, oversized], scope, 1_000)
    assert [item.source_id for item in selected] == ["small"]
    assert [item["sourceId"] for item in payload["sources"]] == ["small"]


def test_validated_result_rejects_cursor_page_change(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    source = _source(project, "HEAD-" + ("q" * 1500) + "-TAIL")
    store = MemoryReviewStore(tmp_path / "review.json", tmp_path / "audit.jsonl")
    consolidator = MemoryConsolidator(store)
    validated = consolidator.validate_provider_result(
        {"candidates": []}, [source], source.scope,
    )
    store.record_source_cursor(
        scope_key=source.scope.scope_key,
        cursor="",
        skipped_oversized_count=0,
        expected_revision=0,
        page_source_id=source.source_id,
        page_source_digest=source.source_digest,
        page_offset=1,
    )
    try:
        consolidator._read_validated_provider_result(validated, [source], source.scope)
    except Exception as exc:
        assert "page changed" in str(exc)
    else:
        raise AssertionError("stale validated page was accepted")


def test_validated_result_binds_actual_payload_cap_and_page(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    source = _source(project, "HEAD-" + ("r" * 1500) + "-TAIL")
    store = MemoryReviewStore(tmp_path / "review.json", tmp_path / "audit.jsonl")
    consolidator = MemoryConsolidator(store)
    payload, selected, selection = consolidator.build_provider_request_with_selection(
        [source], source.scope, 1_000,
    )
    assert payload["sources"][0]["pageDigest"]
    validated = consolidator.validate_provider_result(
        {"candidates": []}, selected, source.scope,
        batch_digest=str(selection["batchDigest"]),
    )
    _other_payload, _other_selected, other_selection = consolidator.build_provider_request_with_selection(
        [source], source.scope, 1_100,
    )
    with pytest.raises(MemoryConsolidationError, match="page changed"):
        consolidator._read_validated_provider_result(
            validated, selected, source.scope,
            expected_batch_digest=str(other_selection["batchDigest"]),
        )


def test_cursor_only_digest_cannot_impersonate_frozen_payload(tmp_path: Path) -> None:
    project = tmp_path / "Project"
    project.mkdir()
    source = _source(project, "HEAD-" + ("s" * 1500) + "-TAIL")
    store = MemoryReviewStore(tmp_path / "review.json", tmp_path / "audit.jsonl")
    consolidator = MemoryConsolidator(store)
    _payload, selected, selection = consolidator.build_provider_request_with_selection(
        [source], source.scope, 1_000,
    )
    validated = consolidator.validate_provider_result(
        {"candidates": []}, selected, source.scope,
        batch_digest=str(selection["batchDigest"]),
    )
    forged = dict(validated)
    forged["batchDigest"] = memory_consolidation_module._provider_batch_digest(
        source.scope, selected, store.source_cursor_record(source.scope.scope_key),
    )
    forged["validationDigest"] = memory_consolidation_module._canonical_digest(
        {key: value for key, value in forged.items() if key != "validationDigest"}
    )
    with pytest.raises(MemoryConsolidationError, match="page changed"):
        consolidator._read_validated_provider_result(
            forged, selected, source.scope,
            expected_batch_digest=str(selection["batchDigest"]),
        )


