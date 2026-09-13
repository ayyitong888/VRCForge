"""Boundary and current public contract evidence for the shared Unity schemas."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

import agent_gateway
import unity_shared_input_schemas


# Includes the approved bounded material, animation, and Animator batch contracts.
BASELINE_SHA256 = {'TEXTURE_IMPORT_SETTINGS_PUBLIC_INPUT_SCHEMA': '1ea44917f443cf4433800b1eb8070e41254325ed63c078f895535c8614ea5ff1', '_PROJECT_PATH_PROPERTY': 'd45f71261d3fc70d0bf0f4e6e574ffcfebf12fff858dd6efd6628ff78aa1a929', '_AVATAR_PATH_PROPERTY': '1b8b8eb058a6d87d51dd175eb0a200d0b5a5ae4c543ee6b653c698bdcdfa981e', 'MATERIAL_TEXTURE_ASSIGNMENT_PUBLIC_INPUT_SCHEMA': '187b0ab9d232e9049cc3bb9f48c88138cccd3e6796289d81311029f97c705ac9', 'RENDERER_MATERIAL_SLOT_PUBLIC_INPUT_SCHEMA': 'c5a7663cf692e67b8ad8978450b231dfbfee186f03310bd3cedafee4a3972990', 'SCENE_OBJECT_DUPLICATE_PUBLIC_INPUT_SCHEMA': 'b235844667bdfd3511bbbd8708fec46be67ce56ed976ff0ec931ade31c827787', 'SCENE_ASSET_DUPLICATE_PUBLIC_INPUT_SCHEMA': '432738b98d0f039799874490b3a2e3706991ca77b97c49602c0205a148071e3f', 'AVATAR_DESCRIPTOR_WRITE_PUBLIC_INPUT_SCHEMA': '70e2966534015afe10660cd3f9745c419537b6226292b0b1ad1237ef140be2ae', 'ANIMATION_CURVE_WRITE_PUBLIC_INPUT_SCHEMA': '63a6dbdd33e2a13e4d1e51203f318770083dfa6155de9681f7be1f96ac6a6645', 'EXPRESSION_PARAMETERS_MANAGE_PUBLIC_INPUT_SCHEMA': '5941a995646041bd7705c8dfde332f8493acac2c0a49efbdb11cb4ba97da4178', 'EXPRESSION_MENU_MANAGE_PUBLIC_INPUT_SCHEMA': '6d582cf0ca829cc6c819b693a0e95a73945ade00b8acb18b322aecb95b6a8499', 'MANAGE_FX_ANIMATOR_PUBLIC_INPUT_SCHEMA': '6e9b53654793bdff9ba47a0ff2a3acf36245b1cca3d4266cf5291df94b462dfc'}


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
    assert not any(isinstance(node, (ast.Import, ast.ClassDef)) for node in ast.walk(tree))
    assert {node.name for node in tree.body if isinstance(node, ast.FunctionDef)} == {"outfit_import_input_schema"}
    assert unity_shared_input_schemas.outfit_import_input_schema(require_project_path=True)["required"] == ["packagePath", "projectPath"]
    assert unity_shared_input_schemas.outfit_import_input_schema(require_project_path=False)["required"] == ["packagePath"]
