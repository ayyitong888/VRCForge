import math

import pytest

from prepared_unity_execution import (
    MAX_PREPARED_CALLS,
    PREPARED_CALLS_KEY,
    PREPARED_UNITY_EXECUTION_ARGUMENT_KEY,
    build_prepared_execution_plan,
    install_prepared_calls,
    prepared_call,
    prepared_evidence,
)


def test_install_seals_exact_calls_and_evidence_without_mutating_inputs():
    arguments = {"projectPath": "C:/Project"}
    calls = [("vrc_create_gameobject", {"name": "Child"})]
    sealed = install_prepared_calls(arguments, calls, {"source": "preview"})
    arguments["projectPath"] = "changed"
    calls[0][1]["name"] = "changed"
    assert build_prepared_execution_plan(sealed) == [("vrc_create_gameobject", {"name": "Child"})]
    assert PREPARED_UNITY_EXECUTION_ARGUMENT_KEY in sealed
    assert prepared_evidence(sealed) == {"source": "preview"}


def test_caller_injected_internal_key_is_rejected():
    with pytest.raises(ValueError, match="reserved"):
        install_prepared_calls({PREPARED_UNITY_EXECUTION_ARGUMENT_KEY: {}}, [("vrc_x", {})], {})


@pytest.mark.parametrize("calls", [[], [("", {})], [("vrc_x", [])], [("vrc_x", {"x": math.nan})], [("vrc_x", {})] * (MAX_PREPARED_CALLS + 1)])
def test_invalid_or_oversized_calls_are_rejected(calls):
    with pytest.raises(ValueError):
        install_prepared_calls({}, calls, {})


def test_non_json_arguments_or_evidence_are_rejected():
    with pytest.raises(ValueError):
        install_prepared_calls({"bad": {1, 2}}, [("vrc_x", {})], {})
    with pytest.raises(ValueError):
        install_prepared_calls({}, [("vrc_x", {})], {"bad": math.inf})


@pytest.mark.parametrize("field", ["callsSha256", "evidenceSha256", "sealSha256"])
def test_tampered_seal_is_rejected(field):
    sealed = install_prepared_calls({}, [("vrc_x", {"value": 1})], {"proof": True})
    sealed[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY][field] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        build_prepared_execution_plan(sealed)


def test_tampered_call_content_is_rejected_and_returned_calls_are_independent():
    sealed = install_prepared_calls({}, [("vrc_x", {"nested": {"value": 1}})], {})
    first = prepared_call(sealed)
    first[1]["nested"]["value"] = 9
    assert prepared_call(sealed) == ("vrc_x", {"nested": {"value": 1}})
    sealed[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY][PREPARED_CALLS_KEY][0]["arguments"]["nested"]["value"] = 2
    with pytest.raises(ValueError, match="calls digest"):
        prepared_call(sealed)


def test_invalid_indexes_are_rejected():
    sealed = install_prepared_calls({}, [("vrc_x", {})], {})
    with pytest.raises(ValueError, match="index"):
        prepared_call(sealed, 1)
    with pytest.raises(ValueError, match="index"):
        prepared_call(sealed, True)
    with pytest.raises(ValueError, match="index"):
        prepared_call(sealed, -1)


def test_unknown_seal_or_call_fields_are_rejected():
    sealed = install_prepared_calls({}, [("vrc_x", {})], {})
    sealed[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY]["unknown"] = True
    with pytest.raises(ValueError, match="fields"):
        build_prepared_execution_plan(sealed)

    sealed = install_prepared_calls({}, [("vrc_x", {})], {})
    sealed[PREPARED_UNITY_EXECUTION_ARGUMENT_KEY][PREPARED_CALLS_KEY][0]["unknown"] = True
    with pytest.raises(ValueError, match="fields"):
        build_prepared_execution_plan(sealed)
