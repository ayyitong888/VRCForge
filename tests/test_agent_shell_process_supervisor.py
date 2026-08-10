from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

import agent_shell_pty_worker as shell_pty_worker
import agent_shell_process_supervisor as shell_process_module
from agent_gateway import AgentGateway, AgentGatewayError
from agent_shell_process_supervisor import (
    ShellProcessSupervisor,
    ShellSessionError,
    ShellSessionPorts,
    build_shell_environment,
    normalize_shell_environment_overrides,
)


class FakeManagedProcess:
    def __init__(self, pid: int = 4242, *, terminate_error: bool = False, read_eof: bool = False) -> None:
        self.pid = pid
        self._alive = True
        self._exit_code: int | None = None
        self._output: list[str] = []
        self._condition = threading.Condition()
        self.writes: list[str] = []
        self.closed = False
        self.terminate_error = terminate_error
        self.read_eof = read_eof
        self.activated = False

    def activate(self) -> None:
        self.activated = True

    def feed(self, text: str) -> None:
        with self._condition:
            self._output.append(text)
            self._condition.notify_all()

    def finish(self, exit_code: int = 0) -> None:
        with self._condition:
            self._alive = False
            self._exit_code = exit_code
            self._condition.notify_all()

    def read(self, _size: int = 4096) -> str:
        if self.read_eof:
            raise EOFError
        with self._condition:
            if not self._output and self._alive:
                self._condition.wait(0.05)
            if self._output:
                return self._output.pop(0)
            return ""

    def write(self, text: str) -> None:
        if not self._alive:
            raise RuntimeError("finished")
        self.writes.append(text)

    def close_input(self) -> None:
        return

    def is_alive(self) -> bool:
        return self._alive

    def exit_code(self) -> int | None:
        return self._exit_code

    def terminate(self) -> None:
        if self.terminate_error:
            raise RuntimeError("termination failed")
        self.finish(-9)

    def close(self) -> None:
        self.closed = True


class FakeJobOwner:
    def __init__(self) -> None:
        self.assigned: list[int] = []
        self.closed = False
        self.active_children = 0

    def assign(self, pid: int) -> None:
        self.assigned.append(pid)

    def active_process_count(self) -> int:
        return 0 if self.closed else self.active_children

    def close(self) -> None:
        self.closed = True


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not reached")
        time.sleep(0.01)


