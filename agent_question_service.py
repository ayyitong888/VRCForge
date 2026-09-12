"""Durable, scope-bound questions between an Agent run and its user.

The service owns only the Question JSONL projection.  It opens no listener,
process, or external connection.  Its one app-lifetime file/lock dependency,
scope rules, and optional Goal continuation are supplied as narrow typed ports
by the composition root.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol


AGENT_QUESTION_SCHEMA = "vrcforge.agent_question.v1"
AGENT_QUESTION_MAX_ITEMS = 60


class AgentQuestionServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class LockPort(Protocol):
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...

    def release(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AgentQuestionPersistencePorts:
    """The one app-owned JSONL target and existing durable-state lock."""

    log_path: Callable[[], Path]
    shared_state_lock: LockPort
    redact: Callable[[object], object]


@dataclass(frozen=True, slots=True)
class AgentQuestionScopePorts:
    """Existing scope and privacy rules, kept out of persistence mechanics."""

    normalize_path: Callable[[str], str]
    summarize: Callable[[str, int], str]
    redact_goal_persistence: Callable[[object], object]


@dataclass(frozen=True, slots=True)
class GoalQuestionResolutionPort:
    """Resolve an answered Question into an already-owned Goal delivery."""

    resolve: Callable[[str, str], Mapping[str, object] | None]


@dataclass(frozen=True, slots=True)
class RuntimeQuestionResolutionPort:
    """Return an answered foreground Question to its owning runtime task."""

    resolve: Callable[[str, Mapping[str, object], str], Mapping[str, object] | None]
    interrupt: Callable[[str, Mapping[str, object], str], object] | None = None


class AgentQuestionPersistence:
    """Append/read one app-owned Question JSONL without broad host access."""

    def __init__(self, ports: AgentQuestionPersistencePorts) -> None:
        self._ports = ports

    @property
    def log_path(self) -> Path:
        return Path(self._ports.log_path())

    @property
    def shared_state_lock(self) -> LockPort:
        return self._ports.shared_state_lock

    def redact(self, value: object) -> object:
        return self._ports.redact(value)

    def append(self, event: Mapping[str, object]) -> dict[str, object]:
        row = self._ports.redact(
            {
                "schema": AGENT_QUESTION_SCHEMA,
                "id": f"evt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}",
                "createdAt": _utc_now_iso(),
                "updatedAt": _utc_now_iso(),
                **dict(event),
            }
        )
        if not isinstance(row, dict):
            raise OSError("Question storage redaction did not return an object.")
        with _locked(self._ports.shared_state_lock):
            path = self.log_path
            path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_append_boundary_locked(path)
            with path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                log_file.flush()
                os.fsync(log_file.fileno())
        return row

    def read(self) -> list[dict[str, object]]:
        with _locked(self._ports.shared_state_lock):
            path = self.log_path
            if not path.exists():
                return []
            try:
                lines = path.read_bytes().splitlines()
            except OSError:
                return []
        events: list[dict[str, object]] = []
        for raw_line in lines:
            try:
                decoded = raw_line.decode("utf-8")
                payload = json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    @staticmethod
    def _ensure_append_boundary_locked(path: Path) -> None:
        try:
            if not path.exists() or path.stat().st_size == 0:
                return
            with path.open("r+b") as handle:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) not in {b"\n", b"\r"}:
                    handle.seek(0, os.SEEK_END)
                    handle.write(b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError:
            # The authoritative append immediately after this helper must
            # surface its own failure rather than losing that error here.
            return


class AgentQuestionService:
    """Create, answer, list and project Questions with exact scope checks."""

    def __init__(
        self,
        persistence: AgentQuestionPersistence,
        scope: AgentQuestionScopePorts,
        goal_resolution: GoalQuestionResolutionPort,
        runtime_resolution: RuntimeQuestionResolutionPort | None = None,
    ) -> None:
        self._persistence = persistence
        self._scope = scope
        self._goal_resolution = goal_resolution
        self._runtime_resolution = runtime_resolution

    @property
    def log_path(self) -> Path:
        return self._persistence.log_path

    def create(self, params: Mapping[str, object] | None = None) -> dict[str, object]:
        values = dict(params or {})
        question = self._summarize(values.get("question") or values.get("prompt"), 1000)
        if not question:
            raise AgentQuestionServiceError("Question is required.", status_code=400)
        raw_options = _as_list(values.get("options") or values.get("choices"))
        options: list[dict[str, str]] = []
        for index, option in enumerate(raw_options):
            if isinstance(option, str):
                label = self._summarize(option, 160)
                value = label
                description = ""
                option_id = f"option-{index + 1}"
            elif isinstance(option, Mapping):
                label = self._summarize(option.get("label") or option.get("value"), 160)
                value = self._summarize(option.get("value") or label, 500)
                description = self._summarize(option.get("description"), 500)
                option_id = self._summarize(option.get("id") or f"option-{index + 1}", 120)
            else:
                continue
            if label:
                options.append({"id": option_id, "label": label, "value": value, "description": description})
        if len(options) == 1:
            raise AgentQuestionServiceError("Question choices require at least two options.", status_code=400)
        question_id = f"question_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        event: dict[str, object] = {
            "event": "question_created",
            "status": "pending",
            "questionId": question_id,
            "header": self._summarize(values.get("header"), 120),
            "question": question,
            "options": options,
            "projectRoot": self._scope_text(values, "projectRoot", "project_root", "projectPath"),
            "sessionId": self._scope_text(values, "sessionId", "session_id"),
            "owner": self._summarize(values.get("owner") or "agent", 80),
            "goalDeliveryId": str(values.get("goalDeliveryId") or values.get("goal_delivery_id") or "").strip(),
        }
        runtime_seed = values.get("_runtimeTaskSeed")
        if isinstance(runtime_seed, Mapping) and runtime_seed:
            event["runtimeTaskSeed"] = dict(runtime_seed)
            event["runtimeSessionId"] = str(values.get("_runtimeSessionId") or values.get("sessionId") or "").strip()
            event["runtimeClientTurnId"] = str(values.get("_runtimeClientTurnId") or "").strip()
            event["runtimeTaskId"] = str(runtime_seed.get("taskId") or "").strip()
        with _locked(self._persistence.shared_state_lock):
            self._persistence.append(event)
            created = self._project(include_answered=True).get(question_id)
        if created is None:
            raise OSError("Question storage did not preserve the new Question.")
        payload: dict[str, object] = {"ok": True, "question": self._redact(created)}
        if (isinstance(runtime_seed, Mapping) and runtime_seed) or event["goalDeliveryId"]:
            payload.update(
                {
                    "status": "needs_user_action",
                    "outcome": {
                        "status": "needs_user_action",
                        "summary": self._summarize(question, 600),
                        "questionId": question_id,
                    },
                }
            )
        return payload

    def answer(self, question_id: str, params: Mapping[str, object] | None = None) -> dict[str, object]:
        values = dict(params or {})
        resolved_id = str(question_id or "").strip()
        if not resolved_id:
            raise AgentQuestionServiceError("questionId is required.", status_code=400)
        with _locked(self._persistence.shared_state_lock):
            current = self._project(include_answered=True)
            existing = current.get(resolved_id)
            if existing is None:
                raise AgentQuestionServiceError(f"Question was not found: {resolved_id}", status_code=404)
            self._require_scope(existing, values)
            if existing.get("status") == "cancelled":
                raise AgentQuestionServiceError("The task that asked this Question was stopped.", status_code=409)
            goal_delivery_id = str(existing.get("goalDeliveryId") or "").strip()
            runtime_seed = existing.get("runtimeTaskSeed")
            if str(existing.get("status") or "") == "answered":
                payload: dict[str, object] = {"ok": True, "question": self._redact(existing), "idempotent": True}
            else:
                selected_option_id = self._summarize(
                    values.get("selectedOptionId") or values.get("optionId"),
                    120,
                )
                answer_text = self._summarize(values.get("answer") or values.get("value"), 2000)
                if not answer_text and selected_option_id:
                    for option in _as_list(existing.get("options")):
                        if not isinstance(option, Mapping) or str(option.get("id") or "") != selected_option_id:
                            continue
                        answer_text = self._summarize(option.get("value") or option.get("label"), 1000)
                        break
                if not answer_text:
                    raise AgentQuestionServiceError("An answer is required.", status_code=400)
                safe_answer = self._scope.redact_goal_persistence(answer_text)
                event = {
                    "event": "question_answered",
                    "status": "answered",
                    "questionId": resolved_id,
                    "answer": str(safe_answer or ""),
                    "selectedOptionId": selected_option_id,
                    "projectRoot": str(values.get("projectRoot") or existing.get("projectRoot") or ""),
                    "sessionId": str(values.get("sessionId") or existing.get("sessionId") or ""),
                }
                if runtime_seed and not goal_delivery_id:
                    # The saved answer and the not-yet-dispatched marker are
                    # one durable event, so a restart cannot lose the handoff.
                    event["runtimeContinuationStatus"] = "queued"
                self._persistence.append(event)
                answered = self._project(include_answered=True).get(resolved_id)
                if answered is None:
                    raise OSError("Question storage did not preserve the answer.")
                payload = {"ok": True, "question": self._redact(answered)}
        if goal_delivery_id:
            payload["goalDelivery"] = self._goal_resolution.resolve(
                resolved_id,
                self._continuation_prompt(payload["question"]),
            )
        if (runtime_seed and not goal_delivery_id and self._runtime_resolution is not None
                and (not payload.get("idempotent") or existing.get("runtimeContinuationStatus") == "queued")):
            payload["runtimeContinuation"] = self._runtime_resolution.resolve(
                resolved_id,
                runtime_seed if isinstance(runtime_seed, Mapping) else {},
                self._continuation_prompt(payload["question"]),
            )
        return payload

    def record_runtime_continuation(self, question_id: str, status: str, **fields: object) -> None:
        """Persist the lifecycle marker used by the app-owned dispatcher."""
        self._persistence.append({
            "event": "question_runtime_continuation_" + str(status or "unknown"),
            "runtimeContinuationStatus": str(status or "unknown")[:40],
            "questionId": str(question_id or "").strip(),
            **{str(key): value for key, value in fields.items()},
        })

    def claim_runtime_continuation(self, question_id: str) -> bool:
        """Only an unanswered dispatch claim can cross into the runtime."""
        with _locked(self._persistence.shared_state_lock):
            question = self._project(include_answered=True).get(question_id, {})
            if question.get("status") != "answered" or question.get("runtimeContinuationStatus") != "queued":
                return False
            self.record_runtime_continuation(question_id, "claimed")
            return True

    def cancel_runtime_questions(self, *, session_id: str = "", turn_id: str = "", client_turn_id: str = "") -> None:
        """Persist cancellation of only the stopped turn's pending questions."""
        with _locked(self._persistence.shared_state_lock):
            for question_id, question in self._project(include_answered=True).items():
                seed = question.get("runtimeTaskSeed")
                if not isinstance(seed, Mapping) or not any((session_id, turn_id, client_turn_id)):
                    continue
                if session_id and seed.get("sessionId") != session_id:
                    continue
                if turn_id and seed.get("turnId") != turn_id:
                    continue
                if client_turn_id and seed.get("clientTurnId") != client_turn_id:
                    continue
                if question.get("status") == "pending" or question.get("runtimeContinuationStatus") == "queued":
                    self._persistence.append({"event": "question_cancelled", "questionId": question_id, "status": "cancelled", "runtimeContinuationStatus": "interrupted"})

    def reconcile_runtime_continuations(self) -> dict[str, int]:
        """Resume unclaimed answers; never replay an ambiguous claimed task."""
        queued: list[dict[str, object]] = []
        interrupted: list[dict[str, object]] = []
        with _locked(self._persistence.shared_state_lock):
            for question_id, question in self._project(include_answered=True).items():
                state = question.get("runtimeContinuationStatus")
                if state == "claimed":
                    self.record_runtime_continuation(question_id, "interrupted", error="App restarted after dispatch began; inspect the task before continuing.")
                    interrupted.append(question)
                elif state == "queued":
                    queued.append(question)
        if self._runtime_resolution is not None:
            if self._runtime_resolution.interrupt is not None:
                for question in interrupted:
                    self._runtime_resolution.interrupt(
                        str(question["questionId"]), dict(question.get("runtimeTaskSeed") or {}),
                        "Your answer was saved. VRCForge restarted after continuation began; inspect the current task before continuing.",
                    )
            for question in queued:
                self._runtime_resolution.resolve(
                    str(question["questionId"]),
                    dict(question.get("runtimeTaskSeed") or {}),
                    self._continuation_prompt(question),
                )
        return {"queued": len(queued), "interrupted": len(interrupted)}

    def list(
        self,
        *,
        limit: int = 50,
        project_root: str = "",
        session_id: str = "",
        include_answered: bool = False,
    ) -> dict[str, object]:
        questions = list(self._project(include_answered=include_answered).values())
        if project_root:
            normalized_project_root = self._scope.normalize_path(project_root)
            questions = [
                item
                for item in questions
                if self._scope.normalize_path(str(item.get("projectRoot") or "")) == normalized_project_root
            ]
        if session_id:
            questions = [item for item in questions if str(item.get("sessionId") or "") == session_id]
        questions.sort(key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""), reverse=True)
        questions = questions[: max(1, min(int(limit), AGENT_QUESTION_MAX_ITEMS))]
        return {
            "ok": True,
            "schema": "vrcforge.agent_questions.v1",
            "questions": [self._redact(item) for item in questions],
            "count": len(questions),
        }

    def _project(self, *, include_answered: bool = False) -> dict[str, dict[str, object]]:
        questions: dict[str, dict[str, object]] = {}
        answered: set[str] = set()
        for event in self._persistence.read():
            question_id = str(event.get("questionId") or "").strip()
            if not question_id:
                continue
            if str(event.get("status") or "") in {"answered", "cancelled"} or str(event.get("event") or "") == "question_answered":
                answered.add(question_id)
            previous = questions.get(question_id, {})
            merged = {
                **previous,
                **event,
                "id": question_id,
                "questionId": question_id,
                "createdAt": previous.get("createdAt") or event.get("createdAt"),
                "updatedAt": event.get("updatedAt") or event.get("createdAt") or previous.get("updatedAt"),
            }
            for field in ("options", "question", "header"):
                if not event.get(field) and previous.get(field):
                    merged[field] = previous[field]
            questions[question_id] = merged
        if include_answered:
            return questions
        return {
            question_id: item
            for question_id, item in questions.items()
            if question_id not in answered and str(item.get("status") or "") == "pending"
        }

    def _require_scope(self, existing: Mapping[str, object], values: Mapping[str, object]) -> None:
        requested_session = self._scope_text(values, "sessionId", "session_id")
        existing_session = str(existing.get("sessionId") or "").strip()
        if requested_session and requested_session != existing_session:
            raise AgentQuestionServiceError("Question does not belong to this session.", status_code=404)
        requested_project = self._scope_text(values, "projectRoot", "project_root", "projectPath")
        existing_project = str(existing.get("projectRoot") or "").strip()
        if requested_project and self._scope.normalize_path(requested_project) != self._scope.normalize_path(existing_project):
            raise AgentQuestionServiceError("Question does not belong to this project.", status_code=404)

    def _continuation_prompt(self, question: object) -> str:
        values = question if isinstance(question, Mapping) else {}
        question_text = self._summarize(values.get("question") or "Pending question", 1000)
        answer_text = self._summarize(values.get("answer"), 2000)
        selected_option_id = self._summarize(values.get("selectedOptionId"), 120)
        return (
            "Continue the same task after the user answered a pending question.\n"
            f"Question: {question_text}\n"
            f"User answer: {answer_text or selected_option_id or 'No text provided.'}\n"
            "Resume the unfinished work under the existing constraints and do not repeat completed steps."
        )

    def _scope_text(self, values: Mapping[str, object], *names: str) -> str:
        return str(next((values[name] for name in names if values.get(name)), "") or "").strip()

    def _summarize(self, value: object, limit: int) -> str:
        return self._scope.summarize(str(value or "").strip(), limit)

    def _redact(self, value: object) -> dict[str, object]:
        redacted = self._persistence.redact(value)
        result = dict(redacted) if isinstance(redacted, Mapping) else {}
        for key in ("runtimeTaskSeed", "runtimeSessionId", "runtimeClientTurnId", "runtimeTaskId"):
            result.pop(key, None)
        return result


class _locked:
    def __init__(self, lock: LockPort) -> None:
        self._lock = lock

    def __enter__(self) -> None:
        if not self._lock.acquire():
            raise OSError("Question storage lock is unavailable.")

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self._lock.release()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []
