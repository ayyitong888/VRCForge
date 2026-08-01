"""Bounded runtime adapters for provider-specific model protocols.

Each adapter owns one request-scoped SDK client.  It may contact only the
configured provider base URL, authenticates with the caller-validated bearer
credential, and never executes a returned tool call.
"""

from __future__ import annotations

import json
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
            "action": {"type": "string", "enum": ["reply", "skill", "shell"]},
            "summary": {"type": "string"},
            "reply": {"type": "string"},
            "skill_tool": {"type": "string"},
            "skill_params": {"type": "object"},
            "shell_command": {"type": "string"},
        },
    },
}


class DeepSeekResponsesAdapter:
    """Responses adapter for the exact public ``deepseek-v4-flash`` model."""

    def __init__(
        self, *, api_key: str, base_url: str, client_factory: Callable[..., Any] | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._api_key = validate_provider_api_key(api_key)
        self._base_url = str(base_url or "").rstrip("/")
        if not self._base_url:
            raise RuntimeError("DeepSeek Responses requires a Base URL.")
        if max_retries is not None and (isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0):
            raise ValueError("LLM SDK max retries must be a non-negative integer.")
        self._client_factory = client_factory
        self._max_retries = max_retries

    def get_models(self) -> list[dict[str, Any]]:
        return [
            {"id": "deepseek-v4-flash", "supportedApiTypes": ["responses", "chat_completions"]},
            {"id": "deepseek-v4-pro", "supportedApiTypes": ["chat_completions"]},
        ]

    def _client(self) -> Any:
        kwargs: dict[str, Any] = {"api_key": self._api_key, "base_url": self._base_url}
        if self._max_retries is not None:
            kwargs["max_retries"] = self._max_retries
        if self._client_factory is not None:
            return self._client_factory(**kwargs)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The 'openai' package is not installed. Run pip install -r requirements.txt and try again.") from exc
        return OpenAI(**kwargs)

    def send_request(self, request: ProviderRuntimeRequest) -> ProviderRuntimeResponse:
        if request.model != "deepseek-v4-flash":
            raise RuntimeError("DeepSeek Responses is only available for deepseek-v4-flash.")
        if request.reference_image_paths:
            raise RuntimeError("DeepSeek Responses does not support reference images or files.")
        if request.mode not in {"planner", "probe"}:
            raise ValueError("DeepSeek Responses request mode is invalid.")
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
        if request.stream_callback is not None:
            return self._consume_stream(
                client.responses.create(**payload, stream=True), request.stream_callback, planner_mode=request.mode == "planner"
            )
        return self.parse_response(client.responses.create(**payload), planner_mode=request.mode == "planner")

    def parse_response(self, response: Any, *, planner_mode: bool = True) -> ProviderRuntimeResponse:
        output = _as_list(_value(response, "output"))
        tool_calls = [item for item in output if str(_value(item, "type") or "") == "function_call"]
        if tool_calls:
            if not planner_mode:
                raise RuntimeError("DeepSeek Responses probe returned an unexpected tool call.")
            non_reasoning = [item for item in output if str(_value(item, "type") or "") != "reasoning"]
            if len(tool_calls) != 1 or len(non_reasoning) != 1:
                raise RuntimeError("DeepSeek Responses returned an invalid planner tool call.")
            return ProviderRuntimeResponse(text=self.parse_tool_call(tool_calls[0]), usage=_usage(response), reasoning_summary=_summaries(output))
        text = str(_value(response, "output_text") or "")
        if not text:
            text = "".join(_message_text(item) for item in output if str(_value(item, "type") or "") == "message")
        if not text:
            raise RuntimeError("DeepSeek Responses returned no message or planner action.")
        return ProviderRuntimeResponse(text=text, usage=_usage(response), reasoning_summary=_summaries(output))

    def parse_tool_call(self, item: Any) -> str:
        if str(_value(item, "name") or "") != _TOOL_NAME:
            raise RuntimeError("DeepSeek Responses returned an unknown planner tool.")
        raw = _value(item, "arguments")
        if not isinstance(raw, str):
            raise RuntimeError("DeepSeek Responses planner arguments are invalid.")
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("DeepSeek Responses planner arguments are invalid.") from exc
        if not isinstance(arguments, dict):
            raise RuntimeError("DeepSeek Responses planner action is invalid.")
        allowed = {"action", "summary", "reply", "skill_tool", "skill_params", "shell_command"}
        if set(arguments) - allowed:
            raise RuntimeError("DeepSeek Responses planner action is invalid.")
        action = arguments.get("action")
        if not isinstance(action, str) or action not in {"reply", "skill", "shell"}:
            raise RuntimeError("DeepSeek Responses planner action is invalid.")
        for text_key in ("summary", "reply", "skill_tool", "shell_command"):
            if text_key in arguments and not isinstance(arguments[text_key], str):
                raise RuntimeError("DeepSeek Responses planner action is invalid.")
        if "skill_params" in arguments and not isinstance(arguments["skill_params"], dict):
            raise RuntimeError("DeepSeek Responses planner action is invalid.")
        if action == "reply" and not (str(arguments.get("reply") or "").strip() or str(arguments.get("summary") or "").strip()):
            raise RuntimeError("DeepSeek Responses planner action is invalid.")
        if action == "skill" and not str(arguments.get("skill_tool") or "").strip():
            raise RuntimeError("DeepSeek Responses planner action is invalid.")
        if action == "shell" and not str(arguments.get("shell_command") or "").strip():
            raise RuntimeError("DeepSeek Responses planner action is invalid.")
        return json.dumps(arguments, ensure_ascii=False)

    def _consume_stream(self, stream: Any, callback: Callable[[str], None], *, planner_mode: bool) -> ProviderRuntimeResponse:
        text_parts: list[str] = []
        tool_args: list[str] = []
        tool_item: Any = None
        usage: dict[str, Any] = {}
        final_seen = False
        for event in stream:
            event_type = str(_value(event, "type") or "")
            if event_type in {"response.failed", "response.incomplete"}:
                raise RuntimeError("DeepSeek Responses stream did not complete.")
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
                        raise RuntimeError("DeepSeek Responses probe returned an unexpected tool call.")
                    assembled = {"type": "function_call", "name": _value(tool_item, "name"), "arguments": "".join(tool_args) or _value(tool_item, "arguments")}
                    return ProviderRuntimeResponse(text=self.parse_tool_call(assembled), usage=usage, reasoning_summary=_summaries(_as_list(_value(final_response, "output"))))
                final_text = str(_value(final_response, "output_text") or "")
                completed_text = final_text or "".join(text_parts)
                if not completed_text:
                    raise RuntimeError("DeepSeek Responses stream completed without a message or planner action.")
                return ProviderRuntimeResponse(text=completed_text, usage=usage, reasoning_summary=_summaries(_as_list(_value(final_response, "output"))))
        if not final_seen:
            raise RuntimeError("DeepSeek Responses stream ended before response.completed.")
        raise RuntimeError("DeepSeek Responses stream returned no response.")


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
