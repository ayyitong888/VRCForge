"""Small adapter for one native provider turn.

The adapter owns only per-turn bookkeeping (receipt ids, observation cursor,
and queued steer input). Conversation storage and validation remain owned by
AgentRuntimeSessionState.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Callable
from agent_native_handoff_blocks import handoff_block_messages
from agent_user_path_context import human_indices, human_user_projection
from agent_runtime_input import RuntimeInputSource, classify_steer
from responses_native_protocol import public_assistant_messages
from agent_scan_result_policy import (
    SCAN_RESULT_CONTINUATION_POLICY_V2,
    is_scan_result_policy_message,
)

if TYPE_CHECKING:
    from agent_runtime_session_state import AgentRuntimeSessionState
    from runtime_planner_service import RuntimePlannerService


class NativeRuntimeTurn:
    """Borrow the gateway's session state for one native provider turn."""

    @staticmethod
    def cancel_question(
        state: "AgentRuntimeSessionState",
        seed: Mapping[str, Any],
        *,
        skipped: bool = False,
        answer_saved: bool = False,
        settle_following_calls: bool = True,
    ) -> list[str]:
        """Settle only ask-user calls from one validated runtime seed."""
        session_id = str(seed.get("sessionId") or "").strip()
        snapshot = seed.get("_nativeConversation")
        if not session_id or not isinstance(snapshot, Mapping):
            return []
        binding = str(snapshot.get("binding") or "").strip()
        if not binding:
            return []
        save_only = skipped or answer_saved
        if save_only:
            if state.session_busy(session_id) and not state.owns_turn(
                session_id=session_id, turn_id=str(seed.get("turnId") or ""),
                client_turn_id=str(seed.get("clientTurnId") or ""),
            ):
                return []
            restored = state.native_conversation(session_id, binding=binding)
            if restored is None or restored["turnId"] != snapshot.get("turnId"):
                return []
        else:
            restored = state.restore_native_conversation(
                session_id, binding=binding, snapshot=copy.deepcopy(dict(snapshot)),
            )
        question_ids: set[str] = set()
        for message in snapshot.get("messages", []):
            if not isinstance(message, Mapping) or message.get("role") != "assistant":
                continue
            for tool_call in message.get("tool_calls", []) or []:
                if not isinstance(tool_call, Mapping):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, Mapping):
                    continue
                name = function.get("name")
                args = {}
                if name == "call_tool":
                    try:
                        args = json.loads(function.get("arguments", ""))
                    except (TypeError, ValueError):
                        continue
                    name = args.get("tool") if isinstance(args, dict) else None
                if name in {"vrcforge_browser", "browser"}:
                    browser_args = args.get("arguments") if isinstance(args, Mapping) else None
                    if not isinstance(browser_args, Mapping):
                        continue
                    open_request = seed.get("_browserOpenRequest")
                    if isinstance(open_request, Mapping):
                        matches = browser_args.get("operation") == "open" and (
                            browser_args.get("url"), browser_args.get("browser", "")
                        ) == (open_request.get("url"), open_request.get("browser", ""))
                    else:
                        target = seed.get("_browserClickTarget")
                        matches = bool(target) and (
                            browser_args.get("operation"), browser_args.get("target")
                        ) == ("click", target)
                    if not matches:
                        continue
                elif name not in {"vrcforge_ask_user", "vrcforge_call_user", "ask_user", "call_user"}:
                    continue
                call_id = str(tool_call.get("id") or "").strip()
                if call_id:
                    question_ids.add(call_id)
        exact_id = str(seed.get("_nativeCallId") or "") if save_only else ""
        if exact_id:
            question_ids = {exact_id} & state._native_pending(snapshot)
        if answer_saved:
            disposition = "answered"
            summary = "The answer was saved without restarting the original turn."
        elif skipped:
            disposition = "skipped"
            summary = "The user skipped this question without an answer."
        else:
            disposition = "cancelled"
            summary = "Question cancelled by user stop."
        cancelled: list[str] = []
        for call_id in sorted(question_ids & state._native_pending(restored)):
            state.settle_native_call(
                session_id,
                binding=binding,
                call_id=call_id,
                content=json.dumps(
                    {"status": disposition, "outcome": {"status": disposition, "summary": summary}},
                    ensure_ascii=False,
                ),
            )
            cancelled.append(call_id)
        if cancelled and settle_following_calls:
            # Closing a waiting question also stops later proposals in its same
            # receipt, but cannot touch calls from a newer unrelated turn.
            pending = state._native_pending(snapshot) & state._native_pending(restored)
            for call_id in sorted(pending - set(cancelled)):
                state.settle_native_call(session_id, binding=binding, call_id=call_id,
                    content=json.dumps({"status": "not_executed", "reason": f"question_{disposition}", "observations": []}))
                cancelled.append(call_id)
        return cancelled

    def __init__(
        self,
        owner: "AgentRuntimeSessionState",
        planner: "RuntimePlannerService",
        session_id: str,
        turn_id: str,
        binding: str,
        message: str,
        loop_state: list[dict[str, Any]],
        continuation_context: Mapping[str, Any] | None,
        continuation_completion: Mapping[str, Any] | None,
        continuation_observation: Mapping[str, Any] | None,
        initial_history: list[dict[str, Any]] | None = None,
        client_turn_id: str = "",
        retain_recovery: Callable[[dict[str, Any]], str] | None = None,
        project_context_active: bool = False,
        on_steer_consumed: Callable[[list[str]], None] | None = None,
        subagent_batch: list[dict[str, Any]] | None = None,
        input_source: RuntimeInputSource = RuntimeInputSource.USER,
        late_approval_id: str = "",
        compaction_only: bool = False,
        source_text: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        question_reference: Callable[[str], dict[str, str] | None] | None = None,
        question_answer: dict[str, str] | None = None,
    ) -> None:
        self._state = owner
        self._planner = planner
        self._project_context_active = project_context_active
        self._session_id = session_id
        self._question_reference = question_reference
        self._turn_id = turn_id
        self._client_turn_id = client_turn_id
        self._binding = binding
        # Transport-only state belongs to this turn, never durable conversation history.
        self.provider_turn_state: dict[str, str] = {}
        from agent_output_truncation import OutputTruncation
        self.output_truncation = OutputTruncation(**dict((continuation_context or {}).get("_outputTruncation") or {}))
        self._loop_state = loop_state
        self._native_decision: dict[str, Any] | None = None
        self._native_steers: list[dict[str, Any]] = []
        # Keep media bound to its human history slot for this turn only.
        self.steer_attachments: dict[int, list[dict[str, Any]]] = {}
        self._consumed_steer_input_ids: list[str] = []
        self._on_steer_consumed = on_steer_consumed
        self._queued_calls: list[dict[str, Any]] = []
        self._queued_failure_scopes: dict[str, str] = {}
        self.receipt_call_id = ""
        self.subagent_batch_call_ids: tuple[str, ...] = ()
        self._parked_subagent_calls = False
        self._terminal_runtime_notice_written = False
        self._queued_public_content: str | None = None
        self._queued_public_messages: list[dict[str, str]] = []
        self._argument_failure_counted = False
        self._scan_result_policy_persisted = False
        self.admitted_tool_names: set[str] | None = None
        self.compaction_attempted_snapshot: dict[str, Any] | None = None
        self.compaction: dict[str, Any] | None = None
        # The gateway owns reader scope and lifecycle; the turn owns no store.
        self.retain_recovery = retain_recovery
        self.retain_recovery_catalog: Callable[[list[dict[str, Any]]], str] | None = None
        if compaction_only:
            return
        state = self._state
        continuation_context = continuation_context or {}
        continuation_completion = continuation_completion or {}
        saved_native = continuation_context.get("_nativeConversation")
        if not binding and not isinstance(saved_native, Mapping):
            return
        if isinstance(saved_native, Mapping):
            saved_binding = str(saved_native.get("binding") or "")
            latest = state.native_conversation(session_id, binding=saved_binding)
            call_id = continuation_context.get("_nativeCallId")
            late_approval = bool(
                late_approval_id and latest is not None
                and latest["turnId"] != saved_native["turnId"]
                and call_id and call_id in state._native_pending(saved_native)
            )

            if late_approval:
                if saved_binding != binding or state._native_pending(latest):
                    raise ValueError("The approval result was recorded while the newer conversation has a different route or pending action.")
                restored = latest
            else:
                restored = state.restore_native_conversation(
                    session_id, binding=saved_binding, snapshot=copy.deepcopy(dict(saved_native)),
                )
                if restored["turnId"] != saved_native["turnId"]:
                    raise ValueError("Native continuation belongs to an earlier user turn.")
            pending_ids = state._native_pending(saved_native) & state._native_pending(restored)
            retained = self._retained_tool_definitions(restored["messages"])

            def result_content(
                context: Mapping[str, Any], completion: Mapping[str, Any],
                observation: Mapping[str, Any] | None,
            ) -> str:
                action_id = context.get("requestedActionId")
                observations = (
                    [{**observation, "actionId": action_id}]
                    if observation
                    else [item for item in loop_state if action_id and item.get("actionId") == action_id]
                )
                return json.dumps({
                    "status": completion.get("status") or "completed",
                    "observations": [
                        planner.native_result_observation(
                            item, project_context_active=self._project_context_active,
                            retained_tool_definitions=retained,
                        )
                        for item in observations
                    ],
                    "actionId": action_id or "",
                }, ensure_ascii=False)

            if late_approval:
                # The old assistant proposal has already been closed for wire
                # ordering. Deliver its terminal evidence against current human
                # history without reissuing the original tool call or its queue.
                state.begin_native_turn(
                    session_id, binding=binding, turn_id=turn_id,
                    message="Earlier approval result: " + json.dumps({
                        "approvalId": late_approval_id,
                        "toolCallId": call_id,
                        **json.loads(result_content(
                            continuation_context, continuation_completion, continuation_observation,
                        )),
                    }, ensure_ascii=False),
                    input_source=RuntimeInputSource.RUNTIME, input_id=client_turn_id,
                )
                return
            if not pending_ids and subagent_batch is None:
                raise ValueError("Native continuation has already been settled.")
            if subagent_batch is not None:
                receipts = {}
                for member in subagent_batch:
                    context = member["context"]
                    call_id = context.get("_nativeCallId")
                    if call_id in pending_ids:
                        receipts[call_id] = result_content(context, member["completion"], member["plannerObservation"])
                state.settle_native_calls(session_id, binding=saved_binding, results=receipts)
                if pending_ids.difference(receipts):
                    # A crash can leave proposals without a registered child.
                    state.normalize_native_pending(session_id, binding=saved_binding)
            elif pending_ids:
                # The first still-pending call is the only dispatched action;
                # later calls remain proposals in the immutable assistant receipt.
                pending_calls = [call for item in restored["messages"]
                                 for call in item.get("tool_calls", []) if call["id"] in pending_ids]
                active_id = continuation_context.get("_nativeCallId") or pending_calls[0]["id"]
                if active_id not in pending_ids:
                    raise ValueError("Native continuation call has already been settled.")
                self.receipt_call_id = next(
                    item["tool_calls"][0]["id"] for item in restored["messages"]
                    if any(call["id"] == active_id for call in item.get("tool_calls", []))
                )
                self._queued_calls = copy.deepcopy([call for call in pending_calls if call["id"] != active_id])
                admitted_names = continuation_context.get("_nativeAdmittedToolNames", [])
                if not isinstance(admitted_names, list) or any(not isinstance(name, str) or not name for name in admitted_names):
                    raise ValueError("Invalid native receipt tool scope.")
                self.admitted_tool_names = set(admitted_names)
                if self._queued_calls and not self.admitted_tool_names:
                    raise ValueError("Native continuation tool scope is missing.")
                state.settle_native_call(
                    session_id, binding=saved_binding, call_id=active_id,
                    content=result_content(continuation_context, continuation_completion, continuation_observation),
                )
            if binding != saved_binding:
                raise ValueError("Provider settings changed. The action result was saved; send a new message to continue with the selected provider.")
            if question_answer:
                question_id, answer = question_answer["id"], question_answer["text"]
                if self._queued_calls:
                    self._native_steers.insert(0, {
                        "inputId": "question-answer:" + question_id, "message": "",
                        "authenticatedAnswer": answer, "attachments": attachments or [],
                    })
                else:
                    self._append_question_answer(question_id, answer, attachments)
        else:
            state.begin_native_turn(
                session_id,
                binding=binding,
                turn_id=turn_id,
                message=message,
                initial_history=initial_history,
                input_source=input_source,
                input_id="question-answer:" + question_answer["id"] if question_answer and input_source.is_human else client_turn_id,
                source_text=source_text,
                attachments=attachments,
                references=[reference] if question_answer and question_reference
                    and (reference := question_reference(question_answer["id"])) else None,
            )
            if question_answer and not input_source.is_human:
                self._append_question_answer(question_answer["id"], question_answer["text"], attachments)

    def _append_question_answer(self, question_id: str, answer: str, attachments=None) -> None:
        reference = self._question_reference(question_id) if self._question_reference else None
        self._state.append_native_user(
            self._session_id, binding=self._binding, message=answer, input_source=RuntimeInputSource.USER,
            input_id="question-answer:" + question_id, attachments=attachments,
            references=[reference] if reference else None,
        )

    def append_hidden(self, text: str) -> None:
        self._state.append_native_user(self._session_id, binding=self._binding, message=text, human=False, hidden=True)

    def append_output_continuation(self) -> None:
        self._state.append_native_output_continuation(self._session_id, binding=self._binding)

    def snapshot(self) -> dict[str, Any]:
        return self._state.native_conversation(self._session_id, binding=self._binding) or {}

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def consumed_steer_input_ids(self) -> list[str]:
        """Exact receipts from successful insertion before a model boundary."""
        return list(self._consumed_steer_input_ids)

    @staticmethod
    def _retained_tool_definitions(messages) -> dict[str, dict[str, Any]]:
        definitions: dict[str, dict[str, Any]] = {}
        for message in messages:
            if not isinstance(message, Mapping) or message.get("role") != "tool":
                continue
            try:
                payload = json.loads(message.get("content", ""))
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            for observation in payload.get("observations", []):
                if not isinstance(observation, dict):
                    continue
                for definition in observation.get("loadedToolDefinitions", []):
                    if isinstance(definition, dict) and isinstance(definition.get("name"), str):
                        # The latest retained occurrence is authoritative when a
                        # same-name definition was refreshed after a permission
                        # or catalog change.
                        definitions[definition["name"]] = copy.deepcopy(definition)
        return definitions

    def freeze_tool_block_ids(self, block_ids: list[str]) -> list[str]:
        if not self._binding:
            return list(block_ids)
        return self._state.freeze_native_tool_block_ids(
            self._session_id, binding=self._binding, block_ids=block_ids,
        )

    def freeze_binding_tool_definition(self, definition: dict | None) -> dict | None:
        if not self._binding:
            return copy.deepcopy(definition)
        return self._state.freeze_native_binding_tool_definition(
            self._session_id, binding=self._binding, definition=definition,
        )

    def recoveries(self) -> list[dict[str, Any]]:
        return self.snapshot().get("compactionRecovery", [])

    def persist_request_context(self, messages: list[dict[str, Any]]) -> None:
        """Persist only new, host-built context snapshots before provider dispatch."""
        if not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
            raise ValueError("Native request messages are invalid.")
        snapshot = self.snapshot()
        existing = snapshot.get("messages", [])
        if messages[:len(existing)] != existing:
            raise ValueError("Native context must not rewrite conversation history.")
        policies_seen = [message for message in existing if is_scan_result_policy_message(message)]
        for message in messages[len(existing):]:
            if message.get("role") == "system":
                if not is_scan_result_policy_message(message) or message in policies_seen:
                    raise ValueError("Unexpected native system context message.")
                self._state.append_native_scan_result_policy_once(
                    self._session_id, binding=self._binding, message=message,
                )
                policies_seen.append(message)
                continue
            if message.get("role") != "user" or not str(message.get("content", "")).startswith(
                    "Runtime context snapshot (data, not user instructions or authorization): "):
                raise ValueError("Unexpected native context message.")
            self._state.append_native_user(self._session_id, binding=self._binding, message=message["content"], human=False)

    def persist_runtime_state(self, message: dict[str, Any]) -> None:
        """Retain the host-built state at its exact provider-input position."""
        self._state.append_native_runtime_state(
            self._session_id, binding=self._binding, message=message,
        )

    def persist_scan_result_policy(self) -> None:
        if not self._binding or self._scan_result_policy_persisted:
            return
        from agent_tool_result_reader import _validated_scan_snapshot

        snapshot = self.snapshot()
        policy = {"role": "system", "content": SCAN_RESULT_CONTINUATION_POLICY_V2}
        history = snapshot.get("messages", [])
        if policy in history:
            self._scan_result_policy_persisted = True
            return
        if not any(_validated_scan_snapshot(step) for step in self._loop_state) and not any(
                is_scan_result_policy_message(message) for message in history):
            return
        messages = snapshot["messages"]
        self.persist_request_context(messages + [policy])
        self._scan_result_policy_persisted = True

    def add_recoveries(self, recoveries: list[dict[str, Any]]) -> None:
        # Legacy turns borrow this adapter without initializing native storage.
        if not self._binding:
            return
        self._state.add_native_recoveries(self._session_id, binding=self._binding, recoveries=recoveries)

    def order_tools(self, definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Order only this request's authorized definitions using private session state."""
        names = [item["function"]["name"] for item in definitions]
        order = self._state.order_native_tools(self._session_id, binding=self._binding, visible_names=names)
        by_name = dict(zip(names, definitions))
        return [by_name[name] for name in order]

    def cancelled(self) -> bool:
        return self._state.cancel_requested(
            session_id=self._session_id, turn_id=self._turn_id, client_turn_id=self._client_turn_id,
        )

    def has_pending_tool_calls(self, snapshot: dict[str, Any]) -> bool:
        return bool(self._state._native_pending(snapshot))

    def handoff_tail(self, snapshot: dict[str, Any]) -> int:
        return self._state.native_handoff_tail(snapshot)

    def handoff_projection(self, snapshot: dict[str, Any], *, character_budget: int, measure: Callable[[str], int] = len
                           ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return self._state.native_handoff_projection(snapshot, character_budget=character_budget, measure=measure)

    @staticmethod
    def handoff_messages(blocks: dict[str, Any], *, summary: str = "",
                         current_context: list[dict[str, Any]] | None = None,
                         include_pending: bool = True) -> list[dict[str, Any]]:
        return handoff_block_messages(blocks, summary=summary, current_context=current_context,
                                      include_pending=include_pending)

    def compaction_projection(self, snapshot: dict[str, Any], *, tail_from: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read-only projection; installation still revalidates snapshot identity."""
        return self._state.native_compaction_projection(snapshot, tail_from=tail_from)

    def replace_compacted_history(self, expected_snapshot: dict[str, Any], summary: str,
                                 *, recovery: dict[str, Any] | None = None,
                                 current_context: list[dict[str, Any]] | None = None,
                                 tail_from: int | None = None,
                                 handoff_blocks: dict[str, Any] | None = None) -> None:
        # Check Stop and compare-and-replace under the same existing state lock.
        with self._state.shared_state_lock:
            if self.cancelled():
                raise ValueError("native compaction cancelled")
            compacted = self._state.replace_native_compacted_history(
                self._session_id, binding=self._binding,
                expected_snapshot=expected_snapshot, summary=summary, recovery=recovery, current_context=current_context,
                tail_from=tail_from, handoff_blocks=handoff_blocks,
            )
            if handoff_blocks is not None:
                # Any steer already consumed by this history enters the handoff
                # as source material; only future steers are bound as attachments.
                self.steer_attachments.clear()
            elif self.steer_attachments:
                retained_indices = (
                    index for index in human_indices(expected_snapshot)
                    if (index in self._state._native_handoff_indices(expected_snapshot, tail_from) if tail_from is not None
                        else human_user_projection(expected_snapshot["messages"][index]) is not None)
                )
                self.steer_attachments = {
                    new_index: self.steer_attachments[old_index]
                    for old_index, new_index in zip(retained_indices, compacted["humanUserMessageIndices"])
                    if old_index in self.steer_attachments
                }

    def admit(self, receipt: dict[str, Any], *, tool_names: Iterable[str] = ()) -> None:
        """Record one provider receipt; execution remains serial in the existing dispatcher."""
        if not self._binding:
            return
        self._state.append_native_assistant(self._session_id, binding=self._binding, message=receipt)
        self._argument_failure_counted = False
        self.subagent_batch_call_ids = ()
        # New receipt: nothing is dispatched yet; the marker proves queued
        # calls never launched until mark_native_dispatch fires.
        if receipt.get("tool_calls"):
            self._state.mark_native_dispatch(self._session_id, binding=self._binding, call_id="")
        self._queued_calls = copy.deepcopy(receipt.get("tool_calls") or [])
        self.receipt_call_id = self._queued_calls[0]["id"] if self._queued_calls else ""
        self._queued_failure_scopes.clear()
        public_content = receipt.get("content")
        self._queued_public_content = public_content if isinstance(public_content, str) else None
        self._queued_public_messages = public_assistant_messages(receipt)
        self.admitted_tool_names = set(tool_names)
        self._native_decision = None if self._queued_calls else {"ids": [], "cursor": len(self._loop_state)}

    def count_argument_failure(self, previous: int) -> int:
        """Charge one correction attempt per provider response, not queued call."""
        if not self._binding:
            return previous + 1
        if self._argument_failure_counted:
            return max(1, previous)
        self._argument_failure_counted = True
        return previous + 1

    @property
    def has_queued_calls(self) -> bool:
        return bool(self._queued_calls)

    @property
    def active_call_id(self) -> str:
        ids = self._native_decision["ids"] if self._native_decision else []
        return ids[0] if len(ids) == 1 else ""

    def next_receipt(self) -> dict[str, Any]:
        if self._native_decision is not None or not self._queued_calls:
            raise ValueError("Native call cannot dispatch before its predecessor settles.")
        call = self._queued_calls.pop(0)
        # Persist the dispatch marker before the call runs so a crash while the
        # tool executes settles as execution_unknown, never not_executed.
        self._state.mark_native_dispatch(self._session_id, binding=self._binding, call_id=str(call["id"]))
        self._native_decision = {"ids": [call["id"]], "cursor": len(self._loop_state)}
        public_content = self._queued_public_content
        self._queued_public_content = None
        public_messages = self._queued_public_messages
        self._queued_public_messages = []
        return {
            "role": "assistant",
            "content": public_content,
            "tool_calls": [copy.deepcopy(call)],
            **({"publicMessages": public_messages} if public_messages else {}),
        }

    def park_subagent_call(self) -> None:
        """Keep the started child's call unresolved while admitting its siblings."""
        self._native_decision = None
        self._parked_subagent_calls = True

    def reject_queued_receipt(self) -> None:
        """Bind one whole-receipt admission failure to every unexecuted call."""
        self._native_decision = {"ids": [call["id"] for call in self._queued_calls], "cursor": len(self._loop_state)}
        self._queued_calls.clear()

    def classify_queued_calls(self, tools) -> None:
        for call in self._queued_calls:
            if call["id"] not in self._queued_failure_scopes:
                self._queued_failure_scopes[call["id"]] = self._planner.queued_call_failure_scope(call, tools)

    def prepare_admitted_batch(self, tools) -> None:
        self.classify_queued_calls(tools)
        scopes = self._queued_failure_scopes.values()
        if all(scope == "delegation" for scope in scopes):
            self.subagent_batch_call_ids = tuple(call["id"] for call in self._queued_calls)
        elif "validation" in scopes:
            self._settle_unexecuted(
                "batch_contains_invalid_call",
                keep_scopes=("validation", "discovery", "delegation", "local_read", "project_read"),
            )

    def _settle_unexecuted(self, reason: str, *, keep_scopes: tuple[str, ...] = ()) -> None:
        remaining = []
        for call in self._queued_calls:
            if self._queued_failure_scopes.get(call["id"]) in keep_scopes:
                remaining.append(call)
                continue
            self._state.settle_native_call(self._session_id, binding=self._binding, call_id=call["id"],
                content=json.dumps({"status": "not_executed", "reason": reason, "observations": []}))
        self._queued_calls = remaining

    def settle(self, terminal: Mapping[str, Any] | None = None, *, flush_steers: bool = True) -> None:
        """Settle native calls only after all canonical observations are final."""
        if not self._binding:
            return
        if self._native_decision is not None:
            observations = self._loop_state[self._native_decision["cursor"] :]
            waiting_statuses = {
                "approval_pending",
                "pending_approval",
                "pending",
                "running",
            }
            def is_waiting(item: Mapping[str, Any]) -> bool:
                from agent_tool_result_contract import pending_execution_receipt, runtime_observation_receipt
                status = str(item.get("status") or "")
                outcome = item.get("outcome")
                outcome_status = (
                    str(outcome.get("status") or "")
                    if isinstance(outcome, Mapping)
                    else ""
                )
                # A write wrapper may retain approval_pending at the transport
                # layer even after its inner approved request has failed (for
                # example, the interrupted-apply recovery guard).  Preserve
                # real approval/question waits, but settle a call when the
                # canonical action outcome is already terminally failed.
                if outcome_status in {"failed", "denied", "rejected", "cancelled", "not_executed"}:
                    return False
                if outcome_status == "pending" and (runtime_observation_receipt(item) or pending_execution_receipt(item)):
                    # The dispatch/start/poll call returned its receipt. The
                    # work remains unverified, but the model needs this result
                    # before it can inspect or poll the existing operation.
                    return False
                result = item.get("result")
                if item.get("kind") == "shell" and isinstance(result, Mapping) and result.get("pollTarget"):
                    return False
                if (
                    item.get("tool") == "vrcforge_read_conversation"
                    and isinstance(result, Mapping)
                    and result.get("ok") is True
                    and result.get("clientTurnId")
                ):
                    # An exact read returned its observation. A peer may still
                    # be running, but it owns no callback for this native call.
                    return False
                if isinstance(result, Mapping) and str(result.get("status") or "") == "running":
                    # Delegation tools return an executed dispatch envelope
                    # whose nested task is still running. Keep the native call
                    # pending until its terminal callback resumes this turn;
                    # otherwise the callback races a second settlement.
                    return True
                if status in waiting_statuses or outcome_status in waiting_statuses:
                    return True
                if status != "needs_user_action" and outcome_status != "needs_user_action":
                    return False
                # Only the explicit question tool owns a user-answer wait.
                # Other needs_user_action outcomes (for example a scope
                # denial) are terminal and must not block the next turn.
                if str(item.get("tool") or "") not in {"vrcforge_ask_user", "vrcforge_call_user", "vrcforge_browser"}:
                    return False
                for candidate in (item.get("result"), item.get("resultRead"), outcome):
                    if isinstance(candidate, Mapping) and str(candidate.get("questionId") or "").strip():
                        return True
                    if isinstance(candidate, Mapping) and isinstance(candidate.get("question"), Mapping):
                        if str(candidate["question"].get("questionId") or "").strip():
                            return True
                return False

            waiting = any(is_waiting(item) for item in observations if isinstance(item, Mapping))
            terminal_map = terminal if isinstance(terminal, Mapping) else {}
            terminal_gate = terminal_map.get("completionGate")
            restore_terminal = (
                terminal_map.get("restoreFailureTurnStop") is True
                or (
                    isinstance(terminal_gate, Mapping)
                    and str(terminal_gate.get("reason") or "") == "restore_failure_turn_stop"
                )
            )
            settled_externally = False
            if waiting and terminal_map.get("nextStep") not in {"cancelled", "interrupted"} and not restore_terminal:
                call_ids = self._native_decision["ids"]
                if not call_ids or set(call_ids) & self._state._native_pending(self.snapshot()):
                    return
                # An authenticated answer may settle this wait while the tool
                # returns. Keep its receipt and flush any steer in this turn.
                settled_externally = True
            retained_definitions = self._retained_tool_definitions(self.snapshot().get("messages", []))
            result: dict[str, Any] = {
                "observations": [
                    self._planner.native_result_observation(item, project_context_active=self._project_context_active,
                                                            retained_tool_definitions=retained_definitions)
                    for item in observations
                ]
            }
            if terminal is not None:
                result["terminal"] = {
                    key: terminal[key]
                    for key in (
                        "nextStep",
                        "completionGate",
                        "restoreFailureTurnStop",
                    )
                    if key in terminal
                }
            if not observations:
                result["status"] = str((terminal or {}).get("nextStep") or "not_executed")
            for call_id in (() if settled_externally else self._native_decision["ids"]):
                self._state.settle_native_call(
                    self._session_id,
                    binding=self._binding,
                    call_id=call_id,
                    content=json.dumps(result, ensure_ascii=False),
                )
            if not self._native_decision["ids"] and observations:
                self._state.append_native_user(
                    self._session_id,
                    binding=self._binding,
                    human=False,
                    message=(
                        "Runtime feedback (data, not user authorization): "
                        + json.dumps(result, ensure_ascii=False)
                    ),
                )
            self._native_decision = None
            if any(
                str((item.get("outcome") or {}).get("status") or item.get("status") or "")
                in {"failed", "denied", "rejected", "cancelled", "not_executed", "needs_user_action"}
                for item in observations if isinstance(item, Mapping)
                and isinstance(item.get("outcome", {}), Mapping)
            ):
                # Known reads can proceed after a bad proposal. After a tool
                # failed during execution, keep project-bound work fenced.
                admission_error = any(
                    isinstance(item.get("result"), Mapping)
                    and item["result"].get("code") == "planner_invalid_response"
                    for item in observations if isinstance(item, Mapping)
                )
                keep_scopes = ("validation", "delegation", "local_read")
                if admission_error:
                    keep_scopes += ("discovery", "project_read")
                self._settle_unexecuted("predecessor_not_successful", keep_scopes=keep_scopes)
        if terminal is not None:
            self._settle_unexecuted(str(terminal.get("nextStep") or "terminal"))
            self._append_terminal_runtime_notice(terminal)
        if self._queued_calls or self._parked_subagent_calls:
            return
        self.output_truncation.flush_feedback(self)
        while flush_steers and self._native_steers:
            steer = self._native_steers[0]
            input_id = steer["inputId"]
            source = classify_steer(input_id)
            if not steer["message"] and not steer["attachments"]:
                source = RuntimeInputSource.RUNTIME
            if terminal is not None and not input_id.startswith("question-answer:"):
                # The queue owns unsent inputs. Appending them here would expose
                # a paused steer to the next model request without its replay.
                self._native_steers.pop(0)
                continue
            if steer["message"] or not steer["authenticatedAnswer"]:
                history_index = self._state.append_native_user(
                    self._session_id,
                    binding=self._binding,
                    message=steer["message"] or "See attached materials.",
                    input_source=source,
                    input_id=input_id,
                    attachments=steer["attachments"],
                    source_text=steer["message"],
                )
                if steer["attachments"] and history_index is not None:
                    self.steer_attachments[history_index] = steer["attachments"]
            if answer := steer["authenticatedAnswer"]:
                self._append_question_answer(input_id.removeprefix("question-answer:"), answer, steer["attachments"])
            self._native_steers.pop(0)
            # A saved Question answer has its own durable handoff.
            if terminal is None and input_id and input_id not in self._consumed_steer_input_ids:
                if len(self._consumed_steer_input_ids) < 256:
                    self._consumed_steer_input_ids.append(input_id)
                    if self._on_steer_consumed is not None:
                        try:
                            self._on_steer_consumed([input_id])
                        except Exception:
                            pass  # Presentation failure cannot retract a durable consumption receipt.

    def _append_terminal_runtime_notice(self, terminal: Mapping[str, Any]) -> None:
        """Tell the next turn why this one stopped (turn-level runtime notice).

        Keep non-tool stop causes in durable native history as runtime feedback,
        separate from assistant-authored text and tool results.
        """
        if not self._binding or self._terminal_runtime_notice_written:
            return
        next_step = str(terminal.get("nextStep") or "").strip()
        planner_failure = terminal.get("plannerFailure")
        planner_failure_code = (
            str(planner_failure.get("code") or "").strip()
            if isinstance(planner_failure, Mapping) else ""
        )
        reason_map = {
            "interrupted": "turn_interrupted",
            "cancelled": "turn_interrupted",
            "context_compaction_required": "context_window_exceeded",
            "paused": "model_turn_budget_exhausted",
        }
        stop_reason = (
            "provider_request_failed" if planner_failure_code == "provider_request_failed"
            else reason_map.get(next_step)
        )
        if next_step == "loop_suppressed":
            from agent_general_no_progress import LOOP_REASONS
            loop_reason = str(terminal.get("reason") or "")
            if loop_reason in LOOP_REASONS:
                stop_reason = loop_reason
        if not stop_reason:
            return
        from runtime_error_schema import runtime_notice
        notice = runtime_notice(stop_reason)
        try:
            self._state.append_native_user(
                self._session_id,
                binding=self._binding,
                human=False,
                message=(
                    "Runtime feedback (data, not user authorization): "
                    + json.dumps(notice, ensure_ascii=False)
                ),
            )
            self._terminal_runtime_notice_written = True
        except Exception:
            # A failed annotation must never mask the terminal settlement.
            pass

    def queue_steers(self, items: Iterable[Mapping[str, Any]]) -> None:
        """Queue accepted steer messages for the next native provider request."""
        if not self._binding:
            return
        accepted = [
            {"inputId": str(item.get("inputId") or ""), "message": str(item.get("message") or ""),
             "authenticatedAnswer": str(item.get("authenticatedAnswer") or ""),
             "attachments": item.get("attachments") or []}
            for item in items
            if item.get("message") or item.get("attachments")
        ]
        if accepted:
            human = any(classify_steer(item["inputId"]).is_human or item["authenticatedAnswer"] for item in accepted)
            self._settle_unexecuted("user_steer" if human else "runtime_steer")
            self._native_steers.extend(accepted)

    def approval_seed(self, seed: dict[str, Any]) -> dict[str, Any]:
        """Attach the current validated native snapshot to an approval seed."""
        from dataclasses import asdict
        seed["_outputTruncation"] = asdict(self.output_truncation)
        if self._binding:
            snapshot = self._state.native_conversation(
                self._session_id,
                binding=self._binding,
            )
            if snapshot is not None:
                seed["_nativeConversation"] = snapshot
                seed["_nativeAdmittedToolNames"] = sorted(self.admitted_tool_names or ())
                if call_id := self.active_call_id:
                    seed["_nativeCallId"] = call_id
        return seed

    def messages(self) -> list[dict[str, Any]]:
        return self.request_history()[0]

    def request_history(self) -> tuple[list[dict[str, Any]], int]:
        """Return copied provider history and the current turn's starting index.

        Assembly-time normalization runs first: any orphaned tool call (crash,
        legacy session, other interruption) settles idempotently so the
        outgoing request always carries paired tool results.
        """
        if not self._binding:
            return [], 0
        self._state.normalize_native_pending(self._session_id, binding=self._binding)
        snapshot = self._state.native_conversation(
            self._session_id,
            binding=self._binding,
        )
        return (snapshot["messages"], snapshot["activeTurnStart"]) if snapshot else ([], 0)
