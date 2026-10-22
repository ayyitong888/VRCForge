"""Durable, scope-bound questions between an Agent run and its user.

The service owns only the Question JSONL projection.  It opens no listener,
process, or external connection.  Its one app-lifetime file/lock dependency,
scope rules, and optional Goal continuation are supplied as narrow typed ports
by the composition root.
"""

from __future__ import annotations
from copy import deepcopy

import json
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol


AGENT_QUESTION_SCHEMA = "vrcforge.agent_question.v1"
AGENT_QUESTION_MAX_ITEMS = 60
QUESTION_KINDS = frozenset({"decision", "visual_checkpoint"})
VISUAL_RESOLUTIONS = frozenset({"pass", "fail", "uncertain", "later"})
QUESTION_INPUT_SCHEMA = {
    "type": "object", "required": ["question"], "additionalProperties": True,
    "properties": {
        "header": {"type": "string", "maxLength": 120},
        "kind": {"type": "string", "enum": ["decision", "visual_checkpoint"]},
        "question": {
            "type": "string", "minLength": 1, "maxLength": 1000,
            "description": "One short decision question. Put alternatives in options, not in a long diagnosis here.",
        },
        "options": {
            "type": "array",
            "description": "Two or more distinct choice cards, recommended first. Omit only for an open-ended question; a custom reply is always available.",
            "items": {"anyOf": [
                {"type": "string", "minLength": 1, "maxLength": 160},
                {"type": "object", "required": ["label"], "properties": {
                    "id": {"type": "string", "maxLength": 120},
                    "label": {"type": "string", "minLength": 1, "maxLength": 160},
                    "description": {"type": "string", "maxLength": 500},
                    "value": {"type": "string", "maxLength": 500},
                }},
            ]},
        },
    },
}


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
        # This file also owns pending runtime continuations. Preserve their
        # private replay; public question projections remove the entire seed.
        seed = event.get("runtimeTaskSeed")
        if isinstance(seed, Mapping) and isinstance(seed.get("_nativeConversation"), Mapping):
            row["runtimeTaskSeed"]["_nativeConversation"] = deepcopy(seed["_nativeConversation"])
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
        source_tool = str(values.get("sourceTool") or values.get("source_tool") or "").strip().lower()
        kind = str(values.get("kind") or values.get("questionKind") or "decision").strip().lower()
        if kind not in QUESTION_KINDS:
            raise AgentQuestionServiceError("Unsupported Question kind.", status_code=400)
        question = _question_text(values.get("question") or values.get("prompt"), "question", 1000)
        if not question:
            raise AgentQuestionServiceError("Question is required.", status_code=400)
        raw_options = _as_list(values.get("options") or values.get("choices"))
        options: list[dict[str, str]] = []
        for index, option in enumerate(raw_options):
            if isinstance(option, str):
                label = _question_text(option, "option.label", 160)
                value = label
                description = ""
                option_id = f"option-{index + 1}"
            elif isinstance(option, Mapping):
                label = _question_text(option.get("label") or option.get("value"), "option.label", 160)
                value = _question_text(option.get("value") or label, "option.value", 500)
                description = _question_text(option.get("description"), "option.description", 500)
                option_id = _question_text(option.get("id") or f"option-{index + 1}", "option.id", 120)
            else:
                continue
            if label:
                options.append({"id": option_id, "label": label, "value": value, "description": description})
        if len(options) == 1:
            raise AgentQuestionServiceError("Question choices require at least two options.", status_code=400)
        project_root = self._scope_text(values, "projectRoot", "project_root", "projectPath")
        session_id = self._scope_text(values, "sessionId", "session_id")
        question_id = f"question_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(3)}"
        event: dict[str, object] = {
            "event": "question_created",
            "status": "pending",
            "questionId": question_id,
            "kind": kind,
            "header": _question_text(values.get("header"), "header", 120),
            "question": question,
            "options": options,
            "projectRoot": project_root,
            "sessionId": session_id,
            "owner": self._summarize(values.get("owner") or "agent", 80),
            "goalDeliveryId": str(values.get("goalDeliveryId") or values.get("goal_delivery_id") or "").strip(),
        }
        if source_tool:
            event["sourceTool"] = source_tool
        runtime_seed = values.get("_runtimeTaskSeed")
        if isinstance(runtime_seed, Mapping) and runtime_seed:
            event["runtimeTaskSeed"] = dict(runtime_seed)
            event["runtimeSessionId"] = str(values.get("_runtimeSessionId") or values.get("sessionId") or "").strip()
            event["runtimeClientTurnId"] = str(values.get("_runtimeClientTurnId") or "").strip()
            event["runtimeTaskId"] = str(runtime_seed.get("taskId") or "").strip()
        with _locked(self._persistence.shared_state_lock):
            if source_tool == "call_user":
                for existing in self._project(include_answered=False).values():
                    if (
                        str(existing.get("sourceTool") or "") == source_tool
                        and str(existing.get("kind") or "decision") == kind
                        and str(existing.get("question") or "") == question
                        and str(existing.get("sessionId") or "") == session_id
                        and self._scope.normalize_path(str(existing.get("projectRoot") or ""))
                        == self._scope.normalize_path(project_root)
                    ):
                        return {"ok": True, "question": self._redact(existing), "deduplicated": True}
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
        continuation_answer: str | None = None
        feedback_pending = False
        feedback_attachments = _question_attachments(values.get("attachments"))
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
                if runtime_seed and not goal_delivery_id and str(existing.get("runtimeContinuationStatus") or "") == "queued":
                    retry_answer = _question_text(values.get("answer") or values.get("value"), "answer", 2000)
                    if retry_answer:
                        continuation_answer = self._sanitize_answer_for_model(retry_answer)
                    elif "<path redacted>" in str(existing.get("answer") or "").casefold():
                        raise AgentQuestionServiceError(
                            "This queued answer contains a redacted location. Resubmit the original answer to resume the task.",
                            status_code=409,
                        )
            else:
                selected_option_id = _question_text(
                    values.get("selectedOptionId") or values.get("optionId"),
                    "selectedOptionId",
                    120,
                )
                answer_text = _question_text(values.get("answer") or values.get("value"), "answer", 2000)
                if not answer_text and selected_option_id:
                    for option in _as_list(existing.get("options")):
                        if not isinstance(option, Mapping) or str(option.get("id") or "") != selected_option_id:
                            continue
                        answer_text = _question_text(option.get("value") or option.get("label"), "answer", 2000)
                        break
                if not answer_text and not feedback_attachments:
                    raise AgentQuestionServiceError("An answer is required.", status_code=400)
                kind = str(existing.get("kind") or "decision").strip().lower()
                resolution = str(values.get("resolution") or "").strip().lower()
                if kind == "visual_checkpoint":
                    try:
                        feedback_revision = int(existing.get("questionFeedbackRevision") or 0) + 1
                    except (TypeError, ValueError):
                        feedback_revision = 1
                    if not resolution:
                        resolution = _visual_resolution(selected_option_id, answer_text)
                    if resolution not in VISUAL_RESOLUTIONS:
                        feedback_pending = True
                        continuation_answer = self._sanitize_answer_for_model(
                            answer_text or "The user attached feedback without an acceptance decision."
                        )
                        self._persistence.append({
                            "event": "question_feedback",
                            "status": "pending",
                            "questionId": resolved_id,
                            "runtimeContinuationStatus": "queued" if runtime_seed and not goal_delivery_id else "",
                            "runtimeQuestionFeedbackPending": bool(runtime_seed and not goal_delivery_id),
                            "answer": str(self._scope.redact_goal_persistence(answer_text) or ""),
                            "attachments": feedback_attachments,
                            "questionFeedbackRevision": feedback_revision,
                        })
                        pending = self._project(include_answered=True).get(resolved_id)
                        if pending is None:
                            raise OSError("Question storage did not preserve the pending feedback.")
                        payload = {"ok": True, "question": self._redact(pending), "feedback": True}
                    if resolution in {"uncertain", "later"}:
                        deferred = {
                            "event": "question_deferred",
                            "status": "pending",
                            "questionId": resolved_id,
                            "resolution": resolution,
                            "answer": str(self._scope.redact_goal_persistence(answer_text) or ""),
                        }
                        self._persistence.append(deferred)
                        pending = self._project(include_answered=True).get(resolved_id)
                        if pending is None:
                            raise OSError("Question storage did not preserve the deferred Question.")
                        return {"ok": True, "question": self._redact(pending), "deferred": True}
                if not feedback_pending:
                    continuation_answer = self._sanitize_answer_for_model(answer_text)
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
                    if kind == "visual_checkpoint":
                        event["resolution"] = resolution
                        event["questionFeedbackRevision"] = feedback_revision
                        event["runtimeQuestionFeedbackPending"] = False
                    if feedback_attachments:
                        event["attachments"] = feedback_attachments
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
                self._continuation_prompt(payload["question"], answer_text=continuation_answer),
            )
        if (runtime_seed and not goal_delivery_id and self._runtime_resolution is not None
                and (not payload.get("idempotent") or existing.get("runtimeContinuationStatus") == "queued")):
            # This copy is ephemeral: durable Question records keep the stricter
            # redacted answer, while the trusted runtime handoff can bind an
            # explicit user instruction to the resumed action.
            dispatch_seed = dict(runtime_seed) if isinstance(runtime_seed, Mapping) else {}
            if continuation_answer:
                dispatch_seed["_authenticatedUserAnswer"] = continuation_answer
            if feedback_attachments:
                dispatch_seed["_authenticatedUserAttachments"] = feedback_attachments
            if feedback_pending:
                dispatch_seed["_authenticatedQuestionFeedbackPending"] = True
            question_snapshot = payload.get("question")
            if isinstance(question_snapshot, Mapping):
                revision = question_snapshot.get("questionFeedbackRevision")
                if revision is not None:
                    dispatch_seed["_questionFeedbackRevision"] = revision
                if str(question_snapshot.get("kind") or "") == "visual_checkpoint":
                    dispatch_seed["_questionKind"] = "visual_checkpoint"
            payload["runtimeContinuation"] = self._runtime_resolution.resolve(
                resolved_id,
                dispatch_seed,
                self._continuation_prompt(payload["question"], answer_text=continuation_answer),
            )
        return payload

    def record_runtime_continuation(self, question_id: str, status: str, **fields: object) -> None:
        """Persist the lifecycle marker used by the app-owned dispatcher."""
        # A newer answer may have queued another continuation while the prior
        # provider turn was still unwinding.  Its queued marker is authoritative;
        # an older worker must not overwrite it with delivered.
        if str(status or "") == "delivered":
            current = self._project(include_answered=True).get(str(question_id or "").strip(), {})
            if current.get("runtimeContinuationStatus") == "queued":
                return
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
            if question.get("status") not in {"answered", "pending"} or question.get("runtimeContinuationStatus") != "queued":
                return False
            self.record_runtime_continuation(question_id, "claimed")
            return True

    def cancel_runtime_questions(self, *, session_id: str = "", turn_id: str = "", client_turn_id: str = "") -> list[dict[str, object]]:
        """Persist cancellation and return trusted seeds for the stopped turn."""
        cancelled_seeds: list[dict[str, object]] = []
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
                    cancelled_seeds.append(deepcopy(dict(seed)))
        return cancelled_seeds

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
                self._resolve_queued_question(question)
        return {"queued": len(queued), "interrupted": len(interrupted)}

    def reconcile_runtime_continuation(self, question_id: str) -> bool:
        """Retry only a newer queued answer for one question during runtime."""
        with _locked(self._persistence.shared_state_lock):
            question = self._project(include_answered=True).get(str(question_id or "").strip())
            if not isinstance(question, Mapping) or question.get("runtimeContinuationStatus") != "queued":
                return False
        return self._resolve_queued_question(question)

    def _resolve_queued_question(self, question: Mapping[str, object]) -> bool:
        if self._runtime_resolution is None:
            return False
        seed = dict(question.get("runtimeTaskSeed") or {})
        attachments = question.get("attachments")
        if isinstance(attachments, list) and attachments:
            seed["_authenticatedUserAttachments"] = deepcopy(attachments)
        if question.get("runtimeQuestionFeedbackPending"):
            seed["_authenticatedQuestionFeedbackPending"] = True
        if question.get("questionFeedbackRevision") is not None:
            seed["_questionFeedbackRevision"] = question.get("questionFeedbackRevision")
        if str(question.get("kind") or "") == "visual_checkpoint":
            seed["_questionKind"] = "visual_checkpoint"
        result = self._runtime_resolution.resolve(
            str(question["questionId"]), seed, self._continuation_prompt(question),
        )
        return isinstance(result, Mapping) and result.get("status") in {"queued", "ok"}

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
        # Answered history must not displace a question that still needs input
        # or whose owned continuation is active in a bounded App snapshot.
        questions.sort(key=lambda item: (
            2 if item.get("status") == "pending" else 1 if item.get("runtimeContinuationStatus") in {"queued", "claimed"} else 0,
            str(item.get("updatedAt") or item.get("createdAt") or ""),
        ), reverse=True)
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

    def _continuation_prompt(self, question: object, *, answer_text: str | None = None) -> str:
        values = question if isinstance(question, Mapping) else {}
        question_text = str(values.get("question") or "Pending question").strip()
        answer = str(answer_text if answer_text is not None else values.get("answer") or "").strip()
        selected_option_id = str(values.get("selectedOptionId") or "").strip()
        return (
            "Continue the same task after the user answered a pending question.\n"
            f"Question: {question_text}\n"
            f"User answer: {answer or selected_option_id or 'No text provided.'}\n"
            "Resume the unfinished work under the existing constraints and do not repeat completed steps."
        )

    @staticmethod
    def _sanitize_answer_for_model(answer: str) -> str:
        # Reuse the planner's established credential scrubber while allowing
        # the user-supplied URL/path locators needed to resume the task.
        from runtime_planner_service import sanitize_planner_observation_text

        return sanitize_planner_observation_text(
            answer, None, preserve_whitespace=True, preserve_urls=True, preserve_paths=True,
        )

    def _scope_text(self, values: Mapping[str, object], *names: str) -> str:
        return str(next((values[name] for name in names if values.get(name)), "") or "").strip()

    def _summarize(self, value: object, limit: int) -> str:
        return self._scope.summarize(str(value or "").strip(), limit)

    def _redact(self, value: object) -> dict[str, object]:
        redacted = self._persistence.redact(value)
        result = dict(redacted) if isinstance(redacted, Mapping) else {}
        # Publish only whether this question holds an owned task for a human.
        # Caller-supplied urgency cannot turn an ordinary question into an alert.
        source = value if isinstance(value, Mapping) else {}
        result["needsUserAction"] = bool(
            source.get("status") == "pending"
            and (source.get("runtimeTaskSeed") or source.get("goalDeliveryId"))
            and source.get("runtimeContinuationStatus") not in {"queued", "claimed", "running"}
        )
        for key in ("runtimeTaskSeed", "runtimeSessionId", "runtimeClientTurnId", "runtimeTaskId"):
            result.pop(key, None)
        return result


