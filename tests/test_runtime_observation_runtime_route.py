from __future__ import annotations

from types import SimpleNamespace

import dashboard_server as ds
from agent_gateway import AgentGateway


def test_pending_observation_status_uses_registered_runtime_read_route(monkeypatch, tmp_path):
    gateway = ds.AGENT_GATEWAY
    tool_name = "vrcforge_get_runtime_observation"
    tool = gateway._tools[tool_name]
    job_id = "00000000000000000000000000000001"  # fixture-job-existing
    avatar_path = "Avatar"
    project_root = tmp_path / "unity-project"
    from tests.test_execution_target_reference_equivalence import _live_target

    target = _live_target(project_root, scope="avatar")
    observed = []

    def read_pending_job(arguments):
        observed.append(arguments)
        return {
            "schema": "vrcforge.runtime_observation.v1",
            "jobId": job_id,
            "avatarPath": avatar_path,
            "coreIdentity": "fixture-core",
            "status": "pending",
            "ok": True,
            "verified": False,
            "mutationStarted": True,
            "commitState": "pending",
            "frames": [{
                "sampleIndex": 0,
                "unityFrame": 12,
                "actualElapsedSeconds": 0.5,
                "states": [{
                    "controllerIndex": 0,
                    "layerIndex": 0,
                    "layerName": "Outfit",
                    "stateHash": 1,
                }],
            }],
        }

    monkeypatch.setattr(ds, "runtime_observation_status_raw", read_pending_job)

    with gateway.resource_context("runtime:runtime-route-regression"):
        execution_target = gateway.bind_execution_target(
            target, project_root=str(project_root)
        )["executionTarget"]
        result = gateway.runtime_skills.execute(
            tool_name,
            {
                "projectPath": str(project_root),
                "avatarPath": avatar_path,
                "jobId": job_id,
                "stateDetail": "inline",
                "stateSelection": {"layerName": "Outfit"},
                "executionTarget": execution_target,
            },
            "runtime-route-regression",
        )

    assert tool.handler is ds.runtime_observation_status_sync
    assert tool.write is False
    assert result["status"] == "pending", result
    assert result["outcome"]["status"] == "pending"
    assert result["result"]["status"] == "pending"
    assert len(observed) == 1
    assert observed[0]["jobId"] == job_id
    assert observed[0]["avatarPath"] == avatar_path
    assert observed[0]["executionTarget"] == target


def test_runtime_observation_start_remains_in_supervised_write_registry():
    gateway = ds.AGENT_GATEWAY
    start = "vrcforge_start_runtime_observation"
    assert start not in gateway._tools
    handler = gateway._write_handlers[start]
    assert handler.requires_approved_execution_context is True
    assert handler.verification_finalize_handler is ds.runtime_observation_finalize


def test_runtime_observation_category_does_not_authorize_write_tools(tmp_path, monkeypatch):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    calls = []
    gateway.register_tool(
        "vrcforge_unapproved_observation_write",
        "fixture write",
        "runtime/observation",
        lambda params: calls.append(params) or {"ok": True},
        write=True,
    )
    monkeypatch.setattr(gateway, "ensure_config", lambda: SimpleNamespace(allow_write_requests=True))

    result = gateway.runtime_skills.execute(
        "vrcforge_unapproved_observation_write", {}, "runtime-route-regression"
    )

    assert result["status"] == "blocked"
    assert result["error"] == "This skill cannot run directly from the runtime loop."
    assert calls == []
