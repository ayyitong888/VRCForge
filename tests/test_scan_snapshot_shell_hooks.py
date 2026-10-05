from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
import threading
from typing import Any

import pytest

from agent_shell_process_supervisor import ShellSessionPorts
from agent_shell_service import AgentShellError, AgentShellPorts, AgentShellService
from test_agent_shell_service import FakeProcessOwner, approved_payload, service


class ScanMarkers:
    def __init__(self) -> None:
        self.snapshots = {"project-a": "scan-a", "project-b": "scan-b"}
        self.pending = 0
        self.events: list[tuple[str, str]] = []

    def invalidate(self, project: str, reason: str) -> None:
        self.events.append((project, reason))
        assert project == ""
        self.snapshots.clear()
        if reason == "shell:starting":
            self.pending += 1
        else:
            assert reason == "shell:finished"
            self.pending -= 1


class SessionProcess:
    pid = 9800

    def __init__(self, *, finish_inline: bool = False, fail_activation: bool = False) -> None:
        self.alive = True
        self.code: int | None = None
        self.finish_inline = finish_inline
        self.fail_activation = fail_activation

    def activate(self) -> None:
        if self.fail_activation:
            raise OSError("activation fixture failed")
        if self.finish_inline:
            self.finish(0)

    def read(self, _size: int = 4096) -> str:
        return ""

    def write(self, _text: str) -> None:
        return

    def close_input(self) -> None:
        return

    def is_alive(self) -> bool:
        return self.alive

    def exit_code(self) -> int | None:
        return self.code

    def finish(self, code: int) -> None:
        self.code = code
        self.alive = False

    def terminate(self) -> None:
        self.finish(-9)

    def close(self) -> None:
        return


class SessionJob:
    def assign(self, _pid: int) -> None:
        return

    def active_process_count(self) -> int:
        return 0

    def close(self) -> None:
        return


def make_shell(
    root: Path,
    markers: ScanMarkers,
    *,
    process_owner: FakeProcessOwner | None = None,
    session_process: SessionProcess | None = None,
    fail_spawn: bool = False,
    cancellation_requested=lambda *_ids: False,
) -> tuple[AgentShellService, threading.Event, list[dict[str, Any]]]:
    original, _approvals, process_owner, _audit = service(
        root, processes=process_owner, cancellation_requested=cancellation_requested
    )
    # Supplying the future port lets the behavioral tests fail on missing hooks,
    # rather than all failing at the pre-fix dataclass constructor.
    values = {field.name: getattr(original._ports, field.name) for field in fields(AgentShellPorts)}
    terminal = threading.Event()
    completions: list[dict[str, Any]] = []

    def session_finished(event: dict[str, Any]) -> None:
        completions.append(event)
        terminal.set()

    values.update(invalidate_scan_results=markers.invalidate, session_finished=session_finished)

    def session_spawn(*_args: Any) -> SessionProcess:
        assert markers.pending == 1 and not markers.snapshots
        if fail_spawn:
            raise OSError("spawn fixture failed")
        assert session_process is not None
        return session_process

    shell = AgentShellService(
        SimpleNamespace(**values),
        process_ports=process_owner.ports(),
        session_ports=ShellSessionPorts(
            spawn_pipe=session_spawn, spawn_pty=session_spawn, create_job_owner=SessionJob
        ),
    )
    return shell, terminal, completions


def test_optional_scan_port_defaults_to_noop(tmp_path: Path) -> None:
    shell, *_owners = service(tmp_path)
    assert shell._ports.invalidate_scan_results("", "shell:starting") is None
    assert shell._ports.invalidate_scan_results("", "shell:finished") is None