def _question_text(value: object, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise AgentQuestionServiceError(
            f"{field} exceeds {limit} characters. Shorten the text; the request was not saved.",
            status_code=400,
        )
    return text


def _visual_resolution(option_id: str, answer: str) -> str:
    """Map only explicit human checkpoint vocabulary; never infer pass/fail from prose."""
    for candidate in (option_id, answer):
        value = str(candidate or "").strip().lower().replace("_", "-")
        if value in VISUAL_RESOLUTIONS:
            return value
        if value in {"passed", "成功", "通过"}:
            return "pass"
        if value in {"failed", "失败", "不通过"}:
            return "fail"
    return ""


def _question_attachments(value: object) -> list[dict[str, object]]:
    """Keep only admitted content-addressed attachment references.

    Question answers never accept arbitrary local paths or inline upload bytes;
    the existing chat attachment admission owns those operations.
    """
    attachments: list[dict[str, object]] = []
    for raw in _as_list(value)[:16]:
        if not isinstance(raw, Mapping):
            raise AgentQuestionServiceError("Attachment reference is invalid.", status_code=400)
        payload_hash = str(raw.get("payloadHash") or raw.get("payload_hash") or "").strip().lower()
        inline_value = str(raw.get("dataUrl") or raw.get("data_url") or raw.get("text") or "")
        if not payload_hash and inline_value:
            payload_hash = hashlib.sha256(inline_value.encode("utf-8")).hexdigest()
        if len(payload_hash) != 64 or any(char not in "0123456789abcdef" for char in payload_hash):
            raise AgentQuestionServiceError("Attachment must be an admitted content reference.", status_code=400)
        item: dict[str, object] = {
            "id": str(raw.get("id") or "")[:120],
            "name": str(raw.get("name") or "attachment")[:240],
            "type": str(raw.get("type") or "application/octet-stream")[:120],
            "size": max(0, int(raw.get("size") or 0)) if str(raw.get("size") or "0").isdigit() else 0,
            "payloadHash": payload_hash,
            "payloadKind": str(raw.get("payloadKind") or raw.get("payload_kind") or "metadata")[:32],
        }
        for key in ("vaultPayloadHash", "vaultKind", "replayable"):
            if key in raw:
                item[key] = raw[key]
        # These are already bounded by the chat admission normalizer; retain
        # them for the next model request instead of converting them to metadata.
        for key in ("dataUrl", "text"):
            if raw.get(key):
                item[key] = str(raw[key])
        attachments.append(item)
    return attachments


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
