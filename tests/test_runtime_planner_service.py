from __future__ import annotations

import ast
import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path

import pytest

from runtime_planner_service import (
    EXPOSURE_LAYER_EXECUTION,
    EXPOSURE_LAYER_PLANNING,
    PlannerCatalogSnapshot,
    PlannerModelResult,
    PlannerProviderNotConfiguredError,
    PlannerSkill,
    PlannerTool,
    PlannerTurnMetadata,
    RuntimePlannerService,
    latest_loop_step_needs_model_correction,
    parse_llm_plan_response,
    planner_tool_input_schema,
    planner_tool_schema_prompt,
    planner_safe_tool_result_fields,
    bounded_planner_tool_schema,
    validate_planner_tool_arguments,
)


ROOT = Path(__file__).resolve().parents[1]


def test_unrelated_success_does_not_hide_an_unresolved_tool_failure() -> None:
    failed = {
        "tool": "vrcforge_scan_materials",
        "actionId": "action-materials",
        "outcome": {"status": "failed"},
    }
    healthy = {
        "tool": "vrcforge_health",
        "actionId": "action-health",
        "outcome": {"status": "ok"},
    }
    corrected = {
        "tool": "vrcforge_scan_materials",
        "actionId": "action-materials-fixed",
        "correctionForActionId": "action-materials",
        "outcome": {"status": "ok"},
    }

    assert latest_loop_step_needs_model_correction([failed, healthy]) is True
    assert latest_loop_step_needs_model_correction([failed, corrected]) is False


@dataclass
class FakeCatalog:
    planning: PlannerCatalogSnapshot = field(default_factory=PlannerCatalogSnapshot)
    execution: PlannerCatalogSnapshot | None = None
    reads: list[str] = field(default_factory=list)

    def read(
        self,
        exposure_layer: str,
        *,
        project_context_active: bool = True,
    ) -> PlannerCatalogSnapshot:
        _ = project_context_active
        self.reads.append(exposure_layer)
        if exposure_layer == EXPOSURE_LAYER_EXECUTION and self.execution is not None:
            return self.execution
        return self.planning


@dataclass
class FakeDesktop:
    summary: str = "desktop-owned-summary"
    values: list[object] = field(default_factory=list)

    def summarize_action_result(self, result: object) -> str:
        self.values.append(result)
        return self.summary


@dataclass
class FakeModel:
    result: PlannerModelResult
    prompts: list[str] = field(default_factory=list)
    error: Exception | None = None

    def plan(self, prompt: str) -> PlannerModelResult:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.result


@dataclass
class FakeCompactor:
    result: dict[str, object]
    calls: list[tuple[tuple[dict[str, object], ...], dict[str, object]]] = field(default_factory=list)
    error: Exception | None = None

    def compact(
        self,
        history: tuple[dict[str, object], ...],
        request: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append((history, request))
        if self.error is not None:
            raise self.error
        return dict(self.result)


@dataclass
class FakeTurn:
    metadata: PlannerTurnMetadata
    events: list[str] = field(default_factory=list)
    requests: list[dict[str, object]] = field(default_factory=list)

    @contextmanager
    def bind(self, request: dict[str, object]):
        self.requests.append(dict(request))
        self.events.append("enter")
        try:
            yield self.metadata
        finally:
            self.events.append("exit")


def tool(
    name: str,
    *,
    category: str = "read/debug",
    write: bool = False,
    activation: bool = False,
) -> PlannerTool:
    return PlannerTool(
        name=name,
        description=f"Inspect with {name}.",
        category=category,
        write=write,
        requires_user_activation=activation,
    )


def service(
    *,
    catalog: FakeCatalog | None = None,
    desktop: FakeDesktop | None = None,
    model: FakeModel | None = None,
    compactor: FakeCompactor | None = None,
    turn: FakeTurn | None = None,
    global_instructions=None,
) -> RuntimePlannerService:
    return RuntimePlannerService(
        catalog=catalog or FakeCatalog(),
        desktop=desktop or FakeDesktop(),
        model=model,
        compactor=compactor,
        turn=turn,
        global_instructions=global_instructions,
    )


def test_plan_agent_turn_without_provider_fails_without_local_fallback() -> None:
    planner = service()

    for message, params in (
        ("git status", {}),
        ("Capture front and back views, then run a visual audit.", {}),
        (
            "Run with explicitly selected supervision.",
            {"skill_tool": "vrcforge_health", "skill_params": {}},
        ),
    ):
        plan = planner.plan_agent_turn(message, params, {})
        assert plan["planner"] == "llm"
        assert plan["plannerFailed"] is True
        assert plan["plannerFailure"] == {
            "code": "provider_not_configured",
            "phase": "initial",
            "retryable": False,
        }
        assert plan["nextStep"] == "planner_failed"
        assert "deterministicTerminal" not in plan


def test_runtime_planner_has_no_private_local_router_or_deterministic_contract() -> None:
    source = (ROOT / "runtime_planner_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(
        item
        for item in tree.body
        if isinstance(item, ast.ClassDef) and item.name == "RuntimePlannerService"
    )
    method_names = {
        item.name for item in owner.body if isinstance(item, ast.FunctionDef)
    }
    module_function_names = {
        item.name for item in tree.body if isinstance(item, ast.FunctionDef)
    }

    assert not {
        "_local_plan_agent_turn",
        "_match_runtime_skill",
        "_runtime_skill_route",
        "_plan_runtime_meta_question",
        "_plan_write_intent",
        "_avatars_from_loop_state",
        "_build_avatar_write_params",
        "_match_package_skill_route",
    }.intersection(method_names)
    assert not {
        "extract_skill_invocation",
        "extract_shell_command_candidate",
        "detect_avatar_write_intent",
        "extract_avatar_paths",
        "runtime_tool_intent_text",
        "has_multi_angle_capture_intent",
        "has_multi_angle_visual_audit_intent",
        "multi_angle_visual_journey_requires_provider",
    }.intersection(module_function_names)
    assert "deterministic-local" not in source
    assert "deterministicTerminal" not in source






def test_model_observation_includes_bounded_canonical_tool_outcome() -> None:
    observation = service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_scan_materials",
            "status": "failed",
            "result": {"ok": True, "privateDump": "must-not-enter-model-context"},
            "outcome": {
                "status": "failed",
                "summary": "Material inventory could not be read.",
                "verification": {"state": "not_required"},
                "error": {
                    "type": "unity_core",
                    "code": "unity_core_not_ready",
                    "likelyCauses": ["Unity is compiling"],
                    "nextActions": ["Wait for compilation"],
                    "retryable": True,
                },
            },
        }
    )

    assert "outcomeStatus=failed" in observation
    assert "outcomeSummary=Material inventory could not be read." in observation
    assert "verificationState=not_required" in observation
    assert "errorType=unity_core" in observation
    assert "errorCode=unity_core_not_ready" in observation
    assert "likelyCauses=Unity is compiling" in observation
    assert "nextActions=Wait for compilation" in observation
    assert "retryable=True" in observation
    assert "privateDump" not in observation


