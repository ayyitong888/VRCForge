"""Typed Shell policy, approval binding, and supervised process lifecycle."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
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


SHELL_RUNNER_NATIVE = "native-win-process"
SHELL_RUNNER_POWERSHELL = "powershell-fallback"
SHELL_NATIVE_BLOCK_PATTERN = re.compile(r"[|;&<>^`$%(){}\[\]#]|@\"|@'")
AUTO_APPROVAL_MANUAL_SHELL_COMMANDS = {
    "del",
    "erase",
    "rd",
    "ri",
    "rm",
    "rmdir",
    "remove-item",
}
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
    error_factory: Callable[[str, int], Exception] = lambda detail, status: AgentShellError(
        detail,
        status,
    )


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
    ) -> None:
        self._ports = ports
        self._process = process_ports or default_shell_process_ports()
        self._lifecycle_lock = threading.RLock()
        self._active_processes: dict[int, ShellProcess] = {}
        self._accepting = True

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

    def shutdown(self, *, grace_seconds: float = 5.0) -> ShellShutdownReport:
        with self._lifecycle_lock:
            self._accepting = False
            snapshot = tuple(self._active_processes.values())
        terminated = 0
        for process in snapshot:
            if process.poll() is not None:
                continue
            self._process.terminate_tree(process)
            terminated += 1
        deadline = self._process.monotonic() + max(0.0, min(float(grace_seconds), 30.0))
        while self._process.monotonic() < deadline:
            with self._lifecycle_lock:
                pending = [process for process in snapshot if process.pid in self._active_processes]
            if not pending:
                break
            self._process.sleep(0.01)
        with self._lifecycle_lock:
            pending_count = sum(process.pid in self._active_processes for process in snapshot)
        return ShellShutdownReport(
            snapshot_count=len(snapshot),
            terminated_count=terminated,
            pending_count=pending_count,
        )

    def classify(self, params: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(params, str):
            params = {"command": params}
        command = str(params.get("command") or "").strip()
        workspace_root = self._resolve_workspace_root(params)
        cwd = self._resolve_cwd(params, workspace_root)
        reasons: list[str] = []

        if not command:
            return self._classification(command, cwd, workspace_root, "reject", ["Command is empty."])
        if len(command) > 4000:
            return self._classification(command, cwd, workspace_root, "reject", ["Command is too long."])
        if not is_path_within(cwd, workspace_root):
            reasons.append("cwd is outside the workspace root.")

        lowered = command.lower()
        if "\n" in command or "\r" in command:
            reasons.append("Command contains multiple lines.")
        if re.search(r"&&|\|\||[;|]|(?:^|\s)(?:\d?>|\*>|>>)", command):
            reasons.append("Command contains chaining, pipeline, or redirection syntax.")
        if "$(" in command or "{" in command or "}" in command or '@"' in command or "@'" in command:
            reasons.append("Command contains advanced PowerShell syntax.")
        if re.search(r"(^|\s|['\"])(?:\\\\|[a-zA-Z]:\\)", command):
            outside_paths = [
                token
                for token in tokenize_command(command)
                if looks_like_absolute_path(strip_quotes(token))
                and not is_path_within(Path(strip_quotes(token)), workspace_root)
            ]
            if outside_paths:
                reasons.append("Command references an absolute path outside the workspace root.")
        if ".." in [
            part
            for token in tokenize_command(command)
            for part in re.split(r"[\\/]+", strip_quotes(token))
        ]:
            reasons.append("Command contains parent path traversal.")
        if re.search(r"\.(ps1|bat|cmd|exe)(?:\s|$)", lowered):
            reasons.append("Command executes a script or executable directly.")

        tokens = tokenize_command(command)
        if not tokens:
            return self._classification(
                command,
                cwd,
                workspace_root,
                "reject",
                ["Command could not be parsed."],
            )
        if reasons:
            return self._classification(command, cwd, workspace_root, "high", reasons)

        command_name = strip_quotes(tokens[0]).lower()
        args = [strip_quotes(token) for token in tokens[1:]]
        low_reasons = self._low_risk_reasons(command_name, args, workspace_root)
        if low_reasons:
            return self._classification(command, cwd, workspace_root, "low", low_reasons)
        return self._classification(
            command,
            cwd,
            workspace_root,
            "high",
            ["Command is not in the low-risk allowlist."],
        )

    def execute(
        self,
        params: dict[str, Any],
        agent_name: str = "desktop-agent",
    ) -> dict[str, Any]:
        self._require_accepting()
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
            approval = self._create_approval(params, classification, agent_name)
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

        result = self._run_command(
            command,
            Path(classification["cwd"]),
            timeout_seconds=int(params.get("timeout_seconds") or 120),
            cancel_ids=_cancel_ids(params),
        )
        self._ports.append_audit(
            {
                "event": "shell_executed",
                "agent": agent_name,
                "classification": classification,
                "result": summarize_shell_result(result),
                **self._ports.permission_audit_context(),
            }
        )
        return {
            "ok": result["ok"],
            "status": "executed",
            "classification": classification,
            "result": result,
        }

    def execute_approved(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_accepting()
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
        self._require_accepting()
        command = str(params.get("command") or "").strip()
        expected_hash = str(params.get("command_hash") or params.get("commandHash") or "")
        if not expected_hash:
            self._raise("Stored shell approval command hash is required.")
        if expected_hash != command_hash(command):
            self._raise("Stored shell approval command hash does not match.")
        workspace_root = self._resolve_workspace_root(params)
        cwd = self._resolve_cwd(params, workspace_root)
        timeout_seconds = int(params.get("timeout_seconds") or params.get("timeoutSeconds") or 120)
        expected_cwd_hash = str(params.get("cwd_hash") or params.get("cwdHash") or "")
        expected_workspace_hash = str(
            params.get("workspace_root_hash") or params.get("workspaceRootHash") or ""
        )
        expected_timeout_hash = str(params.get("timeout_hash") or params.get("timeoutHash") or "")
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

        classification = self.classify(
            {"command": command, "cwd": str(cwd), "workspace_root": str(workspace_root)}
        )
        if classification.get("risk") == "reject":
            self._raise(
                "Approved shell command is no longer executable: "
                + "; ".join(classification.get("reasons") or [])
            )
        if classification.get("commandHash") != expected_hash:
            self._raise("Reclassified shell command hash does not match approval.")

        result = self._run_command(
            command,
            cwd,
            timeout_seconds=timeout_seconds,
            cancel_ids=_cancel_ids(params),
        )
        self._ports.append_audit(
            {
                "event": "shell_approved_executed",
                "sessionId": params.get("session_id") or params.get("sessionId") or "",
                "turnId": params.get("turn_id") or params.get("turnId") or "",
                "commandHash": command_hash(command),
                "cwdHash": stable_hash(str(cwd)),
                "workspaceRootHash": stable_hash(str(workspace_root)),
                "timeoutHash": stable_hash(str(timeout_seconds)),
                "cwd": str(cwd),
                "workspaceRoot": str(workspace_root),
                "result": summarize_shell_result(result),
                **self._ports.permission_audit_context(),
            }
        )
        return result

    def _require_accepting(self) -> None:
        with self._lifecycle_lock:
            if not self._accepting:
                self._raise("Shell execution is shutting down.", 503)

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
    ) -> dict[str, Any]:
        return {
            "ok": risk != "reject",
            "command": command,
            "commandHash": command_hash(command),
            "risk": risk,
            "reasons": reasons,
            "cwd": str(cwd),
            "workspaceRoot": str(workspace_root),
            "readOnly": self._command_is_read_only(command),
            "plannedRunner": (
                SHELL_RUNNER_NATIVE if native_shell_argv(command) is not None else SHELL_RUNNER_POWERSHELL
            ),
        }

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

    def _manual_approval_reason(self, classification: dict[str, Any]) -> str:
        command = str(classification.get("command") or "")
        tokens = [strip_quotes(token).lower() for token in tokenize_command(command)]
        if any(token in AUTO_APPROVAL_MANUAL_SHELL_COMMANDS for token in tokens):
            return "Delete/removal shell commands require manual approval in Auto Approve mode."
        reasons = " ".join(str(reason or "").lower() for reason in _ensure_list(classification.get("reasons")))
        if "outside the workspace root" in reasons or "parent path traversal" in reasons:
            return "Shell commands that reference paths outside the workspace require manual approval in Auto Approve mode."
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
    ) -> dict[str, Any]:
        session_id = str(params.get("session_id") or params.get("sessionId") or "").strip()
        turn_id = str(params.get("turn_id") or params.get("turnId") or "").strip()
        existing = self._ports.approvals.find_pending_shell(session_id, turn_id) if turn_id else None
        if existing is not None and (
            existing.get("targetTool") == "vrcforge_shell_execute"
            and existing.get("status") == "pending"
            and existing.get("sessionId") == session_id
            and existing.get("turnId") == turn_id
        ):
            return self._ports.approvals.redact(dict(existing))

        timeout_seconds = int(params.get("timeout_seconds") or 120)
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
            "classification_snapshot": classification,
        }
        manual_reason = ""
        if normalize_execution_mode(self._ports.approvals.execution_mode()) == "auto":
            manual_reason = self._manual_approval_reason(classification)
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
                    "riskReasons": classification["reasons"],
                },
                risk_level="high",
                user_constraints=self._ports.approvals.read_user_constraints(),
                requires_explicit_approval=bool(manual_reason),
                explicit_approval_reason=manual_reason,
                goal_delivery_id=str(
                    params.get("goalDeliveryId") or params.get("goal_delivery_id") or ""
                ).strip(),
            )
        )
        self._ports.approvals.update_metadata(
            approval["id"],
            {
                "sessionId": session_id,
                "turnId": turn_id,
                "commandHash": classification["commandHash"],
                "cwdHash": stable_hash(classification["cwd"]),
                "workspaceRootHash": stable_hash(classification["workspaceRoot"]),
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
        timeout_seconds: int = 120,
        cancel_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self._require_accepting()
        started = self._process.monotonic()
        started_at = self._process.utc_now()
        env = self._process.environment()
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
        with self._lifecycle_lock:
            self._active_processes[process.pid] = process
            accepting = self._accepting
        if not accepting:
            cleanup_complete = self._terminate_and_reap(process)
            with self._lifecycle_lock:
                if cleanup_complete:
                    self._active_processes.pop(process.pid, None)
            self._raise("Shell execution is shutting down.", 503)
        communicated = False
        try:
            timed_out = False
            cancelled = False
            deadline = self._process.monotonic() + max(1, min(timeout_seconds, 600))
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
                    if self._process.monotonic() >= deadline:
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
            cleanup_complete = communicated and self._process_has_exited(process)
            if not cleanup_complete:
                cleanup_complete = self._terminate_and_reap(process)
            with self._lifecycle_lock:
                if cleanup_complete:
                    self._active_processes.pop(process.pid, None)

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
