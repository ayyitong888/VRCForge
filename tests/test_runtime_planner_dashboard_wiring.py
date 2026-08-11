from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import dashboard_server
from provider_configuration_service import ProviderApiConfig
from runtime_planner_service import EXPOSURE_LAYER_EXECUTION, EXPOSURE_LAYER_PLANNING
from vrchat_blendshape_agent import LlmPlanResponse


def fixture_config() -> ProviderApiConfig:
    return ProviderApiConfig(
        provider="custom",
        api_key="fixture-secret-key",
        base_url="http://127.0.0.1:11434/v1",
        model="fixture-model",
        api_type="chat_completions",
        thinking_level="medium",
    )


def test_turn_binding_freezes_one_provider_config_and_resets_it_in_finally() -> None:
    binding = dashboard_server._RuntimePlannerProviderTurnBinding()
    config = fixture_config()

    with patch.object(
        dashboard_server.PROVIDER_CONFIGURATION,
        "current_api_config",
        return_value=config,
    ) as current_api_config:
        with pytest.raises(RuntimeError, match="stop"):
            with binding.bind(
                {
                    "provider": "custom",
                    "model": "fixture-model",
                    "_requestedContextLimit": 64_000,
                }
            ) as metadata:
                assert metadata.verified_context_limit == 64_000
                assert metadata.planner_label.endswith("fixture-model")
                assert "fixture-secret-key" not in repr(metadata)
                assert binding.current_config() is config
                raise RuntimeError("stop")

    current_api_config.assert_called_once_with()
    with pytest.raises(RuntimeError, match="not bound"):
        binding.current_config()


def test_model_and_compactor_share_the_exact_bound_config_without_projecting_secret() -> None:
    binding = dashboard_server._RuntimePlannerProviderTurnBinding()
    model = dashboard_server._RuntimePlannerModel(binding)
    compactor = dashboard_server._RuntimePlannerCompactor(binding)
    config = fixture_config()
    settings = SimpleNamespace()
    compact_call: dict[str, object] = {}

    def fake_compact_context(history, **kwargs):
        compact_call.update({"history": history, **kwargs})
        return {"summary": "bounded"}

    with (
        patch.object(
            dashboard_server.PROVIDER_CONFIGURATION,
            "current_api_config",
            return_value=config,
        ) as current_api_config,
        patch.object(
            dashboard_server.PROVIDER_TEXT_PROBE,
            "probe_settings",
            return_value=settings,
        ) as probe_settings,
        patch.object(
            dashboard_server,
            "request_llm_plan_with_metadata",
            return_value=LlmPlanResponse(
                text='{"action":"reply","reply":"done"}',
                reasoning={"itemCount": 1, "summary": "fixture"},
                usage={"exact": True, "inputTokens": 4, "outputTokens": 2},
            ),
        ),
        patch.object(dashboard_server, "compact_context", side_effect=fake_compact_context),
    ):
        with binding.bind(
            {
                "provider": "custom",
                "model": "fixture-model",
                "_requestedContextLimit": 64_000,
            }
        ) as metadata:
            model_result = model.plan("prompt")
            compact_result = compactor.compact(
                ({"role": "user", "text": "history"},),
                {"targetTokens": 100, "realContextLimit": metadata.verified_context_limit},
            )

    current_api_config.assert_called_once_with()
    assert probe_settings.call_count == 2
    assert all(call.args[0] is config for call in probe_settings.call_args_list)
    assert model_result.planner_label.endswith("fixture-model")
    assert model_result.reasoning == {"itemCount": 1, "summary": "fixture"}
    assert compact_result == {"summary": "bounded"}
    assert compact_call["provider"] == "custom"
    assert compact_call["model"] == "fixture-model"
    assert "fixture-secret-key" not in repr(model_result)
    assert "fixture-secret-key" not in repr(compact_call)


def test_model_worker_routes_three_visual_journey_samples_through_one_turn_owner() -> None:
    binding = dashboard_server._RuntimePlannerProviderTurnBinding()
    model = dashboard_server._RuntimePlannerModel(binding)
    config = fixture_config()
    prompts: list[str] = []
    settings_seen: list[object] = []
    responses = iter(
        [
            '{"action":"write","write_tool":"vrcforge_capture_multi_screenshot","write_params":{}}',
            '{"action":"skill","skill_tool":"vrcforge_vision_audit_multi","skill_params":{"captureReceipt":"receipt"}}',
            '{"action":"reply","reply":"done","completion_claim":{"satisfied":true,"evidence_action_ids":["capture","audit"]}}',
        ]
    )

    def request(settings, prompt: str, *, stream_callback=None) -> LlmPlanResponse:
        settings_seen.append(settings)
        prompts.append(prompt)
        return LlmPlanResponse(text=next(responses), reasoning={}, usage={})

    turn_context = {
        "sessionId": "visual-worker-session",
        "turnId": "visual-worker-turn",
        "clientTurnId": "visual-worker-client-turn",
    }
    with (
        patch.object(
            dashboard_server.PROVIDER_CONFIGURATION,
            "current_api_config",
            return_value=config,
        ) as current_api_config,
        patch.object(
            dashboard_server,
            "request_llm_plan_with_metadata",
            side_effect=request,
        ) as request_model,
        patch.object(
            type(dashboard_server.AGENT_GATEWAY.runtime_sessions),
            "stream_context",
            return_value=turn_context,
        ),
        patch.object(
            type(dashboard_server.AGENT_GATEWAY.runtime_sessions),
            "cancel_requested",
            return_value=False,
        ),
        patch.object(dashboard_server.EVENT_BUS, "broadcast_from_sync"),
    ):
        with binding.bind(
            {
                "provider": "custom",
                "model": "fixture-model",
                "_requestedContextLimit": 64_000,
            }
        ):
            results = [model.plan(f"visual-journey-sample-{index}") for index in range(3)]

    current_api_config.assert_called_once_with()
    assert request_model.call_count == 3
    assert prompts == [
        "visual-journey-sample-0",
        "visual-journey-sample-1",
        "visual-journey-sample-2",
    ]
    assert len(settings_seen) == 3
    assert all(settings.llm_provider == config.provider for settings in settings_seen)
    assert all(settings.llm_model == config.model for settings in settings_seen)
    assert all(result.planner_label.endswith(config.model) for result in results)
    assert model.active_call_count() == 0