def test_model_observation_includes_precise_internal_failure_facts_without_raw_dump() -> None:
    observation = service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_get_compile_errors",
            "status": "failed",
            "result": {"privateDump": "must-not-enter-model-context"},
            "outcome": {
                "status": "failed",
                "summary": "Core descriptor is invalid.",
                "verification": {"state": "not_required"},
                "error": {
                    "type": "tool",
                    "code": "unity_core_contract_invalid",
                    "retryable": False,
                },
                "diagnostics": {
                    "schema": "vrcforge.internal_tool_diagnostics.v1",
                    "sourceError": {
                        "failureLayer": "unity_core_pre_route",
                        "failurePhase": "core_handshake",
                        "toolRoutingStarted": False,
                        "mutationStarted": False,
                        "committed": False,
                        "commitState": "not_started",
                        "checkpointRecoveryRequired": False,
                        "temporaryCleanupRequired": False,
                        "rawResult": {"privateDump": "must-not-enter-model-context"},
                    },
                },
            },
        }
    )

    assert "errorCode=unity_core_contract_invalid" in observation
    assert "failureLayer=unity_core_pre_route" in observation
    assert "failurePhase=core_handshake" in observation
    assert "toolRoutingStarted=False" in observation
    assert "mutationStarted=False" in observation
    assert "committed=False" in observation
    assert "commitState=not_started" in observation
    assert "privateDump" not in observation


def test_model_observation_preserves_canonical_dispatch_retry_and_recovery_flags() -> None:
    observation = service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_set_material_texture",
            "status": "failed",
            "result": {"privateDump": "must-not-enter-model-context"},
            "outcome": {
                "status": "failed",
                "summary": "Unity did not return its write receipt.",
                "verification": {"state": "not_required"},
                "error": {"code": "unity_write_response_timeout", "retryable": True},
                "toolRoutingStarted": True,
                "mutationStarted": None,
                "committed": None,
                "commitState": "unknown",
                "commitStateKnown": False,
                "retryable": True,
                "safeToRetry": False,
                "checkpointRecoveryRequired": True,
                "temporaryCleanupRequired": False,
            },
        }
    )

    assert '"toolRoutingStarted":true' in observation
    assert '"retryable":true' in observation
    assert '"safeToRetry":false' in observation
    assert '"checkpointRecoveryRequired":true' in observation
    assert '"temporaryCleanupRequired":false' in observation
    assert "privateDump" not in observation


def test_internal_tool_block_observation_keeps_compact_indices_without_schemas() -> None:
    observation = service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_list_internal_tool_blocks",
            "status": "executed",
            "result": {
                "ok": True,
                "loadedBlocks": ["core"],
                "tree": {
                    "children": [
                        {"index": "1", "name": "core", "loaded": True},
                        {"index": "8", "name": "unity", "expandable": True},
                    ]
                },
                "blocks": [
                    {
                        "index": "8.9",
                        "name": "unity/diagnostics",
                        "toolNames": ["vrcforge_get_compile_errors", "vrcforge_unity_status"],
                    }
                ],
                "privateSchema": {"must": "not enter model context"},
            },
            "outcome": {"status": "ok", "summary": "Listed internal tool blocks."},
        }
    )

    assert "loadedBlocks=core" in observation
    assert "toolBlockTree=1:core(loaded) | 8:unity(expand)" in observation
    assert "8.9:unity/diagnostics[vrcforge_get_compile_errors,vrcforge_unity_status]" in observation
    assert "skill_tool=load_internal_tool_block" in observation
    assert "skill_params={\"block\":\"<exact block name>\"}" in observation
    assert "privateSchema" not in observation
    assert len(observation) <= 8_000


def test_model_observation_keeps_bounded_know_yourself_guidance() -> None:
    observation = service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_know_yourself",
            "status": "executed",
            "result": {
                "ok": True,
                "schema": "vrcforge.know_yourself.v1",
                "readyForUnityWork": False,
                "notice": "Reply to the user now; do not inspect project files.",
                "summary": (
                    "readyForUnityWork=false; blockingGaps=editor_plugin_present; "
                    "nextSafeAction=install_or_repair_editor_plugin"
                ),
                "message": "Use the VRCForge project setup surface, then re-run this check.",
                "privateDump": "must-not-enter-model-context",
            },
            "outcome": {
                "status": "ok",
                "summary": "Know Yourself readiness was inspected.",
                "verification": {"state": "not_required"},
            },
        }
    )

    assert "readyForUnityWork=false" in observation
    assert "nextSafeAction=install_or_repair_editor_plugin" in observation
    assert "Reply to the user now" in observation
    assert "Use the VRCForge project setup surface" in observation
    assert "privateDump" not in observation


def test_model_observation_enforces_contract_600_char_ceiling() -> None:
    observation = service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_read_text_file",
            "status": "executed",
            "result": {"summary": "X" * 1799},
            "outcome": {"status": "ok", "summary": "X" * 1799},
        }
    )

    assert len(observation) <= 600
    assert observation.count("X") < 600


def test_capture_approval_observation_exposes_only_opaque_visual_capability() -> None:
    observation = service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_capture_multi_screenshot",
            "kind": "write",
            "status": "applied",
            "result": {
                "captureReceipt": "opaque-managed-capability",
                "captureEvidenceId": "visual_123",
                "angles": ["front", "back"],
                "privatePath": "D:/private/vision_front.png",
            },
            "outcome": {"status": "ok", "summary": "captured"},
        }
    )

    assert "captureReceipt=opaque-managed-capability" in observation
    assert "captureEvidenceId=visual_123" in observation
    assert "captureAngles=front | back" in observation
    assert "privatePath" not in observation
    assert "D:/private" not in observation


def test_failed_tool_feedback_invokes_model_correction_once() -> None:
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(
                tool("vrcforge_scan_materials"),
                tool("vrcforge_health"),
            ),
            routable_tools=(
                tool("vrcforge_scan_materials"),
                tool("vrcforge_health"),
            ),
        )
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "skill",
                    "skill_tool": "vrcforge_health",
                    "skill_params": {},
                    "correction_for_action_id": "scan-materials-failed",
                }
            )
        )
    )

    plan = service(catalog=catalog, model=model).plan_agent_turn(
        "扫描这个 avatar 的材质，并列出 shader。",
        {},
        {},
        loop_state=[
            {
                "tool": "vrcforge_scan_materials",
                "status": "failed",
                "outcome": {
                    "status": "failed",
                    "summary": "Avatar path is missing.",
                    "error": {"code": "missing_avatar_path"},
                },
            }
        ],
    )

    assert len(model.prompts) == 1
    assert "outcomeStatus=failed" in model.prompts[0]
    assert plan["skillTool"] == "vrcforge_health"
    assert plan["correctionForActionId"] == "scan-materials-failed"


def test_failed_tool_feedback_without_model_does_not_replay() -> None:
    plan = service().plan_agent_turn(
        "扫描这个 avatar 的材质，并列出 shader。",
        {},
        {},
        loop_state=[
            {
                "tool": "vrcforge_scan_materials",
                "status": "failed",
                "outcome": {"status": "failed", "summary": "Unity is unavailable."},
            }
        ],
    )

    assert plan["planner"] == "llm"
    assert plan["plannerFailed"] is True
    assert plan["plannerFailure"] == {
        "code": "provider_not_configured",
        "phase": "initial",
        "retryable": False,
    }
    assert plan["nextStep"] == "planner_failed"


def test_verified_loaded_skill_context_is_explicit_and_bounded_for_the_next_plan() -> None:
    observation = service()._llm_loop_step_observation(
        {
            "tool": "fixture-guidance",
            "status": "loaded",
            "skillContext": {
                "name": "fixture-guidance",
                "instructions": "Call vrcforge_health and inspect the canonical outcome.",
                "allowedTools": ["vrcforge_health"],
                "disallowedTools": ["vrcforge_shell_execute"],
            },
        }
    )

    assert "skillContextName=fixture-guidance" in observation
    assert "skillInstructions=Call vrcforge_health" in observation
    assert "skillAllowedTools=vrcforge_health" in observation
    assert "skillDisallowedTools=vrcforge_shell_execute" in observation


