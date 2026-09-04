"""Boundary and pre-extraction contract evidence for the shared Unity schemas."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

import agent_gateway
import unity_shared_input_schemas


# Exact values captured before extraction; changes require a contract decision.
BASELINE_SHA256 = {'TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA': '1ea44917f443cf4433800b1eb8070e41254325ed63c078f895535c8614ea5ff1', '_PROJECT_PATH_PROPERTY': 'd45f71261d3fc70d0bf0f4e6e574ffcfebf12fff858dd6efd6628ff78aa1a929', '_AVATAR_PATH_PROPERTY': '1b8b8eb058a6d87d51dd175eb0a200d0b5a5ae4c543ee6b653c698bdcdfa981e', 'MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA': 'b8256bfd53dcb0a60d44d9d74aa8c036e28cb6f435de22c231173fb0e3c04df9', 'RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA': 'ba395de4cc8f05e558944488b09bd81aa4ea87dbf65210ad70b11cfaa719f247', 'SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA': 'b235844667bdfd3511bbbd8708fec46be67ce56ed976ff0ec931ade31c827787', 'SCENE_ASSET_DUPLICATE_PUBLIC_INPUT_SCHEMA': '432738b98d0f039799874490b3a2e3706991ca77b97c49602c0205a148071e3f', 'AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA': '70e2966534015afe10660cd3f9745c419537b6226292b0b1ad1237ef140be2ae', 'ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA': '652a1071b1af9f4fda526c66c42b7931196dd1b8e70836becdf4592767cac74a', 'EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA': '5941a995646041bd7705c8dfde332f8493acac2c0a49efbdb11cb4ba97da4178', 'EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA': '6d582cf0ca829cc6c819b693a0e95a73945ade00b8acb18b322aecb95b6a8499', 'MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA': '49f7d45fc9e734f496da74ceea1530eaf35863d020b1e3a360b3b00b336776b1'}


@pytest.mark.parametrize("name,expected", BASELINE_SHA256.items())
def test_shared_schema_has_one_owner_and_preserves_its_contract(name: str, expected: str) -> None:
    value = getattr(unity_shared_input_schemas, name)
    assert getattr(agent_gateway, name) is value
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected


def test_shared_schema_owner_is_an_importable_leaf() -> None:
    tree = ast.parse(Path(unity_shared_input_schemas.__file__).read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {"__future__", "typing"}
    assert not any(isinstance(node, (ast.Import, ast.Call, ast.FunctionDef, ast.ClassDef)) for node in ast.walk(tree))
    assert len(Path(unity_shared_input_schemas.__file__).read_bytes()) < 14_000
