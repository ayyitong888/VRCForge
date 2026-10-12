"""Submit guard for durable runtime runs after a backend restart."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

_UNSET = object()


@dataclass(frozen=True)
class RecoveryDecision:
    allowed: bool
    status: str
    reason: str
    run: dict[str, Any] | None = None


class ChannelRuntimeRecovery:
    """Read the existing gateway runtime run ledger; never replays or creates runs."""

    def __init__(self, runtime_runs: Any, *, read_timeout: float = 2.0) -> None:
        self._runtime_runs = runtime_runs
        self._read_timeout = read_timeout

    async def check(self, session_id: str, client_turn_id: str, project_root: str | None = None) -> RecoveryDecision:
        session = str(session_id or "").strip()
        client = str(client_turn_id or "").strip()
        if not session or not client:
            return RecoveryDecision(False, "unknown", "identity_missing")
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._list_runs, session, client, str(project_root or "").strip()),
                timeout=self._read_timeout,
            )
        except asyncio.TimeoutError:
            return RecoveryDecision(False, "unknown", "ledger_timeout")
        except Exception:
            return RecoveryDecision(False, "unknown", "ledger_unavailable")
        runs = result.get("runs") if isinstance(result, dict) else None
        if not isinstance(runs, list):
            return RecoveryDecision(False, "unknown", "ledger_schema_invalid")
        scope = None if project_root is None else str(project_root).strip()
        matching = [item for item in runs if isinstance(item, dict)
                    and self._scope_matches(item, session, scope)
                    and str(item.get("clientTurnId") or "") == client]
        run = matching[0] if matching else None
        if run is not None:
            status = str(run.get("status") or "").strip().lower().replace("-", "_")
            if self._is_project_selection_run(run):
                return RecoveryDecision(False, "terminal", "client_turn_already_recorded", dict(run))
            if status in {"completed", "success", "succeeded", "finished", "failed", "error", "cancelled", "canceled", "denied", "rejected"}:
                return RecoveryDecision(False, "terminal", "client_turn_already_recorded", dict(run))
            if status in {"queued", "running", "active", "started", "blocked", "pending", "waiting_for_approval", "pending_approval"}:
                return RecoveryDecision(False, "active", "existing_run_active", dict(run))
            return RecoveryDecision(False, "unknown", "existing_run_status_unknown", dict(run))
        if not runs:
            return RecoveryDecision(True, "absent", "no_existing_run")
        scoped_runs = [item for item in runs if isinstance(item, dict)
                       and self._scope_matches(item, session, scope)]
        for candidate in scoped_runs:
            # A blocked project chooser is a user-action checkpoint, not an
            # active model/tool run. It must not strand the general chat.
            if self._is_project_selection_run(candidate):
                continue
            status = str(candidate.get("status") or "").strip().lower().replace("-", "_")
            if status in {"queued", "running", "active", "started", "blocked", "pending", "waiting_for_approval", "pending_approval"}:
                return RecoveryDecision(False, "active", "other_run_active", dict(candidate))
            if status not in {"completed", "success", "succeeded", "finished", "failed", "error", "cancelled", "canceled", "denied", "rejected"}:
                return RecoveryDecision(False, "unknown", "other_run_status_unknown", dict(candidate))
        return RecoveryDecision(True, "absent", "no_existing_run")

    async def control(self, session_id: str, client_turn_id: str, project_root: str | None = None) -> RecoveryDecision:
        """Return a narrowly live run that may receive control commands.

        Waiting-for-approval and other blocked states remain recovery-only: they
        are deliberately not advertised as steer/cancel targets.
        """
        if not str(client_turn_id or "").strip():
            session = str(session_id or "").strip()
            project = None if project_root is None else str(project_root).strip()
            if not session:
                return RecoveryDecision(False, "unknown", "identity_missing")
            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._list_runs, session, "", project), timeout=self._read_timeout
                )
            except asyncio.TimeoutError:
                return RecoveryDecision(False, "unknown", "ledger_timeout")
            except Exception:
                return RecoveryDecision(False, "unknown", "ledger_unavailable")
            runs = result.get("runs") if isinstance(result, dict) else None
            if not isinstance(runs, list):
                return RecoveryDecision(False, "unknown", "ledger_schema_invalid")
            candidates = [item for item in runs if isinstance(item, dict)
                          and self._scope_matches(item, session, project)]
            live = [item for item in candidates if str(item.get("status") or "").strip().lower().replace("-", "_") in {"running", "active", "started"}]
            if len(live) != 1:
                return RecoveryDecision(False, "unknown", "active_run_not_unique")
            return RecoveryDecision(True, "control_ready", "live_external_run", dict(live[0]))
        decision = await self.check(session_id, client_turn_id, project_root)
        run = decision.run
        if not run or decision.status != "active":
            return decision
        run_session = str(run.get("sessionId") or run.get("session_id") or "").strip()
        run_project = self._run_project(run)
        requested_project = None if project_root is None else str(project_root).strip()
        if run_session not in {None, str(session_id or "").strip()} or (requested_project is not None and run_project != requested_project):
            return RecoveryDecision(False, "unknown", "run_scope_mismatch", dict(run))
        status = str(run.get("status") or "").strip().lower().replace("-", "_")
        if status not in {"running", "active", "started"}:
            return RecoveryDecision(False, "blocked", "run_not_live_control_state", dict(run))
        if str(run.get("clientTurnId") or "").strip() != str(client_turn_id or "").strip():
            return RecoveryDecision(False, "unknown", "client_turn_mismatch", dict(run))
        return RecoveryDecision(True, "control_ready", "live_external_run", dict(run))

    async def completed_continuations(self, session_id: str, project_root: str | None) -> list[dict[str, Any]]:
        """Read terminal continuation projections without executing anything."""
        session = str(session_id or "").strip()
        project = str(project_root or "").strip()
        if not session or project_root is None:
            return []
        try:
            continuations = await asyncio.wait_for(
                asyncio.to_thread(self._list_continuations), timeout=self._read_timeout
            )
            runs_result = await asyncio.wait_for(
                asyncio.to_thread(self._list_runs, session, "", project), timeout=self._read_timeout
            )
        except Exception:
            return []
        if not isinstance(continuations, list) or not isinstance(runs_result, dict):
            return []
        runs = runs_result.get("runs")
        if not isinstance(runs, list):
            return []
        terminal = {"completed", "failed", "denied", "cancelled"}
        allowed: dict[tuple[str, str], dict[str, Any]] = {}
        for run in runs:
            if not isinstance(run, dict):
                continue
            if self._run_session(run) != session:
                continue
            run_project = self._run_project(run)
            if run_project != project:
                continue
            if str(run.get("status") or "").strip().lower().replace("-", "_") not in terminal:
                continue
            continuation = run.get("continuationEvent")
            if not isinstance(continuation, dict):
                continue
            turn = str(run.get("turnId") or "").strip()
            client = str(run.get("clientTurnId") or "").strip()
            if turn and client:
                allowed[(turn, client)] = continuation
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for continuation in continuations:
            if not isinstance(continuation, dict):
                continue
            if str(continuation.get("sessionId") or "").strip() != session:
                continue
            turn = str(continuation.get("turnId") or "").strip()
            client = str(continuation.get("clientTurnId") or "").strip()
            if not turn or not client or (turn, client) in seen:
                continue
            candidate = allowed.get((turn, client))
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("sessionId") or "").strip() != session:
                continue
            if str(candidate.get("turnId") or "").strip() != turn or str(candidate.get("clientTurnId") or "").strip() != client:
                continue
            seen.add((turn, client))
            result.append(dict(continuation))
            if len(result) >= 32:
                break
        return result

    @staticmethod
    def _run_session(run: dict[str, Any]) -> str:
        if "sessionId" not in run and "session_id" not in run:
            return None
        return str(run.get("sessionId") if "sessionId" in run else run.get("session_id") or "").strip()

    @staticmethod
    def _run_project(run: dict[str, Any]) -> str | None:
        if "projectRoot" in run:
            return str(run.get("projectRoot") or "").strip()
        if "projectPath" in run:
            return str(run.get("projectPath") or "").strip()
        return None

    @classmethod
    def _scope_matches(cls, run: dict[str, Any], session: str, project: str | None) -> bool:
        run_session = cls._run_session(run)
        run_project = cls._run_project(run)
        session_match = run_session in {None, session}
        project_match = project is None or run_project == project or (run_project is None and project != "")
        return session_match and project_match

    @staticmethod
    def _is_project_selection_run(run: dict[str, Any]) -> bool:
        status = str(run.get("status") or "").strip().lower().replace("-", "_")
        next_step = str(run.get("nextStep") or run.get("next_step") or "").strip().lower().replace("-", "_")
        if status != "blocked" or next_step != "needs_user_action":
            return False
        if str(run.get("skillTool") or run.get("skill_tool") or "") != "vrcforge_request_project_selection":
            return False
        if str(run.get("skillStatus") or run.get("skill_status") or "") != "needs_user_action":
            return False
        if run.get("approvalIds") not in (None, []):
            return False
        steps = run.get("steps")
        if not isinstance(steps, list) or not steps or not isinstance(steps[-1], dict):
            return False
        last = steps[-1]
        if str(last.get("tool") or "") != "vrcforge_request_project_selection":
            return False
        if str(last.get("status") or "") != "needs_user_action":
            return False
        outcome = last.get("outcome")
        result = last.get("result")
        if isinstance(outcome, dict) and str(outcome.get("status") or "") != "needs_user_action":
            return False
        if not isinstance(result, dict) or result.get("projectSelectionRequested") is not True:
            return False
        return True

    def _list_runs(self, session_id: str, client_turn_id: str, project_root: str) -> Any:
        method: Callable[..., Any] = self._runtime_runs.list_runs if hasattr(self._runtime_runs, "list_runs") else self._runtime_runs
        # A bounded, session-scoped read. Truncation remains conservative because
        # any active/unknown entry in the returned window blocks a new submit.
        return method(session_id=session_id, project_root=project_root, limit=100)

    def _list_continuations(self) -> Any:
        method = getattr(self._runtime_runs, "list_runtime_continuations", None)
        if not callable(method):
            return []
        return method(limit=100)
