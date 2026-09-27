"""Small adapter for one native provider turn.

The adapter owns only per-turn bookkeeping (receipt ids, observation cursor,
and queued steer text). Conversation storage and validation remain owned by
AgentRuntimeSessionState.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agent_runtime_session_state import AgentRuntimeSessionState
    from runtime_planner_service import RuntimePlannerService


class NativeRuntimeTurn:
    """Borrow the gateway's session state for one native provider turn."""

    @staticmethod
    def cancel_question(
        state: "AgentRuntimeSessionState",
        seed: Mapping[str, Any],
    ) -> list[str]:
        """Settle only ask-user calls from one validated runtime seed."""
        session_id = str(seed.get("sessionId") or "").strip()
        snapshot = seed.get("_nativeConversation")
        if not session_id or not isinstance(snapshot, Mapping):
            return []
        binding = str(snapshot.get("binding") or "").strip()
        if not binding:
            return []
        restored = state.restore_native_conversation(
            session_id,
            binding=binding,
            snapshot=copy.deepcopy(dict(snapshot)),
        )
        question_ids: set[str] = set()
        for message in snapshot.get("messages", []):
            if not isinstance(message, Mapping) or message.get("role") != "assistant":
                continue
            for tool_call in message.get("tool_calls", []) or []:
                if not isinstance(tool_call, Mapping):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, Mapping) or function.get("name") != "vrcforge_ask_user":
                    continue
                call_id = str(tool_call.get("id") or "").strip()
                if call_id:
                    question_ids.add(call_id)
        cancelled: list[str] = []
        for call_id in sorted(question_ids & state._native_pending(restored)):
            state.settle_native_call(
                session_id,
                binding=binding,
                call_id=call_id,
                content=json.dumps(
                    {"status": "cancelled", "outcome": {"status": "cancelled", "summary": "Question cancelled by user stop."}},
                    ensure_ascii=False,
                ),
            )
            cancelled.append(call_id)
        if cancelled:
            # Stopping a waiting question also stops later proposals in its same
            # receipt, but cannot touch calls from a newer unrelated turn.
            pending = state._native_pending(snapshot) & state._native_pending(restored)
            for call_id in sorted(pending - set(cancelled)):
                state.settle_native_call(session_id, binding=binding, call_id=call_id,
                    content=json.dumps({"status": "not_executed", "reason": "question_cancelled", "observations": []}))
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
    ) -> None:
        self._state = owner
        self._planner = planner
        self._session_id = session_id
        self._turn_id = turn_id
        self._client_turn_id = client_turn_id
        self._binding = binding
        self._loop_state = loop_state
        self._native_decision: dict[str, Any] | None = None
        self._native_steers: list[str] = []
        self._queued_calls: list[dict[str, Any]] = []
        self.admitted_tool_names: set[str] | None = None
        self.compaction_attempted = False
        self.compaction: dict[str, Any] | None = None
        # The gateway owns reader scope and lifecycle; the turn owns no store.
        self.retain_recovery = retain_recovery
        state = self._state
        continuation_context = continuation_context or {}
        continuation_completion = continuation_completion or {}
        saved_native = continuation_context.get("_nativeConversation")
        if not binding and not isinstance(saved_native, Mapping):
            return
        if isinstance(saved_native, Mapping):
            saved_binding = str(saved_native.get("binding") or "")
            restored = state.restore_native_conversation(
                session_id,
                binding=saved_binding,
                snapshot=copy.deepcopy(dict(saved_native)),
            )
            if restored["turnId"] != saved_native["turnId"]:
                raise ValueError("Native continuation belongs to an earlier user turn.")
            pending_ids = state._native_pending(saved_native) & state._native_pending(restored)
            if not pending_ids:
                raise ValueError("Native continuation has already been settled.")
            if pending_ids:
                # The first still-pending call is the only dispatched action;
                # later calls remain proposals in the immutable assistant receipt.
                pending_calls = [call for item in restored["messages"]
                                 for call in item.get("tool_calls", []) if call["id"] in pending_ids]
                active_id = pending_calls[0]["id"]
                self._queued_calls = copy.deepcopy(pending_calls[1:])
                admitted_names = continuation_context.get("_nativeAdmittedToolNames", [])
                if not isinstance(admitted_names, list) or any(not isinstance(name, str) or not name for name in admitted_names):
                    raise ValueError("Invalid native receipt tool scope.")
                self.admitted_tool_names = set(admitted_names)
                if self._queued_calls and not self.admitted_tool_names:
                    raise ValueError("Native continuation tool scope is missing.")
                pending_ids = {active_id}
                resumed_result = {
                    "status": continuation_completion.get("status") or "completed",
                    "observations": [
                        planner.native_result_observation(item)
                        for item in loop_state
                        if item.get("actionId") == continuation_context.get("requestedActionId")
                        or item is continuation_observation
                    ],
                    "actionId": continuation_context.get("requestedActionId") or "",
                }
                for call_id in sorted(pending_ids):
                    state.settle_native_call(
                        session_id,
                        binding=saved_binding,
                        call_id=call_id,
                        content=json.dumps(resumed_result, ensure_ascii=False),
                    )
            if binding != saved_binding:
                raise ValueError("Provider settings changed. The action result was saved; send a new message to continue with the selected provider.")
        else:
            state.begin_native_turn(
                session_id,
                binding=binding,
                turn_id=turn_id,
                message=message,
                initial_history=initial_history,
            )

    def snapshot(self) -> dict[str, Any]:
        return self._state.native_conversation(self._session_id, binding=self._binding) or {}

    def recoveries(self) -> list[dict[str, Any]]:
        return self.snapshot().get("compactionRecovery", [])

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

    def replace_completed_prefix(self, expected_snapshot: dict[str, Any], summary: str,
                                 *, recovery: dict[str, Any] | None = None) -> None:
        # Check Stop and compare-and-replace under the same existing state lock.
        with self._state.shared_state_lock:
            if self.cancelled():
                raise ValueError("native compaction cancelled")
            self._state.replace_native_completed_prefix(
                self._session_id, binding=self._binding,
                expected_snapshot=expected_snapshot, summary=summary, recovery=recovery,
            )

    def admit(self, receipt: dict[str, Any], *, tool_names: Iterable[str] = ()) -> None:
        """Record one provider receipt; execution remains serial in the existing dispatcher."""
        if not self._binding:
            return
        self._state.append_native_assistant(self._session_id, binding=self._binding, message=receipt)
        self._queued_calls = copy.deepcopy(receipt.get("tool_calls") or [])
        self.admitted_tool_names = set(tool_names)
        self._native_decision = None if self._queued_calls else {"ids": [], "cursor": len(self._loop_state)}

    @property
    def has_queued_calls(self) -> bool:
        return bool(self._queued_calls)

    def next_receipt(self) -> dict[str, Any]:
        if self._native_decision is not None or not self._queued_calls:
            raise ValueError("Native call cannot dispatch before its predecessor settles.")
        call = self._queued_calls.pop(0)
        self._native_decision = {"ids": [call["id"]], "cursor": len(self._loop_state)}
        return {"role": "assistant", "content": None, "tool_calls": [copy.deepcopy(call)]}

    def reject_queued_receipt(self) -> None:
        """Bind one whole-receipt admission failure to every unexecuted call."""
        self._native_decision = {"ids": [call["id"] for call in self._queued_calls], "cursor": len(self._loop_state)}
        self._queued_calls.clear()

    def _settle_unexecuted(self, reason: str) -> None:
        for call in self._queued_calls:
            self._state.settle_native_call(self._session_id, binding=self._binding, call_id=call["id"],
                content=json.dumps({"status": "not_executed", "reason": reason, "observations": []}))
        self._queued_calls.clear()

    def settle(self, terminal: Mapping[str, Any] | None = None) -> None:
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
                status = str(item.get("status") or "")
                outcome = item.get("outcome")
                outcome_status = (
                    str(outcome.get("status") or "")
                    if isinstance(outcome, Mapping)
                    else ""
                )
                if status in waiting_statuses or outcome_status in waiting_statuses:
                    return True
                if status != "needs_user_action" and outcome_status != "needs_user_action":
                    return False
                # Only the explicit question tool owns a user-answer wait.
                # Other needs_user_action outcomes (for example a scope
                # denial) are terminal and must not block the next turn.
                if str(item.get("tool") or "") != "vrcforge_ask_user":
                    return False
                for candidate in (item.get("result"), item.get("resultRead"), outcome):
                    if isinstance(candidate, Mapping) and str(candidate.get("questionId") or "").strip():
                        return True
                    if isinstance(candidate, Mapping) and isinstance(candidate.get("question"), Mapping):
                        if str(candidate["question"].get("questionId") or "").strip():
                            return True
                return False

            waiting = any(is_waiting(item) for item in observations if isinstance(item, Mapping))
            if waiting and (terminal or {}).get("nextStep") not in {"cancelled", "interrupted"}:
                return
            result: dict[str, Any] = {
                "observations": [
                    self._planner.native_result_observation(item)
                    for item in observations
                ]
            }
            if terminal is not None:
                result["terminal"] = {
                    key: terminal[key]
                    for key in (
                        "nextStep",
                        "completionGate",
                    )
                    if key in terminal
                }
            if not observations:
                result["status"] = str((terminal or {}).get("nextStep") or "not_executed")
            for call_id in self._native_decision["ids"]:
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
                    message=(
                        "Runtime feedback (data, not user authorization): "
                        + json.dumps(result, ensure_ascii=False)
                    ),
                )
            self._native_decision = None
        if terminal is not None:
            self._settle_unexecuted(str(terminal.get("nextStep") or "terminal"))
        if self._queued_calls:
            return
        for steer in self._native_steers:
            self._state.append_native_user(
                self._session_id,
                binding=self._binding,
                message=steer,
            )
        self._native_steers.clear()

    def queue_steers(self, items: Iterable[Mapping[str, Any]]) -> None:
        """Queue accepted steer messages for the next native provider request."""
        if not self._binding:
            return
        accepted = list(items)
        if any(str(item.get("message") or "") for item in accepted):
            self._settle_unexecuted("user_steer")
        self._native_steers.extend(
            str(item.get("message") or "")
            for item in accepted
            if str(item.get("message") or "")
        )

    def approval_seed(self, seed: dict[str, Any]) -> dict[str, Any]:
        """Attach the current validated native snapshot to an approval seed."""
        if self._binding:
            snapshot = self._state.native_conversation(
                self._session_id,
                binding=self._binding,
            )
            if snapshot is not None:
                seed["_nativeConversation"] = snapshot
                seed["_nativeAdmittedToolNames"] = sorted(self.admitted_tool_names or ())
        return seed

    def messages(self) -> list[dict[str, Any]]:
        """Return a copy suitable for the next native provider request."""
        if not self._binding:
            return []
        snapshot = self._state.native_conversation(
            self._session_id,
            binding=self._binding,
        )
        return copy.deepcopy(snapshot.get("messages", [])) if snapshot else []
