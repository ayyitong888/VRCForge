"""Local execution data must retain exact values; privacy redaction belongs to log export."""
import json

import pytest

from runtime_planner_service import (
    _sanitize_complete_planner_value,
    redact_sensitive,
    sanitize_planner_observation_text,
)
from tests.test_runtime_planner_service import service
from agent_tool_result_reader import bind_tool_result_context, read_tool_result, result_continuation


KEY_FIXTURES = (
    "sk-proj-fixture123456",
    "ghp_fixture123456",
    "github_pat_fixture123456",
    "hf_fixture123456",
    "xoxb-fixture123456",
    "AKIA1234567890ABCDEF",
    "AIza" + "A" * 24,
)
JWT_FIXTURE = "eyJmaXh0dXJlMTIz.abcdefghijklmno.pqrstuvwxyzABCD"


@pytest.mark.parametrize("fragment", (*KEY_FIXTURES, JWT_FIXTURE))
def test_credential_shaped_fragment_inside_complete_identifier_is_preserved(fragment):
    identifier = "opaque-random-" + fragment + "-continuation"
    assert sanitize_planner_observation_text(identifier, None) == identifier
    original = {
        "executionTarget": {"schema": "vrcforge.execution_target_ref.v1", "ref": identifier},
        "resultRef": identifier,
        "jobId": identifier,
        "actionId": identifier,
        "nested": [{"operationId": identifier}],
    }
    assert _sanitize_complete_planner_value(redact_sensitive(original)) == original


@pytest.mark.parametrize("credential", (*KEY_FIXTURES, JWT_FIXTURE))
@pytest.mark.parametrize("wrapper", ("{}", 'value="{}"', "({}), next", "凭据：{}；结束"))
def test_local_observations_preserve_standalone_values(credential, wrapper):
    projected = sanitize_planner_observation_text(wrapper.format(credential), None)
    assert projected == wrapper.format(credential)


@pytest.mark.parametrize("field", ("api_key", "token", "authorization", "password", "secret"))
def test_local_field_names_do_not_rewrite_values(field):
    identifier = "opaque-random-hf_fixture123456-continuation"
    projected = _sanitize_complete_planner_value(redact_sensitive({"nested": [{field: identifier}]}))
    assert projected == {"nested": [{field: identifier}]}


def test_local_bearer_and_assignment_text_remains_exact():
    identifier = "opaque-random-hf_fixture123456-continuation"
    for text in ("Bearer " + identifier, "api_key=" + identifier, "token=" + identifier):
        projected = sanitize_planner_observation_text(text, None)
        assert projected == text


@pytest.mark.parametrize("native", (False, True))
def test_bind_target_reference_reaches_model_observation_unchanged(native):
    reference = "opaque-random-hf_fixture123456-continuation"
    step = {
        "tool": "vrcforge_bind_execution_target",
        "kind": "skill",
        "status": "executed",
        "result": {
            "ok": True,
            "executionTarget": {"schema": "vrcforge.execution_target_ref.v1", "ref": reference},
        },
    }
    observation = service()._llm_loop_step_observation(step, native_contract=native)
    assert reference in observation
    assert "<redacted>" not in observation


def test_local_observation_preserves_ref_and_adjacent_field_value():
    reference = "opaque-random-hf_fixture123456-continuation"
    observation = service()._llm_loop_step_observation({
        "tool": "fixture_read", "status": "executed",
        "result": {"message": "Continue using " + reference + "; api_key=standalone-secret"},
    })
    assert reference in observation
    assert "api_key=standalone-secret" in observation
    assert "<redacted>" not in observation


@pytest.mark.parametrize("native", (False, True))
def test_internal_model_receives_executable_file_continuation_unchanged(native):
    root = "C:/Users/Local Workspace/" + "long-folder/" * 14
    arguments = {"projectPath": root, "path": "Assets/Data/fixture.txt",
                 "textOffset": 80, "snapshotDigest": "fixture-snapshot"}
    observation = service()._llm_loop_step_observation({
        "tool": "vrcforge_read_text_file", "kind": "skill", "status": "executed",
        "result": {"ok": True, "path": root + arguments["path"], "text": "source body",
                   "hasMore": True, "nextRequest": {"tool": "vrcforge_read_text_file", "arguments": arguments}},
    }, native_contract=native)
    assert root in observation
    assert arguments["path"] in observation
    assert "source body" in observation


@pytest.mark.parametrize("collection", ("rows", "mapping"))
def test_retained_reader_nested_values_map_keys_and_next_request_preserve_refs(collection):
    reference = "opaque-random-hf_fixture123456-continuation"
    value = {"executionTarget": {"schema": "vrcforge.execution_target_ref.v1", "ref": reference},
             "jobId": reference, "actionId": reference, "resultRef": reference,
             "nested": [{"operationId": reference}], "token": "standalone-secret"}
    step = {"index": 0, "actionId": "read-fixture", "tool": "vrcforge_get_gameobject",
            "status": "executed", "result": {
                "rows": [value, value], "mapping": {reference: value, "tail": value}}}
    step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize_planner_observation_text)
    with bind_tool_result_context("session", "turn", "project", [step]):
        first = read_tool_result({"resultRef": step["resultRead"]["resultRef"],
                                  "jsonPointer": "/" + collection, "limit": 1},
                                 sanitize=sanitize_planner_observation_text)
        assert first["hasMore"]
        assert first["items"][0]["value"]["executionTarget"]["ref"] == reference
        if collection == "mapping":
            assert first["items"][0]["jsonPointer"] == "/mapping/" + reference
        request = first["nextRequest"]["arguments"]
        assert request["resultRef"] == step["resultRead"]["resultRef"]
        second = read_tool_result(request, sanitize=sanitize_planner_observation_text)
        assert second["items"][0]["value"]["jobId"] == reference
        assert "standalone-secret" not in json.dumps([first, second])


def test_retained_reader_preserves_opaque_map_selector_in_json_pointer():
    reference = "opaque-random-hf_fixture123456-continuation"
    step = {"index": 0, "actionId": "read-fixture", "tool": "vrcforge_get_gameobject",
            "status": "executed", "result": {"mapping": {reference: {"jobId": reference, "actionId": reference}}}}
    step["resultRead"] = result_continuation("session", "turn", "project", step, sanitize_planner_observation_text)
    with bind_tool_result_context("session", "turn", "project", [step]):
        page = read_tool_result({"resultRef": step["resultRead"]["resultRef"],
                                 "jsonPointer": "/mapping/" + reference, "limit": 1}, sanitize=sanitize_planner_observation_text)
        request = page["nextRequest"]["arguments"]
        assert request["jsonPointer"] == "/mapping/" + reference
        next_page = read_tool_result(request, sanitize=sanitize_planner_observation_text)
    assert page["items"][0]["value"] == reference
    assert next_page["items"][0]["value"] == reference
