import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_approval_transactions import _domain_write_receipt
from agent_tool_result_contract import normalize_agent_tool_result


def _core_receipt(**overrides):
    return {
        "schema": "vrcforge.constraint_source_write.v1",
        "ok": True,
        "preview": False,
        "verified": True,
        "changed": True,
        "saved": True,
        "scenePath": "Assets/Avatar.unity",
        "targetSourcesDigest": "target-digest",
        **overrides,
    }


def _transport(receipt, **overrides):
    return {
        "ok": True,
        "toolName": "vrc_set_constraint_sources",
        "result": {
            "exitCode": 0,
            "payload": {
                "isError": False,
                "structuredContent": {
                    "success": True,
                    "data": receipt,
                },
            },
        },
        **overrides,
    }


@pytest.mark.parametrize(
    "schema",
    (
        "vrcforge.constraint_source_write.v1",
        "vrcforge.example_domain_write.v1",
    ),
)
def test_transport_wrapped_core_verified_saved_write_becomes_domain_receipt(schema):
    receipt = _domain_write_receipt(_transport(_core_receipt(schema=schema)))

    assert receipt["schema"] == schema
    assert receipt["verified"] is True
    assert receipt["changed"] is True
    assert receipt["saved"] is True
    assert receipt["mutationStarted"] is True
    assert receipt["mutationApplied"] is True
    assert receipt["committed"] is True
    assert receipt["commitState"] == "committed"
    outcome = normalize_agent_tool_result(receipt, fallback_summary="write", write=True)
    assert outcome["status"] == "ok"


def test_direct_core_verified_no_change_maps_without_claiming_a_mutation():
    receipt = _domain_write_receipt(
        _core_receipt(changed=False, saved=False, targetSourcesDigest="same-digest")
    )

    assert receipt["verified"] is True
    assert receipt["changed"] is False
    assert receipt["saved"] is False
    assert receipt["mutationStarted"] is False
    assert receipt["mutationApplied"] is False
    assert receipt["committed"] is True
    assert receipt["commitState"] == "no_change"


def test_unverified_or_unsaved_domain_writes_stay_failed_with_mutation_facts():
    cases = (
        _core_receipt(verified=False),
        _core_receipt(saved=False),
        _core_receipt(preview=True),
        _core_receipt(status="failed"),
        _core_receipt(ok=False),
    )
    for core in cases:
        receipt = _domain_write_receipt(core)
        outcome = normalize_agent_tool_result(receipt, fallback_summary="write", write=True)
        assert outcome["status"] == "failed"
        assert receipt.get("verified") is not True
        assert receipt.get("mutationStarted") is True
        assert receipt.get("mutationApplied") is True


def test_failed_transport_cannot_promote_nested_success_receipt():
    receipt = _domain_write_receipt(
        _transport(_core_receipt(), result={
            "exitCode": 1,
            "payload": {
                "isError": True,
                "structuredContent": {
                    "success": False,
                    "data": _core_receipt(),
                },
            },
        })
    )

    outcome = normalize_agent_tool_result(receipt, fallback_summary="write", write=True)
    assert outcome["status"] == "failed"
    assert receipt.get("verified") is not True
