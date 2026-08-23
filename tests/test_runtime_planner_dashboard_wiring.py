from __future__ import annotations

from types import SimpleNamespace
import threading
import time
from unittest.mock import patch

import pytest

import dashboard_server
from provider_configuration_service import ProviderApiConfig
from runtime_planner_service import (
    EXPOSURE_LAYER_EXECUTION,
    EXPOSURE_LAYER_PLANNING,
    PlannerCatalogSnapshot,
    PlannerModelResult,
    PlannerTool,
)
from profiled_tool_registry import CapabilityProfile
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


def test_internal_tool_index_lists_only_tools_visible_in_the_requested_planner_layer() -> None:
    visible = PlannerTool(
        name="unity_status",
        runtime_name="vrcforge_unity_status",
        description="Inspect Unity status.",
        category="read/debug",
        block="unity/diagnostics",
    )
    snapshot = PlannerCatalogSnapshot(
        visible_tools=(visible,),
        routable_tools=(
            visible,
            PlannerTool(
                name="unity_get_compile_errors",
                runtime_name="vrcforge_get_compile_errors",
                description="Unavailable in this layer.",
                category="read/debug",
                block="unity/diagnostics",
            ),
        ),
    )

    with patch.object(
        dashboard_server._RuntimePlannerCatalog,
        "read",
        return_value=snapshot,
    ) as read:
        inventory = dashboard_server.build_internal_tool_block_inventory(
            {
                "sessionId": "index-layer-test",
                "exposureLayer": EXPOSURE_LAYER_PLANNING,
                "projectContextActive": False,
            }
        )

    diagnostics = next(
        block for block in inventory["blocks"] if block["name"] == "unity/diagnostics"
    )
    assert diagnostics["toolNames"] == ["unity_status"]
    read.assert_called_once_with(
        EXPOSURE_LAYER_PLANNING,
        project_context_active=False,
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

    def request(
        settings,
        prompt: str,
        *,
        stream_callback=None,
        stream_activity_callback=None,
        cancel_event=None,
    ) -> LlmPlanResponse:
        settings_seen.append(settings)
        prompts.append(prompt)
        assert callable(stream_callback)
        assert callable(stream_activity_callback)
        assert cancel_event is not None
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


def test_four_cancelled_provider_workers_release_capacity_and_fifth_enters() -> None:
    binding = dashboard_server._RuntimePlannerProviderTurnBinding()
    config = fixture_config()
    calls = 0
    def request(_settings, _prompt, *, stream_callback=None, cancel_event=None, **_kwargs):
        nonlocal calls
        calls += 1
        while not cancel_event.is_set():
            time.sleep(0.001)
        raise RuntimeError("cancelled")
    context = {"sessionId":"s", "turnId":"t", "clientTurnId":"c"}
    with patch.object(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config", return_value=config), \
         patch.object(dashboard_server.PROVIDER_TEXT_PROBE, "probe_settings", return_value=SimpleNamespace()), \
         patch.object(dashboard_server, "request_llm_plan_with_metadata", side_effect=request), \
         patch.object(type(dashboard_server.AGENT_GATEWAY.runtime_sessions), "stream_context", return_value=context), \
         patch.object(type(dashboard_server.AGENT_GATEWAY.runtime_sessions), "cancel_requested", return_value=True):
        with binding.bind({"provider":"custom", "model":"fixture-model"}):
            model = dashboard_server._RuntimePlannerModel(binding)
            for _ in range(5):
                with pytest.raises(dashboard_server.RuntimePlannerProviderCancelledError):
                    model.plan("cancel")
                assert model.active_call_count() == 0
    assert calls == 5


@pytest.mark.parametrize("phase,activity", [("first_byte", False), ("idle", True), ("overall", True)])
def test_provider_deadline_errors_are_structured_and_reclaim_worker(phase: str, activity: bool) -> None:
    binding = dashboard_server._RuntimePlannerProviderTurnBinding()
    config = fixture_config()
    context = {"sessionId":"s", "turnId":"t", "clientTurnId":"c"}
    def request(_settings, _prompt, *, stream_activity_callback=None, cancel_event=None, **_kwargs):
        if activity:
            stream_activity_callback({"kind":"reasoning_activity"})
        while not cancel_event.is_set():
            if activity and phase == "overall":
                stream_activity_callback({"kind":"reasoning_activity"})
            time.sleep(0.005)
        raise RuntimeError("closed")
    with patch.object(dashboard_server.PROVIDER_CONFIGURATION, "current_api_config", return_value=config), \
         patch.object(dashboard_server.PROVIDER_TEXT_PROBE, "probe_settings", return_value=SimpleNamespace()), \
         patch.object(dashboard_server, "request_llm_plan_with_metadata", side_effect=request), \
         patch.object(type(dashboard_server.AGENT_GATEWAY.runtime_sessions), "stream_context", return_value=context), \
         patch.object(type(dashboard_server.AGENT_GATEWAY.runtime_sessions), "cancel_requested", return_value=False):
        with binding.bind({"provider":"custom", "model":"fixture-model"}):
            model = dashboard_server._RuntimePlannerModel(binding)
            model._FIRST_BYTE_TIMEOUT_SECONDS = 0.03
            model._IDLE_TIMEOUT_SECONDS = 0.03
            model._OVERALL_TIMEOUT_SECONDS = 0.05
            with pytest.raises(dashboard_server.RuntimePlannerProviderTimeoutError) as raised:
                model.plan("timeout")
            assert raised.value.code == "provider_timeout"
            assert raised.value.phase == phase
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

    # Planner-facing names are the profile's plain aliases; runtime_name keeps
    # the registered/internal identity used for routing and handler lookup.
    assert {item.runtime_name for item in planning.visible_tools} == expected_planning
    assert {item.runtime_name for item in execution.visible_tools} == expected_execution
    assert {item.runtime_name for item in planning.routable_tools} == expected_routable
    assert {item.runtime_name for item in execution.routable_tools} == expected_routable
    for profile, snapshot in (
        (CapabilityProfile.UNITY_PROJECT, planning),
        (CapabilityProfile.UNITY_PROJECT, execution),
    ):
        aliases: dict[str, set[str]] = {}
        for item in dashboard_server._RUNTIME_PROFILED_TOOL_REGISTRY.project(profile):
            aliases.setdefault(item.internal_name, set()).add(item.model_name)
        assert all(item.name in aliases[item.runtime_name] for item in snapshot.visible_tools)
        assert all(item.name in aliases[item.runtime_name] for item in snapshot.routable_tools)
    assert {item.name for item in planning.skills} == expected_skills
    assert {item.name for item in execution.skills} == expected_skills
    planning_routable = {item.runtime_name: item for item in planning.routable_tools}
    assert all(planning_routable[name].write for name in routable_writes)
    assert not routable_writes.intersection(item.runtime_name for item in planning.visible_tools)


def test_no_project_catalog_exposes_only_general_agent_capabilities() -> None:
    catalog = dashboard_server._RuntimePlannerCatalog()

    planning = catalog.read(
        EXPOSURE_LAYER_PLANNING,
        project_context_active=False,
    )
    execution = catalog.read(
        EXPOSURE_LAYER_EXECUTION,
        project_context_active=False,
    )

    planning_names = {item.name for item in planning.visible_tools}
    execution_names = {item.name for item in execution.visible_tools}
    routable_names = {item.name for item in execution.routable_tools}
    planning_runtime = {item.runtime_name for item in planning.visible_tools}
    execution_runtime = {item.runtime_name for item in execution.visible_tools}
    routable_runtime = {item.runtime_name for item in execution.routable_tools}
    assert planning_runtime <= dashboard_server.RUNTIME_PLANNER_GENERAL_AGENT_TOOLS
    assert execution_runtime <= dashboard_server.RUNTIME_PLANNER_GENERAL_AGENT_TOOLS
    assert routable_runtime <= dashboard_server.RUNTIME_PLANNER_GENERAL_AGENT_TOOLS
    assert "ask_user" in planning_names
    assert "delegate_subagent" in planning_names
    assert "know_yourself" in planning_names
    assert {"get_goal", "create_goal", "update_goal"} <= planning_names
    assert "vrcforge_know_yourself" in planning_runtime
    assert "shell" in execution_names
    assert "vrcforge_avatar_encryption_scan" not in routable_runtime
    assert "vrcforge_scan_project_index" not in routable_runtime
    assert "vrcforge_unity_status" not in routable_runtime
    assert all(not name.startswith("unity_") for name in planning_names | execution_names | routable_names)
    assert planning.skills == ()
    assert execution.skills == ()


def test_internal_indexed_catalog_loads_per_session_without_leaking_to_external_mcp() -> None:
    session_id = "internal-tree-regression"
    root = dashboard_server.build_internal_tool_block_inventory(
        {"sessionId": session_id, "projectContextActive": True}
    )
    assert root["loadedBlocks"] == ["core"]

    branch = dashboard_server.build_internal_tool_block_inventory(
        {"sessionId": session_id, "index": "8.6", "projectContextActive": True}
    )
    assert branch["tree"]["name"] == "unity/integrations"
    assert [item["index"] for item in branch["tree"]["children"]] == [
        "8.6.1",
        "8.6.2",
        "8.6.3",
        "8.6.4",
    ]

    loaded = dashboard_server.load_internal_tool_block(
        {"sessionId": session_id, "index": "8.6.2"}
    )
    assert loaded["loadedBlocks"] == ["core", "unity/integrations/vrcfury"]
    integrations = dashboard_server.build_internal_tool_block_inventory(
        {"sessionId": session_id, "block": "8.6.2", "projectContextActive": True}
    )
    assert any(
        item["name"] == "unity_scan_vrcfury"
        for item in integrations["tree"]["tools"]
    )

    external_names = {
        item["name"]
        for item in dashboard_server.AGENT_GATEWAY.build_external_mcp_tools(
            "execution", tool_blocks=["*"]
        )
    }
    assert {
        "vrcforge_list_internal_tool_blocks",
        "vrcforge_load_internal_tool_block",
        "vrcforge_unload_internal_tool_block",
    }.isdisjoint(external_names)


def test_no_project_planner_prompt_is_general_and_omits_unity_tools() -> None:
    planner = dashboard_server.RuntimePlannerService(
        catalog=dashboard_server._RuntimePlannerCatalog(),
        desktop=dashboard_server._RuntimePlannerDesktopObservation(),
    )

    prompt = planner._build_llm_plan_prompt(
        r"看下Q:\虚构资料\星海アーカイブ\NebulaArchive是怎么加密的",
        [],
        project_context_active=False,
    )

    assert "general-purpose local Agent" in prompt
    assert "Choose autonomously among the visible general Agent tools" in prompt
    assert "bounded read-only list/read/find/search tools" in prompt
    assert "use Shell for commands, scripts, or processes" in prompt
    assert "questions, TODO/progress, subagents, attachments, vision, or MCP" in prompt
    assert "Never repeat an already successful bounded directory listing through Shell" in prompt
    assert "a top-level directory listing alone is not sufficient evidence" in prompt
    assert "a reply is invalid after only a top-level directory listing" in prompt
    assert "know_yourself" in prompt
    assert '"completion_claim":{"satisfied":true,"evidence_action_ids"' in prompt
    assert "avatar_encryption_scan" not in prompt
    assert "scan_project_index" not in prompt
    assert "unity_status" not in prompt


def test_no_project_planner_can_select_know_yourself_readiness() -> None:
    class Model:
        def plan(self, _prompt: str) -> PlannerModelResult:
            return PlannerModelResult(
                text=(
                    '{"action":"skill","skill_tool":"know_yourself",'
                    '"skill_params":{},"summary":"check readiness"}'
                )
            )

    planner = dashboard_server.RuntimePlannerService(
        catalog=dashboard_server._RuntimePlannerCatalog(),
        desktop=dashboard_server._RuntimePlannerDesktopObservation(),
        model=Model(),
    )

    plan = planner.plan_agent_turn(
        "I am new. Is VRCForge ready, and what should I do next?",
        {"_projectContextActive": False},
        {},
    )

    assert plan["skillNeeded"] is True
    assert plan["skillDisplayTool"] == "know_yourself"
    assert plan["skillTool"] == "vrcforge_know_yourself"


def test_connection_help_prompt_requires_know_yourself_before_generic_tools() -> None:
    planner = dashboard_server.RuntimePlannerService(
        catalog=dashboard_server._RuntimePlannerCatalog(),
        desktop=dashboard_server._RuntimePlannerDesktopObservation(),
    )

    prompt = planner._build_llm_plan_prompt(
        "MCP 一直连不上怎么办？",
        [],
        project_context_active=False,
    )

    assert "VRCForge, Unity, MCP, bridge, editor plugin, or Provider connection problem" in prompt
    assert "choose know_yourself before filesystem, Shell, or repair tools" in prompt
    assert "ordinary Internet, GitHub, or unrelated network troubleshooting" in prompt


def test_no_project_planner_rejects_unity_tool_while_project_turn_keeps_it() -> None:
    class Model:
        def plan(self, _prompt: str) -> PlannerModelResult:
            return PlannerModelResult(
                text=(
                        '{"action":"skill","skill_tool":"unity_avatar_encryption_scan",'
                    '"skill_params":{},"summary":"scan"}'
                )
            )

    planner = dashboard_server.RuntimePlannerService(
        catalog=dashboard_server._RuntimePlannerCatalog(),
        desktop=dashboard_server._RuntimePlannerDesktopObservation(),
        model=Model(),
    )

    general = planner.plan_agent_turn(
        "inspect an ordinary folder",
        {"_projectContextActive": False},
        {},
    )
    project = planner.plan_agent_turn(
        "inspect the selected Unity avatar",
        {"_projectContextActive": True},
        {},
    )

    assert general["plannerFailed"] is True
    assert general["skillNeeded"] is False
    assert project["skillNeeded"] is True
    assert project["skillTool"] == "vrcforge_avatar_encryption_scan"


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
    assert ("vision_audit_multi" in visible) is expected_visible
    assert "vision_audit_multi" in routable
    assert any(item.runtime_name == "vrcforge_vision_audit_multi" for item in planning.visible_tools) is expected_visible
    assert any(item.runtime_name == "vrcforge_vision_audit_multi" for item in planning.routable_tools)
