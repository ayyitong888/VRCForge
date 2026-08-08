from __future__ import annotations

import ast
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_command_safety import is_path_within, strip_quotes, tokenize_command
from agent_gateway import AgentGateway, AgentGatewayError
from agent_shell_service import (
    AgentShellError,
    AgentShellPorts,
    AgentShellService,
    ShellApprovalPorts,
    ShellApprovalRequest,
    ShellProcessPorts,
    command_hash,
    stable_hash,
)


@dataclass
class FakeApprovals:
    mode: str = "approval"
    automatic: bool = False
    block_create: bool = False
    approvals: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[ShellApprovalRequest] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    create_entered: threading.Event = field(default_factory=threading.Event)
    release_create: threading.Event = field(default_factory=threading.Event)

    def ports(self) -> ShellApprovalPorts:
        return ShellApprovalPorts(
            find_pending_shell=self.find_pending_shell,
            create=self.create,
            update_metadata=self.update_metadata,
            find=lambda approval_id: self.approvals.get(approval_id),
            apply=self.apply,
            auto_enabled=lambda: self.automatic,
            auto_execute=lambda _approval: None,
            execution_mode=lambda: self.mode,
            read_user_constraints=lambda: {"active": True},
            redact=lambda approval: {**approval, "redacted": True},
        )

    def find_pending_shell(self, session_id: str, turn_id: str) -> dict[str, Any] | None:
        return next(
            (
                approval
                for approval in self.approvals.values()
                if approval.get("status") == "pending"
                and approval.get("targetTool") == "vrcforge_shell_execute"
                and approval.get("sessionId") == session_id
                and approval.get("turnId") == turn_id
            ),
            None,
        )

    def create(self, request: ShellApprovalRequest) -> dict[str, Any]:
        self.create_entered.set()
        if self.block_create:
            assert self.release_create.wait(timeout=2)
        self.requests.append(request)
        approval_id = f"approval-{len(self.requests)}"
        approval = {
            "id": approval_id,
            "status": "pending",
            "targetTool": request.target_tool,
            "arguments": request.arguments,
        }
        self.approvals[approval_id] = approval
        return approval

    def update_metadata(self, approval_id: str, metadata: dict[str, Any]) -> None:
        self.approvals[approval_id].update(metadata)

    def apply(self, approval_id: str) -> dict[str, Any]:
        self.applied.append(approval_id)
        return {"ok": True, "approvalId": approval_id}


