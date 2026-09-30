"""Regression gate: the reader's model-facing copy must state resultRef turn scope.

These tests pin the CACHE_HIT_FIX batch C guidance: resultRef values are valid
only within the turn that produced the result. The copy may evolve, but it must
never drop the turn-scope statement or gain wording that relaxes that boundary.
"""
import json

from agent_tool_result_reader import INPUT_SCHEMA, TOOL_NAME

REQUIRED_GUIDANCE = (
    "resultRef values are valid only within the turn that produced the result; "
    "in later turns rely on the conversation history instead of re-reading old results."
)

# Wording that would signal a relaxed cross-turn/session boundary. None of these
# may appear anywhere in the model-facing reader copy.
RELAXATION_PHRASES = (
    "across turns",
    "cross-turn",
    "cross turn",
    "any turn",
    "all turns",
    "previous turns",
    "earlier turns",
    "persist",
    "survive",
    "other sessions",
    "across sessions",
    "reuse a later",
)


def test_result_ref_description_states_turn_scope():
    description = INPUT_SCHEMA["properties"]["resultRef"]["description"]
    assert REQUIRED_GUIDANCE in description
    # The existing restrictive copy must remain intact.
    assert "never guess or reuse another turn's reference" in description


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
