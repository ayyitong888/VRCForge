from __future__ import annotations

import hashlib

import pytest

from context_compaction import compact_context, normalize_compaction_recovery


def _recovery(*, entries=None, summary="A complete summary"):
    entries = entries or [{"role": "user", "text": "Keep this complete"}]
    return {
        "schema": "vrcforge.context_compaction_recovery.v1",
        "authority": "historical_context_data",
        "sourceEntries": entries,
        "sourceDigest": hashlib.sha256(
            __import__("json").dumps(entries, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "summary": summary,
        "summaryDigest": hashlib.sha256(summary.encode()).hexdigest(),
        "summarySource": "provider",
    }


def test_existing_generated_recovery_round_trips_without_loss_or_truncation():
    tail = "RECOVERY_TAIL_" + ("x" * 12000)
    result = compact_context(
        [{"role": "user", "text": "goal"}],
        summarizer=lambda _prompt: "summary " + tail,
    )
    recovery = result["recovery"]
    assert normalize_compaction_recovery(recovery) == recovery


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value["sourceEntries"][0].update({"reasoning_content": "private"}),
        lambda value: value["sourceEntries"].append({"role": "unknown", "text": "x"}),
        lambda value: value.update({"extra": "reject"}),
        lambda value: value.update({"summarySource": "other"}),
        lambda value: value.update({"authority": "caller_claim"}),
        lambda value: value.update({"sourceDigest": "0" * 64}),
        lambda value: value.update({"summaryDigest": "0" * 64}),
    ],
)
def test_recovery_rejects_schema_authority_unknown_fields_roles_and_hash_mismatch(mutator):
    value = _recovery()
    mutator(value)
    with pytest.raises(ValueError):
        normalize_compaction_recovery(value)


@pytest.mark.parametrize("role", ["agent", "model", "developer", "assistant "])
def test_recovery_accepts_only_existing_canonical_roles(role):
    value = _recovery(entries=[{"role": role, "text": "safe"}])
    with pytest.raises(ValueError):
        normalize_compaction_recovery(value)


def test_recovery_hashes_the_scanned_safe_payload_without_truncation():
    value = _recovery(entries=[{"role": "user", "text": "password=SECRET_VALUE"}])
    safe = "password=[REDACTED_SECRET]"
    value["sourceDigest"] = hashlib.sha256(
        __import__("json").dumps([{"role": "user", "text": safe}], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    normalized = normalize_compaction_recovery(value)
    assert normalized["sourceEntries"] == [{"role": "user", "text": safe}]

    raw = _recovery(entries=[{"role": "user", "text": "password=SECRET_VALUE"}])
    with pytest.raises(ValueError):
        normalize_compaction_recovery(raw)


def test_recovery_requires_exact_entry_shape_and_string_fields():
    for entries in (
        [{"role": "user", "text": "safe", "content": "extra"}],
        [{"role": "user", "text": 1}],
        [{"role": "user"}],
        "not-a-list",
    ):
        value = _recovery(entries=entries)
        with pytest.raises(ValueError):
            normalize_compaction_recovery(value)
