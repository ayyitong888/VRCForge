"""Gate: raw exception text may never enter response paths.

The exit audit found machine prose reaching the chat UI through HTTP details
and event payloads. All response-surface errors must go through the unified
schema constructors; debug retention (emit_log, audit appends, debug
interaction records) keeps raw text by design and is exempt.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Response-surface files whose non-retention lines must stay schema-only.
GATED_FILES = ("dashboard_server.py",)

RAW_PATTERNS = (
    re.compile(r'HTTPException\([^)]*detail\s*=\s*str\('),
    re.compile(r'HTTPException\([^)]*detail\s*=\s*exc\b'),
)


def _retention_line(line: str) -> bool:
    return "emit_log" in line or "append_audit" in line or "record_debug_interaction" in line


def test_http_details_never_carry_raw_exception_text() -> None:
    for name in GATED_FILES:
        for index, line in enumerate((REPO / name).read_text(encoding="utf-8").splitlines(), 1):
            if _retention_line(line):
                continue
            for pattern in RAW_PATTERNS:
                assert not pattern.search(line), f"{name}:{index} leaks raw exception text into an HTTP detail"


def test_broadcast_payloads_never_carry_raw_exception_text() -> None:
    source = (REPO / "dashboard_server.py").read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(source, 1):
        if _retention_line(line):
            continue
        if "broadcast" not in line and "health_component" not in line:
            continue
        assert '"error": str(' not in line, f"dashboard_server.py:{index} broadcasts raw str(exc)"


def test_unified_constructors_are_used_for_http_errors() -> None:
    source = (REPO / "dashboard_server.py").read_text(encoding="utf-8")
    assert "runtime_http_error(" in source, "the schema HTTP constructor must exist"
    raise_sites = len(re.findall(r"raise HTTPException\(", source))
    schema_sites = len(re.findall(r"raise runtime_http_error\(", source))
    # Literal-detail raises remain allowed; raw exception details do not (test 1).
    assert schema_sites >= 80, "migrated raises must go through the schema constructor"
