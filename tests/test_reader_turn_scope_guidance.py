"""Reader guidance preserves scope, freshness and session ownership boundaries."""
import json

from agent_tool_result_reader import INPUT_SCHEMA, TOOL_NAME

REQUIRED_GUIDANCE = (
    "Honor its scope and freshness; an expired owned reference returns needs_rescan "
    "with a reason and scope, never fresh data."
)

# Never promise unconditional reuse. Validated-scope reuse remains host-checked.
RELAXATION_PHRASES = (
    "any turn",
    "all turns",
    "any session",
    "all sessions",
    "across sessions",
    "without validation",
)


def test_result_ref_description_states_turn_scope():
    description = INPUT_SCHEMA["properties"]["resultRef"]["description"]
    assert REQUIRED_GUIDANCE in description
    # The existing restrictive copy must remain intact.
    assert "Never guess another session's reference" in description


def test_projected_model_facing_schema_carries_turn_scope_guidance():
    # The catalog/projection path is what the model actually receives; the
    # guidance must survive that projection.
    from unity_tool_schema_projection import canonical_unity_read_tool_input_schema

    projected = canonical_unity_read_tool_input_schema(TOOL_NAME)
    assert REQUIRED_GUIDANCE in projected["properties"]["resultRef"]["description"]


def test_reader_schema_copy_contains_no_relaxation_wording():
    rendered = json.dumps(INPUT_SCHEMA, ensure_ascii=False)
    lowered = rendered.lower()
    for phrase in RELAXATION_PHRASES:
        assert phrase.lower() not in lowered, f"relaxing phrase {phrase!r} found in reader schema"


def test_reader_schema_structure_unchanged():
    # The batch C change is copy-only: schema shape and constraints stay fixed.
    assert TOOL_NAME == "vrcforge_read_tool_result"
    assert INPUT_SCHEMA["required"] == ["resultRef"]
    assert INPUT_SCHEMA["additionalProperties"] is False
    result_ref = INPUT_SCHEMA["properties"]["resultRef"]
    assert result_ref["type"] == "string"
    assert result_ref["pattern"] == "^result_[0-9a-f]{32}$"
    assert set(INPUT_SCHEMA["properties"]) == {
        "resultRef", "jsonPointer", "offset", "limit", "textOffset", "source",
    }
