from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from optimization_workflow_service import (
    OptimizationWorkflowPorts,
    OptimizationWorkflowService,
    OptimizerProofStore,
    OptimizerProofStorePorts,
)


class FakeProofs:
    def list(self, limit: int = 10) -> dict[str, Any]:
        return {"kind": "list", "limit": limit}

    def read(self, run_id: str) -> dict[str, Any]:
        return {"kind": "read", "runId": run_id}

    def screenshot_path(self, run_id: str, stage: str) -> Path:
        return Path(run_id) / f"{stage}.png"


def build_owner(
    *,
    preview: dict[str, Any] | None = None,
    calls: dict[str, list[Any]] | None = None,
) -> OptimizationWorkflowService:
    observed = calls if calls is not None else {}

    def record(name: str, value: Any) -> None:
        observed.setdefault(name, []).append(value)

    def create_apply_request(
        params: dict[str, Any],
        *,
        internal_wrapper: bool = False,
    ) -> dict[str, Any]:
        record("apply", (params, internal_wrapper))
        return {"ok": True, "status": "pending", "approval": params}

    return OptimizationWorkflowService(
        OptimizationWorkflowPorts(
            selected_project_path=lambda: "SelectedProject",
            build_validation_report=lambda params: record("validation", params)
            or {"schema": "vrcforge.validation.v1"},
            build_report=lambda params, validation: record(
                "report", (params, validation)
            )
            or {"schema": "vrcforge.optimization.v1"},
            normalize_tool_name=lambda name: name.strip(),
            build_tool_result=lambda name, params, validation: record(
                "tool", (name, params, validation)
            )
            or {"tool": name, "validation": validation},
            build_apply_preview=lambda params: record("preview", params)
            or dict(preview or {}),
            build_validation_delta=lambda params: record("delta", params)
            or {"schema": "vrcforge.optimization.validation_delta.v1"},
            create_apply_request=create_apply_request,
            proofs=FakeProofs(),
            parameter_bit_packing_tool="vrc_build_parameter_bit_packed_clone",
        )
    )


def test_plan_and_nontrivial_tool_share_one_validation_port() -> None:
    calls: dict[str, list[Any]] = {}
    owner = build_owner(calls=calls)

    assert owner.build_plan({"avatarPath": "Avatar"}) == {
        "schema": "vrcforge.optimization.v1"
    }
    result = owner.build_tool(
        "optimization.texture-vram.audit",
        {"avatarPath": "Avatar"},
    )

    assert result["validation"] == {"schema": "vrcforge.validation.v1"}
    assert len(calls["validation"]) == 2
    assert calls["validation"][0]["projectPath"] == "SelectedProject"
    assert calls["report"][0][1] == {"schema": "vrcforge.validation.v1"}


@pytest.mark.parametrize(
    "tool_name",
    ["optimization.target.profile", "optimization.dependency.doctor"],
)
def test_local_profile_and_dependency_tools_do_not_run_full_validation(
    tool_name: str,
) -> None:
    calls: dict[str, list[Any]] = {}
    owner = build_owner(calls=calls)

    result = owner.build_tool(tool_name, {"projectPath": "P"})

    assert result["validation"] == {}
    assert calls.get("validation") is None


def test_ready_apply_request_preserves_explicit_approval_contract() -> None:
    calls: dict[str, list[Any]] = {}
    owner = build_owner(
        calls=calls,
        preview={
            "readyToRequest": True,
            "blockedReasons": [],
            "externalName": "optimization.vrcfury.parameter-compressor-apply-request",
            "targetTool": "vrcforge_unity_mcp_write",
            "applyArguments": {
                "projectPath": "P",
                "toolName": "vrc_build_parameter_bit_packed_clone",
                "arguments": {},
            },
            "dependency": {"packageIds": []},
        },
    )

    result = owner.request_apply({"tool": "parameter"}, agent_name="test-agent")

    assert result["status"] == "pending"
    request, internal_wrapper = calls["apply"][0]
    assert internal_wrapper is True
    assert request["target_tool"] == "vrcforge_unity_mcp_write"
    assert request["agent_name"] == "test-agent"
    assert request["requires_explicit_approval"] is True
    assert request["never_auto_approve"] is True
    assert "even when global auto mode" in request["explicit_approval_reason"]


