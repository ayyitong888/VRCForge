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
            False,
        ),
        (
            SimpleNamespace(provider="openai", api_key="", base_url="", model="gpt-4o"),
            SimpleNamespace(provider="", api_key="", base_url="", model="", enabled=False),
            False,
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
