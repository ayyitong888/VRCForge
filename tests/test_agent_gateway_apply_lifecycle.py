from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_gateway import AgentGateway, AgentGatewayError
from approved_unity_execution import current_approved_unity_execution


def create_project(root: Path) -> Path:
    project = root / "UnityProject"
    (project / "Assets").mkdir(parents=True)
    (project / "Packages").mkdir()
    (project / "ProjectSettings").mkdir()
    (project / "Assets" / "baseline.txt").write_text("before", encoding="utf-8")
    (project / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.22f1\n",
        encoding="utf-8",
    )
    return project


def approved_write(
    gateway: AgentGateway,
    project: Path,
    *,
    handler,
) -> dict[str, object]:
    gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
    gateway.register_write_handler(
        "vrcforge_test_lifecycle_write",
        "Lifecycle write",
        "high",
        handler,
    )
    request = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_test_lifecycle_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    approval_id = request["approval"]["id"]
    gateway.approve(approval_id)
    return gateway.apply_approved({"approval_id": approval_id})


def test_argument_digest_requires_internal_opt_in(tmp_path: Path) -> None:
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.register_write_handler(
        "vrcforge_test_argument_binding",
        "Argument binding test.",
        "high",
        lambda _arguments: {"ok": True},
    )
    arguments = {
        "projectRoot": str(tmp_path / "UnityProject"),
        "references": {"mergeTarget": "FixtureAvatar/Armature"},
    }

    ordinary = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_test_argument_binding",
            "arguments": arguments,
        }
    )
    bound = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_test_argument_binding",
            "arguments": arguments,
        },
        include_arguments_digest=True,
    )

    expected = hashlib.sha256(
        json.dumps(
            arguments,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert "argumentsDigest" not in ordinary["approval"]
    assert bound["approval"]["argumentsDigest"] == expected


def test_lifecycle_observer_runs_at_authoritative_write_boundaries(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    events: list[str] = []
    gateway.apply_lifecycle_observer_fn = (
        lambda stage, _payload: events.append(stage)
    )

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        events.append("write_handler")
        return {"ok": True, "sceneSaved": True}

    result = approved_write(gateway, project, handler=handler)

    assert result["ok"] is True
    assert events == [
        "approval_started",
        "checkpoint_created",
        "handler_starting",
        "write_handler",
        "handler_returned",
    ]


def test_core_write_handler_receives_one_use_context_only_after_checkpoint(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
    observed: dict[str, object] = {}
    observed_context: dict[str, object] = {}
    observed_claim: dict[str, object] = {}

    def handler(arguments: dict[str, object]) -> dict[str, object]:
        observed.update(arguments)
        plan = current_approved_unity_execution()
        assert plan is not None
        observed_context.update(plan.diagnostic_context())
        tool_name = str(arguments["toolName"])
        tool_arguments = dict(arguments["arguments"])
        claim = plan.claim(tool_name, tool_arguments, project)
        observed_claim.update(claim.execution_context)
        claim.complete()
        return {"ok": True}

    gateway.register_write_handler(
        "vrcforge_unity_mcp_write",
        "Core write context test.",
        "high",
        handler,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda arguments: [
            (str(arguments["toolName"]), dict(arguments["arguments"]))
        ],
    )
    request = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_unity_mcp_write",
            "arguments": {
                "projectRoot": str(project),
                "toolName": "vrc_create_gameobject",
                "arguments": {"name": "CreatedByVRCForge"},
            },
        }
    )
    approval_id = str(request["approval"]["id"])
    assert request["approval"]["approvedUnityExecutionPlan"]["calls"][0]["toolName"] == "vrc_create_gameobject"
    gateway.approve(approval_id)
    result = gateway.apply_approved({"approval_id": approval_id})

    assert result["ok"] is True
    context = observed_context
    assert "_vrcforge_approved_execution" not in observed
    assert context["lane"] == "approved_write"
    assert context["approvalId"] == approval_id
    assert context["checkpointId"] == result["checkpoint"]["id"]
    assert context["targetTool"] == "vrcforge_unity_mcp_write"
    assert context["issuedAtUnixMs"] < context["expiresAtUnixMs"]
    assert context["expiresAtUnixMs"] - context["issuedAtUnixMs"] == 60_000
    assert len(context["executionId"]) >= 24
    assert observed_claim["targetTool"] == "vrc_create_gameobject"
    assert observed_claim["gatewayTargetTool"] == "vrcforge_unity_mcp_write"
    assert observed_claim["executionId"] == context["executionId"]
    assert current_approved_unity_execution() is None


def test_core_write_handler_without_exact_plan_fails_before_approval(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.register_write_handler(
        "vrcforge_unplanned_core_write",
        "Unplanned Core write.",
        "high",
        lambda _arguments: {"ok": True},
        requires_approved_execution_context=True,
    )

    with pytest.raises(AgentGatewayError, match="exact Core execution plan"):
        gateway.create_apply_request(
            {
                "target_tool": "vrcforge_unplanned_core_write",
                "arguments": {"projectRoot": str(project)},
            }
        )


def test_core_write_plan_drift_after_approval_blocks_handler(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
    handler_calls = 0

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        return {"ok": True}

    gateway.register_write_handler(
        "vrcforge_planned_core_write",
        "Planned Core write.",
        "high",
        handler,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _arguments: [("vrc_create_gameobject", {"name": "before"})],
    )
    request = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_planned_core_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    approval_id = str(request["approval"]["id"])
    gateway._write_handlers["vrcforge_planned_core_write"].approved_execution_plan_builder = (  # noqa: SLF001
        lambda _arguments: [("vrc_create_gameobject", {"name": "after"})]
    )
    gateway.approve(approval_id)

    result = gateway.apply_approved({"approval_id": approval_id})

    assert result["ok"] is False
    assert "plan drifted" in result["error"]
    assert handler_calls == 0


def test_core_write_handler_must_consume_the_whole_frozen_plan(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
    gateway.register_write_handler(
        "vrcforge_incomplete_core_write",
        "Incomplete Core write.",
        "high",
        lambda _arguments: {"ok": True},
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _arguments: [("vrc_create_gameobject", {"name": "never-called"})],
    )
    request = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_incomplete_core_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    approval_id = str(request["approval"]["id"])
    gateway.approve(approval_id)

    result = gateway.apply_approved({"approval_id": approval_id})

    assert result["ok"] is False
    assert "not consumed exactly" in result["error"]


def test_failed_handler_burns_leaked_execution_plan(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
    leaked = []

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        plan = current_approved_unity_execution()
        assert plan is not None
        leaked.append(plan)
        raise RuntimeError("handler failed")

    gateway.register_write_handler(
        "vrcforge_failed_core_write",
        "Failed Core write.",
        "high",
        handler,
        requires_approved_execution_context=True,
        approved_execution_plan_builder=lambda _arguments: [
            ("vrc_create_gameobject", {"name": "must-not-run"})
        ],
    )
    request = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_failed_core_write",
            "arguments": {"projectRoot": str(project)},
        }
    )
    approval_id = str(request["approval"]["id"])
    gateway.approve(approval_id)
    result = gateway.apply_approved({"approval_id": approval_id})

    assert result["ok"] is False
    assert len(leaked) == 1
    with pytest.raises(Exception, match="uncertain and closed"):
        leaked[0].claim(
            "vrc_create_gameobject",
            {"name": "must-not-run"},
            project,
        )


def test_checkpoint_observer_failure_aborts_before_write(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    handler_calls = 0

    def observer(stage: str, _payload: dict[str, object]) -> None:
        if stage == "checkpoint_created":
            raise RuntimeError("observer rejected checkpoint")

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        return {"ok": True, "sceneSaved": True}

    gateway.apply_lifecycle_observer_fn = observer
    result = approved_write(gateway, project, handler=handler)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["checkpoint"]["ok"] is True
    assert handler_calls == 0
    assert gateway.list_interrupted_apply_recoveries()["activeCount"] == 0


def test_post_write_observer_failure_enters_checkpoint_recovery(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    handler_calls = 0

    def observer(stage: str, _payload: dict[str, object]) -> None:
        if stage == "handler_returned":
            raise RuntimeError("observer rejected result")

    def handler(arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        Path(str(arguments["projectRoot"]), "Assets", "generated.txt").write_text(
            "after",
            encoding="utf-8",
        )
        return {"ok": True, "sceneSaved": True}

    gateway.apply_lifecycle_observer_fn = observer
    result = approved_write(gateway, project, handler=handler)

    assert result["ok"] is False
    assert result["checkpoint"]["ok"] is True
    assert handler_calls == 1
    recoveries = gateway.list_interrupted_apply_recoveries()
    assert recoveries["blockingWrites"] is True
    assert recoveries["activeCount"] == 1
    assert recoveries["recoveries"][0]["checkpointId"] == result["checkpoint"]["id"]


def test_handler_starting_observer_failure_aborts_before_write(tmp_path: Path) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    handler_calls = 0

    def observer(stage: str, _payload: dict[str, object]) -> None:
        if stage == "handler_starting":
            raise RuntimeError("observer rejected write boundary")

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        return {"ok": True, "sceneSaved": True}

    gateway.apply_lifecycle_observer_fn = observer
    result = approved_write(gateway, project, handler=handler)

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["checkpoint"]["ok"] is True
    assert handler_calls == 0
    assert gateway.list_interrupted_apply_recoveries()["activeCount"] == 0


def test_final_handler_arguments_are_bound_after_constraint_refresh(
    tmp_path: Path,
) -> None:
    project = create_project(tmp_path)
    gateway = AgentGateway(tmp_path / "config" / "gateway.json", tmp_path / "audit")
    gateway.checkpoint_prepare_handler = lambda _path: {"ok": True}
    handler_calls = 0
    observed_digest = ""

    def handler(_arguments: dict[str, object]) -> dict[str, object]:
        nonlocal handler_calls
        handler_calls += 1
        return {"ok": True, "sceneSaved": True}

    gateway.register_write_handler(
        "vrcforge_test_constraint_binding",
        "Constraint binding test.",
        "high",
        handler,
    )
    request = gateway.create_apply_request(
        {
            "target_tool": "vrcforge_test_constraint_binding",
            "arguments": {"projectRoot": str(project)},
        },
        include_arguments_digest=True,
    )
    expected_digest = str(request["approval"]["argumentsDigest"])
    gateway.user_constraints_path.write_text(
        "Keep generated assets inside the project.\n",
        encoding="utf-8",
    )

    def observer(stage: str, payload: dict[str, object]) -> None:
        nonlocal observed_digest
        if stage != "handler_starting":
            return
        observed_digest = str(payload.get("argumentsDigest") or "")
        if observed_digest != expected_digest:
            raise RuntimeError("final handler arguments changed")

    gateway.apply_lifecycle_observer_fn = observer
    approval_id = str(request["approval"]["id"])
    gateway.approve(approval_id)
    result = gateway.apply_approved({"approval_id": approval_id})

    assert result["ok"] is False
    assert observed_digest and observed_digest != expected_digest
    assert handler_calls == 0