def test_model_prompt_keeps_all_trigger_sections_for_long_tool_descriptions() -> None:
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(
                PlannerTool(
                    name="vrcforge_long_contract",
                    description=(
                        "When to use: " + "use-detail " * 40
                        + "\nWhen NOT to use: " + "avoid-detail " * 40
                        + "\nNegative example: " + "negative-detail " * 40
                    ),
                    category="read/debug",
                    write=False,
                ),
            ),
        )
    )

    prompt = service(catalog=catalog)._build_llm_plan_prompt("inspect", [])

    tool_line = next(line for line in prompt.splitlines() if "vrcforge_long_contract" in line)
    assert "When to use:" in tool_line
    assert "When NOT to use:" in tool_line
    assert "Negative example:" in tool_line
    assert len(tool_line) < 500


def test_model_prompt_includes_bounded_input_contract_for_high_confusion_tool() -> None:
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(
                PlannerTool(
                    name="vrcforge_scan_materials",
                    description="Inspect avatar materials.",
                    category="read/debug",
                    input_contract=("projectPath?:string", "avatarPath?:string"),
                ),
            ),
        )
    )

    prompt = service(catalog=catalog)._build_llm_plan_prompt("inspect materials", [])

    tool_line = next(line for line in prompt.splitlines() if "vrcforge_scan_materials" in line)
    assert "inputs={projectPath?:string, avatarPath?:string}" in tool_line


def test_model_prompt_requires_evidence_grounded_final_reply_without_hiding_safe_updates() -> None:
    prompt = service()._build_llm_plan_prompt("inspect the project", [])

    assert "推断必须明确标注" in prompt
    assert "不能把 package name 或 private 标记当作产品用途证据" in prompt
    assert "reply 字段是直接展示给用户的对话内容" in prompt


def test_bound_general_project_agents_file_is_injected_before_current_user_message(tmp_path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Read PROJECT_STATUS.md before project work.",
        encoding="utf-8",
    )
    model = FakeModel(PlannerModelResult('{"action":"reply","reply":"done"}'))
    planner = service(model=model, global_instructions=lambda: "Reply in the user's language.")

    plan = planner.plan_agent_turn(
        "Inspect the entry documents.",
        {"projectPath": str(tmp_path), "_projectContextActive": False},
        {},
    )

    assert plan["reply"] == "done"
    prompt = model.prompts[0]
    assert "Reply in the user's language." in prompt
    assert "Read PROJECT_STATUS.md before project work." in prompt
    assert prompt.index("<global_user_instructions>") < prompt.index("<project_instructions>")
    assert prompt.index("<project_instructions>") < prompt.index("用户最新消息：Inspect the entry documents.")
    assert "never authorize a write" in prompt


def test_multi_angle_visual_tool_requires_managed_capture_receipt() -> None:
    schema = planner_tool_input_schema("vrcforge_vision_audit_multi")

    assert schema["required"] == ["captureReceipt"]
    assert validate_planner_tool_arguments(
        schema,
        {"captureReceipt": "opaque-managed-capability"},
    )["ok"] is True
    assert validate_planner_tool_arguments(schema, {})["issues"] == [
        {"path": "captureReceipt", "code": "missing_required", "expected": "present"}
    ]


def test_shallow_tool_schema_validates_required_type_enum_and_closed_extras() -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "mode": {"type": "string", "enum": ["safe", "force"]},
            "count": {"type": "integer"},
        },
        "required": ["name", "mode"],
        "additionalProperties": False,
    }

    assert validate_planner_tool_arguments(
        schema, {"name": "Probe", "mode": "safe", "count": 2}
    )["ok"] is True
    missing = validate_planner_tool_arguments(schema, {"mode": "safe"})
    wrong_type = validate_planner_tool_arguments(
        schema, {"name": "Probe", "mode": "safe", "count": True}
    )
    wrong_enum = validate_planner_tool_arguments(
        schema, {"name": "Probe", "mode": "unsafe"}
    )
    unknown = validate_planner_tool_arguments(
        schema, {"name": "Probe", "mode": "safe", "surprise": 1}
    )

    assert missing["issues"] == [
        {"path": "name", "code": "missing_required", "expected": "present"}
    ]
    assert wrong_type["issues"] == [
        {"path": "count", "code": "wrong_type", "expected": "integer"}
    ]
    assert wrong_enum["issues"] == [
        {"path": "mode", "code": "enum", "expected": "one of the declared values"}
    ]
    assert unknown["issues"] == [
        {"path": "surprise", "code": "unknown_property", "expected": "declared property"}
    ]
    assert all(
        result["code"] == "planner_invalid_response"
        for result in (missing, wrong_type, wrong_enum, unknown)
    )


def test_bounded_schema_preserves_array_items_and_discriminated_required_branches() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["operationKind", "sources"],
        "oneOf": [
            {
                "properties": {"operationKind": {"const": "game_object"}},
                "required": ["targetObjectPath", "newName"],
            },
            {
                "properties": {"operationKind": {"const": "parameter"}},
                "required": ["oldParameterName", "newParameterName"],
            },
        ],
        "properties": {
            "operationKind": {"type": "string", "enum": ["game_object", "parameter"]},
            "sources": {
                "type": "array",
                "maxItems": 64,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sourcePath", "weight"],
                    "properties": {
                        "sourcePath": {"type": "string"},
                        "weight": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    },
                },
            },
            "targetObjectPath": {"type": "string"},
            "newName": {"type": "string"},
            "oldParameterName": {"type": "string"},
            "newParameterName": {"type": "string"},
        },
    }

    bounded = bounded_planner_tool_schema(schema)
    assert bounded["properties"]["sources"]["items"]["required"] == ["sourcePath", "weight"]
    assert bounded["oneOf"][0]["properties"]["operationKind"]["const"] == "game_object"
    assert validate_planner_tool_arguments(
        bounded,
        {
            "operationKind": "game_object",
            "sources": [{"sourcePath": "Avatar/Head", "weight": 0.5}],
            "targetObjectPath": "Avatar/Old",
            "newName": "New",
        },
    )["ok"] is True
    assert validate_planner_tool_arguments(
        bounded,
        {"operationKind": "game_object", "sources": [{"wrong": 1}]},
    )["ok"] is False
    assert validate_planner_tool_arguments(
        bounded,
        {"operationKind": "game_object", "sources": []},
    )["ok"] is False
    prompt = planner_tool_schema_prompt(bounded)
    assert "sources:array<{sourcePath:string,weight:number}>" in prompt
    assert "operationKind=game_object=>targetObjectPath+newName" in prompt


def test_shallow_schema_allows_unknown_fields_unless_explicitly_closed() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": True,
    }

    assert validate_planner_tool_arguments(
        schema, {"name": "Probe", "acceptedByHandler": True}
    )["ok"] is True


def test_llm_tool_schema_failure_returns_a_correctable_non_execution_plan() -> None:
    schema_tool = PlannerTool(
        name="vrcforge_schema_fixture",
        description="Inspect the fixture.",
        category="read/debug",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "mode": {"type": "string", "enum": ["safe", "force"]},
            },
            "required": ["name", "mode"],
            "additionalProperties": False,
        },
    )
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(schema_tool,),
            routable_tools=(schema_tool,),
        )
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "skill",
                    "skill_tool": schema_tool.name,
                    "skill_params": {"name": "Probe", "mode": "unsafe"},
                }
            )
        )
    )

    plan = service(catalog=catalog, model=model)._llm_plan_agent_turn(
        "inspect the fixture",
        {},
        [],
        exposure_layer=EXPOSURE_LAYER_PLANNING,
    )

    assert plan is not None
    assert plan["nextStep"] == "planner_invalid_response"
    assert plan["continueLoop"] is True
    assert plan["skillNeeded"] is False
    assert plan["argumentValidation"]["code"] == "planner_invalid_response"
    assert plan["argumentValidation"]["issues"][0]["code"] == "enum"
    assert "mode:string[enum=safe|force]" in model.prompts[0]
    assert "additionalProperties=false" in model.prompts[0]


