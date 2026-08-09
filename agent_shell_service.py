"""Typed Shell policy, approval binding, and supervised process lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from agent_command_safety import (
    is_path_within,
    looks_like_absolute_path,
    strip_quotes,
    tokenize_command,
)
from agent_shell_process_supervisor import (
    ShellProcessSupervisor,
    ShellSessionError,
    ShellSessionPorts,
    build_shell_environment,
    normalize_shell_environment_overrides,
    shell_control_input_text,
)


SHELL_RUNNER_NATIVE = "native-win-process"
SHELL_RUNNER_POWERSHELL = "powershell-fallback"
SHELL_NATIVE_BLOCK_PATTERN = re.compile(r"[|;&<>^`$%(){}\[\]#]|@\"|@'")
SHELL_APPROVAL_BOUND_FILE_LIMIT = 16 * 1024 * 1024
AUTO_APPROVAL_MANUAL_SHELL_COMMANDS = {
    "del",
    "erase",
    "rd",
    "ri",
    "rm",
    "rmdir",
    "remove-item",
}
UNITY_PROJECT_MARKERS = ("Assets", "Packages", "ProjectSettings")
_POWERSHELL_EXECUTABLE_CACHE: str | None = None


class AgentShellError(RuntimeError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class ShellProcess(Protocol):
    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...

    def communicate(self, *, timeout: float | None = None) -> tuple[str, str]: ...

    def kill(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ShellApprovalRequest:
    agent_name: str
    target_tool: str
    arguments: dict[str, Any]
    reason: str
    preview: dict[str, Any]
    risk_level: str
    user_constraints: Any
    requires_explicit_approval: bool
    explicit_approval_reason: str
    goal_delivery_id: str
    task_context: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ShellApprovalPorts:
    """Approval owner operations; pending lookup is shell-only by contract."""

    find_pending_shell: Callable[[str, str], dict[str, Any] | None]
    create: Callable[[ShellApprovalRequest], dict[str, Any]]
    update_metadata: Callable[[str, dict[str, Any]], None]
    find: Callable[[str], dict[str, Any] | None]
    apply: Callable[[str], dict[str, Any]]
    auto_enabled: Callable[[], bool]
    auto_execute: Callable[[dict[str, Any]], dict[str, Any] | None]
    execution_mode: Callable[[], str]
    read_user_constraints: Callable[[], Any]
    redact: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ShellProcessPorts:
    spawn: Callable[..., ShellProcess]
    terminate_tree: Callable[[ShellProcess], None]
    environment: Callable[[], dict[str, str]]
    monotonic: Callable[[], float]
    utc_now: Callable[[], str]
    sleep: Callable[[float], None]


@dataclass(frozen=True, slots=True)
class AgentShellPorts:
    approvals: ShellApprovalPorts
    append_audit: Callable[[dict[str, Any]], None]
    permission_audit_context: Callable[[], dict[str, Any]]
    cancellation_requested: Callable[[str, str, str], bool]
    default_workspace_root: Callable[[], Path]
    is_unity_project_root: Callable[[Path], bool]
    error_factory: Callable[[str, int], Exception] = lambda detail, status: AgentShellError(
        detail,
        status,
    )
    session_finished: Callable[[dict[str, Any]], None] = lambda _event: None


@dataclass(frozen=True, slots=True)
class ShellShutdownReport:
    snapshot_count: int
    terminated_count: int
    pending_count: int


def default_shell_process_ports() -> ShellProcessPorts:
    return ShellProcessPorts(
        spawn=subprocess.Popen,
        terminate_tree=kill_process_tree,
        environment=lambda: os.environ.copy(),
        monotonic=time.monotonic,
        utc_now=utc_now_iso,
        sleep=time.sleep,
    )


class AgentShellService:
    """One owner for Shell classification, approval payloads, and child processes."""

    def __init__(
        self,
        ports: AgentShellPorts,
        *,
        process_ports: ShellProcessPorts | None = None,
        session_ports: ShellSessionPorts | None = None,
    ) -> None:
        self._ports = ports
        self._process = process_ports or default_shell_process_ports()
        self._lifecycle_lock = threading.RLock()
        self._active_processes: dict[int, ShellProcess] = {}
        self._process_admissions: dict[int, int] = {}
        self._admitted_workers: set[int] = set()
        self._next_admission_id = 0
        self._thread_admission = threading.local()
        self._accepting = True
        self._sessions = ShellProcessSupervisor(session_ports)
        self._session_input_buffers: dict[str, str] = {}

    @property
    def active_process_count(self) -> int:
        with self._lifecycle_lock:
            return len(self._active_processes)

    @property
    def default_workspace_root(self) -> Path:
        return self._ports.default_workspace_root().resolve()

    def start(self) -> None:
        with self._lifecycle_lock:
            self._accepting = True
        self._sessions.start()

    def shutdown(self, *, grace_seconds: float = 5.0) -> ShellShutdownReport:
        with self._lifecycle_lock:
            self._accepting = False
            snapshot_count = self._pending_owner_count_locked()
        deadline = self._process.monotonic() + max(0.0, min(float(grace_seconds), 30.0))
        attempted: set[int] = set()
        terminated = 0
        while True:
            with self._lifecycle_lock:
                active = tuple(self._active_processes.values())
                pending_count = self._pending_owner_count_locked()
            if pending_count == 0:
                break
            for process in active:
                if process.pid in attempted:
                    continue
                attempted.add(process.pid)
                try:
                    if self._request_shutdown_termination(process):
                        terminated += 1
                except BaseException:
                    # One malformed process port must not strand the other owned children.
                    continue
            with self._lifecycle_lock:
                pending_count = self._pending_owner_count_locked()
            if pending_count == 0 or self._process.monotonic() >= deadline:
                break
            self._process.sleep(0.01)
        session_snapshot, session_terminated, session_pending = self._sessions.shutdown(
            grace_seconds=grace_seconds
        )
        return ShellShutdownReport(
            snapshot_count=snapshot_count + session_snapshot,
            terminated_count=terminated + session_terminated,
            pending_count=pending_count + session_pending,
        )

    def _pending_owner_count_locked(self) -> int:
        active_admissions = set(self._process_admissions.values())
        admitted_without_process = self._admitted_workers.difference(active_admissions)
        return len(self._active_processes) + len(admitted_without_process)

    def _request_shutdown_termination(self, process: ShellProcess) -> bool:
        try:
            if process.poll() is not None:
                return False
        except BaseException:
            pass
        try:
            self._process.terminate_tree(process)
            return True
        except BaseException:
            try:
                process.kill()
                return True
            except BaseException:
                return False

    def classify(self, params: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(params, str):
            params = {"command": params}
        command = str(params.get("command") or "").strip()
        workspace_root = self._resolve_workspace_root(params)
        cwd = self._resolve_cwd(params, workspace_root)

        policy_error = self._requested_policy_error(params)
        if policy_error:
            return self._classification(command, cwd, workspace_root, "reject", [policy_error])

        if not command:
            return self._classification(command, cwd, workspace_root, "reject", ["Command is empty."])
        if len(command) > 4000:
            return self._classification(command, cwd, workspace_root, "reject", ["Command is too long."])

        tokens = tokenize_command(command)
        if not tokens:
            return self._classification(
                command,
                cwd,
                workspace_root,
                "reject",
                ["Command could not be parsed."],
            )
        requested_project_value = str(
            params.get("projectRoot")
            or params.get("project_root")
            or params.get("projectPath")
            or params.get("project_path")
            or ""
        ).strip()
        if requested_project_value:
            try:
                requested_project = Path(requested_project_value).expanduser().resolve()
            except (OSError, RuntimeError, ValueError):
                return self._classification(
                    command,
                    cwd,
                    workspace_root,
                    "reject",
                    ["The explicit Unity project root is invalid."],
                )
            if not self._ports.is_unity_project_root(requested_project):
                return self._classification(
                    command,
                    cwd,
                    workspace_root,
                    "reject",
                    ["The explicit Unity project root is not a valid Unity project."],
                )
        read_only = self._command_is_read_only(command)
        protected_project = self._protected_unity_project_root(params, cwd, tokens, command)
        if protected_project is not None and not read_only:
            return self._classification(
                command,
                cwd,
                workspace_root,
                "high",
                ["Command may modify a protected Unity project."],
                project_root=protected_project,
            )
        return self._classification(
            command,
            cwd,
            workspace_root,
            "low",
            [
                "Read-only Unity project inspection command."
                if protected_project is not None
                else "Host shell execution outside a protected Unity project."
            ],
            project_root=protected_project,
        )

    def execute(
        self,
        params: dict[str, Any],
        agent_name: str = "desktop-agent",
        *,
        task_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._execution_admission() as admission_id:
            classification = self.classify(params)
            command = classification["command"]
            if classification["risk"] == "reject":
                self._ports.append_audit(
                    {
                        "event": "shell_rejected",
                        "classification": classification,
                        "agent": agent_name,
                        **self._ports.permission_audit_context(),
                    }
                )
                return {
                    "ok": False,
                    "status": "rejected",
                    "classification": classification,
                    "error": "; ".join(classification["reasons"]),
                }
            if classification["risk"] == "high":
                approval = self._create_approval(
                    params,
                    classification,
                    agent_name,
                    task_context=task_context,
                )
                if self._ports.approvals.auto_enabled():
                    auto_payload = self._ports.approvals.auto_execute(approval)
                    if auto_payload is not None:
                        auto_payload["classification"] = classification
                        return auto_payload
                return {
                    "ok": True,
                    "status": "pending_approval",
                    "classification": classification,
                    "approval": approval,
                    "approval_id": approval["id"],
                    "approvalId": approval["id"],
                }

            execution = self._execute_direct(
                params,
                classification,
                admission_id=admission_id,
                owner_id=self._session_owner(params, agent_name),
            )
            result = execution.get("result")
            self._ports.append_audit(
                {
                    "event": "shell_executed",
                    "agent": agent_name,
                    "classification": classification,
                    "result": summarize_shell_result(result) if isinstance(result, dict) else {
                        "status": execution.get("status"),
                        "sessionId": execution.get("sessionId"),
                    },
                    **self._ports.permission_audit_context(),
                }
            )
            execution["classification"] = classification
            return execution

    def process(self, params: dict[str, Any], agent_name: str = "desktop-agent") -> dict[str, Any]:
        try:
            owner_id = self._process_owner(params, agent_name)
            action = str(params.get("action") or "list").strip().lower().replace("-", "_")
            if action in {"write", "paste", "submit", "send_keys"}:
                with self._lifecycle_lock:
                    pending = self._guard_process_input(params, owner_id=owner_id)
                    result = self._sessions.control(params, owner_id=owner_id)
                    session_id = str(params.get("sessionId") or params.get("session_id") or "").strip()
                    if session_id:
                        self._session_input_buffers[session_id] = pending
                    return result
            result = self._sessions.control(params, owner_id=owner_id)
            if action == "remove":
                session_id = str(params.get("sessionId") or params.get("session_id") or "").strip()
                with self._lifecycle_lock:
                    self._session_input_buffers.pop(session_id, None)
            return result
        except ShellSessionError as exc:
            self._raise(exc.detail, exc.status_code)

    def cancel_owner(self, owner_id: str) -> list[str]:
        return self._sessions.kill_owner(owner_id)

    def execute_approved(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._execution_admission():
            approval_id = str(params.get("approval_id") or params.get("approvalId") or "").strip()
            if not approval_id:
                self._raise("approval_id is required.")
            approval = self._ports.approvals.find(approval_id)
            if not approval:
                self._raise(f"Approval was not found: {approval_id}", 404)
            if approval.get("targetTool") != "vrcforge_shell_execute":
                self._raise("Approval is not a shell execution approval.")
            return self._ports.approvals.apply(approval_id)

    def execute_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._execution_admission() as admission_id:
            return self._execute_payload(params, admission_id=admission_id)

    def _execute_payload(self, params: dict[str, Any], *, admission_id: int) -> dict[str, Any]:
        command = str(params.get("command") or "").strip()
        expected_hash = str(params.get("command_hash") or params.get("commandHash") or "")
        if not expected_hash:
            self._raise("Stored shell approval command hash is required.")
        if expected_hash != command_hash(command):
            self._raise("Stored shell approval command hash does not match.")
        workspace_root = self._resolve_workspace_root(params)
        cwd = self._resolve_cwd(params, workspace_root)
        timeout_seconds = self._timeout_seconds(params)
        if any(
            key in params
            for key in (
                "background",
                "pty",
                "yieldMs",
                "yield_ms",
                "env",
                "host",
                "node",
                "elevated",
                "security",
                "ask",
            )
        ):
            self._raise("Stored Unity-project shell approvals cannot contain advanced process options.")
        expected_cwd_hash = str(params.get("cwd_hash") or params.get("cwdHash") or "")
        expected_workspace_hash = str(
            params.get("workspace_root_hash") or params.get("workspaceRootHash") or ""
        )
        expected_timeout_hash = str(params.get("timeout_hash") or params.get("timeoutHash") or "")
        expected_options_hash = str(
            params.get("execution_options_hash") or params.get("executionOptionsHash") or ""
        )
        project_root_value = str(params.get("projectRoot") or params.get("project_root") or "").strip()
        expected_project_root_hash = str(
            params.get("project_root_hash") or params.get("projectRootHash") or ""
        )
        if not expected_cwd_hash:
            self._raise("Stored shell approval cwd hash is required.")
        if expected_cwd_hash != stable_hash(str(cwd)):
            self._raise("Stored shell approval cwd hash does not match.")
        if not expected_workspace_hash:
            self._raise("Stored shell approval workspace root hash is required.")
        if expected_workspace_hash != stable_hash(str(workspace_root)):
            self._raise("Stored shell approval workspace root hash does not match.")
        if not expected_timeout_hash:
            self._raise("Stored shell approval timeout hash is required.")
        if expected_timeout_hash != stable_hash(str(timeout_seconds)):
            self._raise("Stored shell approval timeout hash does not match.")
        if not expected_options_hash:
            self._raise("Stored shell approval execution options hash is required.")
        actual_options_hash = stable_hash(
            json.dumps(
                {
                    "background": bool(params.get("background")),
                    "pty": bool(params.get("pty")),
                    "yieldMs": max(
                        0,
                        min(int(params.get("yieldMs") or params.get("yield_ms") or 10_000), 60_000),
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if expected_options_hash != actual_options_hash:
            self._raise("Stored shell approval execution options hash does not match.")
        expected_execution_binding_hash = str(
            params.get("execution_binding_hash") or params.get("executionBindingHash") or ""
        )
        if not expected_execution_binding_hash:
            self._raise("Stored shell approval execution binding hash is required.")
        current_execution_binding_hash = stable_hash(
            json.dumps(
                self._command_execution_binding(command, cwd),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if expected_execution_binding_hash != current_execution_binding_hash:
            self._raise("Stored shell approval executable or referenced file binding has changed.", 409)
        if not project_root_value:
            self._raise("Stored shell approval Unity project root is required.")
        project_root = Path(project_root_value).expanduser().resolve()
        if not expected_project_root_hash:
            self._raise("Stored shell approval Unity project root hash is required.")
        if expected_project_root_hash != stable_hash(str(project_root)):
            self._raise("Stored shell approval Unity project root hash does not match.")
        if not self._ports.is_unity_project_root(project_root):
            self._raise("Stored shell approval Unity project root is no longer valid.")

        environment_overrides = self._environment_overrides(params)
        if environment_overrides:
            self._raise("Unity-project shell approvals cannot persist environment override values.")

        classification = self.classify(
            {
                "command": command,
                "cwd": str(cwd),
                "workspace_root": str(workspace_root),
                "projectRoot": str(project_root),
            }
        )
        if classification.get("risk") == "reject":
            self._raise(
                "Approved shell command is no longer executable: "
                + "; ".join(classification.get("reasons") or [])
            )
        if classification.get("commandHash") != expected_hash:
            self._raise("Reclassified shell command hash does not match approval.")
        if classification.get("projectRoot") != str(project_root):
            self._raise("Reclassified shell Unity project root does not match approval.")

        execution = self._execute_direct(
            params,
            classification,
            admission_id=admission_id,
            owner_id=self._session_owner(params, "approved-shell"),
        )
        result = execution.get("result")
        self._ports.append_audit(
            {
                "event": "shell_approved_executed",
                "sessionId": params.get("session_id") or params.get("sessionId") or "",
                "turnId": params.get("turn_id") or params.get("turnId") or "",
                "commandHash": command_hash(command),
                "cwdHash": stable_hash(str(cwd)),
                "workspaceRootHash": stable_hash(str(workspace_root)),
                "timeoutHash": stable_hash(str(timeout_seconds)),
                "projectRootHash": stable_hash(str(project_root)),
                "cwd": str(cwd),
                "workspaceRoot": str(workspace_root),
                "result": summarize_shell_result(result) if isinstance(result, dict) else {
                    "status": execution.get("status"),
                    "sessionId": execution.get("sessionId"),
                },
                **self._ports.permission_audit_context(),
            }
        )
        if isinstance(result, dict):
            return result
        return execution

    def _execute_direct(
        self,
        params: dict[str, Any],
        classification: dict[str, Any],
        *,
        admission_id: int,
        owner_id: str,
    ) -> dict[str, Any]:
        command = str(classification["command"])
        cwd = Path(classification["cwd"])
        timeout_seconds = self._timeout_seconds(params)
        env_overrides = self._environment_overrides(params)
        use_sessions = bool(
            params.get("background")
            or params.get("pty")
            or "yieldMs" in params
            or "yield_ms" in params
            or "env" in params
            or timeout_seconds == 0
        )
        if use_sessions:
            environment = build_shell_environment(self._process.environment(), env_overrides)
            cancel_ids = _cancel_ids(params)

            def cancel_requested() -> bool:
                return bool(
                    cancel_ids
                    and self._ports.cancellation_requested(
                        cancel_ids[0] if len(cancel_ids) > 0 else "",
                        cancel_ids[1] if len(cancel_ids) > 1 else "",
                        cancel_ids[2] if len(cancel_ids) > 2 else "",
                    )
                )

            try:
                return self._sessions.execute(
                    command=command,
                    argv=self._command_argv(command, interactive=bool(params.get("pty"))),
                    cwd=cwd,
                    environment=environment,
                    owner_id=owner_id,
                    background=bool(params.get("background")),
                    yield_ms=max(0, min(int(params.get("yieldMs") or params.get("yield_ms") or 10_000), 60_000)),
                    timeout_seconds=timeout_seconds,
                    pty=bool(params.get("pty")),
                    cancel_requested=cancel_requested,
                    completion_context={
                        "runtimeSessionId": str(
                            params.get("session_id") or params.get("sessionId") or ""
                        ),
                        "turnId": str(params.get("turn_id") or params.get("turnId") or ""),
                        "clientTurnId": str(
                            params.get("client_turn_id") or params.get("clientTurnId") or ""
                        ),
                    },
                    on_finished=self._ports.session_finished,
                )
            except ShellSessionError as exc:
                self._raise(exc.detail, exc.status_code)
        result = self._run_command(
            command,
            cwd,
            admission_id=admission_id,
            timeout_seconds=timeout_seconds,
            cancel_ids=_cancel_ids(params),
            environment_overrides=env_overrides,
        )
        return {"ok": result["ok"], "status": "executed", "result": result}

    @staticmethod
    def _session_owner(params: dict[str, Any], agent_name: str) -> str:
        return str(
            params.get("_trusted_owner_id")
            or params.get("_trustedOwnerId")
            or params.get("turn_id")
            or params.get("turnId")
            or params.get("client_turn_id")
            or params.get("clientTurnId")
            or params.get("session_id")
            or params.get("sessionId")
            or params.get("agent_id")
            or params.get("agentId")
            or agent_name
            or "local-user"
        )

    @staticmethod
    def _process_owner(params: dict[str, Any], agent_name: str) -> str:
        return str(
            params.get("_trusted_owner_id")
            or params.get("_trustedOwnerId")
            or params.get("runtime_session_id")
            or params.get("runtimeSessionId")
            or params.get("owner_session_id")
            or params.get("ownerSessionId")
            or params.get("agent_id")
            or params.get("agentId")
            or agent_name
            or "local-user"
        )

    def _guard_process_input(self, params: dict[str, Any], *, owner_id: str) -> str:
        action = str(params.get("action") or "list").strip().lower().replace("-", "_")
        if action not in {"write", "paste", "submit", "send_keys"}:
            return ""
        session_id = str(params.get("sessionId") or params.get("session_id") or "").strip()
        if not session_id:
            return ""
        context = self._sessions.control(
            {
                "action": "poll",
                "sessionId": session_id,
                "limit": 1,
                "controlToken": params.get("controlToken") or params.get("control_token") or "",
            },
            owner_id=owner_id,
        )
        session = context.get("session") or {}
        cwd = str(session.get("cwd") or self.default_workspace_root)
        if action == "write":
            text = str(params.get("data") if "data" in params else params.get("text") or "")
        elif action == "paste":
            text = str(params.get("text") or "")
        elif action == "submit":
            text = str(params.get("text") or "") + "\n"
        else:
            text = shell_control_input_text(params, pty=bool(session.get("pty")))
        combined = (self._session_input_buffers.get(session_id) or "") + text
        normalized = combined.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        candidates = lines[:-1]
        remainder = lines[-1][-16_384:]
        if remainder.strip():
            candidates.append(remainder)
        for command in candidates:
            if not command.strip():
                continue
            classification = self.classify(
                {
                    "command": command,
                    "cwd": cwd,
                    "workspace_root": cwd,
                    "projectRoot": params.get("projectRoot") or params.get("project_root") or "",
                }
            )
            if classification.get("risk") in {"high", "reject"}:
                self._raise(
                    "Shell process input may target a Unity project. Run it as a new Shell command so approval, checkpoint, and rollback can be applied.",
                    409,
                )
        return remainder

    def _timeout_seconds(self, params: dict[str, Any]) -> int:
        raw = params.get("timeout_seconds")
        if raw is None:
            raw = params.get("timeoutSeconds")
        if raw is None:
            raw = params.get("timeout")
        if raw is None or raw == "":
            return 120
        value = int(raw)
        if value < 0 or value > 86_400:
            self._raise("timeout must be between 0 and 86400 seconds.")
        return value

    @staticmethod
    def _requested_policy_error(params: dict[str, Any]) -> str:
        host = str(params.get("host") or "").strip().lower()
        if host and host not in {"local", "host"}:
            return "This VRCForge Shell supports only the local host."
        if str(params.get("node") or "").strip():
            return "Remote Shell nodes are not supported."
        elevated = params.get("elevated")
        if elevated not in (None, False, 0, "", "false", "False"):
            return "Per-command elevation is not supported; start VRCForge with the required user authority."
        security = str(params.get("security") or "").strip().lower()
        if security:
            if security == "deny":
                return "Shell execution was denied by the requested security policy."
            return "Shell security is selected in VRCForge permission settings, not per command."
        ask = str(params.get("ask") or "").strip().lower()
        if ask:
            return "Shell approval behavior is selected in VRCForge permission settings, not per command."
        return ""

    def _environment_overrides(self, params: dict[str, Any]) -> dict[str, str]:
        try:
            return normalize_shell_environment_overrides(params.get("env"))
        except ShellSessionError as exc:
            self._raise(exc.detail, exc.status_code)

    @staticmethod
    def _command_argv(command: str, *, interactive: bool = False) -> list[str]:
        native_argv = native_shell_argv(command)
        if native_argv is not None:
            return native_argv
        argv = [
            resolve_powershell_executable(),
            "-NoLogo",
            "-NoProfile",
        ]
        if not interactive:
            argv.append("-NonInteractive")
        return [*argv, "-Command", command]

    def _command_execution_binding(self, command: str, cwd: Path) -> dict[str, Any]:
        argv = self._command_argv(command)
        referenced_files: dict[str, str] = {}
        candidates = [*argv[1:], *[strip_quotes(value) for value in tokenize_command(command)[1:]]]
        for value in candidates:
            if not value or value.startswith("-"):
                continue
            try:
                path = Path(value).expanduser()
                if not path.is_absolute():
                    path = cwd / path
                path = path.resolve()
                if not path.is_file():
                    continue
                size = path.stat().st_size
            except (OSError, ValueError):
                continue
            if size > SHELL_APPROVAL_BOUND_FILE_LIMIT:
                self._raise("A referenced shell file is too large to bind to approval.")
            try:
                referenced_files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                self._raise("A referenced shell file could not be bound to approval.", 409)
        return {"argv": argv, "referencedFiles": referenced_files}

    def _raise(self, detail: str, status_code: int = 400) -> None:
        raise self._ports.error_factory(detail, status_code)

    def _resolve_workspace_root(self, params: dict[str, Any]) -> Path:
        raw = str(params.get("workspace_root") or params.get("workspaceRoot") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return self.default_workspace_root

    @staticmethod
    def _resolve_cwd(params: dict[str, Any], workspace_root: Path) -> Path:
        raw = str(params.get("cwd") or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return workspace_root

    def _classification(
        self,
        command: str,
        cwd: Path,
        workspace_root: Path,
        risk: str,
        reasons: list[str],
        *,
        project_root: Path | None = None,
    ) -> dict[str, Any]:
        resolved_project_root = str(project_root.resolve()) if project_root is not None else ""
        return {
            "ok": risk != "reject",
            "command": command,
            "commandHash": command_hash(command),
            "risk": risk,
            "reasons": reasons,
            "cwd": str(cwd),
            "workspaceRoot": str(workspace_root),
            "readOnly": self._command_is_read_only(command),
            "requiresApproval": risk == "high",
            "protectionScope": "unity_project" if resolved_project_root else "host",
            "projectRoot": resolved_project_root,
            "plannedRunner": (
                SHELL_RUNNER_NATIVE if native_shell_argv(command) is not None else SHELL_RUNNER_POWERSHELL
            ),
        }

    def _protected_unity_project_root(
        self,
        params: dict[str, Any],
        cwd: Path,
        tokens: list[str],
        command: str,
    ) -> Path | None:
        requested_value = str(
            params.get("projectRoot")
            or params.get("project_root")
            or params.get("projectPath")
            or params.get("project_path")
            or ""
        ).strip()
        requested = Path(requested_value).expanduser().resolve() if requested_value else None
        cwd_project = self._find_unity_project_root(cwd)
        if cwd_project is not None:
            return cwd_project
        if requested is not None:
            return requested
        environment_project = self._environment_unity_project_root(params, command)
        if environment_project is not None:
            return environment_project
        for token in tokens[1:]:
            value = strip_quotes(token).strip()
            if not value or value.startswith("-"):
                continue
            if not (
                looks_like_absolute_path(value)
                or value.startswith((".", "~"))
                or "/" in value
                or "\\" in value
            ):
                continue
            try:
                candidate = Path(value).expanduser()
                if not candidate.is_absolute():
                    candidate = cwd / candidate
                candidate = candidate.resolve()
            except (OSError, RuntimeError, ValueError):
                continue
            if requested is not None and is_path_within(candidate, requested):
                return requested
            discovered = self._find_unity_project_root(candidate)
            if discovered is not None:
                return discovered
        # Nested shells and interpreters can place the real command in one
        # quoted token. Inspect absolute Windows path substrings as a guardrail
        # so the common `powershell -Command "Set-Content D:\..."` form still
        # reaches the Unity approval lane.
        for value in re.findall(r"(?i)(?:[a-z]:[\\/])[^\s\"'`;|&]+", command):
            try:
                discovered = self._find_unity_project_root(Path(value))
            except (OSError, RuntimeError, ValueError):
                continue
            if discovered is not None:
                return discovered
        return None

    def _environment_unity_project_root(
        self,
        params: dict[str, Any],
        command: str,
    ) -> Path | None:
        raw_environment = params.get("env")
        if not isinstance(raw_environment, dict):
            return None
        for raw_name, raw_value in raw_environment.items():
            name = str(raw_name).strip()
            value = str(raw_value).strip()
            if not name or not value:
                continue
            referenced = any(
                re.search(pattern, command, flags=re.IGNORECASE)
                for pattern in (
                    rf"\$env:{re.escape(name)}\b",
                    rf"\$\{{env:{re.escape(name)}\}}",
                    rf"%{re.escape(name)}%",
                )
            )
            if not referenced or not looks_like_absolute_path(value):
                continue
            project = self._find_unity_project_root(Path(value))
            if project is not None:
                return project
        return None

    def _find_unity_project_root(self, candidate: Path) -> Path | None:
        try:
            current = candidate.expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if current.is_file():
            current = current.parent
        for path in (current, *current.parents):
            try:
                if self._ports.is_unity_project_root(path):
                    return path
            except (OSError, RuntimeError, ValueError):
                return None
        return None

    @staticmethod
    def _command_is_read_only(command: str) -> bool:
        if (
            "\n" in command
            or "\r" in command
            or re.search(r"&&|\|\||[;|]|(?:^|\s)(?:\d?>|\*>|>>)", command)
            or "$(" in command
            or "{" in command
            or "}" in command
            or '@"' in command
            or "@'" in command
        ):
            return False
        tokens = [strip_quotes(token) for token in tokenize_command(command)]
        if not tokens:
            return False
        command_name = tokens[0].lower()
        args = [token.lower() for token in tokens[1:]]
        if command_name in {"get-childitem", "dir", "ls", "get-content", "type", "findstr"}:
            return True
        if command_name == "rg":
            return not any(
                arg in {"--pre", "--pre-glob", "--output"}
                or arg.startswith(("--pre=", "--pre-glob=", "--output="))
                for arg in args
            )
        if command_name in {"python", "node", "npm", "uv"} and args in (["--version"], ["-v"]):
            return True
        if command_name == "where" and len(args) == 1:
            return bool(re.fullmatch(r"[a-zA-Z0-9_.-]+", args[0] or ""))
        return False

    def manual_approval_reason(self, classification: dict[str, Any]) -> str:
        if classification.get("projectRoot") and classification.get("readOnly") is not True:
            return "Shell commands that may modify a Unity project require explicit approval."
        return ""

    def _low_risk_reasons(self, command_name: str, args: list[str], workspace_root: Path) -> list[str]:
        if command_name in {"get-childitem", "dir", "ls", "get-content", "type", "rg", "findstr"}:
            if self._read_args_are_low_risk(command_name, args, workspace_root):
                return ["Read-only workspace inspection command."]
            return []
        if command_name in {"python", "node", "npm", "uv"} and args in (["--version"], ["-v"]):
            return ["Read-only environment version probe."]
        if command_name == "where" and len(args) == 1 and re.fullmatch(r"[a-zA-Z0-9_.-]+", args[0] or ""):
            return ["Read-only executable lookup."]
        if command_name == "git":
            return self._git_low_risk_reasons(args, workspace_root)
        return []

    def _read_args_are_low_risk(self, command_name: str, args: list[str], workspace_root: Path) -> bool:
        if command_name == "rg":
            for arg in args:
                lowered = arg.lower()
                if lowered == "--pre" or lowered.startswith("--pre="):
                    return False
                if lowered == "--pre-glob" or lowered.startswith("--pre-glob="):
                    return False
        return self._args_stay_in_workspace(args, workspace_root)

    def _args_stay_in_workspace(self, args: list[str], workspace_root: Path) -> bool:
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if not arg or arg.startswith("-"):
                if arg in {"--pre", "--pre-glob", "--output"}:
                    return False
                if arg in {"--glob", "-g", "--pathspec-from-file"}:
                    skip_next = True
                continue
            cleaned = strip_quotes(arg)
            if cleaned in {".", "*"}:
                continue
            lowered = cleaned.lower()
            if lowered.startswith(("~", "$", "%userprofile%", "%home%")):
                return False
            if cleaned.startswith(("/", "\\")) and not cleaned.startswith(("./", ".\\", "../", "..\\")):
                return False
            if ".." in re.split(r"[\\/]+", cleaned):
                return False
            if looks_like_absolute_path(cleaned) and not is_path_within(Path(cleaned), workspace_root):
                return False
            if any(separator in cleaned for separator in ("/", "\\")):
                candidate = Path(cleaned)
                if not candidate.is_absolute():
                    candidate = workspace_root / cleaned
                if not is_path_within(candidate, workspace_root):
                    return False
        return True

    def _git_low_risk_reasons(self, args: list[str], workspace_root: Path) -> list[str]:
        if not args or "-c" in args or any(arg.startswith("--config") for arg in args):
            return []
        if args[0] == "--no-pager":
            args = args[1:]
        if not args:
            return []
        verb, rest = args[0], args[1:]
        if verb == "status" and all(arg in {"--short", "-s", "--porcelain", "--branch", "-b"} for arg in rest):
            return ["Read-only git status command."]
        if verb == "log" and self._git_log_args_are_low_risk(rest):
            return ["Read-only git log command."]
        if verb == "diff" and self._git_diff_args_are_low_risk(rest, workspace_root):
            return ["Read-only git diff command."]
        if verb == "show" and self._git_show_args_are_low_risk(rest, workspace_root):
            return ["Read-only git show stat command."]
        return []

    @staticmethod
    def _git_log_args_are_low_risk(args: list[str]) -> bool:
        allowed_flags = {"--oneline", "--decorate", "--no-decorate"}
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in allowed_flags:
                index += 1
                continue
            if arg == "-n" and index + 1 < len(args) and args[index + 1].isdigit():
                index += 2
                continue
            if re.fullmatch(r"-\d{1,3}", arg):
                index += 1
                continue
            return False
        return True

    def _git_diff_args_are_low_risk(self, args: list[str], workspace_root: Path) -> bool:
        if "--ext-diff" in args or "--cached" in args:
            return False
        if args == ["--stat"] or not args:
            return True
        if "--" in args:
            return self._args_stay_in_workspace(args[args.index("--") + 1 :], workspace_root)
        return all(arg in {"--stat", "--name-only", "--name-status"} for arg in args)

    def _git_show_args_are_low_risk(self, args: list[str], workspace_root: Path) -> bool:
        if "--stat" not in args:
            return False
        if any(arg == "--ext-diff" or arg.startswith("--output") for arg in args):
            return False
        allowed_flags = {"--stat", "--no-ext-diff"}
        if "--" in args:
            split_index = args.index("--")
            before_paths, path_args = args[:split_index], args[split_index + 1 :]
        else:
            before_paths, path_args = args, []
        for arg in before_paths:
            if arg in allowed_flags:
                continue
            if arg.startswith("-"):
                return False
            if any(separator in arg for separator in ("/", "\\")) and not self._args_stay_in_workspace(
                [arg], workspace_root
            ):
                return False
        return self._args_stay_in_workspace(path_args, workspace_root) if path_args else True

    def _create_approval(
        self,
        params: dict[str, Any],
        classification: dict[str, Any],
        agent_name: str,
        *,
        task_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        turn_id = str(params.get("turn_id") or params.get("turnId") or "").strip()
        timeout_seconds = self._timeout_seconds(params)
        environment_overrides = self._environment_overrides(params)
        if environment_overrides:
            self._raise("Unity-project shell approvals cannot persist environment override values.")
        if params.get("background") or params.get("pty") or "yieldMs" in params or "yield_ms" in params:
            self._raise(
                "Unity-project shell writes must run in the foreground until the approval transaction finishes."
            )
        if timeout_seconds == 0:
            self._raise("Unity-project shell writes require a finite timeout.")
        background = False
        pty = False
        yield_ms = 10_000
        project_root = str(classification.get("projectRoot") or "").strip()
        if not project_root:
            self._raise("A protected Unity project root is required for shell approval.")
        execution_binding_hash = stable_hash(
            json.dumps(
                self._command_execution_binding(
                    str(classification["command"]),
                    Path(str(classification["cwd"])),
                ),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        expected_binding = {
            "commandHash": classification["commandHash"],
            "cwdHash": stable_hash(classification["cwd"]),
            "workspaceRootHash": stable_hash(classification["workspaceRoot"]),
            "timeoutHash": stable_hash(str(timeout_seconds)),
            "projectRootHash": stable_hash(project_root),
            "executionOptionsHash": stable_hash(
                json.dumps(
                    {
                        "background": background,
                        "pty": pty,
                        "yieldMs": yield_ms,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
            "executionBindingHash": execution_binding_hash,
        }
        existing = self._ports.approvals.find_pending_shell(session_id, turn_id) if turn_id else None
        if existing is not None and (
            existing.get("targetTool") == "vrcforge_shell_execute"
            and existing.get("status") == "pending"
            and existing.get("sessionId") == session_id
            and existing.get("turnId") == turn_id
            and all(existing.get(key) == value for key, value in expected_binding.items())
            and (
                (
                    task_context is None
                    and not isinstance(existing.get("taskContext"), dict)
                )
                or (
                    isinstance(task_context, dict)
                    and isinstance(existing.get("taskContext"), dict)
                    and str(existing["taskContext"].get("taskId") or "")
                    == str(task_context.get("taskId") or "")
                )
            )
        ):
            return self._ports.approvals.redact(dict(existing))

        arguments = {
            "command": classification["command"],
            "command_hash": classification["commandHash"],
            "cwd_hash": stable_hash(classification["cwd"]),
            "workspace_root_hash": stable_hash(classification["workspaceRoot"]),
            "cwd": classification["cwd"],
            "workspace_root": classification["workspaceRoot"],
            "session_id": session_id,
            "turn_id": turn_id,
            "timeout_seconds": timeout_seconds,
            "timeout_hash": stable_hash(str(timeout_seconds)),
            "execution_options_hash": expected_binding["executionOptionsHash"],
            "execution_binding_hash": execution_binding_hash,
            "projectRoot": project_root,
            "project_root_hash": stable_hash(project_root),
            "classification_snapshot": classification,
        }
        manual_reason = (
            self.manual_approval_reason(classification)
            if self._ports.approvals.execution_mode() == "auto"
            else ""
        )
        approval = self._ports.approvals.create(
            ShellApprovalRequest(
                agent_name=agent_name,
                target_tool="vrcforge_shell_execute",
                arguments=arguments,
                reason=str(params.get("reason") or "High-risk shell command requires approval."),
                preview={
                    "command": classification["command"],
                    "cwd": classification["cwd"],
                    "workspaceRoot": classification["workspaceRoot"],
                    "projectRoot": project_root,
                    "riskReasons": classification["reasons"],
                },
                risk_level="high",
                user_constraints=self._ports.approvals.read_user_constraints(),
                requires_explicit_approval=bool(manual_reason),
                explicit_approval_reason=manual_reason,
                goal_delivery_id=str(
                    params.get("goalDeliveryId") or params.get("goal_delivery_id") or ""
                ).strip(),
                task_context=dict(task_context) if isinstance(task_context, dict) else None,
            )
        )
        self._ports.approvals.update_metadata(
            approval["id"],
            {
                "sessionId": session_id,
                "turnId": turn_id,
                **expected_binding,
            },
        )
        self._ports.append_audit(
            {
                "event": "shell_approval_requested",
                "agent": agent_name,
                "approvalId": approval["id"],
                "classification": classification,
            }
        )
        return approval

    def _run_command(
        self,
        command: str,
        cwd: Path,
        *,
        admission_id: int,
        timeout_seconds: int = 120,
        cancel_ids: list[str] | None = None,
        environment_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        process: ShellProcess | None = None
        communicated = False
        cleanup_complete = False
        try:
            started = self._process.monotonic()
            started_at = self._process.utc_now()
            env = build_shell_environment(
                self._process.environment(),
                environment_overrides,
            )
            env["GIT_PAGER"] = "cat"
            env["GIT_EXTERNAL_DIFF"] = ""
            native_argv = native_shell_argv(command)
            if native_argv is not None:
                runner = SHELL_RUNNER_NATIVE
                process_args = native_argv
            else:
                runner = SHELL_RUNNER_POWERSHELL
                process_args = [
                    resolve_powershell_executable(),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ]
            if cancel_ids and self._ports.cancellation_requested(
                cancel_ids[0] if len(cancel_ids) > 0 else "",
                cancel_ids[1] if len(cancel_ids) > 1 else "",
                cancel_ids[2] if len(cancel_ids) > 2 else "",
            ):
                self._raise("Shell execution was cancelled before start.", 409)
            creationflags = (
                subprocess.CREATE_NO_WINDOW
                if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            process = self._process.spawn(
                process_args,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
            accepting = self._register_process(admission_id, process)
            if not accepting:
                cleanup_complete = self._terminate_and_reap(process)
                self._raise("Shell execution is shutting down.", 503)
            timed_out = False
            cancelled = False
            deadline = (
                self._process.monotonic() + timeout_seconds
                if timeout_seconds > 0
                else None
            )
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=0.2)
                    communicated = True
                    break
                except subprocess.TimeoutExpired:
                    if cancel_ids and self._ports.cancellation_requested(
                        cancel_ids[0] if len(cancel_ids) > 0 else "",
                        cancel_ids[1] if len(cancel_ids) > 1 else "",
                        cancel_ids[2] if len(cancel_ids) > 2 else "",
                    ):
                        cancelled = True
                        self._process.terminate_tree(process)
                        stdout, stderr = process.communicate()
                        communicated = True
                        break
                    if deadline is not None and self._process.monotonic() >= deadline:
                        timed_out = True
                        self._process.terminate_tree(process)
                        stdout, stderr = process.communicate()
                        communicated = True
                        break
            duration = self._process.monotonic() - started
            exit_code = process.returncode if process.returncode is not None else -1
            return {
                "ok": exit_code == 0 and not timed_out and not cancelled,
                "command": command,
                "cwd": str(cwd),
                "runner": runner,
                "exitCode": exit_code,
                "timedOut": timed_out,
                "cancelled": cancelled,
                "startedAt": started_at,
                "finishedAt": self._process.utc_now(),
                "durationSeconds": round(duration, 3),
                "stdout": truncate_text(stdout),
                "stderr": truncate_text(stderr),
                "stdoutTruncated": len(stdout or "") > 12000,
                "stderrTruncated": len(stderr or "") > 12000,
            }
        finally:
            if process is not None:
                cleanup_complete = cleanup_complete or (
                    communicated and self._process_has_exited(process)
                )
                if not cleanup_complete:
                    cleanup_complete = self._terminate_and_reap(process)
            self._release_process(admission_id, process, cleanup_complete=cleanup_complete)

    @contextmanager
    def _execution_admission(self) -> Iterator[int]:
        thread_state = vars(self._thread_admission)
        existing_id = thread_state.get("admission_id")
        if existing_id is not None:
            self._thread_admission.depth += 1
            try:
                yield existing_id
            finally:
                self._thread_admission.depth -= 1
            return

        admission_id = self._admit_execution()
        self._thread_admission.admission_id = admission_id
        self._thread_admission.depth = 1
        try:
            yield admission_id
        finally:
            del self._thread_admission.admission_id
            del self._thread_admission.depth
            self._release_admission(admission_id)

    def _admit_execution(self) -> int:
        with self._lifecycle_lock:
            if not self._accepting:
                self._raise("Shell execution is shutting down.", 503)
            self._next_admission_id += 1
            admission_id = self._next_admission_id
            self._admitted_workers.add(admission_id)
            return admission_id

    def _register_process(self, admission_id: int, process: ShellProcess) -> bool:
        with self._lifecycle_lock:
            if admission_id not in self._admitted_workers:
                raise RuntimeError("Shell execution admission was released before process registration.")
            self._active_processes[process.pid] = process
            self._process_admissions[process.pid] = admission_id
            return self._accepting

    def _release_process(
        self,
        admission_id: int,
        process: ShellProcess | None,
        *,
        cleanup_complete: bool,
    ) -> None:
        with self._lifecycle_lock:
            if process is not None and cleanup_complete:
                if self._active_processes.get(process.pid) is process:
                    self._active_processes.pop(process.pid, None)
                    self._process_admissions.pop(process.pid, None)

    def _release_admission(self, admission_id: int) -> None:
        with self._lifecycle_lock:
            self._admitted_workers.remove(admission_id)

    @staticmethod
    def _process_has_exited(process: ShellProcess) -> bool:
        try:
            return process.poll() is not None
        except BaseException:
            return False

    def _terminate_and_reap(self, process: ShellProcess) -> bool:
        if not self._process_has_exited(process):
            try:
                self._process.terminate_tree(process)
            except BaseException:
                pass
        if not self._process_has_exited(process):
            try:
                process.kill()
            except BaseException:
                pass
        communicated = False
        try:
            process.communicate()
            communicated = True
        except BaseException:
            if not self._process_has_exited(process):
                try:
                    process.kill()
                except BaseException:
                    pass
            try:
                process.communicate()
                communicated = True
            except BaseException:
                pass
        return communicated and self._process_has_exited(process)


def resolve_powershell_executable() -> str:
    global _POWERSHELL_EXECUTABLE_CACHE
    if _POWERSHELL_EXECUTABLE_CACHE:
        return _POWERSHELL_EXECUTABLE_CACHE
    candidates: list[str] = []
    pwsh_path = shutil.which("pwsh")
    if pwsh_path:
        candidates.append(pwsh_path)
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        candidates.append(
            str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
        )
    powershell_path = shutil.which("powershell")
    if powershell_path:
        candidates.append(powershell_path)
    for candidate in candidates:
        try:
            if candidate and Path(candidate).is_file():
                _POWERSHELL_EXECUTABLE_CACHE = candidate
                return candidate
        except OSError:
            continue
    _POWERSHELL_EXECUTABLE_CACHE = "powershell"
    return _POWERSHELL_EXECUTABLE_CACHE


def native_shell_argv(command: str) -> list[str] | None:
    if "\n" in command or "\r" in command or SHELL_NATIVE_BLOCK_PATTERN.search(command):
        return None
    tokens = tokenize_command(command)
    if not tokens:
        return None
    argv = [strip_quotes(token) for token in tokens]
    if any('"' in arg or "'" in arg for arg in argv):
        return None
    command_name = argv[0]
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", command_name):
        return None
    executable = shutil.which(command_name)
    if not executable:
        return None
    if os.name == "nt" and not executable.lower().endswith(".exe"):
        return None
    argv[0] = executable
    return argv


def kill_process_tree(process: ShellProcess) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
                check=False,
            )
        except OSError:
            pass
        if process.poll() is None:
            process.kill()
        return
    process.kill()


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", errors="replace")).hexdigest()


def stable_hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()


def truncate_text(text: str, limit: int = 12000) -> str:
    if len(text or "") <= limit:
        return text or ""
    return (text or "")[:limit] + "\n[truncated]"


def summarize_shell_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": result.get("ok"),
        "runner": result.get("runner"),
        "exitCode": result.get("exitCode"),
        "timedOut": result.get("timedOut"),
        "durationSeconds": result.get("durationSeconds"),
        "sessionId": result.get("sessionId"),
        "stdoutSummary": _summarize_text(str(result.get("stdout") or "")),
        "stderrSummary": _summarize_text(str(result.get("stderr") or "")),
    }


def normalize_execution_mode(value: Any) -> str:
    mode = str(value or "approval").strip().lower().replace("-", "_")
    if mode in {
        "roslyn_full_auto",
        "full_auto",
        "roslyn_auto",
        "advanced",
        "full",
        "full_permission",
    }:
        return "roslyn_full_auto"
    if mode in {"auto", "auto_approve", "auto_approval", "autoapprove"}:
        return "auto"
    return "approval"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cancel_ids(params: dict[str, Any]) -> list[str]:
    return [
        str(params.get("session_id") or params.get("sessionId") or ""),
        str(params.get("turn_id") or params.get("turnId") or ""),
        str(params.get("client_turn_id") or params.get("clientTurnId") or ""),
    ]


def _ensure_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _summarize_text(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
