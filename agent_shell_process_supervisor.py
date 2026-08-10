"""Bounded interactive/background Shell process sessions owned by VRCForge."""

from __future__ import annotations

import os
import json
import hashlib
import hmac
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
import ctypes
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


SHELL_SESSION_OUTPUT_LIMIT = 200_000
SHELL_SESSION_POLL_LIMIT = 30_000
SHELL_SESSION_FINISHED_TTL_SECONDS = 30 * 60
SHELL_SESSION_COUNT_LIMIT = 50
SHELL_SESSION_TOTAL_OUTPUT_LIMIT = 2_000_000
SHELL_SESSION_POLL_TIMEOUT_LIMIT_MS = 30_000
SHELL_SESSION_LOG_LINE_LIMIT = 2_000
SHELL_SESSION_INPUT_IDLE_SECONDS = 2.0
SHELL_ENV_ENTRY_LIMIT = 128
SHELL_ENV_VALUE_LIMIT = 32_768
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INHERITED_ENV_NAMES = {
    "ALLUSERSPROFILE",
    "APPDATA",
    "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432",
    "COMPUTERNAME",
    "COMSPEC",
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "CARGO_HOME",
    "COLORTERM",
    "DOTNET_ROOT",
    "DOTNET_ROOT_X64",
    "DOTNET_ROOT_X86",
    "DOCKER_CERT_PATH",
    "DOCKER_CONTEXT",
    "DOCKER_HOST",
    "DOCKER_TLS_VERIFY",
    "GOPATH",
    "GOROOT",
    "GRADLE_HOME",
    "GRADLE_USER_HOME",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "HOMEDRIVE",
    "HOME",
    "HOMEPATH",
    "JAVA_HOME",
    "KUBECONFIG",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "MAVEN_HOME",
    "NODE_PATH",
    "NO_PROXY",
    "NUGET_PACKAGES",
    "NVM_HOME",
    "NVM_SYMLINK",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PSMODULEPATH",
    "PUBLIC",
    "RUSTUP_HOME",
    "AWS_CONFIG_FILE",
    "AWS_DEFAULT_PROFILE",
    "AWS_DEFAULT_REGION",
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_SHARED_CREDENTIALS_FILE",
    "SSH_AUTH_SOCK",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "VIRTUAL_ENV",
    "WINDIR",
}
_FORBIDDEN_ENV_OVERRIDE_NAMES = {
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "PSMODULEPATH",
    "PYTHONHOME",
    "PYTHONPATH",
}


class ShellSessionError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class ManagedShellProcess(Protocol):
    pid: int

    def activate(self) -> None: ...

    def read(self, size: int = 4096) -> str: ...

    def write(self, text: str) -> None: ...

    def close_input(self) -> None: ...

    def is_alive(self) -> bool: ...

    def exit_code(self) -> int | None: ...

    def terminate(self) -> None: ...

    def close(self) -> None: ...


class ShellJobOwner(Protocol):
    def assign(self, pid: int) -> None: ...

    def active_process_count(self) -> int: ...

    def close(self) -> None: ...


class _NoopJobOwner:
    def assign(self, _pid: int) -> None:
        return

    def active_process_count(self) -> int:
        return 0

    def close(self) -> None:
        return


class _WindowsJobOwner:
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        pass

    class _BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", ctypes.c_uint32),
            ("TotalProcesses", ctypes.c_uint32),
            ("ActiveProcesses", ctypes.c_uint32),
            ("TotalTerminatedProcesses", ctypes.c_uint32),
        ]

    _ExtendedLimitInformation._fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        kernel32.QueryInformationJobObject.restype = ctypes.c_int
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        self._kernel32 = kernel32
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        information = self._ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            self._handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(error)

    def assign(self, pid: int) -> None:
        if not self._handle:
            raise RuntimeError("Shell process job is closed.")
        process = self._kernel32.OpenProcess(
            self._PROCESS_TERMINATE
            | self._PROCESS_SET_QUOTA
            | self._PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            int(pid),
        )
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self._kernel32.CloseHandle(process)

    def active_process_count(self) -> int:
        if not self._handle:
            return 0
        information = self._BasicAccountingInformation()
        returned = ctypes.c_uint32(0)
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            self._JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
            ctypes.byref(returned),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(information.ActiveProcesses)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def default_shell_job_owner() -> ShellJobOwner:
    return _WindowsJobOwner() if os.name == "nt" else _NoopJobOwner()


@dataclass(frozen=True, slots=True)
class ShellSessionPorts:
    spawn_pipe: Callable[[list[str], Path, dict[str, str]], ManagedShellProcess]
    spawn_pty: Callable[[list[str], Path, dict[str, str]], ManagedShellProcess]
    monotonic: Callable[[], float] = time.monotonic
    utc_now: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat()
    sleep: Callable[[float], None] = time.sleep
    create_job_owner: Callable[[], ShellJobOwner] = default_shell_job_owner


class _PipeProcess:
    def __init__(self, process: subprocess.Popen[str], *, suspended: bool = False) -> None:
        self._process = process
        self._suspended = suspended
        self.pid = int(process.pid)

    def activate(self) -> None:
        if not self._suspended:
            return
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = int(ntdll.NtResumeProcess(ctypes.c_void_p(int(self._process._handle))))
        if status < 0:
            raise OSError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xFFFFFFFF:08x}")
        self._suspended = False

    def read(self, size: int = 4096) -> str:
        if self._process.stdout is None:
            return ""
        # A buffered pipe read for ``size`` bytes can wait for the entire
        # buffer while a long-running command has already emitted a prompt or
        # short progress line. One-character reads keep the reader responsive;
        # the supervisor batches them into its bounded output buffer.
        return self._process.stdout.read(1 if size > 0 else size)

    def write(self, text: str) -> None:
        if self._process.stdin is None:
            raise ShellSessionError("This process does not accept input.", 409)
        self._process.stdin.write(text)
        self._process.stdin.flush()

    def close_input(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()

    def is_alive(self) -> bool:
        return self._process.poll() is None

    def exit_code(self) -> int | None:
        return self._process.poll()

    def terminate(self) -> None:
        _terminate_windows_process_tree(self.pid, self._process.kill)

    def close(self) -> None:
        for stream in (self._process.stdin, self._process.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


class _PtyProcess:
    def __init__(self, process: Any) -> None:
        self._process = process
        self.pid = int(process.pid)

    def activate(self) -> None:
        return

    def read(self, size: int = 4096) -> str:
        return str(self._process.read(size) or "")

    def write(self, text: str) -> None:
        self._process.write(text)

    def close_input(self) -> None:
        self._process.write("\x1a" if os.name == "nt" else "\x04")

    def is_alive(self) -> bool:
        return bool(self._process.isalive())

    def exit_code(self) -> int | None:
        value = self._process.exitstatus
        return int(value) if value is not None else None

    def terminate(self) -> None:
        _terminate_windows_process_tree(
            self.pid,
            lambda: self._process.terminate(force=True),
        )

    def close(self) -> None:
        try:
            self._process.close(force=True)
        except (OSError, RuntimeError):
            pass


class _PtyWorkerProcess:
    """A suspended pipe worker that creates the ConPTY child after Job assignment."""

    def __init__(self, process: _PipeProcess, config: dict[str, Any]) -> None:
        self._process = process
        self._config = config
        self.pid = process.pid

    def activate(self) -> None:
        self._process.activate()
        self._process.write(json.dumps(self._config, ensure_ascii=True, separators=(",", ":")) + "\n")

    def read(self, size: int = 4096) -> str:
        return self._process.read(size)

    def write(self, text: str) -> None:
        self._process.write(text)

    def close_input(self) -> None:
        self._process.close_input()

    def is_alive(self) -> bool:
        return self._process.is_alive()

    def exit_code(self) -> int | None:
        return self._process.exit_code()

    def terminate(self) -> None:
        self._process.terminate()

    def close(self) -> None:
        self._process.close()


def default_shell_session_ports() -> ShellSessionPorts:
    def spawn_owned_pipe(argv: list[str], cwd: Path, env: dict[str, str]) -> _PipeProcess:
        creationflags = 0
        suspended = False
        if os.name == "nt":
            creationflags = (
                int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                | int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                | int(getattr(subprocess, "CREATE_SUSPENDED", 0x00000004))
            )
            suspended = True
        return _PipeProcess(
            subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            ),
            suspended=suspended,
        )

    def spawn_pipe(argv: list[str], cwd: Path, env: dict[str, str]) -> ManagedShellProcess:
        return spawn_owned_pipe(argv, cwd, env)

    def spawn_pty(argv: list[str], cwd: Path, env: dict[str, str]) -> ManagedShellProcess:
        if os.name != "nt":
            raise ShellSessionError("PTY mode is currently supported only on Windows.", 400)
        worker_argv = (
            [sys.executable, "--shell-pty-worker"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-u", str(Path(__file__).with_name("agent_shell_pty_worker.py"))]
        )
        return _PtyWorkerProcess(
            spawn_owned_pipe(worker_argv, cwd, env),
            {"argv": argv, "cwd": str(cwd)},
        )

    return ShellSessionPorts(spawn_pipe=spawn_pipe, spawn_pty=spawn_pty)


@dataclass(slots=True)
class _ShellSession:
    session_id: str
    owner_id: str
    command: str
    cwd: str
    process: ManagedShellProcess
    job_owner: ShellJobOwner
    control_token_hash: str
    pty: bool
    timeout_seconds: int
    started_at: str
    started_monotonic: float
    last_output_monotonic: float
    completion_context: dict[str, Any]
    on_finished: Callable[[dict[str, Any]], None]
    output: str = ""
    output_base: int = 0
    output_end: int = 0
    output_truncated: bool = False
    status: str = "running"
    exit_code: int | None = None
    finished_at: str = ""
    finished_monotonic: float | None = None
    timed_out: bool = False
    killed: bool = False
    termination_failed: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    reader_done: threading.Event = field(default_factory=threading.Event)


class ShellProcessSupervisor:
    """Owns bounded local process sessions for one Agent Gateway lifetime."""

    def __init__(self, ports: ShellSessionPorts | None = None) -> None:
        self._ports = ports or default_shell_session_ports()
        self._lock = threading.RLock()
        self._sessions: dict[str, _ShellSession] = {}
        self._accepting = True
        self._pending_spawns = 0

    def start(self) -> None:
        with self._lock:
            self._accepting = True

    def execute(
        self,
        *,
        command: str,
        argv: list[str],
        cwd: Path,
        environment: dict[str, str],
        owner_id: str,
        background: bool,
        yield_ms: int,
        timeout_seconds: int,
        pty: bool,
        cancel_requested: Callable[[], bool] | None = None,
        completion_context: dict[str, Any] | None = None,
        on_finished: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        owner = normalize_shell_owner(owner_id)
        env = validate_shell_environment(environment)
        with self._lock:
            self._prune_locked()
            if not self._accepting:
                raise ShellSessionError("Shell execution is shutting down.", 503)
            self._evict_finished_for_capacity_locked()
            if len(self._sessions) + self._pending_spawns >= SHELL_SESSION_COUNT_LIMIT:
                raise ShellSessionError("The Shell process session limit has been reached.", 429)
            self._pending_spawns += 1
        process: ManagedShellProcess | None = None
        job_owner: ShellJobOwner | None = None
        session: _ShellSession | None = None
        registered = False
        control_token = secrets.token_urlsafe(32)
        try:
            if cancel_requested is not None and cancel_requested():
                raise ShellSessionError("Shell execution was cancelled before start.", 409)
            try:
                job_owner = self._ports.create_job_owner()
            except (OSError, RuntimeError) as exc:
                raise ShellSessionError("Shell process lifecycle ownership is unavailable.", 503) from exc
            try:
                process = (self._ports.spawn_pty if pty else self._ports.spawn_pipe)(argv, cwd, env)
            except ShellSessionError:
                raise
            except (OSError, RuntimeError) as exc:
                raise ShellSessionError("Shell process could not be started.", 503) from exc
            session = _ShellSession(
                session_id=f"shell-{uuid.uuid4().hex}",
                owner_id=owner,
                command=command,
                cwd=str(cwd),
                process=process,
                job_owner=job_owner,
                control_token_hash=_control_token_hash(control_token),
                pty=pty,
                timeout_seconds=timeout_seconds,
                started_at=self._ports.utc_now(),
                started_monotonic=self._ports.monotonic(),
                last_output_monotonic=self._ports.monotonic(),
                completion_context=dict(completion_context or {}),
                on_finished=on_finished or (lambda _event: None),
            )
            with self._lock:
                if not self._accepting:
                    raise ShellSessionError("Shell execution is shutting down.", 503)
                try:
                    job_owner.assign(process.pid)
                    self._sessions[session.session_id] = session
                    registered = True
                    if cancel_requested is not None and cancel_requested():
                        raise ShellSessionError("Shell execution was cancelled before activation.", 409)
                    process.activate()
                except ShellSessionError:
                    self._sessions.pop(session.session_id, None)
                    registered = False
                    raise
                except (OSError, RuntimeError) as exc:
                    self._sessions.pop(session.session_id, None)
                    registered = False
                    raise ShellSessionError(
                        "Shell process could not be activated inside its lifecycle owner.", 503
                    ) from exc
        finally:
            if process is not None and not registered:
                self._dispose_unregistered_process(process, job_owner)
            elif process is None and job_owner is not None:
                job_owner.close()
            with self._lock:
                self._pending_spawns -= 1
        assert session is not None
        threading.Thread(
            target=self._read_output,
            args=(session.session_id,),
            name=f"vrcforge-shell-output-{session.session_id[-8:]}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._wait_for_exit,
            args=(session.session_id,),
            name=f"vrcforge-shell-wait-{session.session_id[-8:]}",
            daemon=True,
        ).start()
        if not background:
            session.done.wait(max(0, min(int(yield_ms), 60_000)) / 1000)
        return self._execution_payload(session.session_id, owner, control_token=control_token)

    def control(self, params: dict[str, Any], *, owner_id: str) -> dict[str, Any]:
        action = str(params.get("action") or "list").strip().lower().replace("-", "_")
        owner = normalize_shell_owner(owner_id)
        if action == "list":
            with self._lock:
                self._prune_locked()
                rows = [
                    self._snapshot_locked(session, include_output=False)
                    for session in self._sessions.values()
                    if shell_owners_share_control_scope(session.owner_id, owner)
                ]
            return {"ok": True, "action": action, "sessions": rows, "count": len(rows)}
        session_id = str(params.get("sessionId") or params.get("session_id") or "").strip()
        if not session_id:
            raise ShellSessionError("sessionId is required.", 400)
        session = self._owned_session(
            session_id,
            owner,
            control_token=str(params.get("controlToken") or params.get("control_token") or ""),
            require_control_token=owner.startswith("agent:"),
        )
        if action in {"poll", "log"}:
            cursor = int(params.get("cursor") or 0)
            if action == "poll":
                timeout_ms = max(
                    0,
                    min(
                        int(
                            params.get("timeoutMs")
                            or params.get("timeout_ms")
                            or params.get("timeout")
                            or 0
                        ),
                        SHELL_SESSION_POLL_TIMEOUT_LIMIT_MS,
                    ),
                )
                if timeout_ms:
                    self._wait_for_activity(session_id, owner, cursor=cursor, timeout_ms=timeout_ms)
            if action == "log":
                explicit_offset = any(
                    key in params for key in ("offset", "lineOffset", "line_offset")
                )
                explicit_limit = any(
                    key in params for key in ("limit", "lineLimit", "line_limit")
                )
                offset = int(
                    params.get("lineOffset")
                    if "lineOffset" in params
                    else params.get("line_offset")
                    if "line_offset" in params
                    else params.get("offset")
                    if "offset" in params
                    else -200
                )
                line_limit = (
                    max(
                        1,
                        min(
                            int(
                                params.get("lineLimit")
                                or params.get("line_limit")
                                or params.get("limit")
                            ),
                            SHELL_SESSION_LOG_LINE_LIMIT,
                        ),
                    )
                    if explicit_limit
                    else None
                    if explicit_offset
                    else 200
                )
                return self._read_line_payload(
                    session_id,
                    owner,
                    offset=offset,
                    limit=line_limit,
                )
            limit = max(1, min(int(params.get("limit") or SHELL_SESSION_POLL_LIMIT), SHELL_SESSION_POLL_LIMIT))
            return self._read_payload(session_id, owner, cursor=cursor, limit=limit, full=action == "log")
        if action in {"write", "paste", "submit"}:
            text = shell_control_input_text(params, pty=session.pty)
            if not text and not bool(params.get("eof")):
                raise ShellSessionError("text or eof is required.", 400)
            if text:
                self._write(session, text)
            if bool(params.get("eof")):
                session.process.close_input()
            return {"ok": True, "action": action, "session": self._snapshot(session_id, owner)}
        if action == "send_keys":
            text = shell_control_input_text(params, pty=session.pty)
            if not text:
                raise ShellSessionError("keys are required.", 400)
            self._write(session, text)
            return {"ok": True, "action": action, "session": self._snapshot(session_id, owner)}
        if action == "kill":
            with self._lock:
                session.killed = True
            self._terminate_session(session)
            return {"ok": True, "action": action, "session": self._snapshot(session_id, owner)}
        if action == "clear":
            with self._lock:
                if self._session_is_alive(session):
                    raise ShellSessionError("A running Shell process cannot be cleared.", 409)
                self._sessions.pop(session_id, None)
            session.job_owner.close()
            session.process.close()
            return {"ok": True, "action": action, "cleared": True, "sessionId": session_id}
        if action == "remove":
            removed_running = False
            with self._lock:
                if self._session_is_alive(session):
                    session.killed = True
                    removed_running = True
            if removed_running:
                try:
                    self._terminate_session(session)
                except (OSError, RuntimeError) as exc:
                    raise ShellSessionError("The running Shell process could not be removed.", 503) from exc
            with self._lock:
                self._sessions.pop(session_id, None)
            session.job_owner.close()
            session.process.close()
            return {
                "ok": True,
                "action": action,
                "removed": True,
                "killed": removed_running,
                "sessionId": session_id,
            }
        raise ShellSessionError(f"Unsupported process action: {action}", 400)

    def kill_owner(self, owner_id: str) -> list[str]:
        """Request termination for every live process owned by one runtime session."""
        owner = normalize_shell_owner(owner_id)
        with self._lock:
            sessions = [
                session
                for session in self._sessions.values()
                if session.owner_id == owner and self._session_is_alive(session)
            ]
            for session in sessions:
                session.killed = True
        killed: list[str] = []
        for session in sessions:
            try:
                self._terminate_session(session)
            except (OSError, RuntimeError):
                continue
            killed.append(session.session_id)
        return killed

    def shutdown(self, *, grace_seconds: float = 5.0) -> tuple[int, int, int]:
        with self._lock:
            self._accepting = False
            sessions = list(self._sessions.values())
            pending_spawns = self._pending_spawns
            initial_pending_spawns = pending_spawns
        running = [session for session in sessions if self._session_is_alive(session)]
        terminated = 0
        for session in running:
            try:
                self._terminate_session(session)
                terminated += 1
            except (OSError, RuntimeError):
                pass
        deadline = self._ports.monotonic() + max(0.0, min(float(grace_seconds), 30.0))
        while self._ports.monotonic() < deadline:
            with self._lock:
                pending_spawns = self._pending_spawns
            if not any(self._session_is_alive(session) for session in running) and pending_spawns == 0:
                break
            self._ports.sleep(0.01)
        with self._lock:
            pending_spawns = self._pending_spawns
        for session in sessions:
            session.job_owner.close()
        pending = sum(1 for session in running if self._session_is_alive(session)) + pending_spawns
        return len(running) + initial_pending_spawns, terminated, pending

    def _execution_payload(
        self,
        session_id: str,
        owner: str,
        *,
        control_token: str,
    ) -> dict[str, Any]:
        snapshot = self._snapshot(session_id, owner)
        if snapshot["status"] == "running":
            return {
                "ok": True,
                "status": "running",
                "session": snapshot,
                "sessionId": session_id,
                "controlToken": control_token,
            }
        result = self._terminal_result(session_id, owner)
        return {
            "ok": bool(result["ok"]),
            "status": "executed",
            "session": snapshot,
            "sessionId": session_id,
            "controlToken": control_token,
            "result": result,
        }

    def _terminal_result(self, session_id: str, owner: str) -> dict[str, Any]:
        session = self._owned_session(session_id, owner)
        with self._lock:
            return {
                "ok": session.status == "finished" and session.exit_code == 0,
                "command": session.command,
                "cwd": session.cwd,
                "runner": "windows-pty" if session.pty else "supervised-process",
                "exitCode": session.exit_code if session.exit_code is not None else -1,
                "timedOut": session.timed_out,
                "cancelled": session.killed,
                "terminationFailed": session.termination_failed,
                "startedAt": session.started_at,
                "finishedAt": session.finished_at,
                "durationSeconds": round(
                    (session.finished_monotonic or self._ports.monotonic()) - session.started_monotonic,
                    3,
                ),
                "stdout": session.output,
                "stderr": "",
                "stdoutTruncated": session.output_truncated,
                "stderrTruncated": False,
                "sessionId": session.session_id,
            }

    def _snapshot(self, session_id: str, owner: str) -> dict[str, Any]:
        session = self._owned_session(session_id, owner)
        with self._lock:
            return self._snapshot_locked(session, include_output=False)

    def _snapshot_locked(self, session: _ShellSession, *, include_output: bool) -> dict[str, Any]:
        payload = {
            "sessionId": session.session_id,
            "pid": session.process.pid,
            "command": session.command,
            "cwd": session.cwd,
            "status": session.status,
            "pty": session.pty,
            "exitCode": session.exit_code,
            "timedOut": session.timed_out,
            "killed": session.killed,
            "terminationFailed": session.termination_failed,
            "startedAt": session.started_at,
            "finishedAt": session.finished_at,
            "cursorStart": session.output_base,
            "cursorEnd": session.output_end,
            "outputTruncated": session.output_truncated,
            "waitingForInput": bool(
                session.pty
                and self._session_is_alive(session)
                and self._ports.monotonic() - session.last_output_monotonic
                >= SHELL_SESSION_INPUT_IDLE_SECONDS
            ),
        }
        if include_output:
            payload["output"] = session.output
        return payload

    def _read_payload(
        self,
        session_id: str,
        owner: str,
        *,
        cursor: int,
        limit: int | None,
        full: bool,
    ) -> dict[str, Any]:
        session = self._owned_session(session_id, owner)
        with self._lock:
            start = session.output_base if full else max(cursor, session.output_base)
            offset = max(0, start - session.output_base)
            available = session.output[offset:]
            if len(available) > limit:
                available = available[-limit:]
                start = session.output_end - len(available)
            return {
                "ok": True,
                "action": "log" if full else "poll",
                "session": self._snapshot_locked(session, include_output=False),
                "output": available,
                "cursor": session.output_end,
                "fromCursor": start,
                "cursorExpired": cursor < session.output_base,
            }

    def _read_line_payload(
        self,
        session_id: str,
        owner: str,
        *,
        offset: int,
        limit: int | None,
    ) -> dict[str, Any]:
        session = self._owned_session(session_id, owner)
        with self._lock:
            lines = session.output.splitlines(keepends=True)
            start = max(0, offset if offset >= 0 else len(lines) + offset)
            selected = lines[start:] if limit is None else lines[start : start + limit]
            return {
                "ok": True,
                "action": "log",
                "session": self._snapshot_locked(session, include_output=False),
                "output": "".join(selected),
                "lineOffset": start,
                "nextLineOffset": start + len(selected),
                "lineCount": len(lines),
                "hasMore": start + len(selected) < len(lines),
                "cursor": session.output_end,
            }

    def _wait_for_activity(
        self,
        session_id: str,
        owner: str,
        *,
        cursor: int,
        timeout_ms: int,
    ) -> None:
        deadline = self._ports.monotonic() + (timeout_ms / 1000)
        while self._ports.monotonic() < deadline:
            session = self._owned_session(session_id, owner)
            with self._lock:
                if session.output_end > cursor or session.status != "running":
                    return
            self._ports.sleep(0.01)

    def _owned_session(
        self,
        session_id: str,
        owner: str,
        *,
        control_token: str = "",
        require_control_token: bool = False,
    ) -> _ShellSession:
        with self._lock:
            self._prune_locked()
            session = self._sessions.get(session_id)
            if session is None or not shell_owners_share_control_scope(session.owner_id, owner):
                raise ShellSessionError("Shell process session was not found.", 404)
            if require_control_token and not hmac.compare_digest(
                session.control_token_hash,
                _control_token_hash(control_token),
            ):
                raise ShellSessionError("Shell process session was not found.", 404)
            return session

    @staticmethod
    def _write(session: _ShellSession, text: str) -> None:
        if not ShellProcessSupervisor._session_is_alive(session):
            raise ShellSessionError("Shell process has already finished.", 409)
        session.process.write(text)

    def _read_output(self, session_id: str) -> None:
        session: _ShellSession | None = None
        try:
            while True:
                with self._lock:
                    session = self._sessions.get(session_id)
                if session is None:
                    return
                try:
                    chunk = session.process.read(4096)
                except (EOFError, OSError, RuntimeError):
                    return
                if not chunk:
                    if not self._session_is_alive(session):
                        return
                    self._ports.sleep(0.01)
                    continue
                with self._lock:
                    if self._sessions.get(session_id) is session:
                        self._append_output_locked(session, chunk)
        finally:
            if session is not None:
                session.reader_done.set()

    def _wait_for_exit(self, session_id: str) -> None:
        while True:
            with self._lock:
                session = self._sessions.get(session_id)
            if session is None:
                return
            if not self._session_is_alive(session):
                break
            if session.timeout_seconds > 0 and (
                self._ports.monotonic() - session.started_monotonic >= session.timeout_seconds
            ):
                with self._lock:
                    session.timed_out = True
                try:
                    self._terminate_session(session)
                except (OSError, RuntimeError):
                    with self._lock:
                        session.termination_failed = True
                break
            self._ports.sleep(0.05)
        deadline = self._ports.monotonic() + 2.0
        while self._session_is_alive(session) and self._ports.monotonic() < deadline:
            self._ports.sleep(0.01)
        session.reader_done.wait(2.0)
        with self._lock:
            if self._sessions.get(session_id) is session:
                session.exit_code = session.process.exit_code()
                session.status = (
                    "termination_failed"
                    if session.termination_failed and self._session_is_alive(session)
                    else "timed_out"
                    if session.timed_out
                    else "killed"
                    if session.killed
                    else "finished"
                )
                session.finished_at = self._ports.utc_now()
                session.finished_monotonic = self._ports.monotonic()
                session.done.set()
                completion_event = {
                    **session.completion_context,
                    "shellSessionId": session.session_id,
                    "status": session.status,
                    "exitCode": session.exit_code,
                    "timedOut": session.timed_out,
                    "cancelled": session.killed,
                    "terminationFailed": session.termination_failed,
                    "finishedAt": session.finished_at,
                    "result": {
                        "ok": session.status == "finished" and session.exit_code == 0,
                        "runner": "windows-pty" if session.pty else "supervised-process",
                        "exitCode": session.exit_code if session.exit_code is not None else -1,
                        "timedOut": session.timed_out,
                        "cancelled": session.killed,
                        "terminationFailed": session.termination_failed,
                        "stdout": session.output,
                        "stderr": "",
                        "stdoutTruncated": session.output_truncated,
                        "stderrTruncated": False,
                        "sessionId": session.session_id,
                    },
                }
            else:
                completion_event = None
        if completion_event is not None:
            try:
                session.on_finished(completion_event)
            except Exception:
                # Completion reporting is advisory and cannot change the
                # already-finalized process result or strand lifecycle cleanup.
                pass

    @staticmethod
    def _dispose_unregistered_process(
        process: ManagedShellProcess,
        job_owner: ShellJobOwner | None,
    ) -> None:
        if job_owner is not None:
            try:
                job_owner.close()
            except (OSError, RuntimeError):
                pass
        try:
            process.terminate()
        except (OSError, RuntimeError):
            pass
        try:
            process.close()
        except (OSError, RuntimeError):
            pass

    @staticmethod
    def _session_is_alive(session: _ShellSession) -> bool:
        if session.process.is_alive():
            return True
        try:
            return session.job_owner.active_process_count() > 0
        except (OSError, RuntimeError):
            return True

    @staticmethod
    def _terminate_session(session: _ShellSession) -> None:
        # Closing this session's Job is the authoritative tree termination;
        # the root-process fallback covers non-Windows test/dev ports.
        session.job_owner.close()
        if session.process.is_alive():
            session.process.terminate()

    def _append_output_locked(self, session: _ShellSession, chunk: str) -> None:
        session.output += chunk
        session.output_end += len(chunk)
        session.last_output_monotonic = self._ports.monotonic()
        if len(session.output) > SHELL_SESSION_OUTPUT_LIMIT:
            removed = len(session.output) - SHELL_SESSION_OUTPUT_LIMIT
            session.output = session.output[removed:]
            session.output_base += removed
            session.output_truncated = True
        total = sum(len(item.output) for item in self._sessions.values())
        if total > SHELL_SESSION_TOTAL_OUTPUT_LIMIT:
            excess = total - SHELL_SESSION_TOTAL_OUTPUT_LIMIT
            for item in sorted(self._sessions.values(), key=lambda value: value.started_monotonic):
                if excess <= 0:
                    break
                removed = min(excess, len(item.output))
                if removed:
                    item.output = item.output[removed:]
                    item.output_base += removed
                    item.output_truncated = True
                    excess -= removed

    def _prune_locked(self) -> None:
        now = self._ports.monotonic()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if session.finished_monotonic is not None
            and now - session.finished_monotonic >= SHELL_SESSION_FINISHED_TTL_SECONDS
        ]
        for session_id in expired:
            session = self._sessions.pop(session_id)
            session.job_owner.close()
            session.process.close()

    def _evict_finished_for_capacity_locked(self) -> None:
        while len(self._sessions) + self._pending_spawns >= SHELL_SESSION_COUNT_LIMIT:
            finished = [
                session
                for session in self._sessions.values()
                if session.finished_monotonic is not None and session.status != "running"
            ]
            if not finished:
                return
            session = min(finished, key=lambda value: value.finished_monotonic or 0.0)
            self._sessions.pop(session.session_id, None)
            session.job_owner.close()
            session.process.close()


def normalize_shell_owner(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "local-user"
    if len(normalized) <= 256:
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()
    prefix = "agent:" if normalized.startswith("agent:") else "owner:"
    return f"{prefix}sha256:{digest}"


def shell_owner_control_scope(value: str) -> str:
    owner = normalize_shell_owner(value)
    if owner.startswith("runtime-session:"):
        return owner.split("|origin:", 1)[0]
    return owner


def shell_owners_share_control_scope(left: str, right: str) -> bool:
    return shell_owner_control_scope(left) == shell_owner_control_scope(right)


def _control_token_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def validate_shell_environment(environment: dict[str, str]) -> dict[str, str]:
    if len(environment) > 4096:
        raise ShellSessionError("The inherited process environment is too large.", 400)
    for key, value in environment.items():
        if "\x00" in str(key) or "\x00" in str(value):
            raise ShellSessionError("The inherited process environment contains a null byte.", 400)
    return {str(key): str(value) for key, value in environment.items()}


def normalize_shell_environment_overrides(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ShellSessionError("env must be an object of string values.", 400)
    if len(value) > SHELL_ENV_ENTRY_LIMIT:
        raise ShellSessionError("Too many environment overrides were supplied.", 400)
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if not _ENV_NAME.fullmatch(key):
            raise ShellSessionError("An environment variable name is invalid.", 400)
        if key.upper() in _FORBIDDEN_ENV_OVERRIDE_NAMES:
            raise ShellSessionError(f"Environment override is not allowed for {key}.", 400)
        text = str(raw_value)
        if len(text) > SHELL_ENV_VALUE_LIMIT:
            raise ShellSessionError("An environment variable value is too large.", 400)
        if "\x00" in text:
            raise ShellSessionError("An environment variable value contains a null byte.", 400)
        result[key] = text
    return result


def build_shell_environment(
    inherited: dict[str, str],
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    result = {
        str(key): str(value)
        for key, value in inherited.items()
        if str(key).upper() in _INHERITED_ENV_NAMES
    }
    result.update(overrides or {})
    return validate_shell_environment(result)


def _key_sequence(value: str) -> str:
    key = value.strip().lower()
    aliases = {
        "enter": "\r",
        "return": "\r",
        "tab": "\t",
        "escape": "\x1b",
        "esc": "\x1b",
        "backspace": "\x08",
        "ctrl+c": "\x03",
        "c-c": "\x03",
        "ctrl+d": "\x04",
        "c-d": "\x04",
        "ctrl+z": "\x1a",
        "c-z": "\x1a",
        "up": "\x1b[A",
        "down": "\x1b[B",
        "right": "\x1b[C",
        "left": "\x1b[D",
    }
    return aliases.get(key, value)


def shell_control_input_text(params: dict[str, Any], *, pty: bool) -> str:
    action = str(params.get("action") or "").strip().lower().replace("-", "_")
    if action == "write":
        return str(params.get("data") if "data" in params else params.get("text") or "")
    if action == "paste":
        text = str(params.get("text") or "")
        bracketed = bool(params.get("bracketed", params.get("bracketedPaste", True)))
        return f"\x1b[200~{text}\x1b[201~" if pty and bracketed and text else text
    if action == "submit":
        text = str(params.get("text") or "")
        return text + "\r"
    if action == "send_keys":
        if "literal" in params:
            return str(params.get("literal") or "")
        if "hex" in params:
            value = params.get("hex")
            if isinstance(value, list):
                raw = "".join(
                    f"{item:02x}" if isinstance(item, int) else str(item).removeprefix("0x")
                    for item in value
                )
            else:
                raw = str(value or "").replace(" ", "").removeprefix("0x")
            try:
                return bytes.fromhex(raw).decode("latin-1")
            except ValueError as exc:
                raise ShellSessionError("send_keys hex is invalid.", 400) from exc
        keys = params.get("keys")
        values = keys if isinstance(keys, list) else [keys]
        return "".join(_key_sequence(str(item or "")) for item in values)
    return ""


def _terminate_windows_process_tree(pid: int, fallback: Callable[[], None]) -> None:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    fallback()
