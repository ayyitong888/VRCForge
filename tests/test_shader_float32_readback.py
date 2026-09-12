"""Shader readback compares the exact Unity storage value, never a loose epsilon."""
import struct

import pytest
from test_shader_persisted_receipt import receipt

from dashboard_server import normalize_shader_material_value, require_shader_apply_readback


def _single(value):
    return struct.unpack("!f", struct.pack("!f", value))[0]


def _change(after, **kwargs):
    return {"material_id": "RobotSurface", "semantic_property": "smoothness", "after": after, **kwargs}


@pytest.mark.parametrize("value", [0.5, 0.123456789, 0.8])
def test_readback_accepts_the_exact_float32_written_by_unity(value):
    normalized, warning = normalize_shader_material_value("smoothness", value)
    assert warning == ""
    # Includes the shortest decimal form emitted by Unity's float serializer.
    actual = _change(float(format(_single(normalized), ".9g")))
    assert require_shader_apply_readback(receipt([actual]), [_change(normalized)]) == [actual]


def test_restore_before_value_uses_the_same_float32_storage_contract():
    expected = _change(0.5, before=0.123456789)
    actual = _change(0.5, before=_single(expected["before"]))
    assert require_shader_apply_readback(receipt([actual]), [expected]) == [actual]


@pytest.mark.parametrize("field,value", [
    ("material_id", "OtherSurface"),
    ("semantic_property", "metallic"),
    ("after", 0.25),
    ("after", True),
    ("after", "0.123456789"),
    ("after", float("nan")),
])
def test_readback_rejects_wrong_identity_or_wrong_stored_value(field, value):
    expected = _change(0.123456789)
    actual = {**expected, field: value}
    with pytest.raises((RuntimeError, ValueError)):
        require_shader_apply_readback(receipt([actual]), [expected])


def test_even_one_float32_step_is_rejected():
    expected = _change(0.123456789)
    bits = struct.unpack("!I", struct.pack("!f", expected["after"]))[0]
    next_float = struct.unpack("!f", struct.pack("!I", bits + 1))[0]
    with pytest.raises(RuntimeError):
        require_shader_apply_readback(receipt([_change(next_float)]), [expected])


def test_color_values_remain_exact():
    expected = {**_change("#AABBCCFF"), "semantic_property": "main_color"}
    actual = {**expected, "after": "#AABBCDFF"}
    with pytest.raises(RuntimeError):
        require_shader_apply_readback(receipt([actual]), [expected])