def test_internal_planner_prompt_only_expands_loaded_tool_blocks() -> None:
    core_tool = PlannerTool(
        name="list_internal_tool_blocks",
        description="List the indexed internal tool tree.",
        category="read/debug",
        block="core",
    )
    file_tool = PlannerTool(
        name="read_text_file",
        description="Read one text file.",
        category="read/debug",
        block="files",
    )
    unity_tool = PlannerTool(
        name="unity_scan_vrcfury",
        description="Scan VRCFury.",
        category="read/debug",
        block="unity/integrations",
    )
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(core_tool, file_tool, unity_tool),
            routable_tools=(core_tool, file_tool, unity_tool),
        )
    )
    model = FakeModel(PlannerModelResult('{"action":"reply","reply":"ok"}'))

    service(catalog=catalog, model=model).plan_agent_turn(
        "inspect",
        {"_internalToolBlocks": ["core", "files"]},
        {},
    )

    prompt = model.prompts[0]
    assert "list_internal_tool_blocks" in prompt
    assert "read_text_file" in prompt
    assert "unity_scan_vrcfury" not in prompt
    assert "loaded internal tool blocks: core, files" in prompt
    assert "call it with action=skill" in prompt
    assert "never in action" in prompt
    assert "action 只能是 skill、shell、reply 或 enter_execution" in prompt




def test_llm_execution_layer_has_a_first_class_supervised_write_action() -> None:
    write_tool = tool("vrcforge_create_gameobject", category="supervised-write", write=True)
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(),
        execution=PlannerCatalogSnapshot(
            visible_tools=(write_tool,),
            routable_tools=(write_tool,),
        ),
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "write",
                    "write_tool": "vrcforge_create_gameobject",
                    "write_params": {"name": "Probe"},
                    "correction_for_action_id": "action_failed",
                }
            )
        )
    )

    plan = service(catalog=catalog, model=model)._llm_plan_agent_turn(
        "create Probe",
        {},
        [],
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert plan is not None
    assert plan["writeNeeded"] is True
    assert plan["writeTool"] == "vrcforge_create_gameobject"
    assert plan["writeParams"] == {"name": "Probe"}
    assert plan["correctionForActionId"] == "action_failed"
    assert plan["nextStep"] == "request_write"


def test_profiled_tool_alias_returns_internal_runtime_name() -> None:
    write_tool = PlannerTool(
        name="edit_file",
        runtime_name="vrcforge_edit_file",
        description="Edit an existing file.",
        category="supervised-write",
        write=True,
        input_contract=("path:string", "content:string"),
    )
    catalog = FakeCatalog(
        execution=PlannerCatalogSnapshot(
            visible_tools=(write_tool,),
            routable_tools=(write_tool,),
        )
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "write",
                    "write_tool": "edit_file",
                    "write_params": {"path": "C:/notes/a.txt", "content": "updated"},
                }
            )
        )
    )

    plan = service(catalog=catalog, model=model)._llm_plan_agent_turn(
        "edit the note",
        {},
        [],
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert plan["writeTool"] == "vrcforge_edit_file"
    assert plan["writeDisplayTool"] == "edit_file"


def test_unity_shell_projection_becomes_capability_bound_shell_step() -> None:
    unity_shell = PlannerTool(
        name="unity_shell",
        runtime_name="vrcforge_execute_shell",
        capabilities=("unity_project_access",),
        description="Run Shell for the current Unity project.",
        category="supervised-write",
        write=True,
        input_contract=("command:string", "cwd?:string"),
    )
    catalog = FakeCatalog(
        execution=PlannerCatalogSnapshot(
            visible_tools=(unity_shell,),
            routable_tools=(unity_shell,),
        )
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "write",
                    "write_tool": "unity_shell",
                    "write_params": {"command": "git status", "cwd": "C:/Unity/Avatar"},
                }
            )
        )
    )

    plan = service(catalog=catalog, model=model)._llm_plan_agent_turn(
        "inspect the current Unity project",
        {},
        [],
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert plan["shellNeeded"] is True
    assert plan["shellCommand"] == "git status"
    assert plan["shellParams"] == {"cwd": "C:/Unity/Avatar"}
    assert plan["toolCapabilities"] == ["unity_project_access"]
    assert plan["writeDisplayTool"] == "unity_shell"


@pytest.mark.parametrize(
    "exposure_layer",
    [EXPOSURE_LAYER_PLANNING, EXPOSURE_LAYER_EXECUTION],
)
def test_llm_skill_action_returns_correctable_kind_mismatch_for_any_supervised_write(
    exposure_layer: str,
) -> None:
    write_tool = tool("fixture-supervised-write", category="supervised-write", write=True)
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(),
            routable_tools=(write_tool,),
        ),
        execution=PlannerCatalogSnapshot(
            visible_tools=(write_tool,),
            routable_tools=(write_tool,),
        )
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "skill",
                    "skill_tool": "fixture-supervised-write",
                    "skill_params": {"name": "Probe"},
                }
            )
        )
    )

    plan = service(catalog=catalog, model=model)._llm_plan_agent_turn(
        "create Probe",
        {},
        [],
        exposure_layer=exposure_layer,
    )

    assert plan is not None
    validation = plan["argumentValidation"]
    assert validation["ok"] is False
    assert validation["actionKind"] == "skill"
    assert validation["tool"] == "fixture-supervised-write"
    assert validation["issues"] == [
        {
            "path": "action",
            "code": "wrong_action_kind",
            "expected": "write",
        }
    ]
    assert plan["skillNeeded"] is False
    assert plan["writeNeeded"] is False
    assert plan.get("enterExecution") is (
        exposure_layer == EXPOSURE_LAYER_PLANNING
    )
    assert plan["continueLoop"] is True
    assert plan["nextStep"] == "planner_invalid_response"










