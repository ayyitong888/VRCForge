from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import dashboard_server as server
from background_goal_runtime import RuntimeLaneBudget
from sub_agent_collaboration_service import (
    SubAgentCollaborationPorts,
    SubAgentCollaborationService,
    SubAgentExecutionPorts,
)
from sub_agent_delegate import build_sub_agent_roles
from sub_agent_tasks import SubAgentTaskRegistry
from tests.test_native_permission_modes import _set_mode, _write_gateway_for_mode
from tests.test_native_runtime_gateway import call, finish, setup_gateway


@pytest.mark.parametrize("delivery", [
    "reverse", "together", "failed", "paused", "stop", "capacity", "stop_launch",
    "restart_pending", "restart_capacity", "restart_claimed", "mixed_write", "chain_stop",
])
def test_native_parent_waits_for_both_delegated_reads(tmp_path, monkeypatch, delivery):
    batch = call("call-first", "vrcforge_delegate_subagent", {"role": "llm_readonly", "task": "first"})
    mixed_write = delivery == "mixed_write"
    project = str(tmp_path / "UnityProject")
    second = (
        call("call-second", "fixture_write", {"projectRoot": project, "value": "approved"})
        if mixed_write else
        call("call-second", "vrcforge_delegate_subagent", {"role": "llm_readonly", "task": "second"})
    )
    batch["tool_calls"].extend(second["tool_calls"])
    single_child = mixed_write or delivery in {"capacity", "restart_capacity", "stop_launch"}
    stopped = delivery in {"stop", "stop_launch"}
    restart_pending = delivery in {"restart_pending", "restart_capacity"}
    summary = (
        "Parent reported the available findings and the unresolved child."
        if delivery in {"failed", "paused", "capacity", "restart_capacity"} else
        "Parent consolidated the two reports."
    )
    final = (
        call("partial-parent", "vrcforge_runtime_action", {
            "action": "reply", "reply": summary, "completion_claim": {"satisfied": False},
        })
        if delivery in {"paused", "capacity", "restart_capacity"} else {"role": "assistant", "content": summary}
    )
    if delivery == "chain_stop":
        final = call("chain-first", "vrcforge_delegate_subagent", {"role": "llm_readonly", "task": "chain-first"})
        final["tool_calls"].extend(
            call("chain-second", "vrcforge_delegate_subagent", {
                "role": "llm_readonly", "task": "chain-second",
            })["tool_calls"]
        )
    if mixed_write:
        gateway, model, writes = _write_gateway_for_mode(tmp_path, [batch, finish])
        _set_mode(gateway, "approval")
    else:
        gateway, model, _ = setup_gateway(tmp_path, [batch, final])
    started = {name: threading.Event() for name in ("first", "second", "chain-first", "chain-second")}
    release = {name: threading.Event() for name in started}
    delivered = []
    launch_return = threading.Event()
    launch_return.set()
    stop_identity = []
    launching = threading.Event()

    def worker(params, cancellation):
        name = params["task"]
        started[name].set()
        assert release[name].wait(5)
        if delivery == "failed" and name == "first":
            return {"ok": False, "error": "first missing", "summaryText": "first finding"}
        if delivery == "paused" and name == "first":
            return {"ok": False, "status": "paused", "resumable": True, "summaryText": "first finding"}
        return {"ok": True, "summaryText": name + " finding"}

    def deliver(event):
        delivered.append(gateway.resume_runtime_task_after_sub_agent(event))

    roles = build_sub_agent_roles()
    service = SubAgentCollaborationService(SubAgentCollaborationPorts(
        artifact_dir=tmp_path / "sub-agents",
        gateway=SubAgentExecutionPorts(
            execute=gateway.runtime_skills.execute,
            run_sub_agent_llm=gateway.run_sub_agent_llm,
            wait_runtime_session_idle=gateway.wait_runtime_session_idle,
        ),
        lane_budget=RuntimeLaneBudget(),
        build_roles=lambda: roles,
        build_handlers=lambda _ports: {"llm_readonly": worker},
        task_finished=deliver,
    ))
    registry = service._registry
    if delivery in {"capacity", "restart_capacity"}:
        registry.max_concurrent = 1
    if restart_pending:
        monkeypatch.setattr(registry, "_wait_parent_idle", lambda *_args, **_kwargs: False)
    if delivery == "restart_claimed":
        record = registry._record_parent_delivery_locked

        def interrupt_delivery_record(task, status, **details):
            if status == "delivered":
                raise OSError("Process exited before its delivery receipt.")
            return record(task, status, **details)

        monkeypatch.setattr(registry, "_record_parent_delivery_locked", interrupt_delivery_record)
    monkeypatch.setattr(server, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(server, "SUB_AGENT_COLLABORATION", service)
    gateway.bind_runtime_subagent_control(
        active_parent_task_ids=registry.active_parent_task_ids, cancel_task=registry.cancel_task,
        parent_delivery_pending=registry.parent_delivery_pending,
    )
    def delegate(params):
        result = server.delegate_sub_agent_runtime(params)
        if delivery == "stop_launch" and params["task"] == "first":
            seed = params["_taskSeed"]
            stop_identity.append({key: seed[key] for key in ("sessionId", "turnId", "clientTurnId")})
            launching.set()
            assert launch_return.wait(5)
        return result

    gateway.register_tool(
        "vrcforge_delegate_subagent",
        "When to use: delegate a read. When NOT to use: write.",
        "plan/preview", delegate,
    )
    try:
        identity = {"sessionId": "batch-parent", "clientTurnId": "batch-input"}
        request = {**identity, "message": "Run the independent reads."}
        if mixed_write:
            request.update(projectRoot=project, message="Read first, then request approval to write.")
        if delivery == "stop_launch":
            launch_return.clear()
            with ThreadPoolExecutor(max_workers=1) as pool:
                original_future = pool.submit(gateway.runtime_message, request)
                assert launching.wait(5)
                assert gateway.request_runtime_cancel(stop_identity[0])["dispatchStopped"]
                launch_return.set()
                original = original_future.result(5)
            assert original["plan"]["nextStep"] == "cancelled"
        else:
            original = gateway.runtime_message(request)
            assert original["plan"]["reason"] == "sub_agent_pending", original["plan"]
        assert started["first"].wait(2)
        if not single_child:
            assert started["second"].wait(2)
        tasks = registry.list_tasks()["tasks"]
        assert len(tasks) == (1 if single_child else 2)
        if not single_child:
            assert tasks[0]["dispatchBatchId"] and tasks[0]["dispatchBatchId"] == tasks[1]["dispatchBatchId"]
        threads = {task["task"]: registry._threads[task["id"]] for task in tasks}
        if delivery == "reverse":
            release["second"].set()
            threads["second"].join(3)
            assert len(model.requests) == 1 and not delivered
        if delivery == "stop":
            assert gateway.request_runtime_cancel({**identity, "turnId": original["turnId"]})["ok"]
        release["first"].set()
        release["second"].set()
        for thread in threads.values():
            thread.join(5)
        assert all(not thread.is_alive() for thread in threads.values())
        if delivery == "chain_stop":
            assert len(delivered) == 1 and len(model.requests) == 2
            current = delivered[0]
            assert current["plan"]["reason"] == "sub_agent_pending"
            assert started["chain-first"].wait(2) and started["chain-second"].wait(2)
            chained = [task for task in registry.list_tasks()["tasks"] if task["task"].startswith("chain-")]
            assert len(chained) == 2 and all(task["parentTurnId"] == current["turnId"] for task in chained)
            chain_threads = [registry._threads[task["id"]] for task in chained]
            stop = gateway.request_runtime_cancel({
                key: current[key] for key in ("sessionId", "turnId", "clientTurnId")
            })
            assert set(stop["cancelledSubAgentTaskIds"]) == {task["id"] for task in chained}
            release["chain-first"].set()
            release["chain-second"].set()
            for thread in chain_threads:
                thread.join(5)
            assert len(model.requests) == len(delivered) == 2
            assert delivered[-1]["plan"]["nextStep"] == "cancelled"
            return
        if mixed_write:
            assert len(model.requests) == len(delivered) == 1
            assert not writes
            assert delivered[0]["write"]["status"] == "approval_pending"
            approval_id = delivered[0]["approval_id"]
            approval = gateway._approvals[approval_id]
            gateway.approval_transactions.approve(approval_id)
            applied = gateway.approval_transactions.apply_approved({"approval_id": approval_id})
            assert applied["status"] == "applied"
            assert gateway.resume_runtime_task_after_approval(approval, applied)["plan"]["nextStep"] == "done"
            assert len(writes) == 1 and len(model.requests) == 2
            return
        if restart_pending or delivery == "restart_claimed":
            registry.shutdown()
            recovered_gateway, recovered_model, _ = setup_gateway(tmp_path, [final])
            registry = SubAgentTaskRegistry(
                tmp_path / "sub-agents", roles, {"llm_readonly": worker},
                task_finished=lambda event: delivered.append(
                    recovered_gateway.resume_runtime_task_after_sub_agent(event),
                ),
                wait_parent_idle=recovered_gateway.wait_runtime_session_idle,
                reconcile_on_init=False,
            )
            assert registry.reconcile_startup()
            if delivery == "restart_claimed":
                assert len(delivered) == 1 and len(model.requests) == 2
                assert not recovered_model.requests
                assert len(registry.interrupted_parent_continuations()) == 1
                assert {task["parentContinuationStatus"] for task in registry.list_tasks()["tasks"]} == {"interrupted"}
                return
            assert len(model.requests) == 1
            model = recovered_model
        assert len(delivered) == 1
        assert len(model.requests) == (1 if stopped or restart_pending else 2)
        for task in registry.list_tasks()["tasks"]:
            assert task["parentContinuationStatus"] == "delivered"
        if stopped:
            assert delivered[0]["plan"]["nextStep"] == "cancelled"
            return
        output = [message for message in model.requests[-1]["messages"] if message["role"] == "tool"]
        outputs_by_id = {message["tool_call_id"]: json.loads(message["content"]) for message in output}
        assert len(output) == len(outputs_by_id) == 2
        assert set(outputs_by_id) == {"call-first", "call-second"}
        assert "first finding" in json.dumps(outputs_by_id["call-first"])
        assert outputs_by_id["call-first"]["status"] == (
            delivery if delivery in {"failed", "paused"} else "completed"
        )
        if single_child:
            assert "concurren" in json.dumps(outputs_by_id["call-second"]).lower()
        else:
            assert "second finding" in json.dumps(outputs_by_id["call-second"])
        assert delivered[0]["plan"]["reply"] == summary
    finally:
        launch_return.set()
        for gate in release.values():
            gate.set()
        registry.shutdown(timeout_seconds=5)
