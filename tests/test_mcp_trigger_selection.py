from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from mcp_trigger_selection import SelectionReceiptAuthority, plan_mcp_tool_selection, tools_for_exposure_layer


@dataclass
class Response:
    text: str


TOOLS = [{
    "name": "vrc_get_gameobject",
    "description": (
        "When to use: Read one object.\n"
        "When NOT to use: Do not use for unrelated questions.\n"
        "Negative example: Explain GameObjects without reading the project."
    ),
    "annotations": {"readOnlyHint": True},
    "_meta": {"exposureLayer": "planning"},
}]
WRITE_TOOL = {
    "name": "vrc_create_gameobject",
    "description": (
        "When to use: Create an approved object.\n"
        "When NOT to use: Do not use during planning.\n"
        "Negative example: Explain GameObjects without changing the project."
    ),
    "annotations": {"readOnlyHint": False},
    "_meta": {"exposureLayer": "execution"},
}
RECEIPT_BINDING = {
    "provider": "configured-provider",
    "model": "configured-model",
    "config_digest": "a" * 64,
    "resolved_api_type": "responses",
}


def test_selection_only_metadata_binds_provider_model_and_frozen_tools() -> None:
    result = plan_mcp_tool_selection(
        "Read Main Camera",
        TOOLS,
        provider="configured-provider",
        model="configured-model",
        request_text=lambda _prompt: Response('{"toolCalls":["vrc_get_gameobject"]}'),
    )
    assert result["toolCalls"] == ["vrc_get_gameobject"]
    assert result["providerEvidence"]["source"] == "dashboard-llm-plan"
    assert result["providerEvidence"]["selectionOnly"] is True
    assert result["providerEvidence"]["toolsExecuted"] is False
    assert len(result["providerEvidence"]["visibleToolsHash"]) == 64


def test_process_owned_receipt_binds_request_response_and_is_one_use() -> None:
    authority = SelectionReceiptAuthority()
    result = plan_mcp_tool_selection(
        "Read Main Camera",
        TOOLS,
        provider="configured-provider",
        model="configured-model",
        request_text=lambda _prompt: Response('{"toolCalls":["vrc_get_gameobject"]}'),
    )
    result["providerEvidence"] = authority.issue(
        "Read Main Camera",
        TOOLS,
        result,
        provider="configured-provider",
        model="configured-model",
        config_digest="a" * 64,
        resolved_api_type="responses",
    )
    assert authority.verify_and_consume("Read Main Camera", TOOLS, result, **RECEIPT_BINDING) is True
    assert authority.verify_and_consume("Read Main Camera", TOOLS, result, **RECEIPT_BINDING) is False


def test_process_owned_receipt_rejects_tamper_without_consuming_valid_receipt() -> None:
    authority = SelectionReceiptAuthority()
    result = {"toolCalls": ["vrc_get_gameobject"]}
    result["providerEvidence"] = authority.issue(
        "Read Main Camera",
        TOOLS,
        result,
        provider="configured-provider",
        model="configured-model",
        config_digest="a" * 64,
        resolved_api_type="responses",
    )
    result["toolCalls"] = []
    assert authority.verify_and_consume("Read Main Camera", TOOLS, result, **RECEIPT_BINDING) is False
    result["toolCalls"] = ["vrc_get_gameobject"]
    assert authority.verify_and_consume("Read Main Camera", TOOLS, result, **RECEIPT_BINDING) is True


def test_process_owned_receipt_rejects_changed_provider_configuration_without_consuming() -> None:
    authority = SelectionReceiptAuthority()
    result = {"toolCalls": ["vrc_get_gameobject"]}
    result["providerEvidence"] = authority.issue(
        "Read Main Camera",
        TOOLS,
        result,
        provider="configured-provider",
        model="configured-model",
        config_digest="a" * 64,
        resolved_api_type="responses",
    )
    changed = {**RECEIPT_BINDING, "config_digest": "b" * 64}
    assert authority.verify_and_consume("Read Main Camera", TOOLS, result, **changed) is False
    assert authority.verify_and_consume("Read Main Camera", TOOLS, result, **RECEIPT_BINDING) is True


def test_exposure_layer_filters_writes_and_is_bound_into_receipts() -> None:
    visible = [*TOOLS, WRITE_TOOL]
    assert [tool["name"] for tool in tools_for_exposure_layer(visible, "planning")] == ["vrc_get_gameobject"]
    assert {tool["name"] for tool in tools_for_exposure_layer(visible, "execution")} == {
        "vrc_get_gameobject",
        "vrc_create_gameobject",
    }

    authority = SelectionReceiptAuthority()
    result = {"toolCalls": ["vrc_create_gameobject"]}
    result["providerEvidence"] = authority.issue(
        "Create an object",
        visible,
        result,
        provider="configured-provider",
        model="configured-model",
        config_digest="a" * 64,
        resolved_api_type="responses",
        exposure_layer="execution",
    )
    assert authority.verify_and_consume(
        "Create an object",
        visible,
        result,
        **RECEIPT_BINDING,
        exposure_layer="planning",
    ) is False
    assert authority.verify_and_consume(
        "Create an object",
        visible,
        result,
        **RECEIPT_BINDING,
        exposure_layer="execution",
    ) is True