@pytest.mark.parametrize("gate", ["classification", "approval", "denied", "binding", "cancel-direct", "cancel-session"])
def test_rejected_or_unapproved_shell_preserves_snapshots(tmp_path: Path, gate: str) -> None:
    markers = ScanMarkers()
    shell, _terminal, _completions = make_shell(
        tmp_path, markers, cancellation_requested=lambda *_ids: gate.startswith("cancel-")
    )
    if gate == "classification":
        assert shell.execute({"command": "Get-ChildItem", "host": "remote"})["status"] == "rejected"
    elif gate == "approval":
        approved_payload(tmp_path)
        assert shell.execute({"command": "Set-Content Assets/test.txt value"})["status"] == "pending_approval"
    elif gate == "denied":
        with pytest.raises(AgentShellError, match="not found"):
            shell.execute_approved({"approvalId": "denied-fixture"})
    elif gate == "binding":
        with pytest.raises(AgentShellError, match="command hash does not match"):
            shell.execute_payload({**approved_payload(tmp_path), "command_hash": "tampered"})
    elif gate == "cancel-direct":
        with pytest.raises(AgentShellError, match="cancelled before start"):
            shell.execute_payload(approved_payload(tmp_path))
    else:
        with pytest.raises(AgentShellError, match="cancelled before start"):
            shell.execute({"command": "Get-ChildItem", "background": True, "session_id": "session-a"})
    assert markers.events == [] and markers.pending == 0
    assert len(markers.snapshots) == 2


@pytest.mark.parametrize("approved", [False, True])
def test_direct_spawn_expires_all_snapshots_before_and_after(tmp_path: Path, approved: bool) -> None:
    markers = ScanMarkers()
    owner = FakeProcessOwner()
    actual_spawn = owner.spawn

    def spawn(*args: Any, **kwargs: Any):
        assert markers.events == [("", "shell:starting")]
        assert markers.pending == 1 and not markers.snapshots
        return actual_spawn(*args, **kwargs)

    owner.spawn = spawn
    shell, _terminal, _completions = make_shell(tmp_path, markers, process_owner=owner)
    if approved:
        result = shell.execute_payload(approved_payload(tmp_path))
    else:
        result = shell.execute({"command": "Get-ChildItem"})
        assert result["classification"]["risk"] == "low"
        assert result["classification"]["protectionScope"] == "host"
    assert result["ok"]
    assert markers.events == [("", "shell:starting"), ("", "shell:finished")]
    assert markers.pending == 0 and not markers.snapshots


@pytest.mark.parametrize("kind", ["direct", "session", "activation"])
def test_failed_spawn_clears_pending_without_reviving_snapshots(tmp_path: Path, kind: str) -> None:
    markers = ScanMarkers()
    owner = FakeProcessOwner()
    if kind == "direct":
        def failed_spawn(*_args: Any, **_kwargs: Any):
            assert markers.pending == 1 and not markers.snapshots
            raise OSError("spawn fixture failed")
        owner.spawn = failed_spawn
    process = SessionProcess(fail_activation=kind == "activation")
    shell, _terminal, _completions = make_shell(
        tmp_path, markers, process_owner=owner, session_process=process, fail_spawn=kind == "session"
    )
    with pytest.raises((AgentShellError, OSError)):
        shell.execute({"command": "Get-ChildItem", **({"background": True} if kind != "direct" else {})})
    assert markers.events == [("", "shell:starting"), ("", "shell:finished")]
    assert markers.pending == 0 and not markers.snapshots
    if kind == "activation":
        assert not process.alive


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_background_keeps_mutation_pending_until_terminal(tmp_path: Path, outcome: str) -> None:
    markers = ScanMarkers()
    process = SessionProcess()
    shell, terminal, completions = make_shell(tmp_path, markers, session_process=process)
    result = shell.execute({"command": "Get-ChildItem", "background": True, "session_id": "session-a"})
    try:
        assert result["status"] == "running"
        assert markers.events == [("", "shell:starting")]
        assert markers.pending == 1 and process.alive
        if outcome == "cancelled":
            shell.cancel_owner("session-a")
        else:
            process.finish(0 if outcome == "success" else 7)
        assert terminal.wait(2)
        assert markers.events == [("", "shell:starting"), ("", "shell:finished")]
        assert markers.pending == 0 and not markers.snapshots
        assert len(completions) == 1 and not process.alive
    finally:
        shell.shutdown(grace_seconds=1)


def test_inline_session_finishes_marker_without_async_continuation(tmp_path: Path) -> None:
    markers = ScanMarkers()
    process = SessionProcess(finish_inline=True)
    shell, _terminal, completions = make_shell(tmp_path, markers, session_process=process)
    try:
        result = shell.execute({"command": "Get-ChildItem", "yieldMs": 1000})
        assert result["status"] == "executed"
        assert markers.events == [("", "shell:starting"), ("", "shell:finished")]
        assert markers.pending == 0 and completions == []
    finally:
        shell.shutdown(grace_seconds=1)
