import json
from approval_auto_review import review_auto_approval


def test_reviewer_sees_complete_safe_mutation_content_and_identifiers():
    captured = []
    record = {"id": "approval-" + "a" * 200, "targetTool": "tool-" + "b" * 200,
        "riskLevel": "r" * 90, "arguments": {"script": "print('review-this-operation')",
        "nested": {"body": "safe body"}, "apiKey": "hidden-credential"},
        "preview": {"patch": "old line -> new line"},
        "taskContext": {"objective": "Review exactly this change."}}
    assert review_auto_approval(record, lambda prompt: captured.append(prompt) or '{"decision":"manual"}') == "manual"
    evidence = json.loads(captured[0].split("\n")[-1])
    assert evidence["approvalId"] == record["id"]
    assert evidence["tool"] == record["targetTool"]
    assert evidence["risk"] == record["riskLevel"]
    assert evidence["arguments"]["script"] == record["arguments"]["script"]
    assert evidence["arguments"]["nested"]["body"] == "safe body"
    assert evidence["preview"]["patch"] == record["preview"]["patch"]
    assert "hidden-credential" not in captured[0]


def test_oversized_content_is_not_approved_using_only_a_byte_count():
    calls = []
    record = {"targetTool": "write_file", "arguments": {"content": "x" * 20000}}
    assert review_auto_approval(record, lambda prompt: calls.append(prompt) or '{"decision":"allow_auto"}') == "manual"
    assert calls == []


def test_mutation_text_keeps_tail_but_not_embedded_credentials():
    captured = []
    record = {"targetTool": "write_file", "arguments": {"content":
        "safe prefix\n-----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY-----\nsafe tail"}}
    review_auto_approval(record, lambda prompt: captured.append(prompt) or '{"decision":"manual"}')
    assert "private-material" not in captured[0]
    assert "safe prefix" in captured[0] and "safe tail" in captured[0]