def test_dashboard_wrapper_uses_saved_config_tool_free_structured_probe_and_receipt(monkeypatch) -> None:
    import dashboard_server

    config = dashboard_server.DashboardApiConfig(
        provider="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_type="responses",
    )
    observed: dict[str, object] = {}

    def probe(actual_config, prompt: str, *, structured: bool = False) -> str:
        observed.update(config=actual_config, prompt=prompt, structured=structured)
        return '{"toolCalls":["vrc_get_gameobject"]}'

    monkeypatch.setattr(dashboard_server, "DASHBOARD_API_CONFIG", config)
    monkeypatch.setattr(dashboard_server, "_run_provider_text_probe", probe)
    monkeypatch.setattr(dashboard_server, "MCP_TRIGGER_SELECTION_RECEIPTS", SelectionReceiptAuthority())

    result = dashboard_server.mcp_trigger_selection_planner("Read Main Camera", TOOLS)

    assert observed["config"] is config
    assert observed["structured"] is True
    assert "Frozen MCP tools" in str(observed["prompt"])
    assert result["providerEvidence"]["source"] == "dashboard-selection-receipt"
    assert dashboard_server.verify_mcp_trigger_selection_receipt("Read Main Camera", TOOLS, result) is True
    assert dashboard_server.verify_mcp_trigger_selection_receipt("Read Main Camera", TOOLS, result) is False


def test_dashboard_wrapper_rejects_receipt_after_saved_config_changes(monkeypatch) -> None:
    import dashboard_server

    original = dashboard_server.DashboardApiConfig(
        provider="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_type="responses",
    )
    changed = dashboard_server.DashboardApiConfig(
        provider="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com/v2",
        model="deepseek-v4-flash",
        api_type="responses",
    )
    monkeypatch.setattr(dashboard_server, "DASHBOARD_API_CONFIG", original)
    monkeypatch.setattr(
        dashboard_server,
        "_run_provider_text_probe",
        lambda *_args, **_kwargs: '{"toolCalls":["vrc_get_gameobject"]}',
    )
    monkeypatch.setattr(dashboard_server, "MCP_TRIGGER_SELECTION_RECEIPTS", SelectionReceiptAuthority())
    result = dashboard_server.mcp_trigger_selection_planner("Read Main Camera", TOOLS)
    monkeypatch.setattr(dashboard_server, "DASHBOARD_API_CONFIG", changed)
    assert dashboard_server.verify_mcp_trigger_selection_receipt("Read Main Camera", TOOLS, result) is False


def test_app_selection_routes_require_app_session_and_consume_receipt(monkeypatch) -> None:
    import dashboard_server

    config = dashboard_server.DashboardApiConfig(
        provider="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_type="responses",
    )
    monkeypatch.setattr(dashboard_server, "DASHBOARD_API_CONFIG", config)
    monkeypatch.setattr(
        dashboard_server,
        "_run_provider_text_probe",
        lambda *_args, **_kwargs: '{"toolCalls":["vrc_get_gameobject"]}',
    )
    monkeypatch.setattr(dashboard_server, "MCP_TRIGGER_SELECTION_RECEIPTS", SelectionReceiptAuthority())
    monkeypatch.setattr(dashboard_server, "APP_AUTH_REQUIRED", True)
    monkeypatch.setattr(dashboard_server, "APP_SESSION_TOKEN", "selection-route-test-token")
    client = TestClient(dashboard_server.app)
    payload = {"message": "Read Main Camera", "visibleTools": TOOLS}

    assert client.post("/api/app/provider/mcp-selection", json=payload).status_code == 401
    headers = {"Authorization": "Bearer selection-route-test-token"}
    issue = client.post("/api/app/provider/mcp-selection", json=payload, headers=headers)
    assert issue.status_code == 200
    verify_payload = {**payload, "result": issue.json()}
    verified = client.post(
        "/api/app/provider/mcp-selection/verify",
        json=verify_payload,
        headers=headers,
    )
    assert verified.status_code == 200
    assert verified.json()["accepted"] is True
    replay = client.post(
        "/api/app/provider/mcp-selection/verify",
        json=verify_payload,
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["accepted"] is False


@pytest.mark.parametrize(
    "response",
    [
        '{"toolCalls":["unknown"]}',
        '{"toolCalls":["vrc_get_gameobject","vrc_get_gameobject"]}',
        '{"toolCalls":"vrc_get_gameobject"}',
    ],
)
def test_selection_only_rejects_unknown_multi_or_malformed_calls(response: str) -> None:
    with pytest.raises(RuntimeError):
        plan_mcp_tool_selection(
            "probe",
            TOOLS,
            provider="configured-provider",
            model="configured-model",
            request_text=lambda _prompt: Response(response),
        )