class FakeProcess:
    _next_pid = 9000

    def __init__(self, *, blocking: bool = False) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.returncode: int | None = None
        self.blocking = blocking
        self.killed = False
        self.communicate_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def communicate(self, *, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_calls += 1
        if self.killed:
            self.returncode = -9
            return "", "terminated"
        if self.blocking:
            time.sleep(0.005)
            raise subprocess.TimeoutExpired("fixture", timeout or 0)
        self.returncode = 0
        return "fixture stdout", ""

    def kill(self) -> None:
        self.killed = True


@dataclass
class FakeProcessOwner:
    blocking: bool = False
    block_spawn: bool = False
    processes: list[FakeProcess] = field(default_factory=list)
    spawned: threading.Event = field(default_factory=threading.Event)
    spawn_entered: threading.Event = field(default_factory=threading.Event)
    release_spawn: threading.Event = field(default_factory=threading.Event)

    def ports(self) -> ShellProcessPorts:
        return ShellProcessPorts(
            spawn=self.spawn,
            terminate_tree=self.terminate,
            environment=lambda: {"FIXTURE": "1"},
            monotonic=time.monotonic,
            utc_now=lambda: "2026-08-08T00:00:00+00:00",
            sleep=time.sleep,
        )

    def spawn(self, *_args: Any, **_kwargs: Any) -> FakeProcess:
        self.spawn_entered.set()
        if self.block_spawn:
            assert self.release_spawn.wait(timeout=2)
        process = FakeProcess(blocking=self.blocking)
        self.processes.append(process)
        self.spawned.set()
        return process

    @staticmethod
    def terminate(process: FakeProcess) -> None:
        process.kill()


def service(
    root: Path,
    *,
    approvals: FakeApprovals | None = None,
    processes: FakeProcessOwner | None = None,
    cancellation_requested=lambda _session, _turn, _client: False,
) -> tuple[AgentShellService, FakeApprovals, FakeProcessOwner, list[dict[str, Any]]]:
    approval_owner = approvals or FakeApprovals()
    process_owner = processes or FakeProcessOwner()
    audits: list[dict[str, Any]] = []
    shell = AgentShellService(
        AgentShellPorts(
            approvals=approval_owner.ports(),
            append_audit=audits.append,
            permission_audit_context=lambda: {"permissionMode": "approval"},
            cancellation_requested=cancellation_requested,
            default_workspace_root=lambda: root,
        ),
        process_ports=process_owner.ports(),
    )
    return shell, approval_owner, process_owner, audits


def approved_payload(root: Path, *, command: str = "Get-ChildItem", timeout: int = 120) -> dict[str, Any]:
    resolved = root.resolve()
    return {
        "command": command,
        "command_hash": command_hash(command),
        "cwd": str(resolved),
        "cwd_hash": stable_hash(str(resolved)),
        "workspace_root": str(resolved),
        "workspace_root_hash": stable_hash(str(resolved)),
        "timeout_seconds": timeout,
        "timeout_hash": stable_hash(str(timeout)),
        "session_id": "session-1",
        "turn_id": "turn-1",
    }


def test_shared_command_and_path_safety_are_conservative(tmp_path: Path) -> None:
    assert tokenize_command('rg "needle value" .') == ["rg", '"needle value"', "."]
    assert strip_quotes('"needle value"') == "needle value"
    assert tokenize_command('"unterminated') == []
    assert is_path_within(tmp_path / "nested" / "file.txt", tmp_path)
    assert not is_path_within(tmp_path.parent / "outside.txt", tmp_path)


def test_classification_preserves_plan_vs_execute_and_workspace_boundaries(tmp_path: Path) -> None:
    shell, _approvals, _processes, _audits = service(tmp_path)

    assert shell.classify({"command": "Get-ChildItem"})["risk"] == "low"
    assert shell.classify({"command": "git --no-pager status --short"})["risk"] == "low"
    assert shell.classify({"command": "Remove-Item file.txt"})["risk"] == "high"
    assert shell.classify({"command": "Get-ChildItem; Remove-Item file.txt"})["risk"] == "high"
    assert shell.classify({"command": "Get-ChildItem ../outside"})["risk"] == "high"
    outside = shell.classify({"command": "Get-ChildItem", "cwd": str(tmp_path.parent)})
    assert outside["risk"] == "high"
    assert shell.classify({"command": ""})["risk"] == "reject"


def test_high_risk_approval_binds_command_cwd_workspace_timeout_and_hashes(tmp_path: Path) -> None:
    approvals = FakeApprovals(mode="auto")
    shell, approvals, processes, audits = service(tmp_path, approvals=approvals)

    result = shell.execute(
        {
            "command": "Remove-Item file.txt",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "timeout_seconds": 45,
        },
        agent_name="fixture-agent",
    )

    assert result["status"] == "pending_approval"
    assert processes.processes == []
    request = approvals.requests[0]
    arguments = request.arguments
    assert request.requires_explicit_approval is True
    assert "Delete/removal" in request.explicit_approval_reason
    assert arguments["command_hash"] == command_hash(arguments["command"])
    assert arguments["cwd_hash"] == stable_hash(arguments["cwd"])
    assert arguments["workspace_root_hash"] == stable_hash(arguments["workspace_root"])
    assert arguments["timeout_seconds"] == 45
    assert arguments["timeout_hash"] == stable_hash("45")
    assert audits[-1]["event"] == "shell_approval_requested"

    duplicate = shell.execute(
        {
            "command": "Remove-Item other.txt",
            "session_id": "session-1",
            "turn_id": "turn-1",
        }
    )
    assert duplicate["approval"]["redacted"] is True
    assert len(approvals.requests) == 1


def test_high_risk_shell_does_not_reuse_other_write_approval(tmp_path: Path) -> None:
    approvals = FakeApprovals(
        approvals={
            "other-write": {
                "id": "other-write",
                "status": "pending",
                "targetTool": "vrcforge_other_write",
                "sessionId": "session-1",
                "turnId": "turn-1",
            }
        }
    )
    shell, approvals, processes, _audits = service(tmp_path, approvals=approvals)

    result = shell.execute(
        {
            "command": "Remove-Item file.txt",
            "session_id": "session-1",
            "turn_id": "turn-1",
        }
    )

    assert result["status"] == "pending_approval"
    assert result["approval_id"] != "other-write"
    assert result["approval"]["targetTool"] == "vrcforge_shell_execute"
    assert len(approvals.requests) == 1
    assert processes.processes == []


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("command", "Get-ChildItem changed", "command hash"),
        ("cwd", "outside", "cwd hash"),
        ("workspace_root", "outside", "workspace root hash"),
        ("timeout_seconds", 121, "timeout hash"),
    ],
)
def test_approved_payload_rejects_bound_field_tampering(
    tmp_path: Path,
    field: str,
    replacement: Any,
    message: str,
) -> None:
    shell, _approvals, processes, _audits = service(tmp_path)
    payload = approved_payload(tmp_path)
    payload[field] = replacement

    with pytest.raises(AgentShellError, match=message):
        shell.execute_payload(payload)
    assert processes.processes == []


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("command_hash", "command hash is required"),
        ("cwd_hash", "cwd hash is required"),
        ("workspace_root_hash", "workspace root hash is required"),
        ("timeout_hash", "timeout hash is required"),
    ],
)
def test_approved_payload_rejects_missing_bound_hash_without_spawning(
    tmp_path: Path,
    field: str,
    message: str,
) -> None:
    shell, _approvals, processes, _audits = service(tmp_path)
    payload = approved_payload(tmp_path)
    del payload[field]

    with pytest.raises(AgentShellError, match=message):
        shell.execute_payload(payload)
    assert processes.processes == []


