from memory_consolidation_sources import admit_memory_source, resolve_memory_scope, redact_memory_text


def source(text):
    return admit_memory_source({"sourceType": "user_chat", "sourceId": "full-source",
        "sourceRevision": "1", "role": "user", "status": "completed",
        "signalKind": "preference", "memoryScope": "user", "text": text},
        scope=resolve_memory_scope("user"))


def test_source_retains_tail_and_digest_binds_tail_changes():
    prefix = "Ordinary retained preference. " * 200
    first = source(prefix + "TAIL_ONE")
    second = source(prefix + "TAIL_TWO")
    assert first.text == prefix + "TAIL_ONE"
    assert second.text.endswith("TAIL_TWO")
    assert first.source_digest != second.source_digest


def test_full_source_redacts_secrets_beyond_old_limit_before_any_paging():
    prefix = "Ordinary retained preference. " * 200
    secret = "sk-" + "example-secret-1234567890"
    projection = source(prefix + " api_key=" + secret + " safe-tail")
    assert secret not in projection.text
    assert projection.text.endswith("safe-tail")


def test_explicit_display_limit_remains_available():
    text, _ = redact_memory_text("A" * 100, limit=20)
    assert len(text) == 20
    assert text.endswith("…")