def test_missing_dependency_queues_only_supported_install_request() -> None:
    calls: dict[str, list[Any]] = {}
    owner = build_owner(
        calls=calls,
        preview={
            "readyToRequest": False,
            "blockedReasons": ["dependency missing"],
            "externalName": "optimization.lac.apply-request",
            "targetTool": "vrcforge_configure_optimizer_component",
            "applyArguments": {"projectPath": "P"},
            "dependency": {
                "packageIds": ["dev.limitex.avatar-compressor"],
                "vpmRepository": "https://example.invalid/index.json",
            },
            "dependencyInstallPlan": {
                "canExecuteCommandInstall": True,
                "repository": "https://repo.invalid/index.json",
            },
        },
    )

    result = owner.request_apply(
        {"installMissingDependencies": True, "includePrerelease": True}
    )

    assert result["status"] == "pending"
    request, internal_wrapper = calls["apply"][0]
    assert internal_wrapper is True
    assert request["target_tool"] == "vrcforge_install_vpm_package"
    assert request["arguments"] == {
        "projectPath": "P",
        "packageId": "dev.limitex.avatar-compressor",
        "repository": "https://repo.invalid/index.json",
        "includePrerelease": True,
    }
    assert request["requires_explicit_approval"] is True


def test_blocked_preview_never_creates_an_approval() -> None:
    calls: dict[str, list[Any]] = {}
    owner = build_owner(
        calls=calls,
        preview={
            "readyToRequest": False,
            "blockedReasons": ["writer is experimental"],
            "dependency": {"packageIds": []},
        },
    )

    result = owner.request_apply({})

    assert result == {
        "ok": False,
        "status": "blocked",
        "preview": {
            "readyToRequest": False,
            "blockedReasons": ["writer is experimental"],
            "dependency": {"packageIds": []},
        },
        "error": "writer is experimental",
    }
    assert calls.get("apply") is None


def test_validation_and_proof_entrypoints_delegate_only_to_frozen_ports() -> None:
    calls: dict[str, list[Any]] = {}
    owner = build_owner(calls=calls)

    assert owner.build_validation_delta({"before": {}})["schema"].endswith(
        "validation_delta.v1"
    )
    assert owner.list_proofs(7) == {"kind": "list", "limit": 7}
    assert owner.read_proof("run-1") == {"kind": "read", "runId": "run-1"}
    assert owner.proof_screenshot_path("run-1", "before") == Path(
        "run-1/before.png"
    )
    assert calls["delta"] == [{"before": {}}]


def test_proof_store_reads_bounded_report_and_screenshot(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    proof_root = artifact_root / "optimizer-apply-smoke"
    proof_root.mkdir(parents=True)
    image = proof_root / "before.png"
    image.write_bytes(b"png")
    report = {
        "schema": "vrcforge.optimizer_apply_smoke.v1",
        "ok": True,
        "summary": {
            "status": "passed",
            "tool": "optimization.lac.apply-request",
            "checkpointId": "ckpt-1",
            "rollbackDone": True,
            "failedSteps": [],
        },
        "steps": [
            {
                "name": "optimizer.verify_checkpoint_delta",
                "changedFileCount": 2,
            },
            {
                "name": "validation.delta_after_rollback",
                "rollbackProof": {"provided": True},
                "profileDiff": {"pc": {"rankBefore": "Poor"}},
                "parameterBudgetDelta": {"syncedBitsDelta": -8},
            },
        ],
        "visualRegression": {
            "schema": "vrcforge.optimizer_visual_regression.v1",
            "status": "passed",
            "proofPassed": True,
            "requiresHumanReview": False,
            "screenshots": {
                "before": {
                    "captured": True,
                    "artifactOk": True,
                    "exists": True,
                    "artifactImagePath": str(image),
                }
            },
        },
    }
    (proof_root / "run-1.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )
    store = OptimizerProofStore(
        OptimizerProofStorePorts(
            artifact_root=artifact_root,
            to_artifact_url=lambda path: f"artifact:{Path(path).name}",
            to_runtime_artifact_url=lambda path: f"runtime:{Path(path).name}",
        )
    )

    index = store.list(10)
    detail = store.read("run-1")

    assert index["schema"] == "vrcforge.optimization.proof_index.v1"
    assert index["proofs"][0]["changedFileCount"] == 2
    assert index["proofs"][0]["visualRegression"]["screenshots"]["before"][
        "imageUrl"
    ] == "artifact:before.png"
    assert detail["proof"]["parameterBudgetDelta"] == {"syncedBitsDelta": -8}
    assert store.screenshot_path("run-1", "before") == image.resolve()


def test_proof_store_rejects_unbound_ids_stages_and_images(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    proof_root = artifact_root / "optimizer-apply-smoke"
    proof_root.mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"png")
    (proof_root / "run-1.json").write_text(
        json.dumps(
            {
                "visualRegression": {
                    "screenshots": {
                        "before": {"artifactImagePath": str(outside)}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = OptimizerProofStore(
        OptimizerProofStorePorts(
            artifact_root=artifact_root,
            to_artifact_url=lambda _path: "",
            to_runtime_artifact_url=lambda _path: "",
        )
    )

    with pytest.raises(ValueError, match="run id"):
        store.read("../outside")
    with pytest.raises(ValueError, match="stage"):
        store.screenshot_path("run-1", "../before")
    with pytest.raises(PermissionError):
        store.screenshot_path("run-1", "before")
