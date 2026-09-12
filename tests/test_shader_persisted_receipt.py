"""Receipt validation regressions; no claim of executing Unity persistence."""
import copy
import pytest
from dashboard_server import require_shader_apply_readback


def receipt(rows):
    readback = [{**row, "assetPath": "Assets/Test.mat", "assetGuid": "1" * 32} for row in rows]
    return {"schema": "vrcforge.material_tuning_write.v1", "ok": True,
            "verified": True, "persistedReadback": True, "saved": True, "pending": False,
            "committed": True, "commitState": "committed", "commitStateKnown": True,
            "appliedCount": len(rows), "skippedCount": 0, "skipped": [], "applied": rows,
            "readback": readback}


ROW = {"material_id": "RobotSurface", "semantic_property": "smoothness", "before": 0.2, "after": 0.5}


def test_memory_only_success_is_not_persisted_proof():
    with pytest.raises(RuntimeError):
        require_shader_apply_readback({"applied": [ROW]}, [ROW])


@pytest.mark.parametrize("field,value", [("schema", "unknown"), ("verified", False),
    ("persistedReadback", False), ("saved", False), ("pending", True),
    ("commitState", "unknown"), ("readback", []), ("appliedCount", 0)])
def test_incomplete_receipt_rejects(field, value):
    result = receipt([ROW]); result[field] = value
    with pytest.raises(RuntimeError):
        require_shader_apply_readback(result, [ROW])


def test_persisted_value_must_match_request_and_applied_row():
    result = receipt([ROW]); result["readback"][0]["after"] = 0.4
    with pytest.raises(RuntimeError):
        require_shader_apply_readback(result, [ROW])


def test_valid_receipt():
    assert require_shader_apply_readback(receipt([ROW]), [ROW]) == [ROW]
