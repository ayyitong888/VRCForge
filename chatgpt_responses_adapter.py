"""Responses transport for ChatGPT subscription OAuth accounts."""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from model_provider_adapters import validate_provider_api_key
from provider_runtime_adapters import (
    OpenAIResponsesAdapter,
    ProviderRuntimeRequest,
    ProviderRuntimeResponse,
    _TOOL_SCHEMA,
)


CHATGPT_RESPONSES_BASE_URL = "https://api.openai.com/v1"
_NAMESPACE = "vrcforge"


@dataclass(frozen=True)
class ChatGPTNativeResponse:
    text: str
    assistant_message: dict[str, Any]
    finish_reason: str
    usage: dict[str, Any] = field(default_factory=dict)
    reasoning_summary: list[Any] = field(default_factory=list)


class ChatGPTResponsesAdapter:
    """Request-scoped, streamed Responses calls using a selected OAuth account."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        client_factory: Callable[..., Any] | None = None,
        access_token_provider: Callable[[str], str] | None = None,
    ) -> None:
        self._account_id = validate_provider_api_key(api_key)
        if not self._account_id:
            raise ValueError("A selected ChatGPT account is required.")
        if str(base_url or "").rstrip("/") != CHATGPT_RESPONSES_BASE_URL:
            raise ValueError("ChatGPT subscription requests require the official OpenAI API endpoint.")
        self._client_factory = client_factory
        self._access_token_provider = access_token_provider
        # Reuse the existing planner action validator; this adapter owns only
        # the subscription transport and its input/output conversion.
        self._planner_parser = OpenAIResponsesAdapter(
            api_key="local-parser-only",
            base_url=CHATGPT_RESPONSES_BASE_URL,
        )

    def get_models(self) -> list[dict[str, Any]]:
        return []

    def send_request(self, request: ProviderRuntimeRequest) -> ProviderRuntimeResponse:
        if request.reference_image_paths:
            raise RuntimeError("ChatGPT Responses planner requests do not accept local reference image paths.")
        return self._send_request(request, request.prompt)

    def send_content(
        self,
        request: ProviderRuntimeRequest,
        content: list[dict[str, Any]],
    ) -> ProviderRuntimeResponse:
        """Run a probe with Responses-native text and data-image content blocks."""
        if request.mode != "probe":
            raise ValueError("ChatGPT Responses content requests are restricted to provider probes.")
        if not isinstance(content, list) or any(not isinstance(item, Mapping) for item in content):
            raise ValueError("ChatGPT Responses content blocks are invalid.")
        normalized_content: list[dict[str, Any]] = []
        for item in content:
            kind = str(item.get("type") or "")
            if kind == "input_text" and isinstance(item.get("text"), str):
                normalized_content.append({"type": "input_text", "text": item["text"]})
            elif kind == "input_image" and isinstance(item.get("image_url"), str) and item["image_url"].startswith("data:"):
                image = {"type": "input_image", "image_url": item["image_url"]}
                if item.get("detail") in {"low", "high", "auto"}:
                    image["detail"] = item["detail"]
                normalized_content.append(image)
            else:
                raise ValueError("ChatGPT Responses content supports input_text and data URL input_image blocks only.")
        if not normalized_content:
            raise ValueError("ChatGPT Responses content blocks are empty.")
        return self._send_request(request, normalized_content)

    def _send_request(self, request: ProviderRuntimeRequest, user_content: Any) -> ProviderRuntimeResponse:
        if request.mode not in {"planner", "probe"}:
            raise ValueError("ChatGPT Responses request mode is invalid.")
        if request.structured_output and request.mode != "probe":
            raise ValueError("Structured output is only supported for a provider probe.")
        payload: dict[str, Any] = {
            "model": self._require_model(request.model),
            "input": [{"role": "user", "content": user_content}],
            "instructions": request.instructions,
            "store": False,
            "stream": True,
            "include": ["reasoning.encrypted_content"],
        }
        if request.mode == "planner":
            payload["tools"] = _namespace_tools([_TOOL_SCHEMA])
        elif request.structured_output:
            payload["text"] = {"format": {"type": "json_object"}}
        response = self._stream_response(
            payload,
            cancel_event=request.cancel_event,
            stream_callback=request.stream_callback,
            activity_callback=request.stream_activity_callback,
        )
        return self._planner_parser.parse_response(
            response,
            planner_mode=request.mode == "planner",
        )

    def send_native_request(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        stream_callback: Callable[[str], None] | None = None,
        cancel_event: Any | None = None,
        stream_activity_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ChatGPTNativeResponse:
        payload: dict[str, Any] = {
            "model": self._require_model(model),
            "input": build_responses_input(messages),
            "store": False,
            "stream": True,
            "include": ["reasoning.encrypted_content"],
        }
        grouped_tools = _namespace_tools(tools)
        if grouped_tools:
            payload["tools"] = grouped_tools
        response = self._stream_response(
            payload,
            cancel_event=cancel_event,
            stream_callback=stream_callback,
            activity_callback=stream_activity_callback,
        )
        return _parse_native_response(response, tools)

    @staticmethod
    def _require_model(model: str) -> str:
        resolved = str(model or "").strip()
        if not resolved:
            raise RuntimeError("Select a model available to the signed-in ChatGPT account.")
        return resolved

    def _access_token(self) -> str:
        if self._access_token_provider is not None:
            token = self._access_token_provider(self._account_id)
        else:
            try:
                from chatgpt_oauth_service import get_chatgpt_auth_service
            except ImportError as exc:
                raise RuntimeError("The ChatGPT sign-in service is unavailable.") from exc
            token = get_chatgpt_auth_service().access_token(self._account_id)
        validated = validate_provider_api_key(token)
        if not validated:
            raise RuntimeError("The selected ChatGPT account has no usable access token.")
        return validated

    def _client(self) -> Any:
        kwargs: dict[str, Any] = {
            "api_key": self._access_token(),
            "base_url": CHATGPT_RESPONSES_BASE_URL,
            "max_retries": 0,
        }
        if self._client_factory is not None:
            return self._client_factory(**kwargs)
        try:
            import httpx
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The 'openai' package is not installed. Run pip install -r requirements.txt and try again."
            ) from exc
        kwargs["http_client"] = httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
        )
        return OpenAI(**kwargs)

    def _stream_response(
        self,
        payload: dict[str, Any],
        *,
        cancel_event: Any | None,
        stream_callback: Callable[[str], None] | None,
        activity_callback: Callable[[dict[str, Any]], None] | None,
    ) -> Any:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("ChatGPT Responses request cancelled.")
        client = self._client()
        watcher_stop = threading.Event()
        close_lock = threading.Lock()
        client_closed = False

        def close_client() -> None:
            nonlocal client_closed
            with close_lock:
                if client_closed:
                    return
                client_closed = True
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        watcher: threading.Thread | None = None
        stream: Any = None
        if cancel_event is not None:
            def close_on_cancel() -> None:
                while not watcher_stop.wait(0.05):
                    if cancel_event.is_set():
                        close_client()
                        return

            watcher = threading.Thread(
                target=close_on_cancel,
                name="vrcforge-chatgpt-responses-cancel",
                daemon=True,
            )
            watcher.start()
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("ChatGPT Responses request cancelled.")
            stream = client.responses.create(**payload)
            for event in stream:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("ChatGPT Responses request cancelled.")
                event_type = str(_value(event, "type") or "")
                if "reasoning" in event_type or "summary" in event_type:
                    if activity_callback is not None:
                        activity_callback({"kind": "reasoning_activity"})
                if event_type in {"error", "response.error", "response.failed", "response.incomplete"}:
                    raise _stream_failure(event)
                if event_type == "response.output_text.delta":
                    delta = str(_value(event, "delta") or "")
                    if delta and stream_callback is not None:
                        stream_callback(delta)
                elif event_type in {"response.output_item.added", "response.output_item.done"}:
                    item = _value(event, "item")
                    item_type = str(_value(item, "type") or "")
                    if item_type == "function_call" and activity_callback is not None:
                        activity_callback({"kind": "tool_call_activity"})
                if event_type == "response.completed":
                    response = _value(event, "response")
                    if response is None:
                        raise RuntimeError("ChatGPT Responses completed event omitted its response.")
                    status = str(_value(response, "status") or "completed").lower()
                    if status != "completed":
                        raise RuntimeError("ChatGPT Responses stream did not complete successfully.")
                    return response
            raise RuntimeError("ChatGPT Responses stream ended before response.completed.")
        except Exception:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("ChatGPT Responses request cancelled.") from None
            raise
        finally:
            watcher_stop.set()
            close_stream = getattr(stream, "close", None)
            if callable(close_stream):
                try:
                    close_stream()
                except Exception:
                    pass
            close_client()
            if watcher is not None:
                watcher.join(0.2)


def build_responses_input(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert ordered Chat Completions history into stateless Responses items."""
    result: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise ValueError("Native ChatGPT history contains an invalid message.")
        role = str(message.get("role") or "")
        if role in {"system", "developer"}:
            result.append({"role": "developer", "content": _responses_content(message.get("content"))})
            continue
        if role == "user":
            result.append({"role": "user", "content": _responses_content(message.get("content"))})
            continue
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "").strip()
            if not call_id:
                raise ValueError("Native ChatGPT tool result omitted its call ID.")
            output = message.get("content")
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
            result.append({"type": "function_call_output", "call_id": call_id, "output": output})
            continue
        if role != "assistant":
            raise ValueError("Native ChatGPT history contains an unsupported role.")

        if "responses_output" in message:
            response_items = _decode_responses_output(
                message.get("responses_output"),
                assistant_message=message,
            )
            result.extend(response_items)
            continue
        content = message.get("content")
        if content is not None:
            result.append({"role": "assistant", "content": _responses_content(content)})
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ValueError("Native ChatGPT assistant tool calls are invalid.")
        for call in calls:
            function = call.get("function") if isinstance(call, Mapping) else None
            if not isinstance(function, Mapping):
                raise ValueError("Native ChatGPT assistant tool call is invalid.")
            call_id = str(call.get("id") or "").strip()
            name = str(function.get("name") or "").strip()
            arguments = function.get("arguments")
            if not call_id or not name or not isinstance(arguments, str):
                raise ValueError("Native ChatGPT assistant tool call is incomplete.")
            result.append({
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            })
        if content is None and not calls:
            raise ValueError("Native ChatGPT assistant history is empty.")
    return result


