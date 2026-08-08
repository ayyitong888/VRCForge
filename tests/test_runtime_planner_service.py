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
    PlannerSkill,
    PlannerTool,
    PlannerTurnMetadata,
    RuntimePlannerService,
    planner_safe_tool_result_fields,
)


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class FakeCatalog:
    planning: PlannerCatalogSnapshot = field(default_factory=PlannerCatalogSnapshot)
    execution: PlannerCatalogSnapshot | None = None
    reads: list[str] = field(default_factory=list)

    def read(self, exposure_layer: str) -> PlannerCatalogSnapshot:
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
) -> RuntimePlannerService:
    return RuntimePlannerService(
        catalog=catalog or FakeCatalog(),
        desktop=desktop or FakeDesktop(),
        model=model,
        compactor=compactor,
        turn=turn,
    )


def test_local_shell_meta_and_disconnected_paths_preserve_runtime_contract() -> None:
    planner = service()

    shell = planner.plan_agent_turn("git status", {}, {})
    assert shell["planner"] == "deterministic-local"
    assert shell["shellCommand"] == "git --no-pager status --short"
    assert shell["nextStep"] == "classify_shell"

    disconnected = planner.plan_agent_turn("ordinary conversation", {}, {})
    assert disconnected["deterministicTerminal"] is True
    assert disconnected["providerConnected"] is False
    assert disconnected["nextStep"] == "done"

    metadata = planner.plan_agent_turn(
        "which model did you use for the previous response?",
        {"providerLabel": "Fixture Provider", "model": "fixture-model"},
        {},
    )
    assert metadata["plannerLabel"] == "Fixture Provider · fixture-model"
    assert metadata["deterministicTerminal"] is True
    assert metadata["shellNeeded"] is False
    assert metadata["writeNeeded"] is False

    hidden_health = service(
        catalog=FakeCatalog(
            planning=PlannerCatalogSnapshot(
                visible_tools=(),
                routable_tools=(tool("vrcforge_health", category="read/debug"),),
            )
        )
    ).plan_agent_turn("check health", {}, {})
    assert hidden_health["skillTool"] == "vrcforge_health"
    assert hidden_health["skillCategory"] == "read/debug"


def test_write_intent_scans_resolves_one_avatar_and_rejects_ambiguous_targets() -> None:
    catalog = FakeCatalog(
        planning=PlannerCatalogSnapshot(
            visible_tools=(
                tool("vrcforge_list_avatars"),
                tool("vrcforge_create_gameobject", category="write", write=True),
            ),
            routable_tools=(
                tool("vrcforge_list_avatars"),
                tool("vrcforge_create_gameobject", category="write", write=True),
            ),
        )
    )
    planner = service(catalog=catalog)

    scan = planner.plan_agent_turn("create an object named Probe", {}, {})
    assert scan["skillTool"] == "vrcforge_list_avatars"
    assert scan["continueLoop"] is True
    assert scan["writeNeeded"] is False

    resolved = planner.plan_agent_turn(
        "create an object named Probe",
        {"projectPath": "fixture-project"},
        {},
        loop_state=[
            {
                "tool": "vrcforge_list_avatars",
                "status": "executed",
                "result": {"avatars": [{"avatarPath": "AvatarRoot"}]},
            }
        ],
    )
    assert resolved["writeTool"] == "vrcforge_create_gameobject"
    assert resolved["writeParams"] == {
        "name": "Probe",
        "parentPath": "AvatarRoot",
        "preview": False,
        "writeIntent": "add_object",
        "targetAvatar": "AvatarRoot",
        "projectPath": "fixture-project",
    }
    assert resolved["nextStep"] == "request_write"

    ambiguous = planner.plan_agent_turn(
        "create an object",
        {},
        {},
        loop_state=[
            {
                "tool": "vrcforge_list_avatars",
                "result": {"avatarPaths": ["AvatarA", "AvatarB"]},
            }
        ],
    )
    assert ambiguous["deterministicTerminal"] is True
    assert ambiguous["writeNeeded"] is False

    conflicting = planner.plan_agent_turn(
        "create an object at the active scene root",
        {"avatarPath": "AvatarRoot"},
        {},
    )
    assert conflicting["deterministicTerminal"] is True
    assert conflicting["writeNeeded"] is False


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
