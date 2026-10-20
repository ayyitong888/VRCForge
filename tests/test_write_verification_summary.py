import json
from pathlib import Path

import pytest

from agent_write_verification_summary import (
    MAX_SUMMARY_EDITS,
    bounded_write_verification_summary,
)


FIXTURE_RECEIPT = Path(__file__).parent / "fixtures" / "fx_write_verified_receipt.json"


def _fixture_result() -> dict:
    return json.loads(FIXTURE_RECEIPT.read_text(encoding="utf-8"))


def _result(*, target: bool = True) -> dict:
    value = {
        "schema": "vrcforge.fx_animator_write.v1",
        "ok": True,
        "preview": False,
        "verified": True,
        "persistedReadback": True,
        "mutationApplied": True,
        "committed": True,
        "commitState": "committed",
        "commitStateKnown": True,
        "checkpointRecoveryRequired": False,
        "temporaryCleanupRequired": False,
        "action": "update_state",
        "layerName": "Left Hand",
        "stateName": "Fist",
        # This must never become the actual scalar in the summary.
        "speed": 99.0,
        "readback": {
            "persisted": True,
            "controllerPath": "Assets/FX.controller",
            "state": {
                "layers": [
                    {
                        "name": "Left Hand",
                        "machine": {
                            "name": "Left Hand",
                            "states": [
                                {
                                    "name": "Fist",
                                    "speed": 0.75,
                                    "writeDefaultValues": True,
                                    "timeParameterActive": True,
                                    "timeParameter": "GestureLeftWeight",
                                    "motionPath": "Assets/close.anim",
                                }
                            ],
                            "machines": [],
                        },
                    }
                ]
            },
        },
    }
    if not target:
        value.pop("layerName")
        value.pop("stateName")
    return value


def test_replays_deidentified_authoritative_fixture_and_uses_persisted_scalar():
    result = _fixture_result()
    # The selector is supplied by the owning write step, while the scalar still
    # comes only from persisted readback.
    summary = bounded_write_verification_summary(
        result,
        target_arguments={"layerName": "Left Hand", "stateName": "Fist", "speed": 999.0},
    )

    assert summary is not None
    assert summary["applied"] is True
    assert summary["verified"] is True
    assert summary["evidenceSource"] == "persisted_authoritative_receipt"
    assert summary["readback"]["target"]["layerName"] == "Left Hand"
    assert summary["readback"]["target"]["stateName"] == "Fist"
    assert summary["readback"]["target"]["speed"] == 0.75
    assert "approval" not in json.dumps(summary)
    assert "parameters" not in json.dumps(summary)


def test_single_edit_does_not_use_requested_speed_as_readback():
    summary = bounded_write_verification_summary(_result(), target_arguments={"speed": 12.0})

    assert summary["readback"]["target"]["speed"] == 0.75
    assert summary["readback"]["target"]["speed"] != 99.0


def test_fixture_state_without_explicit_selector_does_not_guess_target_from_speed():
    value = _fixture_result()
    value["readback"]["state"]["layers"][1]["machine"]["states"][0]["speed"] = 0.5

    summary = bounded_write_verification_summary(value)

    assert summary["applied"] is True
    assert summary["verified"] is True
    assert summary["readback"]["targetUnavailable"] is True
    assert "target" not in summary["readback"]


def test_duplicate_state_target_is_readback_unavailable():
    value = _result()
    duplicate = value["readback"]["state"]["layers"][0]["machine"]["states"][0].copy()
    duplicate["name"] = "Fist"
    value["readback"]["state"]["layers"][0]["machine"]["states"].append(duplicate)

    summary = bounded_write_verification_summary(value)

    assert summary["verified"] is None
    assert summary["readback"]["readbackUnavailable"] is True
    assert summary["readback"]["selector"]["stateName"] == "Fist"
    assert "present" not in summary["readback"]


@pytest.mark.parametrize("action", ["delete_state", "delete_layer"])
def test_delete_target_does_not_fabricate_absence(action):
    value = _result()
    value["action"] = action

    summary = bounded_write_verification_summary(value)

    assert summary["verified"] is None
    assert summary["readback"]["readbackUnavailable"] is True
    assert "present" not in summary["readback"]


@pytest.mark.parametrize(
    "changes",
    [
        {"pending": True},
        {"preview": True},
        {"ok": False, "verified": False, "persistedReadback": False, "mutationApplied": False, "committed": False, "commitState": "not_started"},
        {"committed": False},
        {"readback": {"persisted": False}},
    ],
)
def test_non_execution_or_negative_receipts_never_claim_success(changes):
    value = _result()
    value.update(changes)
    summary = bounded_write_verification_summary(value)

    assert summary is not None
    assert summary["applied"] is not True
    assert summary["verified"] is not True


def test_incomplete_receipt_is_unknown_and_unrecognized_schema_is_rejected():
    value = _result()
    value.pop("commitState")
    summary = bounded_write_verification_summary(value)
    assert summary["applied"] is None
    assert summary["verified"] is None
    assert bounded_write_verification_summary({"schema": "other.v1"}) is None


def test_batch_projection_is_bounded_and_marks_truncation():
    value = _result(target=False)
    states = []
    edits = []
    for index in range(MAX_SUMMARY_EDITS + 3):
        name = f"State{index}"
        edits.append({"action": "update_state", "layerName": "Layer", "stateName": name, "speed": 1234})
        states.append({"name": name, "speed": index + 0.5, "writeDefaultValues": True})
    value.pop("action")
    value["edits"] = edits
    value["readback"]["state"] = {"layers": [{"name": "Layer", "machine": {"name": "Layer", "states": states, "machines": []}}]}

    summary = bounded_write_verification_summary(value)
    assert summary["applied"] is True
    assert summary["readback"]["total"] == len(edits)
    assert summary["readback"]["truncated"] is True
    assert len(summary["readback"]["edits"]) == MAX_SUMMARY_EDITS
    assert summary["readback"]["edits"][0]["speed"] == 0.5


def test_projection_does_not_leak_secret_extras_or_split_oversized_utf8_target():
    value = _result()
    value["approval"] = {"secret": "should-not-appear"}
    value["readback"]["state"]["secretToken"] = "should-not-appear"
    value["readback"]["state"]["layers"][0]["machine"]["states"][0]["extra"] = {
        "token": "should-not-appear",
        "huge": "x" * 100_000,
    }
    summary = bounded_write_verification_summary(value)
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "should-not-appear" not in serialized
    assert "secretToken" not in serialized
    assert len(serialized.encode("utf-8")) < 16_384

    oversized = _result()
    oversized["layerName"] = "层" * 300
    bounded = bounded_write_verification_summary(oversized)
    assert "层" * 300 not in json.dumps(bounded, ensure_ascii=False)
