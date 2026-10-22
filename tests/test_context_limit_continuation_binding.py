from unittest.mock import patch

import pytest

import dashboard_server
from provider_configuration_service import ProviderApiConfig
from runtime_planner_service import RuntimePlannerService


@pytest.mark.parametrize("requested, expected", [(None, 500_000), (800_000, 500_000), (250_000, 250_000)])
def test_continuation_uses_host_model_capacity_when_client_limit_missing(requested, expected):
    # A saved question may predate frontend model discovery. It must still
    # participate in the same backend pre-sampling guard as ordinary turns.
    config = ProviderApiConfig(provider="chatgpt", api_key="fixture-account", model="gpt-6.1-sol", base_url="")
    binding = dashboard_server._RuntimePlannerProviderTurnBinding()
    request = {"provider": config.provider, "model": config.model, "_requestedContextLimit": requested}
    planner = RuntimePlannerService(catalog=None, desktop=None, turn=binding)
    with patch.object(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config", return_value=config), patch.object(
        dashboard_server.PROVIDER_MODEL_CATALOG, "resolve_context_limit", return_value=500_000, create=True,
    ):
        with planner.bind_turn(request) as metadata:
            assert metadata.verified_context_limit == expected
            guard = planner.native_context_guard({"instructions": "", "messages": [], "tools": []}, context_usage={
                "lastInputTokens": 558_025, "lastPromptEstimatedTokens": 1, "lastUsageExact": True,
            })
            assert guard is not None
            assert guard["contextLimit"] == expected
            assert guard["blocked"] is True


def test_unmatched_client_limit_does_not_override_host_capacity():
    config = ProviderApiConfig(provider="custom", api_key="fixture", model="fixture-model", base_url="")
    binding = dashboard_server._RuntimePlannerProviderTurnBinding()
    with patch.object(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config", return_value=config), patch.object(
        dashboard_server.PROVIDER_MODEL_CATALOG, "resolve_context_limit", return_value=128_000, create=True,
    ):
        with binding.bind({"_requestedContextLimit": 1_000_000}) as metadata:
            assert metadata.verified_context_limit == 128_000


def test_missing_client_limit_compacts_before_first_native_request_and_keeps_user_message():
    from tests.test_native_context_compaction import Compactor, fixture

    compactor = Compactor()
    state, planner, model, turn = fixture(compactor, current="Keep the accepted model unchanged.")
    planner._turn = dashboard_server._RuntimePlannerProviderTurnBinding()
    config = ProviderApiConfig(provider="custom", api_key="fixture", model="fixture-model", base_url="")
    with patch.object(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config", return_value=config), patch.object(
        dashboard_server.PROVIDER_MODEL_CATALOG, "resolve_context_limit", return_value=8_000,
    ):
        with planner.bind_turn({"provider": config.provider, "model": config.model, "_requestedContextLimit": None}):
            result = planner.plan_agent_turn("Keep the accepted model unchanged.", {}, {}, native_turn=turn, context_usage={})
    assert result["nextStep"] == "done"
    assert len(compactor.calls) == 1
    assert len(model.requests) == 1
    assert turn.compaction["applied"] is True
    assert any(m.get("content") == "Keep the accepted model unchanged." for m in model.requests[0]["messages"])
