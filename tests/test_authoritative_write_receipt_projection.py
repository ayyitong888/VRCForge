from copy import deepcopy

import pytest

from authoritative_unity_writes import validated_unity_write_receipt_fields


def receipt():
    return {
        "schema": "vrcforge.example_asset_write.v2",
        "ok": True,
        "preview": False,
        "verified": True,
        "saved": True,
        "changed": True,
        "after": {"assetPath": "Assets/Example.asset", "guid": "a" * 32},
    }


@pytest.mark.parametrize("field,value", [
    ("ok", False), ("verified", False), ("verified", 1), ("saved", False),
    ("preview", True), ("changed", None), ("schema", "unrecognized"),
])
def test_incomplete_or_unsaved_facts_do_not_gain_verification(field, value):
    payload = receipt()
    payload[field] = value
    assert validated_unity_write_receipt_fields(payload) == {}


@pytest.mark.parametrize("field", ["ok", "verified", "saved", "preview", "changed"])
def test_missing_fact_does_not_gain_verification(field):
    payload = receipt()
    del payload[field]
    assert validated_unity_write_receipt_fields(payload) == {}


@pytest.mark.parametrize("explicit", [
    {"commitState": "unknown"}, {"committed": False},
    {"mutationStarted": None}, {"persistedReadback": False}, {"readback": {}},
])
def test_existing_explicit_receipt_is_not_reinterpreted(explicit):
    payload = receipt() | explicit
    assert validated_unity_write_receipt_fields(payload) == {}


def test_projection_preserves_all_evidence_without_aliasing_or_tool_dispatch():
    payload = receipt()
    original = deepcopy(payload)
    projected = validated_unity_write_receipt_fields(payload)
    assert projected["readback"] == {"persisted": True, "data": original}
    assert projected["commitState"] == "committed"
    projected["readback"]["data"]["after"]["guid"] = "b" * 32
    assert payload == original


def test_verified_saved_no_change_does_not_claim_a_mutation():
    payload = receipt() | {"changed": False}
    projected = validated_unity_write_receipt_fields(payload)
    assert projected["commitState"] == "no_change"
    assert projected["mutationStarted"] is False
    assert projected["mutationApplied"] is False