def _namespace_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    nested: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, Mapping):
            raise ValueError("ChatGPT Responses tool definition is invalid.")
        kind = str(tool.get("type") or "")
        if kind == "function":
            function = tool.get("function")
            source = function if isinstance(function, Mapping) else tool
            name = str(source.get("name") or "").strip()
            parameters = source.get("parameters")
            if not name or not isinstance(parameters, Mapping):
                raise ValueError("ChatGPT Responses function tool definition is incomplete.")
            item: dict[str, Any] = {
                "type": "function",
                "name": name,
                "parameters": copy.deepcopy(dict(parameters)),
            }
            for key in ("description", "strict"):
                if key in source:
                    item[key] = copy.deepcopy(source[key])
            item.setdefault("strict", False)
            nested.append(item)
        elif kind == "custom":
            custom = tool.get("custom")
            source = custom if isinstance(custom, Mapping) else tool
            name = str(source.get("name") or "").strip()
            if not name:
                raise ValueError("ChatGPT Responses custom tool definition is incomplete.")
            item = {"type": "custom", "name": name}
            for key in ("description", "format"):
                if key in source:
                    item[key] = copy.deepcopy(source[key])
            nested.append(item)
        else:
            raise ValueError("ChatGPT subscription supports only namespaced function and custom tools.")
    if not nested:
        return []
    return [{
        "type": "namespace",
        "name": _NAMESPACE,
        "description": "VRCForge tools for the current supervised task.",
        "tools": nested,
    }]


