from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_gateway import AgentGateway


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