def test_catalog_filters_only_visible_tools_and_keeps_full_routing_metadata() -> None:
    catalog = dashboard_server._RuntimePlannerCatalog()
    planning = catalog.read(EXPOSURE_LAYER_PLANNING)
    execution = catalog.read(EXPOSURE_LAYER_EXECUTION)
    gateway = dashboard_server.AGENT_GATEWAY
    config = gateway.ensure_config()

    expected_planning = {
        name
        for name, tool in gateway._tools.items()
        if gateway._tool_runtime_visible(tool, config, EXPOSURE_LAYER_PLANNING)
        and dashboard_server._runtime_planner_provider_capability_visible(tool)
    }
    expected_execution = {
        name
        for name, tool in gateway._tools.items()
        if gateway._tool_runtime_visible(tool, config, EXPOSURE_LAYER_EXECUTION)
        and dashboard_server._runtime_planner_provider_capability_visible(tool)
    }
    routable_writes = {
        name
        for name in gateway._write_handlers
        if name not in gateway._tools
        and name not in dashboard_server.WRAPPER_ONLY_WRITE_TARGETS
    }
    visible_execution_writes = {
        name
        for name, handler in gateway._write_handlers.items()
        if name in routable_writes
        and config.allow_write_requests
        and gateway._write_handler_visible(handler, config, EXPOSURE_LAYER_EXECUTION)
    }
    expected_execution.update(visible_execution_writes)
    expected_routable = set(gateway._tools) | routable_writes
    expected_skills = {
        str(item.get("name") or "")
        for item in gateway.skills.build_skill_registry(config, EXPOSURE_LAYER_EXECUTION)["skills"]
        if str(item.get("name") or "")
    }

    assert {item.name for item in planning.visible_tools} == expected_planning
    assert {item.name for item in execution.visible_tools} == expected_execution
    assert {item.name for item in planning.routable_tools} == expected_routable
    assert {item.name for item in execution.routable_tools} == expected_routable
    assert {item.name for item in planning.skills} == expected_skills
    assert {item.name for item in execution.skills} == expected_skills
    planning_routable = {item.name: item for item in planning.routable_tools}
    assert all(planning_routable[name].write for name in routable_writes)
    assert not routable_writes.intersection(item.name for item in planning.visible_tools)


@pytest.mark.parametrize(
    ("provider_config", "vision_config", "expected_visible"),
    [
        (
            SimpleNamespace(
                provider="openai",
                api_key="fixture-key",
                base_url="",
                model="gpt-4o",
            ),
            SimpleNamespace(provider="", api_key="", base_url="", model="", enabled=False),
            True,
        ),
        (
            SimpleNamespace(
                provider="deepseek",
                api_key="fixture-key",
                base_url="",
                model="deepseek-v4-flash",
            ),
            SimpleNamespace(
                provider="anthropic",
                api_key="vision-key",
                base_url="",
                model="claude-sonnet-4",
                enabled=True,
            ),
            True,
        ),
        (
            SimpleNamespace(
                provider="deepseek",
                api_key="fixture-key",
                base_url="",
                model="deepseek-v4-flash",
            ),
            SimpleNamespace(provider="", api_key="", base_url="", model="", enabled=False),
            True,
        ),
        (
            SimpleNamespace(provider="openai", api_key="", base_url="", model="gpt-4o"),
            SimpleNamespace(provider="", api_key="", base_url="", model="", enabled=False),
            False,
        ),
        (
            SimpleNamespace(
                provider="custom",
                api_key="fixture-key",
                base_url="https://future.example/v1",
                model="future-multimodal-model",
            ),
            SimpleNamespace(provider="", api_key="", base_url="", model="", enabled=False),
            True,
        ),
    ],
)
def test_catalog_exposes_visual_audit_for_any_configured_vision_capability(
    provider_config: SimpleNamespace,
    vision_config: SimpleNamespace,
    expected_visible: bool,
) -> None:
    with patch.object(
        dashboard_server.PROVIDER_CONFIGURATION,
        "current_api_config",
        return_value=provider_config,
    ), patch.object(
        dashboard_server.PROVIDER_CONFIGURATION,
        "current_vision_config",
        return_value=vision_config,
    ):
        planning = dashboard_server._RuntimePlannerCatalog().read(
            EXPOSURE_LAYER_PLANNING
        )

    visible = {item.name for item in planning.visible_tools}
    routable = {item.name for item in planning.routable_tools}
    assert ("vrcforge_vision_audit_multi" in visible) is expected_visible
    assert "vrcforge_vision_audit_multi" in routable
