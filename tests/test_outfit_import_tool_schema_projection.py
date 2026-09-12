from unity_tool_schema_projection import canonical_unity_read_tool_input_schema, canonical_unity_write_tool_input_schema


def test_outfit_import_write_schema_exposes_real_preparer_arguments():
    schema = canonical_unity_write_tool_input_schema("vrcforge_import_outfit_package")
    assert schema["required"] == ["packagePath", "projectPath", "executionTarget"]
    assert {"packagePath", "projectPath", "targetFolder", "selectedUnityPackage", "dependencyMode", "selectedPrefab", "baseAvatarName", "maxEntries"} <= set(schema["properties"])
    assert schema["properties"]["dependencyMode"]["enum"] == ["auto", "selected_only"]
    assert schema["additionalProperties"] is False


def test_outfit_import_plan_and_inspect_read_schemas_are_discoverable():
    plan = canonical_unity_read_tool_input_schema("vrcforge_plan_outfit_import")
    inspect = canonical_unity_read_tool_input_schema("vrcforge_inspect_outfit_package")
    assert plan["required"] == ["packagePath"]
    assert "projectPath" in plan["properties"]
    assert inspect["required"] == ["packagePath"]
    assert set(inspect["properties"]) == {"packagePath", "maxEntries"}
