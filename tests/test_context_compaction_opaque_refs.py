from __future__ import annotations

import hashlib
import json

from context_compaction import (
    COMPACTION_RECOVERY_SCHEMA,
    compact_context,
    normalize_compaction_recovery,
    redact_context_text,
)


def _recovery(text: str, summary: str) -> dict[str, object]:
    entries = [{"role": "assistant", "text": text}]
    return {
        "schema": COMPACTION_RECOVERY_SCHEMA,
        "authority": "historical_context_data",
        "sourceEntries": entries,
        "sourceDigest": hashlib.sha256(
            json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "summary": summary,
        "summaryDigest": hashlib.sha256(summary.encode()).hexdigest(),
        "summarySource": "provider",
    }


def test_opaque_execution_target_ref_survives_compaction_and_recovery_round_trip():
    ref = "vrcforge-target-abc123-sK-def456ghi789"

    redacted, counts = redact_context_text(ref)
    assert redacted == ref
    assert counts["secrets"] == 0

    result = compact_context([{"role": "assistant", "text": f"Bound target {ref}"}])
    assert result["recovery"]["sourceEntries"] == [
        {"role": "assistant", "text": f"Bound target {ref}"}
    ]

    raw = _recovery(f"Bound target {ref}", f"Selected target {ref}")
    assert normalize_compaction_recovery(raw) == raw


def test_secret_detection_still_catches_standalone_tokens_and_jwts():
    opaque = "vrcforge-target-prefix-sk-proj-secretvalue"
    sanitized, report = redact_context_text(opaque)
    assert sanitized == opaque
    assert report["secrets"] == 0

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue"
    embedded_jwt = f"vrcforge-target-prefix-{jwt}-suffix"
    assert redact_context_text(embedded_jwt)[0] == embedded_jwt

    for text in (
        "sk-proj-secretvalue",
        "authorization=sk-proj-secretvalue",
        "Bearer sk-proj-secretvalue",
        jwt,
    ):
        value, report = redact_context_text(text)
        assert "[REDACTED_SECRET]" in value
        assert report["secrets"] >= 1