def test_explicit_multi_angle_audit_execution_selection_is_provider_owned() -> None:
    capture = tool(
        "vrcforge_capture_multi_screenshot",
        category="supervised-write",
        write=True,
    )
    audit = tool("vrcforge_vision_audit_multi")
    snapshot = PlannerCatalogSnapshot(
        visible_tools=(capture, audit),
        routable_tools=(capture, audit),
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "write",
                    "write_tool": "vrcforge_capture_multi_screenshot",
                    "write_params": {},
                    "summary": "Capture the approved fixed-angle views.",
                }
            )
        )
    )

    plan = service(
        catalog=FakeCatalog(planning=snapshot, execution=snapshot),
        model=model,
    ).plan_agent_turn(
        "Capture front and back views, then run a visual audit.",
        {},
        {},
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert len(model.prompts) == 1
    assert plan["planner"] == "llm"
    assert plan["writeTool"] == "vrcforge_capture_multi_screenshot"
    assert plan["continueLoop"] is True


def test_successful_multi_angle_audit_result_is_refed_for_provider_completion() -> None:
    capture = tool(
        "vrcforge_capture_multi_screenshot",
        category="supervised-write",
        write=True,
    )
    audit = tool("vrcforge_vision_audit_multi")
    snapshot = PlannerCatalogSnapshot(
        visible_tools=(capture, audit),
        routable_tools=(capture, audit),
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "reply",
                    "reply": "The fixed-angle visual audit passed.",
                    "completion_claim": {
                        "satisfied": True,
                        "evidence_action_ids": ["capture-action", "audit-action"],
                    },
                }
            )
        )
    )
    loop_state = [
        {
            "tool": "vrcforge_capture_multi_screenshot",
            "kind": "write",
            "actionId": "capture-action",
            "status": "applied",
            "result": {"captureReceipt": "consumed-capture"},
            "outcome": {"status": "ok"},
        },
        {
            "tool": "vrcforge_vision_audit_multi",
            "kind": "skill",
            "actionId": "audit-action",
            "status": "executed",
            "result": {"visualVerified": True, "coverageComplete": True},
            "outcome": {"status": "ok"},
        },
    ]

    plan = service(
        catalog=FakeCatalog(planning=snapshot, execution=snapshot),
        model=model,
    ).plan_agent_turn(
        "Capture front and back views, then run a visual audit.",
        {},
        {},
        loop_state=loop_state,
        context_usage={"requestCount": 2},
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert len(model.prompts) == 1
    assert "vrcforge_vision_audit_multi" in model.prompts[0]
    assert plan["planner"] == "llm"
    assert plan["nextStep"] == "done"


def test_visual_provider_loss_after_capture_stops_before_model_resample() -> None:
    capture = tool(
        "vrcforge_capture_multi_screenshot",
        category="supervised-write",
        write=True,
    )
    audit = tool("vrcforge_vision_audit_multi")
    model = FakeModel(PlannerModelResult('{"action":"reply","reply":"skip audit"}'))
    planner = service(
        catalog=FakeCatalog(
            planning=PlannerCatalogSnapshot(
                visible_tools=(),
                routable_tools=(capture, audit),
            ),
            execution=PlannerCatalogSnapshot(
                visible_tools=(capture,),
                routable_tools=(capture, audit),
            ),
        ),
        model=model,
    )

    plan = planner.plan_agent_turn(
        "Capture front and back views, then run a visual audit.",
        {},
        {},
        loop_state=[
            {
                "tool": "vrcforge_capture_multi_screenshot",
                "kind": "write",
                "status": "applied",
                "result": {"captureReceipt": "managed-receipt"},
                "outcome": {"status": "ok"},
            }
        ],
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert len(model.prompts) == 1
    assert "managed-receipt" in model.prompts[0]
    assert plan["planner"] == "llm"
    assert plan["nextStep"] == "done"
    assert plan["reply"] == "skip audit"
    assert plan["writeNeeded"] is False
    assert plan["skillNeeded"] is False




def test_transient_visual_audit_observation_exposes_only_opaque_retry_capability() -> None:
    observation = service()._llm_loop_step_observation(
        {
            "tool": "vrcforge_vision_audit_multi",
            "kind": "skill",
            "status": "failed",
            "result": {
                "captureReceipt": "opaque-retry-capability",
                "retryable": True,
                "retainImages": True,
                "privatePath": "D:/private/front.png",
            },
            "outcome": {"status": "failed", "summary": "HTTP 503"},
        }
    )

    assert "visualRetryCaptureReceipt=opaque-retry-capability" in observation
    assert "visualRetryImagesRetained=true" in observation
    assert "privatePath" not in observation
    assert "D:/private" not in observation


def test_provider_cannot_replay_consumed_visual_capture_receipt_after_permanent_failure() -> None:
    audit = tool("vrcforge_vision_audit_multi")
    snapshot = PlannerCatalogSnapshot(
        visible_tools=(audit,),
        routable_tools=(audit,),
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "skill",
                    "skill_tool": "vrcforge_vision_audit_multi",
                    "skill_params": {"captureReceipt": "consumed-original"},
                }
            )
        )
    )
    planner = service(
        catalog=FakeCatalog(planning=snapshot, execution=snapshot),
        model=model,
    )

    plan = planner._llm_plan_agent_turn(
        "Capture front and back views, then run a visual audit.",
        {},
        [],
        loop_state=[
            {
                "tool": "vrcforge_capture_multi_screenshot",
                "kind": "write",
                "status": "applied",
                "result": {"captureReceipt": "consumed-original"},
                "outcome": {"status": "ok"},
            },
            {
                "tool": "vrcforge_vision_audit_multi",
                "kind": "skill",
                "status": "failed",
                "result": {
                    "error": "selected provider rejected image input",
                    "retryable": False,
                    "retainImages": False,
                },
                "outcome": {"status": "failed"},
            },
        ],
        context_usage={"requestCount": 1},
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert len(model.prompts) == 1
    assert "selected provider rejected image input" in model.prompts[0]
    assert "captureReceipt=consumed-original" not in model.prompts[0]
    assert plan is not None
    assert plan["skillNeeded"] is False
    assert plan["writeNeeded"] is False
    assert plan["nextStep"] == "needs_user_action"
    assert plan["completionGate"]["reason"] == "visual_audit_image_discarded"


def test_provider_stale_visual_receipt_gets_typed_correction_for_exact_reissue() -> None:
    audit = tool("vrcforge_vision_audit_multi")
    snapshot = PlannerCatalogSnapshot(
        visible_tools=(audit,),
        routable_tools=(audit,),
    )
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "skill",
                    "skill_tool": "vrcforge_vision_audit_multi",
                    "skill_params": {"captureReceipt": "consumed-original"},
                }
            )
        )
    )
    planner = service(
        catalog=FakeCatalog(planning=snapshot, execution=snapshot),
        model=model,
    )

    plan = planner._llm_plan_agent_turn(
        "Retry the visual audit after the transient provider failure.",
        {},
        [],
        loop_state=[
            {
                "tool": "vrcforge_capture_multi_screenshot",
                "kind": "write",
                "status": "applied",
                "result": {"captureReceipt": "consumed-original"},
                "outcome": {"status": "ok"},
            },
            {
                "tool": "vrcforge_vision_audit_multi",
                "kind": "skill",
                "status": "failed",
                "result": {
                    "captureReceipt": "reissued-exact-retry",
                    "error": "HTTP 503",
                    "retryable": True,
                    "retainImages": True,
                },
                "outcome": {"status": "failed"},
            },
        ],
        context_usage={"requestCount": 1},
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert len(model.prompts) == 1
    assert "visualRetryCaptureReceipt=reissued-exact-retry" in model.prompts[0]
    assert "captureReceipt=consumed-original" not in model.prompts[0]
    assert plan is not None
    assert plan["skillNeeded"] is False
    assert plan["argumentValidation"]["ok"] is False
    assert plan["argumentValidation"]["issues"] == [
        {
            "path": "captureReceipt",
            "code": "stale_runtime_capability",
            "expected": "current runtime-owned capture receipt",
        }
    ]
    assert plan["continueLoop"] is True
    assert plan["nextStep"] == "planner_invalid_response"










@pytest.mark.parametrize(
    "payload",
    [
        {
            "action": "skill",
            "skill_tool": "vrcforge_health",
            "skill_params": [],
        },
        {
            "action": "write",
            "write_tool": "vrcforge_create_gameobject",
            "write_params": "name=Probe",
        },
        {
            "action": "shell",
            "shell_command": "git status",
            "shell_params": False,
        },
    ],
)
def test_llm_non_object_action_params_fail_closed(payload: dict[str, object]) -> None:
    model = FakeModel(PlannerModelResult(json.dumps(payload)))

    plan = service(model=model)._llm_plan_agent_turn(
        "continue",
        {},
        [],
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert len(model.prompts) == 1
    assert plan is not None
    assert plan["plannerFailed"] is True
    assert plan["plannerFailure"]["code"] == "planner_invalid_response"
    assert plan["skillNeeded"] is False
    assert plan["writeNeeded"] is False
    assert plan["shellNeeded"] is False
    assert plan["nextStep"] == "planner_failed"


def test_typed_model_port_owns_reasoning_usage_label_and_exposure() -> None:
    planning = PlannerCatalogSnapshot(
        visible_tools=(tool("vrcforge_health"),),
        routable_tools=(tool("vrcforge_health"),),
    )
    execution = PlannerCatalogSnapshot(
        visible_tools=(tool("vrcforge_health"),),
        routable_tools=(tool("vrcforge_health"),),
        skills=(PlannerSkill(name="fixture-writer", category="user", source="user"),),
    )
    catalog = FakeCatalog(planning=planning, execution=execution)
    model = FakeModel(
        PlannerModelResult(
            text=json.dumps(
                {
                    "action": "skill",
                    "skill_tool": "fixture-writer",
                    "skill_params": {"value": 3},
                }
            ),
            usage={
                "exact": True,
                "inputTokens": 120,
                "outputTokens": 8,
                "totalTokens": 128,
            },
            reasoning={"summary": "fixture reasoning"},
            planner_label="Fixture Provider · model-a",
        )
    )
    planner = service(catalog=catalog, model=model)
    usage: dict[str, object] = {}
    reasoning: dict[str, object] = {}

    plan = planner.plan_agent_turn(
        "perform the fixture task",
        {},
        {},
        context_usage=usage,
        reasoning_trace=reasoning,
        exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert plan["planner"] == "llm"
    assert plan["plannerLabel"] == "Fixture Provider · model-a"
    assert plan["skillTool"] == "fixture-writer"
    assert plan["skillCategory"] == "user"
    assert plan["nextStep"] == "call_skill"
    assert reasoning == {"summary": "fixture reasoning"}
    assert usage["lastInputTokens"] == 120
    assert usage["peakInputTokens"] == 120
    assert catalog.reads.count(EXPOSURE_LAYER_EXECUTION) >= 2


@pytest.mark.parametrize(
    "response_text",
    [
        "",
        "not-json",
        "[]",
        "{}",
        '{"action":"skill","skill_tool":"missing-tool","summary":"done"}',
        '{"action":"shell","summary":"done"}',
        '{"action":"unknown","summary":"done"}',
    ],
)
def test_invalid_model_actions_fail_closed_instead_of_claiming_success(response_text: str) -> None:
    planner = service(model=FakeModel(PlannerModelResult(response_text)))

    plan = planner._llm_plan_agent_turn("continue", {}, [])

    assert plan is not None
    assert plan["planner"] == "llm"
    assert plan["plannerFailed"] is True
    failure = plan["plannerFailure"]
    assert failure["code"] == "planner_invalid_response"
    assert failure["phase"] == "initial"
    assert failure["retryable"] is True
    if response_text:
        assert failure["invalidResponse"]["preview"] == response_text
        assert failure["invalidResponse"]["stage"] == (
            "json_object_parse" if response_text in {"not-json", "[]"} else "action_validation"
        )
    else:
        assert "invalidResponse" not in failure
    assert plan["nextStep"] == "planner_failed"
    assert plan["skillNeeded"] is False
    assert plan["shellNeeded"] is False
    assert "done" not in str(plan.get("reply") or "")


def test_registered_tool_name_in_action_is_refed_as_one_precise_correction() -> None:
    block_tool = PlannerTool(
        name="vrcforge_load_internal_tool_block",
        description="Load one indexed internal tool block.",
        category="read/debug",
        input_schema={
            "type": "object",
            "properties": {"block": {"type": "string"}},
            "required": ["block"],
            "additionalProperties": False,
        },
    )
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(block_tool,),
            routable_tools=(block_tool,),
        )
    )
    planner = service(
        catalog=catalog,
        model=FakeModel(
            PlannerModelResult(
                json.dumps(
                    {
                        "action": "load_internal_tool_block",
                        "skill_tool": "load_internal_tool_block",
                        "skill_params": {"block_index": 8},
                    }
                )
            )
        ),
    )

    plan = planner._llm_plan_agent_turn("inspect", {}, [])

    assert plan is not None
    assert plan["continueLoop"] is True
    assert plan["nextStep"] == "planner_invalid_response"
    validation = plan["argumentValidation"]
    assert validation["tool"] == "vrcforge_load_internal_tool_block"
    assert validation["actionKind"] == "skill"
    assert {item["path"] for item in validation["issues"]} >= {
        "action",
        "skill_tool",
        "block",
        "block_index",
    }


def test_provider_native_tool_call_uses_existing_skill_validation_path() -> None:
    block_tool = PlannerTool(
        name="vrcforge_load_internal_tool_block",
        description="Load one indexed internal tool block.",
        category="read/debug",
        input_schema={
            "type": "object",
            "properties": {"block": {"type": "string"}},
            "required": ["block"],
            "additionalProperties": False,
        },
    )
    planner = service(
        catalog=FakeCatalog(
            planning=PlannerCatalogSnapshot(
                visible_tools=(block_tool,),
                routable_tools=(block_tool,),
            )
        ),
        model=FakeModel(
            result=PlannerModelResult(
                text=(
                    "<tool_call><function=vrcforge_load_internal_tool_block>"
                    "<parameter=block>\"unity/diagnostics\"</parameter>"
                    "</function></tool_call>"
                )
            )
        ),
    )

    plan = planner._llm_plan_agent_turn("inspect compile errors", {}, [])

    assert plan is not None
    assert plan["nextStep"] == "call_skill"
    assert plan["skillTool"] == "vrcforge_load_internal_tool_block"
    assert plan["skillParams"] == {"block": "unity/diagnostics"}


def test_provider_native_tool_call_allows_plain_prose_around_one_call() -> None:
    payload = parse_llm_plan_response(
        "好的，我先加载工具。<tool_call><function=load_internal_tool_block>"
        "<parameter=block>unity/diagnostics</parameter></function></tool_call>继续。"
    )

    assert payload == {
        "action": "skill",
        "skill_tool": "load_internal_tool_block",
        "skill_params": {"block": "unity/diagnostics"},
    }


def test_provider_native_skill_selector_wrapper_uses_existing_skill_validation_path() -> None:
    block_tool = PlannerTool(
        name="vrcforge_load_internal_tool_block",
        description="Load one indexed internal tool block.",
        category="read/debug",
        input_schema={
            "type": "object",
            "properties": {"block": {"type": "string"}},
            "required": ["block"],
            "additionalProperties": False,
        },
    )
    planner = service(
        catalog=FakeCatalog(
            planning=PlannerCatalogSnapshot(
                visible_tools=(block_tool,),
                routable_tools=(block_tool,),
            )
        ),
        model=FakeModel(
            result=PlannerModelResult(
                text=(
                    "<tool_call><function=skill_tool_selector>"
                    "<parameter=skill_tool>vrcforge_load_internal_tool_block</parameter>"
                    "<parameter=skill_params>{\"block\":\"unity/diagnostics\"}</parameter>"
                    "<parameter=summary>Load the exact diagnostics leaf.</parameter>"
                    "</function></tool_call>"
                )
            )
        ),
    )

    plan = planner._llm_plan_agent_turn("inspect compile errors", {}, [])

    assert plan is not None
    assert plan["nextStep"] == "call_skill"
    assert plan["skillTool"] == "vrcforge_load_internal_tool_block"
    assert plan["skillParams"] == {"block": "unity/diagnostics"}


@pytest.mark.parametrize(
    "response_text",
    [
        (
            "<tool_call><function=vrcforge_load_internal_tool_block>"
            "<parameter=block>unity/diagnostics</parameter></function></tool_call>"
            "<tool_call><function=vrcforge_get_goal></function></tool_call>"
        ),
        (
            "<tool_call><function=vrcforge_load_internal_tool_block>"
            "<parameter=block>files</parameter><parameter=block>unity/diagnostics</parameter>"
            "</function></tool_call>"
        ),
        (
            "<tool_call><function=vrcforge_load_internal_tool_block>"
            "<parameter=block><nested>unity/diagnostics</nested></parameter>"
            "</function></tool_call>"
        ),
    ],
)
def test_provider_native_tool_call_rejects_ambiguous_or_nested_payloads(response_text: str) -> None:
    assert parse_llm_plan_response(response_text) is None


def test_llm_shell_plan_preserves_bounded_process_options_and_documents_them() -> None:
    model = FakeModel(
        PlannerModelResult(
            json.dumps(
                {
                    "action": "shell",
                    "shell_command": "python worker.py",
                    "shell_params": {
                        "cwd": r"D:\\work",
                        "background": True,
                        "pty": True,
                        "yieldMs": 250,
                        "timeout": 0,
                        "env": {"MODE": "fixture"},
                    },
                }
            )
        )
    )
    planner = service(model=model)

    plan = planner._llm_plan_agent_turn("start the worker", {}, [])

    assert plan is not None
    assert plan["shellNeeded"] is True
    assert plan["shellCommand"] == "python worker.py"
    assert plan["shellParams"] == {
        "cwd": r"D:\\work",
        "background": True,
        "pty": True,
        "yieldMs": 250,
        "timeout": 0,
        "env": {"MODE": "fixture"},
    }
    assert '"shell_params"' in model.prompts[0]
    assert "background/pty/yieldMs/timeout/env" in model.prompts[0]
    assert "Unity" in model.prompts[0]


def test_post_tool_provider_failure_preserves_result_context_without_fake_disconnect() -> None:
    model = FakeModel(
        PlannerModelResult('{"action":"reply","reply":"unused"}'),
        error=RuntimeError("upstream stream ended token=secret"),
    )
    planner = service(model=model)

    plan = planner._llm_plan_agent_turn(
        "continue",
        {},
        [],
        loop_state=[
            {"tool": "vrcforge_scan_materials", "status": "executed", "result": {"materialCount": 3}}
        ],
        context_usage={"requestCount": 1},
        planner_label="DeepSeek · fixture-model",
    )

    assert plan is not None
    assert plan["plannerFailed"] is True
    assert plan["plannerFailure"] == {
        "code": "provider_connection_failed",
        "phase": "post_tool",
        "retryable": True,
        "providerError": {
            "type": "RuntimeError",
            "message": "upstream stream ended token=<redacted>",
        },
    }
    assert plan["providerConnected"] is True
    assert plan["nextStep"] == "planner_failed"
    assert "结果也已保留" in str(plan["reply"])
    assert "没配置" not in str(plan["reply"])
    assert "secret" not in str(plan)


def test_bootstrap_observation_does_not_mislabel_first_provider_failure_as_post_tool() -> None:
    planner = service(
        model=FakeModel(
            PlannerModelResult('{"action":"reply","reply":"unused"}'),
            error=RuntimeError("upstream connection failed"),
        )
    )

    plan = planner._llm_plan_agent_turn(
        "continue",
        {},
        [],
        loop_state=[
            {"tool": "vrcforge_agent_desktop_action", "status": "executed", "result": {"apps": []}}
        ],
        context_usage={"requestCount": 0},
    )

    assert plan is not None
    assert plan["plannerFailure"]["phase"] == "initial"
    assert "providerConnected" not in plan


def test_missing_provider_configuration_is_the_only_explicit_disconnected_failure() -> None:
    model = FakeModel(
        PlannerModelResult('{"action":"reply","reply":"unused"}'),
        error=PlannerProviderNotConfiguredError("missing key"),
    )
    planner = service(model=model)

    plan = planner._llm_plan_agent_turn("continue", {}, [])

    assert plan is not None
    assert plan["providerConnected"] is False
    assert plan["plannerFailure"] == {
        "code": "provider_not_configured",
        "phase": "initial",
        "retryable": False,
    }
    assert plan["nextStep"] == "planner_failed"


def test_background_planner_still_propagates_provider_failure_for_retry_policy() -> None:
    failure = RuntimeError("background provider failed")
    planner = service(
        model=FakeModel(
            PlannerModelResult('{"action":"reply","reply":"unused"}'),
            error=failure,
        )
    )

    with pytest.raises(RuntimeError, match="background provider failed"):
        planner._llm_plan_agent_turn("continue", {}, [], propagate_provider_errors=True)


def test_prompt_and_observation_use_only_typed_read_ports_and_bounded_projection() -> None:
    desktop = FakeDesktop()
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(
                tool("visible-read"),
                tool("activation-read", activation=True),
            ),
            routable_tools=(
                tool("visible-read"),
                tool("activation-read", activation=True),
            ),
            computer_use_model_invocable=False,
        )
    )
    model = FakeModel(PlannerModelResult('{"action":"reply","reply":"done"}'))
    planner = service(catalog=catalog, desktop=desktop, model=model)

    plan = planner.plan_agent_turn(
        "continue",
        {},
        {},
        loop_state=[
            {
                "tool": "vrcforge_agent_desktop_action",
                "status": "executed",
                "result": {
                    "summary": "ok token=secret C:\\private\\file.txt",
                    "payload": "must-not-cross",
                    "itemCount": 4,
                },
            }
        ],
    )

    assert plan["reply"] == "done"
    assert len(model.prompts) == 1
    prompt = model.prompts[0]
    assert "visible-read" in prompt
    assert "activation-read" not in prompt
    assert "desktop-owned-summary" in prompt
    assert "itemCount=4" in prompt
    assert "must-not-cross" not in prompt
    assert desktop.values

    projected = planner_safe_tool_result_fields(
        {
            "summary": "Bearer fixture-secret /private/path",
            "data": {"secret": "must-not-cross"},
            "warningCount": 2,
        }
    )
    assert projected == {
        "summary": "Bearer <redacted> <path redacted>",
        "warningCount": 2,
    }


