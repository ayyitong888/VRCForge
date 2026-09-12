import jsonschema

from unity_tool_schema_projection import (
    canonical_unity_read_tool_input_schema,
    canonical_unity_write_tool_input_schema,
)


def test_interrupted_apply_recovery_public_schemas_expose_real_handler_fields():
    listed = canonical_unity_read_tool_input_schema("vrcforge_list_interrupted_apply_recoveries")
    jsonschema.validate({"projectRoot": "D:/Unity", "limit": 20, "includeResolved": True}, listed)
    jsonschema.validate({"project_root": "D:/Unity", "include_resolved": False}, listed)
    assert listed["properties"]["limit"]["maximum"] == 500

    preview = canonical_unity_read_tool_input_schema("vrcforge_preview_interrupted_apply_recovery")
    jsonschema.validate({"recoveryId": "recovery-1"}, preview)
    jsonschema.validate({"checkpoint_id": "checkpoint-1", "include_resolved": True}, preview)
    assert {"recoveryId", "recovery_id", "id", "checkpointId", "checkpoint_id"} <= set(preview["properties"])

    resolve = canonical_unity_read_tool_input_schema("vrcforge_resolve_interrupted_apply_recovery")
    jsonschema.validate({"recovery_id": "recovery-1", "confirm_resolved": True, "reason": "handled"}, resolve)
    jsonschema.validate({"recoveryId": "recovery-1", "confirmResolved": True, "note": "handled"}, resolve)
    try:
        jsonschema.validate({"recoveryId": "recovery-1"}, resolve)
    except jsonschema.ValidationError:
        pass
    else:
        raise AssertionError("resolve schema must require explicit confirmation")

    # resolve is registered through the supervised write route; exercise the
    # same canonical entry point used to project write-tool descriptors.
    write = canonical_unity_write_tool_input_schema("vrcforge_resolve_interrupted_apply_recovery")
    jsonschema.validate({"recoveryId": "recovery-1", "confirmResolved": True}, write)
    assert write["properties"]["recoveryId"]["minLength"] == 1
    assert write["anyOf"] == resolve["anyOf"]