def test_cancel_request_terminates_supervised_process_without_background_job(tmp_path: Path) -> None:
    process_owner = FakeProcessOwner(blocking=True)
    shell, _approvals, process_owner, _audits = service(
        tmp_path,
        processes=process_owner,
        cancellation_requested=lambda session, turn, client: (session, turn, client)
        == ("session-1", "turn-1", "client-1"),
    )

    result = shell.execute_payload(
        {
            **approved_payload(tmp_path),
            "client_turn_id": "client-1",
        }
    )

    assert result["cancelled"] is True
    assert result["ok"] is False
    assert process_owner.processes[0].killed is True
    assert shell.active_process_count == 0


def test_cancel_callback_failure_terminates_and_reaps_before_untracking(tmp_path: Path) -> None:
    process_owner = FakeProcessOwner(blocking=True)

    def fail_cancel_check(_session: str, _turn: str, _client: str) -> bool:
        raise RuntimeError("cancel check failed")

    shell, _approvals, process_owner, _audits = service(
        tmp_path,
        processes=process_owner,
        cancellation_requested=fail_cancel_check,
    )

    with pytest.raises(RuntimeError, match="cancel check failed"):
        shell.execute_payload(approved_payload(tmp_path))

    process = process_owner.processes[0]
    assert process.killed is True
    assert process.poll() == -9
    assert process.communicate_calls >= 2
    assert shell.active_process_count == 0


def test_shutdown_stops_admission_terminates_and_reaps_active_child(tmp_path: Path) -> None:
    process_owner = FakeProcessOwner(blocking=True)
    shell, _approvals, process_owner, _audits = service(tmp_path, processes=process_owner)
    result: dict[str, Any] = {}

    def run() -> None:
        result.update(shell.execute_payload(approved_payload(tmp_path)))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert process_owner.spawned.wait(timeout=1)
    assert shell.active_process_count == 1

    report = shell.shutdown(grace_seconds=1)
    worker.join(timeout=1)

    assert report.snapshot_count == 1
    assert report.terminated_count == 1
    assert report.pending_count == 0
    assert result["ok"] is False
    assert shell.active_process_count == 0
    with pytest.raises(AgentShellError, match="shutting down") as raised:
        shell.execute({"command": "Get-ChildItem"})
    assert raised.value.status_code == 503