def test_compaction_port_preserves_success_and_fail_closed_metadata() -> None:
    history = [{"role": "user", "text": "x" * 20_000}]
    usage: dict[str, object] = {
        "exact": True,
        "lastInputTokens": 10_000,
        "lastPromptEstimatedTokens": 5_000,
        "peakInputTokens": 10_000,
    }
    compactor = FakeCompactor(
        {
            "summary": "bounded summary",
            "providerAttempts": 1,
            "entryCount": 1,
            "retainedEntryCount": 1,
            "summaryDigest": "a" * 64,
        }
    )
    planner = service(compactor=compactor)

    replacement, metadata, blocked = planner.maybe_compact_runtime_history(
        message="continue",
        params={"_contextCompactionLimit": 10_000},
        observe={},
        history=history,
        loop_state=[],
        context_usage=usage,
    )
    assert replacement == [{"role": "agent", "text": "bounded summary"}]
    assert metadata is not None and metadata["applied"] is True
    assert blocked is False
    assert usage["compactionCount"] == 1
    assert "lastInputTokens" not in usage
    assert len(compactor.calls) == 1

    failing = service(
        compactor=FakeCompactor({}, error=ConnectionError("provider unavailable"))
    )
    original, failed_metadata, failed_blocked = failing.maybe_compact_runtime_history(
        message="continue",
        params={"_contextCompactionLimit": 10_000},
        observe={},
        history=history,
        loop_state=[],
        context_usage={
            "exact": True,
            "lastInputTokens": 10_000,
            "lastPromptEstimatedTokens": 5_000,
        },
    )
    assert original is history
    assert failed_metadata is not None
    assert failed_metadata["failureClass"] == "transient"
    assert failed_blocked is True