def _parse_native_response(
    response: Any,
    tools: Sequence[Mapping[str, Any]],
) -> ChatGPTNativeResponse:
    output = _value(response, "output")
    if not isinstance(output, list):
        raise RuntimeError("ChatGPT Responses returned an invalid output list.")
    allowed_names = _tool_names(tools)
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    response_items: list[dict[str, Any]] = []
    summaries: list[Any] = []
    seen_ids: set[str] = set()
    for item in output:
        kind = str(_value(item, "type") or "")
        if kind == "reasoning":
            summary = _value(item, "summary")
            if summary:
                summaries.append(summary)
            if not isinstance(_value(item, "encrypted_content"), str):
                raise RuntimeError("ChatGPT Responses omitted encrypted reasoning required for stateless continuation.")
            response_items.append(_model_mapping(item))
            continue
        if kind == "message":
            if _value(item, "role") != "assistant":
                raise RuntimeError("ChatGPT Responses returned a non-assistant message in its output.")
            phase = _value(item, "phase")
            if phase is not None and phase not in {"commentary", "final_answer"}:
                raise RuntimeError("ChatGPT Responses returned an unsupported assistant phase.")
            _validate_message_output(item)
            response_items.append(_model_mapping(item))
            text_parts.append(_message_text(item))
            continue
        if kind == "function_call":
            call_id = str(_value(item, "call_id") or "").strip()
            name = str(_value(item, "name") or "").strip()
            arguments = _value(item, "arguments")
            if not call_id or call_id in seen_ids or not name or not isinstance(arguments, str):
                raise RuntimeError("ChatGPT Responses returned an invalid native tool call.")
            if str(_value(item, "namespace") or "") != _NAMESPACE:
                raise RuntimeError("ChatGPT Responses returned a tool call from an unknown namespace.")
            if name not in allowed_names:
                raise RuntimeError("ChatGPT Responses returned a tool that was not requested.")
            seen_ids.add(call_id)
            response_items.append(_model_mapping(item))
            calls.append({
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            })
            continue
        raise RuntimeError("ChatGPT Responses returned an unsupported native output item.")

    text = "".join(text_parts)
    if not text and not calls:
        raise RuntimeError("ChatGPT Responses completed without a message or tool call.")
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": text or None,
        "tool_calls": calls,
    }
    if response_items:
        assistant_message["responses_output"] = response_items
    return ChatGPTNativeResponse(
        text=text,
        assistant_message=assistant_message,
        finish_reason="tool_calls" if calls else "stop",
        usage=_normalized_usage(response),
        reasoning_summary=summaries,
    )


