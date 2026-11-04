from unity_yaml_inspection import inspect_unity_yaml


def inspect(tmp_path, text, **kwargs):
    asset = tmp_path / "Assets/test.unity"
    asset.parent.mkdir(exist_ok=True)
    asset.write_text(text, encoding="utf-8")
    result = inspect_unity_yaml({"projectPath": str(tmp_path), "assetPath": "Assets/test.unity", **kwargs}, str)
    assert asset.read_text(encoding="utf-8") == text
    return result


def test_offline_scene_local_references_and_null_script_are_distinct(tmp_path):
    result = inspect(tmp_path, "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!1 &1\nGameObject:\n  m_Component:\n  - component: {fileID: 2}\n--- !u!114 &2\nMonoBehaviour:\n  m_GameObject: {fileID: 1}\n  m_Script: {fileID: 0}\n  lost: {fileID: 99}\n")
    assert result["objectCount"] == 2
    assert {f["code"] for f in result["findings"]} == {"missing_script_reference", "unresolved_local_reference"}
    assert [f["fileID"] for f in result["findings"] if f["code"] == "unresolved_local_reference"] == [99]
    assert result["unityLoadVerified"] is False and result["mutationStarted"] is False


def test_valid_stripped_objects_and_external_guid_are_not_local_dangling_refs(tmp_path):
    result = inspect(tmp_path, "--- !u!1 &100 stripped\nGameObject:\n  m_CorrespondingSourceObject: {fileID: 100100000, guid: 00001234567890123456789012345678, type: 3}\n--- !u!4 &-20\nTransform:\n  m_GameObject: {fileID: 100}\n  m_Father: {fileID: 0}\n")
    assert result["ok"] and result["findings"] == []
    assert result["externalGuidCount"] == 1
    assert result["status"] == "no_findings_in_checked_scope"
    assert any("external GUID lookup" in item for item in result["unchecked"])


def test_duplicate_ids_and_malformed_yaml_are_reported(tmp_path):
    result = inspect(tmp_path, "--- !u!1 &1\nGameObject: {}\n--- !u!1 &1\nGameObject: {}\n")
    assert result["findings"] == [{"severity": "error", "code": "duplicate_file_id", "fileID": 1}]
    result = inspect(tmp_path, "--- !u!1 &1\nGameObject: [broken\n")
    assert result["ok"] is False


def test_external_metadata_lookup_preserves_digit_only_guids(tmp_path):
    guid = "00001234567890123456789012345678"
    folder = tmp_path / "Assets"
    folder.mkdir()
    (folder / "asset.meta").write_text("fileFormatVersion: 2\nguid: " + guid + "\n")
    result = inspect(tmp_path, f"--- !u!1 &1\nGameObject:\n  asset: {{fileID: 100100000, guid: {guid}, type: 3}}\n", checkExternalReferences=True)
    assert result["findings"] == []


def test_custom_file_id_mapping_is_not_certified_as_broken_unity_reference(tmp_path):
    result = inspect(tmp_path, "--- !u!114 &1\nMonoBehaviour:\n  customData: {fileID: 42, label: custom}\n  ambiguousData: {fileID: 43}\n")
    assert len(result["findings"]) == 1
    assert result["findings"][0]["fileID"] == 43
    assert result["findings"][0]["severity"] == "warning"
    assert result["unityLoadVerified"] is False


def test_offline_yaml_tool_uses_registered_schema_without_live_unity_binding(tmp_path):
    from agent_gateway import AgentGateway
    from external_mcp_tool_blocks import EXTERNAL_MCP_READ_TOOL_BLOCKS
    from internal_tool_blocks import INTERNAL_GENERAL_TOOL_NAMES
    from unity_tool_schema_projection import canonical_unity_read_tool_input_schema
    from unity_yaml_inspection import TOOL_NAME, INPUT_SCHEMA, register_yaml_inspection
    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit")
    register_yaml_inspection(gateway, str)
    assert TOOL_NAME in gateway._tools
    assert TOOL_NAME in EXTERNAL_MCP_READ_TOOL_BLOCKS["diagnostics"]
    assert TOOL_NAME in INTERNAL_GENERAL_TOOL_NAMES
    assert canonical_unity_read_tool_input_schema(TOOL_NAME)["required"] == INPUT_SCHEMA["required"]