def test_turn_binding_returns_only_nonsecret_metadata_and_always_releases() -> None:
    turn = FakeTurn(
        PlannerTurnMetadata(
            verified_context_limit=64_000,
            planner_label="Fixture Provider · model-a",
        )
    )
    planner = service(turn=turn)

    with pytest.raises(RuntimeError, match="stop"):
        with planner.bind_turn(
            {
                "provider": "fixture",
                "model": "model-a",
                "_requestedContextLimit": 64_000,
            }
        ) as metadata:
            assert metadata == PlannerTurnMetadata(
                verified_context_limit=64_000,
                planner_label="Fixture Provider · model-a",
            )
            assert not hasattr(metadata, "api_key")
            raise RuntimeError("stop")

    assert turn.events == ["enter", "exit"]
    assert turn.requests == [
        {
            "provider": "fixture",
            "model": "model-a",
            "_requestedContextLimit": 64_000,
        }
    ]


def test_compaction_projects_pre_and_post_prompts_with_current_exposure_layer() -> None:
    catalog = FakeCatalog()
    planner = service(
        catalog=catalog,
        compactor=FakeCompactor(
            {
                "summary": "bounded summary",
                "providerAttempts": 1,
                "summaryDigest": "a" * 64,
            }
        ),
    )

    replacement, metadata, blocked = planner.maybe_compact_runtime_history(
        message="continue",
        params={"_contextCompactionLimit": 10_000},
        observe={},
        history=[{"role": "user", "text": "x" * 20_000}],
        loop_state=[],
        context_usage={
            "exact": True,
            "lastInputTokens": 10_000,
            "lastPromptEstimatedTokens": 5_000,
        },
        runtime_exposure_layer=EXPOSURE_LAYER_EXECUTION,
    )

    assert replacement == [{"role": "agent", "text": "bounded summary"}]
    assert metadata is not None and metadata["applied"] is True
    assert blocked is False
    assert catalog.reads == [EXPOSURE_LAYER_EXECUTION, EXPOSURE_LAYER_EXECUTION]


def _function_map(tree: ast.Module, class_name: str | None = None) -> dict[str, ast.FunctionDef]:
    body: list[ast.stmt]
    if class_name is None:
        body = tree.body
    else:
        owner = next(
            item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == class_name
        )
        body = owner.body
    return {item.name: item for item in body if isinstance(item, ast.FunctionDef)}


class _NormalizeEquivalentAst(ast.NodeTransformer):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node = copy.deepcopy(node)
        node.decorator_list = []
        node.returns = None
        node.type_comment = None
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            argument.annotation = None
        if node.args.vararg is not None:
            node.args.vararg.annotation = None
        if node.args.kwarg is not None:
            node.args.kwarg.annotation = None
        node.name = {
            "_plan_agent_turn": "plan_agent_turn",
            "_record_llm_context_usage": "record_context_usage",
        }.get(node.name, node.name)
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in {"Any", "object"}:
            return ast.copy_location(ast.Name(id="Type", ctx=node.ctx), node)
        if node.id in {"AgentGatewayError", "RuntimePlannerError"}:
            return ast.copy_location(ast.Name(id="PlannerError", ctx=node.ctx), node)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        node = self.generic_visit(node)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr in {"llm_planner_label", "_planner_label", "planner_label"}
        ):
            node.attr = "planner_label_state"
        return node


def normalized(node: ast.FunctionDef) -> str:
    transformed = _NormalizeEquivalentAst().visit(node)
    ast.fix_missing_locations(transformed)
    return ast.dump(transformed, include_attributes=False)




def test_gateway_and_dashboard_expose_only_one_typed_runtime_planner_root() -> None:
    gateway_tree = ast.parse((ROOT / "agent_gateway.py").read_text(encoding="utf-8"))
    dashboard_tree = ast.parse((ROOT / "dashboard_server.py").read_text(encoding="utf-8"))
    gateway_owner = next(
        item for item in gateway_tree.body if isinstance(item, ast.ClassDef) and item.name == "AgentGateway"
    )
    old_class_methods = {
        "_plan_agent_turn",
        "_disconnected_local_plan",
        "_local_plan_agent_turn",
        "_plan_runtime_meta_question",
        "_plan_write_intent",
        "_avatars_from_loop_state",
        "_build_avatar_write_params",
        "_llm_plan_agent_turn",
        "_record_llm_context_usage",
        "_maybe_compact_runtime_history",
        "_message_with_runtime_context",
        "_llm_loop_step_observation",
        "_build_llm_plan_prompt",
        "_match_runtime_skill",
        "_match_package_skill_route",
        "_runtime_skill_route",
        "_desktop_action_observation",
    }
    assert not old_class_methods.intersection(
        item.name for item in gateway_owner.body if isinstance(item, ast.FunctionDef)
    )
    old_module_helpers = {
        "parse_llm_plan_response",
        "normalize_llm_plan_result",
        "usage_int",
        "estimate_runtime_context_tokens",
        "classify_runtime_compaction_failure",
        "bounded_runtime_compaction_integer",
        "runtime_compaction_audit_view",
        "runtime_compaction_cancelled_view",
        "_sanitize_planner_tool_observation_text",
        "planner_safe_tool_result_fields",
        "format_planner_tool_observation",
        "extract_shell_command_candidate",
    }
    assert not old_module_helpers.intersection(
        item.name for item in gateway_tree.body if isinstance(item, ast.FunctionDef)
    )
    old_roots = {
        "llm_plan_fn",
        "runtime_context_compact_fn",
        "llm_planner_label",
        "llm_reasoning_trace",
        "llm_context_usage",
    }
    assert not old_roots.intersection(
        item.attr for item in ast.walk(gateway_owner) if isinstance(item, ast.Attribute)
    )
    assert not {
        "_agent_gateway_llm_plan",
        "_agent_gateway_context_compact",
        "verified_runtime_context_limit",
    }.intersection(
        item.name for item in dashboard_tree.body if isinstance(item, ast.FunctionDef)
    )
    bind_calls = [
        item
        for item in ast.walk(dashboard_tree)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "bind_runtime_planner"
    ]
    assert len(bind_calls) == 1


def test_gateway_runtime_planner_binding_is_single_assignment(tmp_path: Path) -> None:
    from agent_gateway import AgentGateway

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    planner = service()
    gateway.bind_runtime_planner(planner)

    assert gateway.runtime_planner is planner
    with pytest.raises(RuntimeError, match="already bound"):
        gateway.bind_runtime_planner(planner)


def test_owner_surface_has_no_dynamic_host_or_execution_authority() -> None:
    source = (ROOT / "runtime_planner_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    owner = next(
        item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "RuntimePlannerService"
    )
    self_attributes = {
        item.attr
        for item in ast.walk(owner)
        if isinstance(item, ast.Attribute)
        and isinstance(item.value, ast.Name)
        and item.value.id == "self"
    }

    assert "getattr(" not in source
    assert "__getattr__" not in source
    assert "_impl" not in source
    assert "sys.modules" not in source
    assert "host:" not in source
    assert "\bAny\b" not in source
    assert not self_attributes.intersection(
        {
            "execute_shell",
            "execute_approved_shell",
            "request_approval",
            "apply_approved",
            "desktop_action",
            "vision_analyze",
            "runtime_message",
            "runtime_session",
        }
    )
    assert self_attributes.intersection({"_catalog", "_desktop", "_model", "_compactor", "_turn"}) == {
        "_catalog",
        "_desktop",
        "_model",
        "_compactor",
        "_turn",
    }