def test_shutdown_waits_for_admitted_high_risk_approval_before_returning(tmp_path: Path) -> None:
    approvals = FakeApprovals(block_create=True)
    shell, _approvals, _process_owner, _audits = service(tmp_path, approvals=approvals)
    worker_result: dict[str, Any] = {}
    shutdown_result: dict[str, Any] = {}

    worker = threading.Thread(
        target=lambda: worker_result.update(
            shell.execute(
                {
                    "command": "Remove-Item fixture.txt",
                    "workspace_root": str(tmp_path),
                    "cwd": str(tmp_path),
                }
            )
        ),
        daemon=True,
    )
    worker.start()
    assert approvals.create_entered.wait(timeout=1)

    shutdown_worker = threading.Thread(
        target=lambda: shutdown_result.update(report=shell.shutdown(grace_seconds=1)),
        daemon=True,
    )
    shutdown_worker.start()
    time.sleep(0.05)
    assert shutdown_worker.is_alive()

    approvals.release_create.set()
    worker.join(timeout=1)
    shutdown_worker.join(timeout=1)

    assert worker_result["status"] == "pending_approval"
    assert shutdown_result["report"].snapshot_count == 1
    assert shutdown_result["report"].pending_count == 0


def test_shutdown_waits_for_admitted_worker_blocked_before_spawn(tmp_path: Path) -> None:
    process_owner = FakeProcessOwner(block_spawn=True)
    shell, _approvals, process_owner, _audits = service(tmp_path, processes=process_owner)
    worker_error: dict[str, BaseException] = {}
    shutdown_result: dict[str, Any] = {}

    def run() -> None:
        try:
            shell.execute({"command": "Get-ChildItem", "workspace_root": str(tmp_path)})
        except BaseException as exc:  # test captures the exact shutdown result from the worker.
            worker_error["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert process_owner.spawn_entered.wait(timeout=1)

    shutdown_worker = threading.Thread(
        target=lambda: shutdown_result.update(report=shell.shutdown(grace_seconds=1)),
        daemon=True,
    )
    shutdown_worker.start()
    time.sleep(0.05)
    assert shutdown_worker.is_alive()

    process_owner.release_spawn.set()
    worker.join(timeout=1)
    shutdown_worker.join(timeout=1)

    assert isinstance(worker_error["error"], AgentShellError)
    assert worker_error["error"].status_code == 503
    assert process_owner.processes[0].killed is True
    assert shutdown_result["report"].snapshot_count == 1
    assert shutdown_result["report"].pending_count == 0


def test_shutdown_isolates_one_termination_failure_and_reaps_other_children(
    tmp_path: Path,
) -> None:
    process_owner = FakeProcessOwner(blocking=True)
    base_ports = process_owner.ports()
    termination_calls: list[int] = []

    def terminate(process: FakeProcess) -> None:
        termination_calls.append(process.pid)
        if len(termination_calls) == 1:
            raise RuntimeError("fixture termination failure")
        process.kill()

    ports = ShellProcessPorts(
        spawn=base_ports.spawn,
        terminate_tree=terminate,
        environment=base_ports.environment,
        monotonic=base_ports.monotonic,
        utc_now=base_ports.utc_now,
        sleep=base_ports.sleep,
    )
    approvals = FakeApprovals()
    shell = AgentShellService(
        AgentShellPorts(
            approvals=approvals.ports(),
            append_audit=lambda _event: None,
            permission_audit_context=lambda: {},
            cancellation_requested=lambda _session, _turn, _client: False,
            default_workspace_root=lambda: tmp_path,
        ),
        process_ports=ports,
    )
    results: list[dict[str, Any]] = []
    workers = [
        threading.Thread(
            target=lambda: results.append(shell.execute_payload(approved_payload(tmp_path))),
            daemon=True,
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    deadline = time.monotonic() + 1
    while len(process_owner.processes) < 2 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(process_owner.processes) == 2

    report = shell.shutdown(grace_seconds=1)
    for worker in workers:
        worker.join(timeout=1)

    assert len(termination_calls) == 2
    assert all(process.killed for process in process_owner.processes)
    assert report.pending_count == 0
    assert len(results) == 2


def test_service_has_no_dynamic_host_or_compatibility_facade() -> None:
    source_path = Path(__file__).resolve().parents[1] / "agent_shell_service.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "_host" not in source
    assert "_impl" not in source
    assert "getattr(" not in source
    assert "agent_gateway" not in source
    assert not any(isinstance(node, ast.FunctionDef) and node.name.startswith("execute_shell") for node in ast.walk(tree))


def test_gateway_composes_shell_owner_with_fake_process_and_nonrecoverable_v14_approval(
    tmp_path: Path,
) -> None:
    process_owner = FakeProcessOwner()
    gateway = AgentGateway(
        tmp_path / "agent_gateway.json",
        tmp_path / "audit",
        shell_process_ports=process_owner.ports(),
    )
    assert gateway.shell.active_process_count == 0

    config = gateway.ensure_config()
    config.enabled = True
    config.execution_mode = "auto"
    gateway.save_config(config)

    pending = gateway.shell.execute(
        {
            "command": "Get-Content ../outside.txt",
            "workspace_root": str(tmp_path),
            "cwd": str(tmp_path),
            "session_id": "session-1",
            "turn_id": "turn-1",
        }
    )
    assert pending["status"] == "pending_approval"
    approval = pending["approval"]
    assert approval["requiresExplicitApproval"] is True
    arguments = approval["arguments"]
    assert arguments["command_hash"]
    assert arguments["cwd_hash"]
    assert arguments["workspace_root_hash"]
    assert arguments["timeout_hash"]
    assert process_owner.processes == []

    approval_id = pending["approval_id"]
    assert gateway.approve(approval_id)["ok"] is True
    applied = gateway.shell.execute_approved({"approval_id": approval_id})
    assert applied["ok"] is True
    assert process_owner.processes

    restarted = AgentGateway(
        tmp_path / "agent_gateway.json",
        tmp_path / "audit",
        shell_process_ports=FakeProcessOwner().ports(),
    )
    with pytest.raises(AgentGatewayError, match="Approval was not found") as missing:
        restarted.shell.execute_approved({"approval_id": approval_id})
    assert missing.value.status_code == 404

    report = gateway.shell.shutdown()
    assert report.pending_count == 0
    with pytest.raises(AgentGatewayError, match="shutting down") as stopped:
        gateway.shell.execute({"command": "Get-ChildItem", "workspace_root": str(tmp_path)})
    assert stopped.value.status_code == 503
    gateway.shell.start()
    resumed = gateway.shell.execute({"command": "Get-ChildItem", "workspace_root": str(tmp_path)})
    assert resumed["status"] == "executed"


def test_shell_owner_wiring_has_no_gateway_compatibility_surface() -> None:
    import agent_gateway

    root = Path(__file__).resolve().parents[1]
    gateway_source = (root / "agent_gateway.py").read_text(encoding="utf-8")
    dashboard_source = (root / "dashboard_server.py").read_text(encoding="utf-8")
    approval_source = (root / "agent_approval_transactions.py").read_text(encoding="utf-8")

    for method_name in (
        "classify_shell",
        "execute_shell",
        "execute_approved_shell",
        "execute_shell_payload",
        "_run_shell_command",
    ):
        assert f"def {method_name}(" not in gateway_source
    assert "AGENT_GATEWAY.shell.start()" in dashboard_source
    assert "AGENT_GATEWAY.shell.shutdown" in dashboard_source
    assert "AGENT_GATEWAY.shell.execute_payload" in dashboard_source
    assert "self.shell.execute_payload" in approval_source
    assert "self.shell.manual_approval_reason" in approval_source
    for legacy_global in (
        "summarize_shell_result",
        "normalize_filesystem_path",
        "tokenize_command",
        "strip_quotes",
        "is_path_within",
        "looks_like_absolute_path",
    ):
        assert not hasattr(agent_gateway, legacy_global)
