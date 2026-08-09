from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

import dashboard_server
from mcp_trigger_selection import SelectionReceiptAuthority, plan_mcp_tool_selection, tools_for_exposure_layer
from provider_configuration_service import ProviderApiConfig
from provider_test_integration_service import ProviderTestIntegrationService, ProviderTestServicePorts


@dataclass
class Response:
    text: str


class FakeSelectionProbe:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[ProviderApiConfig, str, bool]] = []

    def probe(
        self,
        config: ProviderApiConfig,
        prompt: str,
        *,
        structured: bool = False,
    ) -> str:
        self.calls.append((config, prompt, structured))
        return self.response


def selection_service(probe: FakeSelectionProbe) -> ProviderTestIntegrationService:
    return ProviderTestIntegrationService(
        ProviderTestServicePorts(
            resolve_api_request=lambda request: request,
            normalize_provider_name=lambda provider: provider,
            provider_display_name=lambda provider: provider,
            provider_config_descriptor=lambda _config: {
                "apiType": "responses",
                "resolvedApiType": "responses",
            },
            provider_requires_api_key=lambda _provider: True,
            extract_json_block=lambda text: text,
        ),
        probe,
    )


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


def test_typed_selection_composition_uses_tool_free_structured_probe_and_receipt() -> None:
    config = ProviderApiConfig(
        provider="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_type="responses",
    )
    probe = FakeSelectionProbe('{"toolCalls":["vrc_get_gameobject"]}')
    service = selection_service(probe)
    authority = SelectionReceiptAuthority()
    binding = dashboard_server.mcp_trigger_selection_config_binding(config)
    result = plan_mcp_tool_selection(
        "Read Main Camera",
        TOOLS,
        provider=config.provider,
        model=config.model,
        request_text=lambda prompt: service.probe_text(config, prompt, structured=True),
    )
    result["providerEvidence"] = authority.issue(
        "Read Main Camera",
        TOOLS,
        result,
        provider=binding[0],
        model=binding[1],
        config_digest=binding[2],
        resolved_api_type=binding[3],
    )

    assert probe.calls[0][0] is config
    assert probe.calls[0][2] is True
    assert "Frozen MCP tools" in probe.calls[0][1]
    assert result["providerEvidence"]["source"] == "dashboard-selection-receipt"
    binding_kwargs = {
        "provider": binding[0],
        "model": binding[1],
        "config_digest": binding[2],
        "resolved_api_type": binding[3],
    }
    assert authority.verify_and_consume("Read Main Camera", TOOLS, result, **binding_kwargs) is True
    assert authority.verify_and_consume("Read Main Camera", TOOLS, result, **binding_kwargs) is False


def test_typed_selection_receipt_rejects_changed_provider_configuration() -> None:
    original = ProviderApiConfig(
        provider="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        api_type="responses",
    )
    changed = ProviderApiConfig(
        provider="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com/v2",
        model="deepseek-v4-flash",
        api_type="responses",
    )
    probe = FakeSelectionProbe('{"toolCalls":["vrc_get_gameobject"]}')
    service = selection_service(probe)
    authority = SelectionReceiptAuthority()
    original_binding = dashboard_server.mcp_trigger_selection_config_binding(original)
    changed_binding = dashboard_server.mcp_trigger_selection_config_binding(changed)
    result = plan_mcp_tool_selection(
        "Read Main Camera",
        TOOLS,
        provider=original.provider,
        model=original.model,
        request_text=lambda prompt: service.probe_text(original, prompt, structured=True),
    )
    result["providerEvidence"] = authority.issue(
        "Read Main Camera",
        TOOLS,
        result,
        provider=original_binding[0],
        model=original_binding[1],
        config_digest=original_binding[2],
        resolved_api_type=original_binding[3],
    )
    assert authority.verify_and_consume(
        "Read Main Camera",
        TOOLS,
        result,
        provider=changed_binding[0],
        model=changed_binding[1],
        config_digest=changed_binding[2],
        resolved_api_type=changed_binding[3],
    ) is False


def test_app_selection_routes_require_app_session_and_consume_typed_receipt(monkeypatch) -> None:
    config = dashboard_server.PROVIDER_CONFIGURATION.current_api_config()
    binding = dashboard_server.mcp_trigger_selection_config_binding(config)
    authority = SelectionReceiptAuthority()
    result = {"toolCalls": ["vrc_get_gameobject"]}
    result["providerEvidence"] = authority.issue(
        "Read Main Camera",
        TOOLS,
        result,
        provider=binding[0],
        model=binding[1],
        config_digest=binding[2],
        resolved_api_type=binding[3],
    )
    monkeypatch.setattr(dashboard_server, "MCP_TRIGGER_SELECTION_RECEIPTS", authority)
    monkeypatch.setattr(dashboard_server, "APP_AUTH_REQUIRED", True)
    monkeypatch.setattr(dashboard_server, "APP_SESSION_TOKEN", "selection-route-test-token")
    client = TestClient(dashboard_server.app)
    payload = {"message": "Read Main Camera", "visibleTools": TOOLS}

    assert client.post("/api/app/provider/mcp-selection", json=payload).status_code == 401
    headers = {"Authorization": "Bearer selection-route-test-token"}
    verify_payload = {**payload, "result": result}
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


def test_app_selection_request_accepts_the_bounded_full_gateway_catalog() -> None:
    request = dashboard_server.McpSelectionAcceptanceRequest(
        message="Inspect the selected avatar.",
        visibleTools=[
            {
                "name": f"vrcforge_fixture_{index}",
                "description": "fixture",
            }
            for index in range(128)
        ],
    )

    assert len(request.visible_tools) == 128