def test_background_process_sessions_are_owner_scoped_and_interactive(tmp_path: Path) -> None:
    process = FakeManagedProcess()
    job = FakeJobOwner()
    clock = [0.0]
    spawn_calls: list[tuple[list[str], Path, dict[str, str], str]] = []

    def spawn_pipe(argv: list[str], cwd: Path, env: dict[str, str]) -> FakeManagedProcess:
        spawn_calls.append((argv, cwd, env, "pipe"))
        return process

    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=spawn_pipe,
            spawn_pty=spawn_pipe,
            create_job_owner=lambda: job,
            monotonic=lambda: clock[0],
        )
    )
    started = supervisor.execute(
        command="fixture",
        argv=["fixture.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture", "TOKEN": "secret"},
        owner_id="agent-a",
        background=True,
        yield_ms=10_000,
        timeout_seconds=0,
        pty=True,
    )
    session_id = started["sessionId"]
    assert started["status"] == "running"
    assert job.assigned == [process.pid]
    assert process.activated is True
    assert spawn_calls == [(["fixture.exe"], tmp_path, {"PATH": "fixture", "TOKEN": "secret"}, "pipe")]

    assert started["session"]["waitingForInput"] is False
    clock[0] = 3.0
    silent_idle = supervisor.control(
        {"action": "poll", "sessionId": session_id, "cursor": 0},
        owner_id="agent-a",
    )
    assert silent_idle["session"]["waitingForInput"] is True

    clock[0] = 4.0
    process.feed("first line\n")
    wait_until(lambda: supervisor.control({"action": "poll", "sessionId": session_id}, owner_id="agent-a")["output"])
    poll = supervisor.control(
        {"action": "poll", "sessionId": session_id, "cursor": 0},
        owner_id="agent-a",
    )
    assert poll["output"] == "first line\n"
    assert "TOKEN" not in str(poll)
    assert poll["session"]["waitingForInput"] is False
    clock[0] = 7.0
    idle = supervisor.control(
        {"action": "poll", "sessionId": session_id, "cursor": poll["cursor"]},
        owner_id="agent-a",
    )
    assert idle["session"]["waitingForInput"] is True

    supervisor.control(
        {"action": "write", "sessionId": session_id, "text": "hello"},
        owner_id="agent-a",
    )
    supervisor.control(
        {"action": "submit", "sessionId": session_id, "text": "world"},
        owner_id="agent-a",
    )
    supervisor.control(
        {"action": "send_keys", "sessionId": session_id, "keys": ["ctrl+c", "enter"]},
        owner_id="agent-a",
    )
    assert process.writes[0] == "hello"
    assert process.writes[1] == "world\r"
    assert "\x03" in process.writes[2]
    supervisor.control(
        {"action": "send_keys", "sessionId": session_id, "keys": ["C-c"]},
        owner_id="agent-a",
    )
    supervisor.control(
        {"action": "send_keys", "sessionId": session_id, "hex": [0x41, "42"]},
        owner_id="agent-a",
    )
    assert process.writes[3] == "\x03"
    assert process.writes[4] == "AB"

    with pytest.raises(ShellSessionError, match="not found"):
        supervisor.control({"action": "poll", "sessionId": session_id}, owner_id="agent-b")

    killed = supervisor.control({"action": "kill", "sessionId": session_id}, owner_id="agent-a")
    assert killed["session"]["killed"] is True
    wait_until(
        lambda: supervisor.control({"action": "poll", "sessionId": session_id}, owner_id="agent-a")[
            "session"
        ]["status"]
        != "running"
    )
    removed = supervisor.control({"action": "remove", "sessionId": session_id}, owner_id="agent-a")
    assert removed["removed"] is True
    assert process.closed is True


def test_poll_waits_for_new_output_and_log_supports_line_offsets(tmp_path: Path) -> None:
    process = FakeManagedProcess(pid=4250)
    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            create_job_owner=FakeJobOwner,
        )
    )
    started = supervisor.execute(
        command="fixture",
        argv=["fixture.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id="agent-a",
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
    )
    session_id = started["sessionId"]
    feeder = threading.Thread(target=lambda: (time.sleep(0.03), process.feed("one\ntwo\nthree\n")))
    feeder.start()
    polled = supervisor.control(
        {"action": "poll", "sessionId": session_id, "cursor": 0, "timeout": 1_000},
        owner_id="agent-a",
    )
    feeder.join(1)
    assert polled["output"] == "one\ntwo\nthree\n"
    log = supervisor.control(
        {"action": "log", "sessionId": session_id, "lineOffset": 1, "lineLimit": 1},
        owner_id="agent-a",
    )
    assert log["output"] == "two\n"
    assert log["nextLineOffset"] == 2
    assert log["hasMore"] is True
    remainder = supervisor.control(
        {"action": "log", "sessionId": session_id, "lineOffset": 1},
        owner_id="agent-a",
    )
    assert remainder["output"] == "two\nthree\n"
    assert remainder["hasMore"] is False
    tail = supervisor.control(
        {"action": "log", "sessionId": session_id},
        owner_id="agent-a",
    )
    assert tail["output"] == "one\ntwo\nthree\n"
    supervisor.shutdown(grace_seconds=0.1)


def test_pty_incremental_decoder_preserves_split_unicode() -> None:
    decoder = shell_pty_worker.codecs.getincrementaldecoder("utf-8")(errors="replace")
    encoded = "中文テスト".encode("utf-8")

    decoded = "".join(
        decoder.decode(encoded[index : index + 1], final=False)
        for index in range(len(encoded))
    )
    decoded += decoder.decode(b"", final=True)

    assert decoded == "中文テスト"


def test_runtime_shell_owner_is_lossless_for_surrogates_and_long_external_agents() -> None:
    assert AgentGateway._runtime_shell_owner("turn", "", "\ud800") != AgentGateway._runtime_shell_owner(
        "turn", "", "\ud801"
    )
    left = shell_process_module.normalize_shell_owner("agent:" + ("x" * 300) + "a")
    right = shell_process_module.normalize_shell_owner("agent:" + ("x" * 300) + "b")
    assert left.startswith("agent:sha256:")
    assert right.startswith("agent:sha256:")
    assert left != right


def test_long_external_agent_names_cannot_list_each_others_sessions(tmp_path: Path) -> None:
    process = FakeManagedProcess(pid=4261)
    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            create_job_owner=FakeJobOwner,
        )
    )
    left = "agent:" + ("x" * 300) + "a"
    right = "agent:" + ("x" * 300) + "b"
    supervisor.execute(
        command="fixture",
        argv=["fixture.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id=left,
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
    )

    assert supervisor.control({"action": "list"}, owner_id=left)["count"] == 1
    assert supervisor.control({"action": "list"}, owner_id=right)["count"] == 0
    supervisor.shutdown(grace_seconds=0.1)


def test_background_completion_emits_one_bounded_runtime_event(tmp_path: Path) -> None:
    process = FakeManagedProcess(pid=4259)
    events: list[dict[str, object]] = []
    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            create_job_owner=FakeJobOwner,
        )
    )
    started = supervisor.execute(
        command="secret command must not enter completion event",
        argv=["fixture.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id=AgentGateway._runtime_shell_owner("turn-a", "client-a", "chat-a"),
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
        completion_context={
            "runtimeSessionId": "chat-a",
            "turnId": "turn-a",
            "clientTurnId": "client-a",
        },
        on_finished=events.append,
    )

    process.feed("terminal-marker\n")
    process.finish(0)
    wait_until(lambda: len(events) == 1)

    event = events[0]
    assert event["runtimeSessionId"] == "chat-a"
    assert event["turnId"] == "turn-a"
    assert event["clientTurnId"] == "client-a"
    assert event["shellSessionId"] == started["sessionId"]
    assert event["status"] == "finished"
    assert event["exitCode"] == 0
    assert event["timedOut"] is False
    assert event["cancelled"] is False
    assert event["terminationFailed"] is False
    assert event["result"]["stdout"] == "terminal-marker\n"
    assert event["result"]["stdoutTruncated"] is False
    assert "secret command" not in str(events)
    supervisor.shutdown(grace_seconds=0.1)


def test_remove_running_session_terminates_owned_tree_and_removes_record(tmp_path: Path) -> None:
    process = FakeManagedProcess(pid=4251)
    job = FakeJobOwner()
    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            create_job_owner=lambda: job,
        )
    )
    started = supervisor.execute(
        command="fixture",
        argv=["fixture.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id="agent-a",
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
    )
    removed = supervisor.control(
        {"action": "remove", "sessionId": started["sessionId"]},
        owner_id="agent-a",
    )
    assert removed["removed"] is True
    assert removed["killed"] is True
    assert job.closed is True
    assert process.closed is True
    with pytest.raises(ShellSessionError, match="not found"):
        supervisor.control(
            {"action": "poll", "sessionId": started["sessionId"]},
            owner_id="agent-a",
        )


def test_session_limit_evicts_oldest_finished_session_before_rejecting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell_process_module, "SHELL_SESSION_COUNT_LIMIT", 2)
    processes = [FakeManagedProcess(pid=4260 + index) for index in range(3)]
    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: processes.pop(0),
            spawn_pty=lambda _argv, _cwd, _env: processes.pop(0),
            create_job_owner=FakeJobOwner,
        )
    )
    first = supervisor.execute(
        command="first",
        argv=["fixture.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id="agent-a",
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
    )
    first_process = supervisor._sessions[first["sessionId"]].process
    assert isinstance(first_process, FakeManagedProcess)
    first_process.finish(0)
    wait_until(
        lambda: supervisor.control(
            {"action": "poll", "sessionId": first["sessionId"]}, owner_id="agent-a"
        )["session"]["status"]
        == "finished"
    )
    for command in ("second", "third"):
        supervisor.execute(
            command=command,
            argv=["fixture.exe"],
            cwd=tmp_path,
            environment={"PATH": "fixture"},
            owner_id="agent-a",
            background=True,
            yield_ms=0,
            timeout_seconds=0,
            pty=False,
        )
    with pytest.raises(ShellSessionError, match="not found"):
        supervisor.control(
            {"action": "poll", "sessionId": first["sessionId"]}, owner_id="agent-a"
        )
    assert supervisor.control({"action": "list"}, owner_id="agent-a")["count"] == 2
    supervisor.shutdown(grace_seconds=0.1)


def test_pty_transport_is_selected_without_exposing_environment_values(tmp_path: Path) -> None:
    process = FakeManagedProcess()
    job = FakeJobOwner()
    calls: list[str] = []

    def pipe(_argv: list[str], _cwd: Path, _env: dict[str, str]) -> FakeManagedProcess:
        calls.append("pipe")
        return process

    def pty(_argv: list[str], _cwd: Path, _env: dict[str, str]) -> FakeManagedProcess:
        calls.append("pty")
        return process

    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=pipe,
            spawn_pty=pty,
            create_job_owner=lambda: job,
        )
    )
    payload = supervisor.execute(
        command="interactive",
        argv=["interactive.exe"],
        cwd=tmp_path,
        environment={"SECRET": "must-not-be-returned"},
        owner_id="agent",
        background=True,
        yield_ms=0,
        timeout_seconds=30,
        pty=True,
    )
    assert calls == ["pty"]
    assert payload["session"]["pty"] is True
    assert "must-not-be-returned" not in str(payload)
    supervisor.shutdown(grace_seconds=0.1)


def test_gateway_injects_the_exact_session_process_owner(tmp_path: Path) -> None:
    process = FakeManagedProcess()
    job = FakeJobOwner()
    spawn_calls: list[list[str]] = []

    def spawn(argv: list[str], _cwd: Path, _env: dict[str, str]) -> FakeManagedProcess:
        spawn_calls.append(argv)
        return process

    ports = ShellSessionPorts(
        spawn_pipe=spawn,
        spawn_pty=spawn,
        create_job_owner=lambda: job,
    )
    gateway = AgentGateway(
        tmp_path / "agent_gateway.json",
        tmp_path / "audit",
        shell_session_ports=ports,
    )
    result = gateway.shell.execute(
        {
            "command": "fixture-command",
            "background": True,
            "pty": True,
            "sessionId": "chat-a",
        }
    )
    assert result["status"] == "running"
    assert spawn_calls
    assert "-NonInteractive" not in spawn_calls[0]
    assert job.assigned == [process.pid]
    polled = gateway.shell.process(
        {
            "action": "poll",
            "sessionId": result["sessionId"],
            "runtimeSessionId": "chat-a",
        }
    )
    assert polled["session"]["sessionId"] == result["sessionId"]
    with pytest.raises(AgentGatewayError, match="not found"):
        gateway.shell.process({"action": "poll", "sessionId": result["sessionId"]})
    cancelled = gateway.request_runtime_cancel({"sessionId": "chat-a", "reason": "fixture-stop"})
    assert cancelled["cancelledShellSessionIds"] == [result["sessionId"]]
    gateway.shell.shutdown(grace_seconds=0.1)


def test_client_turn_only_stop_resolves_backend_turn_and_cancels_shell(tmp_path: Path) -> None:
    process = FakeManagedProcess(pid=4289)
    gateway = AgentGateway(
        tmp_path / "agent_gateway.json",
        tmp_path / "audit",
        shell_session_ports=ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            create_job_owner=FakeJobOwner,
        ),
    )
    owner = gateway._runtime_shell_owner("backend-turn-a", "client-turn-a", "chat-a")
    started = gateway.shell.execute(
        {
            "command": "fixture",
            "background": True,
            "_trusted_owner_id": owner,
        }
    )
    gateway._runtime_run_ledger.append(
        {
            "event": "runtime_turn_started",
            "status": "running",
            "sessionId": "chat-a",
            "turnId": "backend-turn-a",
            "clientTurnId": "client-turn-a",
        }
    )

    cancelled = gateway.request_runtime_cancel(
        {"clientTurnId": "client-turn-a", "reason": "fixture-stop"}
    )

    assert cancelled["cancelledShellSessionIds"] == [started["sessionId"]]
    assert process.is_alive() is False
    gateway.shell.shutdown(grace_seconds=0.1)


def test_gateway_ignores_forged_process_owner_fields(tmp_path: Path) -> None:
    process = FakeManagedProcess(pid=4290)
    gateway = AgentGateway(
        tmp_path / "agent_gateway.json",
        tmp_path / "audit",
        shell_session_ports=ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            create_job_owner=FakeJobOwner,
        ),
    )
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    gateway.register_tool(
        "fixture_shell_execute",
        "Execute a fixture host process.",
        "supervised-write",
        gateway.execute_shell_tool,
        write=True,
    )
    gateway.register_tool(
        "fixture_shell_process",
        "Control a fixture host process.",
        "supervised-write",
        gateway.control_shell_tool,
        write=True,
    )

    started = gateway.call_tool(
        "fixture_shell_execute",
        {"command": "fixture", "background": True},
        agent_name="agent-a",
    )["result"]
    assert started["status"] == "running"
    forged = gateway.call_tool(
        "fixture_shell_process",
        {"action": "list", "runtimeSessionId": "agent:agent-a"},
        agent_name="agent-b",
    )["result"]
    assert forged["count"] == 0
    owned = gateway.call_tool(
        "fixture_shell_process",
        {"action": "list"},
        agent_name="agent-a",
    )["result"]
    assert owned["count"] == 1
    forged_control = gateway.call_tool(
        "fixture_shell_process",
        {"action": "poll", "sessionId": started["sessionId"]},
        agent_name="agent-a",
    )
    assert forged_control["ok"] is False
    assert "not found" in forged_control["error"]
    controlled = gateway.call_tool(
        "fixture_shell_process",
        {
            "action": "poll",
            "sessionId": started["sessionId"],
            "controlToken": started["controlToken"],
        },
        agent_name="agent-a",
    )
    assert controlled["result"]["session"]["sessionId"] == started["sessionId"]
    written = gateway.call_tool(
        "fixture_shell_process",
        {
            "action": "write",
            "sessionId": started["sessionId"],
            "controlToken": started["controlToken"],
            "data": "echo fixture",
        },
        agent_name="agent-a",
    )
    assert written["result"]["ok"] is True
    assert process.writes == ["echo fixture"]
    audit_text = gateway.audit_log_path.read_text(encoding="utf-8")
    assert started["controlToken"] not in audit_text
    gateway.shell.shutdown(grace_seconds=0.1)


def test_runtime_session_can_control_prior_turn_but_cancel_remains_turn_exact(tmp_path: Path) -> None:
    process_queue = [FakeManagedProcess(pid=4291), FakeManagedProcess(pid=4292)]
    job_queue = [FakeJobOwner(), FakeJobOwner()]
    first_process = process_queue[0]
    second_process = process_queue[1]
    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process_queue.pop(0),
            spawn_pty=lambda _argv, _cwd, _env: process_queue.pop(0),
            create_job_owner=lambda: job_queue.pop(0),
        )
    )
    owner_a = AgentGateway._runtime_shell_owner("turn-a", "", "chat-a")
    owner_b = AgentGateway._runtime_shell_owner("turn-b", "", "chat-a")
    owner_c = AgentGateway._runtime_shell_owner("turn-c", "", "chat-a")
    other_chat = AgentGateway._runtime_shell_owner("turn-c", "", "chat-b")
    first = supervisor.execute(
        command="first",
        argv=["first.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id=owner_a,
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
    )
    second = supervisor.execute(
        command="second",
        argv=["second.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id=owner_b,
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
    )

    prior_turn = supervisor.control(
        {"action": "poll", "sessionId": first["sessionId"]},
        owner_id=owner_c,
    )
    assert prior_turn["session"]["sessionId"] == first["sessionId"]
    with pytest.raises(ShellSessionError, match="not found"):
        supervisor.control(
            {"action": "poll", "sessionId": first["sessionId"]},
            owner_id=other_chat,
        )

    assert supervisor.kill_owner(owner_b) == [second["sessionId"]]
    assert first_process.is_alive() is True
    assert second_process.is_alive() is False
    supervisor.shutdown(grace_seconds=0.1)


def test_runtime_owner_encoding_rejects_delimiter_and_long_prefix_collisions() -> None:
    ordinary = AgentGateway._runtime_shell_owner("turn-a", "", "chat")
    delimiter = AgentGateway._runtime_shell_owner("turn-b", "", "chat|origin:forged")
    long_a = AgentGateway._runtime_shell_owner("turn-a", "", "x" * 300 + "a")
    long_b = AgentGateway._runtime_shell_owner("turn-b", "", "x" * 300 + "b")

    assert ordinary.split("|origin:", 1)[0] != delimiter.split("|origin:", 1)[0]
    assert long_a.split("|origin:", 1)[0] != long_b.split("|origin:", 1)[0]


def test_environment_overrides_are_bounded_and_validated() -> None:
    assert normalize_shell_environment_overrides({"GOOD_NAME": 3}) == {"GOOD_NAME": "3"}
    with pytest.raises(ShellSessionError, match="name"):
        normalize_shell_environment_overrides({"BAD-NAME": "x"})
    with pytest.raises(ShellSessionError, match="null"):
        normalize_shell_environment_overrides({"GOOD": "bad\x00value"})

    environment = build_shell_environment(
        {
            "SystemRoot": r"C:\Windows",
            "PATH": r"C:\Windows\System32",
            "JAVA_HOME": r"C:\Program Files\Java\jdk",
            "DOTNET_ROOT": r"C:\Program Files\dotnet",
            "VIRTUAL_ENV": r"D:\work\.venv",
            "SSH_AUTH_SOCK": r"\\.\pipe\openssh-ssh-agent",
            "HTTPS_PROXY": "http://127.0.0.1:8080",
            "NO_PROXY": "127.0.0.1,localhost",
            "KUBECONFIG": r"D:\work\.kube\config",
            "DOCKER_HOST": "npipe:////./pipe/docker_engine",
            "AWS_PROFILE": "development",
            "VRCFORGE_APP_SESSION_TOKEN": "must-not-reach-child",
            "OPENAI_API_KEY": "must-not-reach-child",
        },
        {"EXPLICIT_VALUE": "allowed"},
    )
    assert environment["SystemRoot"] == r"C:\Windows"
    assert environment["JAVA_HOME"] == r"C:\Program Files\Java\jdk"
    assert environment["DOTNET_ROOT"] == r"C:\Program Files\dotnet"
    assert environment["VIRTUAL_ENV"] == r"D:\work\.venv"
    assert environment["SSH_AUTH_SOCK"] == r"\\.\pipe\openssh-ssh-agent"
    assert environment["HTTPS_PROXY"] == "http://127.0.0.1:8080"
    assert environment["NO_PROXY"] == "127.0.0.1,localhost"
    assert environment["KUBECONFIG"] == r"D:\work\.kube\config"
    assert environment["DOCKER_HOST"] == "npipe:////./pipe/docker_engine"
    assert environment["AWS_PROFILE"] == "development"
    assert environment["EXPLICIT_VALUE"] == "allowed"
    assert "VRCFORGE_APP_SESSION_TOKEN" not in environment
    assert "OPENAI_API_KEY" not in environment


def test_owner_cancel_terminates_only_matching_live_sessions(tmp_path: Path) -> None:
    processes = [FakeManagedProcess(pid=4301), FakeManagedProcess(pid=4302)]
    job = FakeJobOwner()

    def spawn(_argv: list[str], _cwd: Path, _env: dict[str, str]) -> FakeManagedProcess:
        return processes.pop(0)

    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(spawn_pipe=spawn, spawn_pty=spawn, create_job_owner=lambda: job)
    )
    owned = supervisor.execute(
        command="owned",
        argv=["owned.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id="chat-a",
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
    )
    other = supervisor.execute(
        command="other",
        argv=["other.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id="chat-b",
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
    )

    assert supervisor.kill_owner("chat-a") == [owned["sessionId"]]
    wait_until(
        lambda: supervisor.control(
            {"action": "poll", "sessionId": owned["sessionId"]}, owner_id="chat-a"
        )["session"]["status"]
        != "running"
    )
    assert supervisor.control(
        {"action": "poll", "sessionId": other["sessionId"]}, owner_id="chat-b"
    )["session"]["status"] == "running"
    supervisor.shutdown(grace_seconds=0.1)


def test_root_exit_does_not_finish_session_while_owned_child_is_alive(tmp_path: Path) -> None:
    process = FakeManagedProcess(pid=4310)
    job = FakeJobOwner()
    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            create_job_owner=lambda: job,
        )
    )
    started = supervisor.execute(
        command="starts-child",
        argv=["fixture.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id="turn-a",
        background=True,
        yield_ms=0,
        timeout_seconds=0,
        pty=False,
    )
    job.active_children = 1
    process.finish(0)
    time.sleep(0.05)
    polled = supervisor.control(
        {"action": "poll", "sessionId": started["sessionId"]},
        owner_id="turn-a",
    )
    assert polled["session"]["status"] == "running"
    assert supervisor.kill_owner("turn-a") == [started["sessionId"]]
    assert job.closed is True
    wait_until(
        lambda: supervisor.control(
            {"action": "poll", "sessionId": started["sessionId"]},
            owner_id="turn-a",
        )["session"]["status"]
        != "running"
    )


def test_process_send_keys_cannot_bypass_unity_approval(tmp_path: Path) -> None:
    process = FakeManagedProcess(pid=4311)
    project = tmp_path / "UnityProject"
    for marker in ("Assets", "Packages", "ProjectSettings"):
        (project / marker).mkdir(parents=True, exist_ok=True)
    gateway = AgentGateway(
        tmp_path / "agent_gateway.json",
        tmp_path / "audit",
        shell_session_ports=ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            create_job_owner=FakeJobOwner,
        ),
    )
    started = gateway.shell.execute(
        {
            "command": "fixture-command",
            "background": True,
            "_trusted_owner_id": "turn-a",
        }
    )
    with pytest.raises(AgentGatewayError, match="approval") as blocked:
        gateway.shell.process(
            {
                "action": "send_keys",
                "sessionId": started["sessionId"],
                "keys": [f"Set-Content {project / 'Assets' / 'blocked.txt'} value", "enter"],
                "_trusted_owner_id": "turn-a",
            }
        )
    assert blocked.value.status_code == 409
    assert process.writes == []
    gateway.shell.shutdown(grace_seconds=0.1)


def test_timeout_termination_failure_is_reported_without_stranding_waiters(tmp_path: Path) -> None:
    process = FakeManagedProcess(terminate_error=True, read_eof=True)
    job = FakeJobOwner()
    ticks = iter(range(100))
    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            monotonic=lambda: float(next(ticks)),
            sleep=lambda _seconds: None,
            create_job_owner=lambda: job,
        )
    )

    payload = supervisor.execute(
        command="stuck",
        argv=["stuck.exe"],
        cwd=tmp_path,
        environment={"PATH": "fixture"},
        owner_id="chat-a",
        background=False,
        yield_ms=10_000,
        timeout_seconds=1,
        pty=False,
    )

    assert payload["status"] == "executed"
    assert payload["result"]["terminationFailed"] is True
    assert payload["session"]["status"] == "termination_failed"
    assert payload["result"]["ok"] is False


def test_shutdown_waits_for_reserved_spawn_and_never_activates_after_stop(tmp_path: Path) -> None:
    process = FakeManagedProcess()
    job = FakeJobOwner()
    spawn_entered = threading.Event()
    release_spawn = threading.Event()
    execute_errors: list[Exception] = []
    shutdown_reports: list[tuple[int, int, int]] = []

    def spawn(_argv: list[str], _cwd: Path, _env: dict[str, str]) -> FakeManagedProcess:
        spawn_entered.set()
        assert release_spawn.wait(2)
        return process

    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(spawn_pipe=spawn, spawn_pty=spawn, create_job_owner=lambda: job)
    )

    def execute() -> None:
        try:
            supervisor.execute(
                command="race",
                argv=["race.exe"],
                cwd=tmp_path,
                environment={"PATH": "fixture"},
                owner_id="chat-a",
                background=True,
                yield_ms=0,
                timeout_seconds=0,
                pty=False,
            )
        except Exception as exc:  # noqa: BLE001 - captured for the concurrency assertion.
            execute_errors.append(exc)

    execute_thread = threading.Thread(target=execute)
    execute_thread.start()
    assert spawn_entered.wait(2)
    shutdown_thread = threading.Thread(
        target=lambda: shutdown_reports.append(supervisor.shutdown(grace_seconds=2))
    )
    shutdown_thread.start()
    time.sleep(0.02)
    assert shutdown_thread.is_alive()
    release_spawn.set()
    execute_thread.join(2)
    shutdown_thread.join(2)

    assert len(execute_errors) == 1
    assert isinstance(execute_errors[0], ShellSessionError)
    assert process.activated is False
    assert process.closed is True
    assert job.assigned == []
    assert job.closed is True
    assert shutdown_reports == [(1, 0, 0)]


def test_cancel_latch_after_spawn_prevents_process_activation(tmp_path: Path) -> None:
    process = FakeManagedProcess(pid=4401)
    job = FakeJobOwner()
    checks = iter((False, True))
    supervisor = ShellProcessSupervisor(
        ShellSessionPorts(
            spawn_pipe=lambda _argv, _cwd, _env: process,
            spawn_pty=lambda _argv, _cwd, _env: process,
            create_job_owner=lambda: job,
        )
    )
    with pytest.raises(ShellSessionError, match="cancelled before activation"):
        supervisor.execute(
            command="race",
            argv=["race.exe"],
            cwd=tmp_path,
            environment={"PATH": "fixture"},
            owner_id="turn-a",
            background=True,
            yield_ms=0,
            timeout_seconds=0,
            pty=False,
            cancel_requested=lambda: next(checks),
        )
    assert process.activated is False
    assert process.closed is True
    assert job.closed is True


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("VRCFORGE_RUN_REAL_PTY_TESTS") != "1",
    reason="real PTY execution is an explicit integration gate",
)
def test_real_windows_pty_executes_and_returns_output(tmp_path: Path) -> None:
    supervisor = ShellProcessSupervisor()
    payload = supervisor.execute(
        command="Write-Output vrcforge-pty-ok",
        argv=[
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-Command",
            "Write-Output vrcforge-pty-ok",
        ],
        cwd=tmp_path,
        environment=build_shell_environment(dict(os.environ), {}),
        owner_id="pty-smoke",
        background=False,
        yield_ms=10_000,
        timeout_seconds=20,
        pty=True,
    )
    assert payload["status"] == "executed"
    assert payload["result"]["ok"] is True
    assert "vrcforge-pty-ok" in payload["result"]["stdout"]