def _tool_names(tools: Sequence[Mapping[str, Any]]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        source = tool.get("function") if str(tool.get("type") or "") == "function" else tool.get("custom")
        if not isinstance(source, Mapping):
            source = tool
        name = str(source.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _decode_responses_output(
    value: Any,
    *,
    assistant_message: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if value is None:
        raise ValueError("Native ChatGPT Responses output history is invalid.")
    if not isinstance(value, list) or not value or len(value) > 256:
        raise ValueError("Native ChatGPT Responses output history is invalid.")
    items = copy.deepcopy(value)
    response_calls: dict[str, tuple[str, str]] = {}
    output_text: list[str] = []
    has_assistant_output = False
    seen_item_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Native ChatGPT Responses output history is invalid.")
        item_id = item.get("id")
        if item_id is not None:
            if not isinstance(item_id, str) or not item_id or item_id in seen_item_ids:
                raise ValueError("Native ChatGPT Responses output item ID is invalid.")
            seen_item_ids.add(item_id)
        kind = item.get("type")
        if kind == "reasoning":
            if not isinstance(item.get("encrypted_content"), str):
                raise ValueError("Native ChatGPT encrypted reasoning history is invalid.")
        elif kind == "message":
            if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                raise ValueError("Native ChatGPT assistant output history is invalid.")
            phase = item.get("phase")
            if phase is not None and phase not in {"commentary", "final_answer"}:
                raise ValueError("Native ChatGPT assistant output phase is invalid.")
            has_assistant_output = True
            _validate_message_output(item)
            output_text.append(_message_text(item))
        elif kind == "function_call":
            if not all(isinstance(item.get(key), str) and item[key] for key in ("call_id", "name", "arguments")):
                raise ValueError("Native ChatGPT tool output history is invalid.")
            if item.get("namespace") != _NAMESPACE:
                raise ValueError("Native ChatGPT tool output namespace is invalid.")
            call_id = item["call_id"]
            if call_id in response_calls:
                raise ValueError("Native ChatGPT tool output call ID is duplicate.")
            response_calls[call_id] = (item["name"], item["arguments"])
            has_assistant_output = True
        else:
            raise ValueError("Native ChatGPT Responses output history has an unsupported item.")
    if not has_assistant_output:
        raise ValueError("Native ChatGPT Responses output history has no assistant output.")
    receipt_calls: dict[str, tuple[str, str]] = {}
    calls = assistant_message.get("tool_calls") or []
    if not isinstance(calls, list):
        raise ValueError("Native ChatGPT assistant tool calls are invalid.")
    for call in calls:
        function = call.get("function") if isinstance(call, Mapping) else None
        if not isinstance(function, Mapping):
            raise ValueError("Native ChatGPT assistant tool call is invalid.")
        call_id = str(call.get("id") or "").strip()
        name = str(function.get("name") or "").strip()
        arguments = function.get("arguments")
        if not call_id or call_id in receipt_calls or not name or not isinstance(arguments, str):
            raise ValueError("Native ChatGPT assistant tool call is incomplete.")
        receipt_calls[call_id] = (name, arguments)
    if response_calls != receipt_calls:
        raise ValueError("Native ChatGPT Responses output calls do not match the assistant receipt.")
    content = assistant_message.get("content")
    if content is not None and "".join(output_text) != content:
        raise ValueError("Native ChatGPT Responses output text does not match the assistant receipt.")
    return items


def _responses_content(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ValueError("Native ChatGPT message content is invalid.")
    converted: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("Native ChatGPT message content is invalid.")
        kind = str(item.get("type") or "")
        if kind in {"text", "input_text"}:
            converted.append({"type": "input_text", "text": str(item.get("text") or "")})
        elif kind == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, Mapping):
                image_url = image_url.get("url")
            if not isinstance(image_url, str) or not image_url:
                raise ValueError("Native ChatGPT image content is invalid.")
            converted.append({"type": "input_image", "image_url": image_url})
        elif kind == "input_image":
            converted.append(copy.deepcopy(dict(item)))
        else:
            raise ValueError("Native ChatGPT message content has an unsupported item.")
    return converted


def _message_text(item: Any) -> str:
    parts: list[str] = []
    for content in _as_list(_value(item, "content")):
        kind = str(_value(content, "type") or "")
        if kind in {"output_text", "text"}:
            parts.append(str(_value(content, "text") or ""))
        elif kind == "refusal":
            parts.append(str(_value(content, "refusal") or _value(content, "text") or ""))
    return "".join(parts)


def _validate_message_output(item: Any) -> None:
    content = _value(item, "content")
    if not isinstance(content, list):
        raise RuntimeError("ChatGPT Responses returned an invalid assistant message content list.")
    for part in content:
        kind = str(_value(part, "type") or "")
        if kind == "output_text":
            if not isinstance(_value(part, "text"), str):
                raise RuntimeError("ChatGPT Responses returned invalid assistant output text.")
        elif kind == "refusal":
            refusal = _value(part, "refusal")
            if refusal is not None and not isinstance(refusal, str):
                raise RuntimeError("ChatGPT Responses returned invalid refusal text.")
            if refusal is None and not isinstance(_value(part, "text"), str):
                raise RuntimeError("ChatGPT Responses returned invalid refusal text.")
        else:
            raise RuntimeError("ChatGPT Responses returned an unsupported assistant content item.")


def _model_mapping(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return copy.deepcopy(dict(item))
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        value = model_dump(mode="json", exclude_none=True)
        if isinstance(value, dict):
            return value
    keys = (
        "id", "type", "role", "content", "call_id", "name", "arguments", "namespace",
        "status", "summary", "encrypted_content", "phase", "metadata",
        "input_tokens", "output_tokens", "total_tokens", "reasoning_tokens", "cached_tokens",
        "audio_tokens", "text_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens",
    )
    return {key: copy.deepcopy(_value(item, key)) for key in keys if _value(item, key) is not None}


def _normalized_usage(response: Any) -> dict[str, Any]:
    usage = _value(response, "usage") or {}
    output_details = _value(usage, "output_tokens_details") or {}
    normalized = {
        "input_tokens": _value(usage, "input_tokens"),
        "output_tokens": _value(usage, "output_tokens"),
        "total_tokens": _value(usage, "total_tokens"),
        "reasoning_tokens": _value(usage, "reasoning_tokens") or _value(output_details, "reasoning_tokens"),
    }
    for key in ("prompt_tokens_details", "input_tokens_details", "output_tokens_details"):
        details = _value(usage, key)
        if details is not None:
            normalized[key] = _usage_detail_mapping(details)
    return normalized


def _usage_detail_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        result = model_dump(mode="json", exclude_none=True)
        if isinstance(result, dict):
            return result
    keys = (
        "cached_tokens", "reasoning_tokens", "audio_tokens", "text_tokens",
        "accepted_prediction_tokens", "rejected_prediction_tokens",
    )
    return {key: copy.deepcopy(_value(value, key)) for key in keys if _value(value, key) is not None}


def _stream_failure(event: Any) -> RuntimeError:
    response = _value(event, "response")
    error = _value(response, "error") or _value(event, "error") or {}
    code = str(_value(error, "code") or "").strip()[:100]
    message = str(_value(error, "message") or "").strip()[:240]
    detail = ": ".join(part for part in (code, message) if part)
    suffix = f" ({detail})" if detail else ""
    return RuntimeError(f"ChatGPT Responses stream did not complete successfully{suffix}.")


def _value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []
