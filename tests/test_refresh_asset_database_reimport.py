from jsonschema import Draft202012Validator

from unity_write_input_schemas import EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS


def test_reimport_assets_schema_is_strict_and_bounded():
    schema = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_refresh_asset_database"]
    valid = {"projectPath": "D:/Project", "reimportAssets": [{
        "assetPath": "Assets/Shaders/a.lilcontainer",
        "guid": "a" * 32,
        "expectedImporterType": "UnityEditor.AssetImporter", "expectedSourceSha256": "b" * 64,
    }]}
    Draft202012Validator(schema).validate(valid)
    for value in [[], valid["reimportAssets"] * 17]:
        assert list(Draft202012Validator(schema).iter_errors({"projectPath": "D:/Project", "reimportAssets": value}))
    assert list(Draft202012Validator(schema).iter_errors({"projectPath": "D:/Project", "reimportAssets": [{"assetPath": "Packages/a", "guid": "a" * 32, "expectedImporterType": "UnityEditor.AssetImporter", "expectedSourceSha256": "b" * 64}]}))
    assert list(Draft202012Validator(schema).iter_errors({"projectPath": "D:/Project", "reimportAssets": [{**valid["reimportAssets"][0], "unexpected": True}]}))


def test_reimport_assets_accepts_embedded_package_csharp_only():
    schema = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_refresh_asset_database"]
    base = {
        "guid": "a" * 32,
        "expectedImporterType": "UnityEditor.MonoImporter",
        "expectedSourceSha256": "b" * 64,
    }
    Draft202012Validator(schema).validate({"projectPath": "D:/Project", "reimportAssets": [
        {**base, "assetPath": "Packages/jp.lilxyzw.liltoon/Editor/lilShaderContainerImporter.cs"},
    ]})
    for path in [
        "Packages/jp.lilxyzw.liltoon",
        "Packages/jp.lilxyzw.liltoon/package.json",
        "Packages/jp.lilxyzw.liltoon/Editor/lilShaderContainerImporter.cs.meta",
        "Packages/jp.lilxyzw.liltoon/../other/Importer.cs",
        "Packages/com.example/Editor/Importer.txt",
        "Packages/PackageCache/com.example@1.0.0/Editor/Importer.cs",
        "Library/PackageCache/com.example/Editor/Importer.cs",
    ]:
        assert list(Draft202012Validator(schema).iter_errors({"projectPath": "D:/Project", "reimportAssets": [{**base, "assetPath": path}]})), path


def test_scripted_importer_restore_reference_is_exact_and_assets_only():
    schema = EXTERNAL_MCP_WRITE_TOOL_INPUT_SCHEMAS["vrcforge_refresh_asset_database"]
    item = {
        "assetPath": "Assets/Shaders/a.lilcontainer",
        "guid": "a" * 32,
        "expectedImporterType": "UnityEditor.AssetImporters.ScriptedImporter",
        "expectedSourceSha256": "b" * 64,
        "restoreScriptedImporterReference": {
            "scriptGuid": "c" * 32,
            "expectedMetadataSha256": "d" * 64,
            "restoredMetadataSha256": "e" * 64,
        },
    }
    Draft202012Validator(schema).validate({"projectPath": "D:/Project", "reimportAssets": [item]})
    for bad_restore in [
        {"scriptGuid": "c" * 32, "expectedMetadataSha256": "d" * 64},
        {**item["restoreScriptedImporterReference"], "extra": True},
        {**item["restoreScriptedImporterReference"], "scriptGuid": "C" * 32},
        {**item["restoreScriptedImporterReference"], "expectedMetadataSha256": "g" * 63},
    ]:
        invalid = {**item, "restoreScriptedImporterReference": bad_restore}
        assert list(Draft202012Validator(schema).iter_errors({"projectPath": "D:/Project", "reimportAssets": [invalid]}))
    package_item = {**item, "assetPath": "Packages/com.example/Editor/Importer.cs"}
    assert list(Draft202012Validator(schema).iter_errors({"projectPath": "D:/Project", "reimportAssets": [package_item]}))


def test_refresh_plan_forwards_reimport_items_without_changing_legacy_defaults():
    import dashboard_server
    items = [{"assetPath": "Assets/a.lilcontainer", "guid": "a" * 32, "expectedImporterType": "UnityEditor.AssetImporter", "expectedSourceSha256": "b" * 64}]
    assert dashboard_server.build_refresh_asset_database_execution_plan({"projectPath": "D:/Project", "reimportAssets": items}) == [
        ("vrc_refresh_asset_database", {"projectPath": "D:/Project", "resolvePackages": False, "packageResolveTimeoutSeconds": 120, "reimportAssets": items})
    ]
    assert dashboard_server.build_refresh_asset_database_execution_plan({"projectPath": "D:/Project"}) == [
        ("vrc_refresh_asset_database", {"projectPath": "D:/Project", "resolvePackages": False, "packageResolveTimeoutSeconds": 120})
    ]
