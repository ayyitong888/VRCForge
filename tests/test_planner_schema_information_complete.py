"""Repair feedback must retain the complete rule and exact property path."""
import pytest

from runtime_planner_service import _validate_planner_schema_node


@pytest.mark.parametrize("kind", ["const", "pattern", "property"])
def test_schema_repair_feedback_retains_full_information(kind):
    text = "a" * 180 + "distinct_tail"
    if kind == "property":
        schema = {"type": "object", "additionalProperties": False}
        value = {text: 1}
        field, expected = "path", text
    else:
        expected = text if kind == "const" else "^" + text + "$"
        schema = {"type": "string", kind: expected}
        value = "wrong"
        field = "expected"
    issues = []
    _validate_planner_schema_node(schema, value, "", issues)
    assert len(issues) == 1
    assert issues[0][field] == expected


def test_schema_repair_feedback_keeps_all_invalid_properties():
    names = [f"field_{index}" for index in range(25)]
    issues = []
    _validate_planner_schema_node(
        {"type": "object", "additionalProperties": False},
        dict.fromkeys(names, 1), "", issues,
    )
    assert [issue["path"] for issue in issues] == names
