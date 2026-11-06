"""Output-limit continuation state for one existing native runtime turn."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any

from credential_redaction import redact_credential_text, redact_persistence_text

CONTINUE_MESSAGE = "上条回复因长度上限中断，请从断点直接继续，不要重复已写内容。"
OUTPUT_LIMIT_NOTICE = "输出长度上限：已续写 3 次，回复仍被截断，本轮已停止。"


@dataclass
class OutputTruncation:
    parts: list[str] = field(default_factory=list)
    continuations: int = 0
    max_output_tokens: int | None = None
    pending_calls: list[dict[str, Any]] = field(default_factory=list)
    discarded: list[dict[str, Any]] = field(default_factory=list)
    notices: list[dict[str, Any]] = field(default_factory=list)

    def accept(self, receipt, metadata, turn) -> str:
        """Return tools/continue/stop; never execute or reconstruct a tool."""
        calls = receipt.get("tool_calls") or []
        discarded = metadata.get("discardedToolCalls") or []
        text = receipt.get("content") or ""
        if calls or discarded:
            self.pending_calls = list(calls)
            self.discarded = [self.safe_issue(item) for item in discarded]
            self.notices.extend(self.discarded)
            if calls or text.strip():
                turn.admit(receipt, tool_names=metadata.get("toolNames", []))
            if not calls:
                self.flush_feedback(turn)
            return "tools" if calls else "continue"
        if text.strip():
            turn.admit(receipt, tool_names=())
            self.parts.append(text)
            if self.continuations >= 3:
                return OUTPUT_LIMIT_NOTICE
            self.continuations += 1
            turn.append_hidden(CONTINUE_MESSAGE)
            return "continue"
        current = metadata.get("maxOutputTokens")
        maximum = metadata.get("modelMaxOutputTokens")
        if not isinstance(current, int) or isinstance(current, bool) or current <= 0:
            return "输出长度上限：未生成正文，当前输出额度未知，无法安全提高额度。"
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
            return "输出长度上限：未生成正文，模型输出上限未知，无法安全提高额度。"
        if current >= maximum:
            return "输出长度上限：已达到该模型的最大输出额度，仍未生成正文，本轮已停止。"
        self.max_output_tokens = min(maximum, current * 2)
        return "continue"

    @staticmethod
    def safe_issue(item):
        # Discarded argument fragments are data, never parsed or repaired.
        return {"id": str(item.get("id") or ""), "name": str(item.get("name") or "unknown"),
                "arguments": redact_persistence_text(str(item.get("arguments") or "")),
                "reason": "输出长度上限，工具调用块未完整结束，未执行。",
                "suggestion": "将参数或操作拆成更小的工具调用后重发。"}

    def flush_feedback(self, turn):
        if not self.pending_calls and not self.discarded:
            return
        snapshot = turn.snapshot()
        if turn._state._native_pending(snapshot):
            return
        results = {m["tool_call_id"]: m["content"] for m in snapshot.get("messages", []) if m.get("role") == "tool"}
        completed = []
        for call in self.pending_calls:
            function = call.get("function") if isinstance(call, Mapping) else None
            function = function if isinstance(function, Mapping) else {}
            call_id = str(call.get("id") or "") if isinstance(call, Mapping) else ""
            name, arguments = self._call_identity(function)
            raw_result = results.get(call_id, "未执行")
            status, reason, suggestion, safe_result = self._settled_result(raw_result)
            item = {"id": call_id, "name": name, "arguments": arguments,
                    "status": status, "result": safe_result}
            if status in {"failed", "error", "denied", "rejected"}:
                item.update(reason=reason, suggestion=suggestion)
                self.notices.append({"id": call_id, "name": name, "arguments": arguments,
                                     "status": status, "reason": reason, "suggestion": suggestion})
            completed.append(item)
        turn.append_hidden("本次输出因长度上限截断。已结算调用及结果、未执行调用如下；请拆成更小的调用重发："
                           + json.dumps({"settledCalls": completed, "discardedToolCalls": self.discarded}, ensure_ascii=False))
        self.pending_calls.clear()
        self.discarded.clear()

    @classmethod
    def _call_identity(cls, function: Mapping[str, Any]) -> tuple[str, str]:
        name = str(function.get("name") or "unknown")
        raw_arguments = function.get("arguments")
        if name == "call_tool" and isinstance(raw_arguments, str):
            try:
                decoded = json.loads(raw_arguments)
            except (TypeError, ValueError):
                decoded = None
            if (isinstance(decoded, Mapping) and isinstance(decoded.get("tool"), str)
                    and isinstance(decoded.get("arguments"), Mapping)):
                name = decoded["tool"]
                raw_arguments = decoded["arguments"]
        return name, cls._safe_arguments(raw_arguments)

    @staticmethod
    def _safe_arguments(value: object) -> str:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                text = "{}"
        return redact_credential_text(text)[:4_000]

    @staticmethod
    def _safe_text(value: object, default: str) -> str:
        if isinstance(value, list):
            value = "；".join(str(item) for item in value if isinstance(item, (str, int, float)))
        elif isinstance(value, Mapping):
            value = value.get("summary") or value.get("reason") or value.get("message") or ""
        text = redact_credential_text(str(value or "")).strip()
        lowered = text.lower()
        if any(marker in lowered for marker in ("traceback", "stack trace", "exception:", "\n  file \"")):
            return default
        return text[:1_000] or default

    @classmethod
    def _settled_result(cls, raw: object) -> tuple[str, str, str, object]:
        """Project settled tool feedback without exposing exception dumps."""
        if not isinstance(raw, str):
            raw = str(raw)
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return "completed", "", "", raw
        if not isinstance(payload, Mapping):
            return "completed", "", "", payload
        observations = payload.get("observations")
        observation = observations[0] if isinstance(observations, list) and observations and isinstance(observations[0], Mapping) else {}
        outcome = observation.get("outcome") if isinstance(observation.get("outcome"), Mapping) else {}
        if not outcome:
            outcome = payload.get("outcome") if isinstance(payload.get("outcome"), Mapping) else {}
        error = outcome.get("error") if isinstance(outcome.get("error"), Mapping) else {}
        status = str(outcome.get("status") or payload.get("status") or "completed").lower()
        failed = status in {"failed", "error", "denied", "rejected"} or payload.get("ok") is False
        if not failed:
            return status, "", "", payload

        observation_text = str(observation.get("observation") or "")
        def observed_field(*keys: str) -> str:
            for key in keys:
                match = re.search(rf"(?:^|;\s*){re.escape(key)}=([^;]+)", observation_text)
                if match:
                    return match.group(1).strip()
            return ""

        reason = cls._safe_text(
            error.get("likelyCauses") or observed_field("likelyCauses"),
            "工具执行失败，具体原因未提供。",
        )
        suggestion = cls._safe_text(
            error.get("nextActions") or observed_field("nextActions"),
            "根据失败原因修正参数或操作后再重试。",
        )
        # Do not propagate arbitrary error prose: executor exception strings
        # can be useful in logs but are not a user-facing diagnostic contract.
        safe_result = {}
        safe_status = outcome.get("status") or payload.get("status")
        if isinstance(safe_status, str) and safe_status:
            safe_result["status"] = safe_status
        safe_code = outcome.get("errorCode") or payload.get("errorCode") or payload.get("code")
        if isinstance(safe_code, str) and safe_code:
            safe_result["errorCode"] = safe_code
        safe_result["reason"] = reason
        safe_result["suggestion"] = suggestion
        return status, reason, suggestion, safe_result or {"status": status, "reason": reason, "suggestion": suggestion}

    def finish(self, plan):
        if not self.parts:
            return plan
        result = dict(plan)
        separator = "\n\n" if plan.get("nextStep") == "output_length_limit" else ""
        text = "".join(self.parts) + separator + str(plan.get("reply") or "")
        result.update(reply=text, summary=text)
        if plan.get("publicMessages"):
            messages = list(plan["publicMessages"])
            finals = [m for m in messages if m.get("phase") != "commentary"]
            result["publicMessages"] = [m for m in messages if m.get("phase") == "commentary"] + [
                {"id": (finals[-1]["id"] if finals else "output-continuation-final"), "phase": "final_answer", "text": text}]
        self.parts.clear()
        return result
