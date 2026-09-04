from __future__ import annotations

import ast
from pathlib import Path

import dashboard_server
import validation_report_summary as summary


def test_summary_has_no_runtime_owner_dependency() -> None:
    tree = ast.parse(Path(summary.__file__).read_text(encoding="utf-8"))
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {"__future__", "typing"}
    assert {item.name for node in ast.walk(tree) if isinstance(node, ast.Import) for item in node.names} == {"re"}
    assert dashboard_server.VALIDATION_SECTION_ORDER is summary.VALIDATION_SECTION_ORDER
    assert dashboard_server._validation_gate is summary._validation_gate


def test_findings_redaction_keeps_root_callback_late_bound(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(dashboard_server, "_redact_doctor_detail", lambda detail: seen.append(detail) or {"redacted": True})
    findings = []
    dashboard_server._validation_add_finding(findings, "Custom section", "unknown", "Title", "Message", "source", {"private": "value"})
    dashboard_server._validation_add_finding(findings, "Unity compile", "Error", "Failure", "Compile failed", "compile")
    assert seen == [{"private": "value"}]
    assert findings[0]["severity"] == "Info"
    assert findings[0]["id"] == "source.1"
    assert findings[0]["detail"] == {"redacted": True}
    assert "preview, approval, checkpoint, apply, validation, and restore" in findings[0]["fixPolicy"]
    assert "detail" not in findings[1]
    sections = dashboard_server._validation_section_summaries(findings, include_all=False)
    assert [section["name"] for section in sections] == ["Unity compile", "Custom section"]
    assert sections[0]["status"] == "error"
    assert sections[1]["id"] == "custom_section"
    assert sections[1]["counts"]["Info"] == 1
    assert summary._validation_gate(findings, enabled=True)["blockingFindingIds"] == ["compile.2"]
    assert summary._validation_gate(findings, enabled=False)["status"] == "pass"


def test_source_summary_is_bounded_before_redaction(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(dashboard_server, "_redact_doctor_detail", lambda value: seen.append(value) or value)
    value = dashboard_server._validation_source_summary({
        "ok": False, "error": "failed", "discard": "hidden", "summary": {str(i): i for i in range(20)},
    })
    assert set(value) == {"ok", "error", "summary"}
    assert len(value["summary"]) == 12
    assert seen == [value]
    assert dashboard_server._validation_source_summary([]) == {"type": "list"}
    assert len(seen) == 1


def test_recursive_counts_and_empty_sections_keep_existing_semantics() -> None:
    payload = {"Count": 2, "nested": [{"COUNT": 3, "Items": [1, 2]}, {"items": [3]}]}
    assert summary._validation_max_number(payload, "count") == 3
    assert summary._validation_list_count(payload, "items") == 3
    assert summary._validation_max_number(payload, "missing") == 0
    sections = summary._validation_section_summaries([])
    assert [section["name"] for section in sections] == list(summary.VALIDATION_SECTION_ORDER)
    assert all(section["status"] == "not_run" for section in sections)
