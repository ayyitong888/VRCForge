"""Bounded runtime adapters for provider-specific model protocols.

Each adapter owns one request-scoped SDK client.  It may contact only the
configured provider base URL, authenticates with the caller-validated bearer
credential, and never executes a returned tool call.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from model_provider_adapters import validate_provider_api_key


class ModelProviderAdapter(Protocol):
    """Narrow provider runtime contract; callers retain planning authority."""

    def get_models(self) -> list[dict[str, Any]]: ...

    def send_request(self, request: "ProviderRuntimeRequest") -> "ProviderRuntimeResponse": ...

    def parse_response(self, response: Any) -> "ProviderRuntimeResponse": ...

    def parse_tool_call(self, item: Any) -> str: ...


@dataclass(frozen=True)
class ProviderRuntimeRequest:
    model: str
    prompt: str
    instructions: str
    reasoning_effort: str = ""
    max_output_tokens: int | None = None
    reference_image_paths: tuple[str, ...] = ()
    stream_callback: Callable[[str], None] | None = None
    # Planner is the production default.  Provider health probes are explicitly
    # tool-free so they cannot be mistaken for an Agent Gateway action request.
    mode: str = "planner"
    structured_output: bool = False
    cancel_event: Any | None = None
    stream_activity_callback: Callable[[dict[str, Any]], None] | None = None


@dataclass(frozen=True)
class ProviderRuntimeResponse:
    text: str
    usage: dict[str, Any] = field(default_factory=dict)
    reasoning_summary: list[Any] = field(default_factory=list)


_TOOL_NAME = "vrcforge_plan_action"
_TOOL_SCHEMA = {
    "type": "function",
    "name": _TOOL_NAME,
    "description": "Return one Agent Gateway planner action. This tool never executes actions.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["reply", "skill", "shell", "enter_execution", "write"],
            },
            "summary": {"type": "string"},
            "reply": {"type": "string"},
            "skill_tool": {"type": "string"},
            "skill_params": {"type": "object", "additionalProperties": True},
            "shell_command": {"type": "string"},
            "shell_params": {"type": "object", "additionalProperties": True},
            "write_tool": {"type": "string"},
            "write_params": {"type": "object", "additionalProperties": True},
            "correction_for_action_id": {"type": "string"},
            "completion_claim": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "satisfied": {"type": "boolean"},
                    "evidence_action_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


class _ResponsesRequestBase:
    """Provider-neutral OpenAI Responses transport for one configured origin."""

    _provider_label = "OpenAI-compatible Responses"

    def __init__(
        self, *, api_key: str, base_url: str, client_factory: Callable[..., Any] | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._api_key = validate_provider_api_key(api_key)
        self._base_url = str(base_url or "").rstrip("/")
        if not self._base_url:
            raise RuntimeError(f"{self._provider_label} requires a Base URL.")
        if max_retries is not None and (isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0):
            raise ValueError("LLM SDK max retries must be a non-negative integer.")
        self._client_factory = client_factory
        self._max_retries = max_retries

    def get_models(self) -> list[dict[str, Any]]:
        return []

    def _client(self) -> Any:
        kwargs: dict[str, Any] = {"api_key": self._api_key, "base_url": self._base_url}
        if self._max_retries is not None:
            kwargs["max_retries"] = self._max_retries
        if self._client_factory is not None:
            return self._client_factory(**kwargs)
        try:
            from openai import OpenAI
            import httpx
        except ImportError as exc:
            raise RuntimeError("The 'openai' package is not installed. Run pip install -r requirements.txt and try again.") from exc
        # Explicit bounded transport policy; OpenAI's defaults permit a
        # 600-second read and implicit retries, which is unsafe for interactive
        # planner turns and reasoning-only SSE streams.
        kwargs["http_client"] = httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
        )
        return OpenAI(**kwargs)

    def _empty_completion_error(self) -> str:
        return f"{self._provider_label} returned no message or planner action."

    def _validate_model(self, model: str) -> None:
        if not str(model).strip():
            raise RuntimeError(f"{self._provider_label} requires a model.")

    def send_request(self, request: ProviderRuntimeRequest) -> ProviderRuntimeResponse:
        self._validate_model(request.model)
        if request.reference_image_paths:
            raise RuntimeError(f"{self._provider_label} does not support reference images or files in this planner lane.")
        if request.mode not in {"planner", "probe"}:
            raise ValueError("Responses request mode is invalid.")
        if request.structured_output and request.mode != "probe":
            raise ValueError("Structured output is only supported for a provider probe.")
        payload: dict[str, Any] = {
            "model": request.model,
            "input": request.prompt,
            "instructions": request.instructions,
            "store": False,
        }
        if request.mode == "planner":
            payload["tools"] = [_TOOL_SCHEMA]
        elif request.structured_output:
            payload["text"] = {"format": {"type": "json_object"}}
        if request.reasoning_effort:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        if request.max_output_tokens is not None:
            payload["max_output_tokens"] = request.max_output_tokens
        client = self._client()
        watcher_stop = threading.Event()
        cancel_watcher: threading.Thread | None = None
        if request.cancel_event is not None:
            def close_on_cancel() -> None:
                while not watcher_stop.is_set():
                    if request.cancel_event.wait(0.05):
                        close = getattr(client, "close", None)
                        if callable(close):
                            close()
                        return
            cancel_watcher = threading.Thread(target=close_on_cancel, name="vrcforge-responses-cancel", daemon=True)
            cancel_watcher.start()
        try:
            if request.stream_callback is not None:
                    return self._consume_stream(
                    client.responses.create(**payload, stream=True), request.stream_callback, planner_mode=request.mode == "planner", activity_callback=request.stream_activity_callback
                )
            for attempt in range(2):
                try:
                    return self.parse_response(
                        client.responses.create(**payload),
                        planner_mode=request.mode == "planner",
                    )
                except RuntimeError as exc:
                    if attempt != 0 or str(exc) != self._empty_completion_error():
                        raise
            raise AssertionError("bounded Responses retry loop exhausted unexpectedly")
        finally:
            watcher_stop.set()
            if cancel_watcher is not None:
                cancel_watcher.join(0.2)
            close = getattr(client, "close", None)
            if callable(close):
                close()


class _ResponsesProtocolAdapter(_ResponsesRequestBase):
    """Shared Responses parsing and streaming implementation."""

    _provider_label = "OpenAI-compatible Responses"

    def get_models(self) -> list[dict[str, Any]]:
        return []

    def _validate_model(self, model: str) -> None:
        if not str(model).strip():
            raise RuntimeError(f"{self._provider_label} requires a model.")

    def parse_response(self, response: Any, *, planner_mode: bool = True) -> ProviderRuntimeResponse:
        output = _as_list(_value(response, "output"))
        tool_calls = [item for item in output if str(_value(item, "type") or "") == "function_call"]
        if tool_calls:
            if not planner_mode:
                raise RuntimeError(f"{self._provider_label} probe returned an unexpected tool call.")
            unexpected = [
                item
                for item in output
                if str(_value(item, "type") or "")
                not in {"reasoning", "message", "function_call"}
            ]
            # DeepSeek may emit one explanatory message before the authoritative
            # planner function call.  Ignore that text, but still fail closed on
            # multiple calls or any unknown output item.
            if len(tool_calls) != 1 or unexpected:
                raise RuntimeError(f"{self._provider_label} returned an invalid planner tool call.")
            return ProviderRuntimeResponse(text=self.parse_tool_call(tool_calls[0]), usage=_usage(response), reasoning_summary=_summaries(output))
        text = str(_value(response, "output_text") or "")
        if not text:
            text = "".join(_message_text(item) for item in output if str(_value(item, "type") or "") == "message")
        if not text:
            raise RuntimeError(self._empty_completion_error())
        return ProviderRuntimeResponse(text=text, usage=_usage(response), reasoning_summary=_summaries(output))

    def parse_tool_call(self, item: Any) -> str:
        if str(_value(item, "name") or "") != _TOOL_NAME:
            raise RuntimeError(f"{self._provider_label} returned an unknown planner tool.")
        raw = _value(item, "arguments")
        if not isinstance(raw, str):
            raise RuntimeError(f"{self._provider_label} planner arguments are invalid.")
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{self._provider_label} planner arguments are invalid.") from exc
        if not isinstance(arguments, dict):
            raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        allowed = {
            "action",
            "summary",
            "reply",
            "skill_tool",
            "skill_params",
            "shell_command",
            "shell_params",
            "write_tool",
            "write_params",
            "correction_for_action_id",
            "completion_claim",
        }
        if set(arguments) - allowed:
            raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        action = arguments.get("action")
        if not isinstance(action, str) or action not in {
            "reply",
            "skill",
            "shell",
            "enter_execution",
            "write",
        }:
            raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        for text_key in (
            "summary",
            "reply",
            "skill_tool",
            "shell_command",
            "write_tool",
            "correction_for_action_id",
        ):
            if text_key in arguments and not isinstance(arguments[text_key], str):
                raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        for params_key in ("skill_params", "shell_params", "write_params"):
            if params_key in arguments and not isinstance(arguments[params_key], dict):
                raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        if "completion_claim" in arguments:
            claim = arguments["completion_claim"]
            if not isinstance(claim, dict) or set(claim) - {"satisfied", "evidence_action_ids"}:
                raise RuntimeError(f"{self._provider_label} planner action is invalid.")
            if "satisfied" in claim and not isinstance(claim["satisfied"], bool):
                raise RuntimeError(f"{self._provider_label} planner action is invalid.")
            evidence_ids = claim.get("evidence_action_ids")
            if evidence_ids is not None and (
                not isinstance(evidence_ids, list)
                or not all(isinstance(item, str) for item in evidence_ids)
            ):
                raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        if action == "reply" and not (str(arguments.get("reply") or "").strip() or str(arguments.get("summary") or "").strip()):
            raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        if action == "skill" and not str(arguments.get("skill_tool") or "").strip():
            raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        if action == "shell" and not str(arguments.get("shell_command") or "").strip():
            raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        if action == "write" and not str(arguments.get("write_tool") or "").strip():
            raise RuntimeError(f"{self._provider_label} planner action is invalid.")
        return json.dumps(arguments, ensure_ascii=False)

    def _consume_stream(self, stream: Any, callback: Callable[[str], None], *, planner_mode: bool, activity_callback: Callable[[dict[str, Any]], None] | None = None) -> ProviderRuntimeResponse:
        text_parts: list[str] = []
        tool_args: list[str] = []
        tool_item: Any = None
        usage: dict[str, Any] = {}
        final_seen = False
        for event in stream:
            event_type = str(_value(event, "type") or "")
            if "reasoning" in event_type or "summary" in event_type:
                if activity_callback is not None:
                    activity_callback({"kind": "reasoning_activity"})
            if event_type in {"response.failed", "response.incomplete"}:
                raise RuntimeError(f"{self._provider_label} stream did not complete.")
            if event_type == "response.output_text.delta":
                delta = str(_value(event, "delta") or "")
                if delta:
                    text_parts.append(delta)
                    callback(delta)
            elif event_type in {"response.function_call_arguments.delta", "response.output_item.added"}:
                item = _value(event, "item")
                if item is not None and str(_value(item, "type") or "") == "function_call":
                    tool_item = item
                delta = _value(event, "delta")
                if isinstance(delta, str):
                    tool_args.append(delta)
            elif event_type == "response.completed":
                final_seen = True
                final_response = _value(event, "response") or event
                usage = _usage(final_response)
                final_output = _as_list(_value(final_response, "output"))
                if final_output:
                    parsed = self.parse_response(final_response, planner_mode=planner_mode)
                    return ProviderRuntimeResponse(
                        text=parsed.text,
                        usage=parsed.usage or usage,
                        reasoning_summary=parsed.reasoning_summary,
                    )
                if tool_item is not None:
                    if not planner_mode:
                        raise RuntimeError(f"{self._provider_label} probe returned an unexpected tool call.")
                    assembled = {"type": "function_call", "name": _value(tool_item, "name"), "arguments": "".join(tool_args) or _value(tool_item, "arguments")}
                    return ProviderRuntimeResponse(text=self.parse_tool_call(assembled), usage=usage, reasoning_summary=_summaries(_as_list(_value(final_response, "output"))))
                final_text = str(_value(final_response, "output_text") or "")
                completed_text = final_text or "".join(text_parts)
                if not completed_text:
                    raise RuntimeError(f"{self._provider_label} stream completed without a message or planner action.")
                return ProviderRuntimeResponse(text=completed_text, usage=usage, reasoning_summary=_summaries(_as_list(_value(final_response, "output"))))
        if not final_seen:
            raise RuntimeError(f"{self._provider_label} stream ended before response.completed.")
        raise RuntimeError(f"{self._provider_label} stream returned no response.")


class OpenAIResponsesAdapter(_ResponsesProtocolAdapter):
    """Generic Responses adapter for OpenAI and explicitly configured sites."""

    _provider_label = "OpenAI-compatible Responses"


class DeepSeekResponsesAdapter(_ResponsesProtocolAdapter):
    """Responses adapter for the exact public DeepSeek V4 GA models."""

    _provider_label = "DeepSeek Responses"

    def get_models(self) -> list[dict[str, Any]]:
        return [
            {"id": "deepseek-v4-flash", "supportedApiTypes": ["responses", "messages", "chat_completions"]},
            {"id": "deepseek-v4-pro", "supportedApiTypes": ["responses", "messages", "chat_completions"]},
        ]

    def _validate_model(self, model: str) -> None:
        if model not in {"deepseek-v4-flash", "deepseek-v4-pro"}:
            raise RuntimeError("DeepSeek Responses requires an exact DeepSeek V4 GA model ID.")


def _value(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _message_text(item: Any) -> str:
    pieces: list[str] = []
    for content in _as_list(_value(item, "content")):
        if str(_value(content, "type") or "") in {"output_text", "text"}:
            pieces.append(str(_value(content, "text") or ""))
    return "".join(pieces)


def _summaries(output: list[Any]) -> list[Any]:
    return [_value(item, "summary") for item in output if str(_value(item, "type") or "") == "reasoning" and _value(item, "summary")]


def _usage(response: Any) -> dict[str, Any]:
    usage = _value(response, "usage") or {}
    output_details = _value(usage, "output_tokens_details") or {}
    return {
        "input_tokens": _value(usage, "input_tokens"),
        "output_tokens": _value(usage, "output_tokens"),
        "total_tokens": _value(usage, "total_tokens"),
        "reasoning_tokens": _value(usage, "reasoning_tokens") or _value(output_details, "reasoning_tokens"),
    }
