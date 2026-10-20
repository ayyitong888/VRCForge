"""Protocol-neutral conversion and streaming helpers for native Responses APIs."""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

__all__ = [
    "NativeResponse",
    "build_responses_input",
    "flat_function_tools",
    "parse_native_response",
    "stream_response",
]


@dataclass(frozen=True)
class NativeResponse:
    text: str
    assistant_message: dict[str, Any]
    finish_reason: str
    usage: dict[str, Any] = field(default_factory=dict)
    reasoning_summary: list[Any] = field(default_factory=list)
    model: str = ""


class NativeToolRejection(RuntimeError):
    """Complete, structurally valid receipt retained only for denial feedback."""

    def __init__(self, receipt: NativeResponse) -> None:
        super().__init__("Responses returned a tool that was not requested.")
        self.receipt = receipt


def build_responses_input(
    messages: Sequence[Mapping[str, Any]],
    namespace: str | None = "vrcforge",
    require_encrypted_reasoning: bool = True,
    system_role: str = "developer",
) -> list[dict[str, Any]]:
    """Convert ordered assistant history into stateless Responses input items."""
    if system_role not in {"system", "developer"}:
        raise ValueError("Responses system role must be 'system' or 'developer'.")

    result: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise ValueError("Native Responses history contains an invalid message.")
        role = str(message.get("role") or "")
        if role == "system":
            result.append({"role": system_role, "content": _responses_content(message.get("content"))})
            continue
        if role == "developer":
            result.append({"role": "developer", "content": _responses_content(message.get("content"))})
            continue
        if role == "user":
            result.append({"role": "user", "content": _responses_content(message.get("content"))})
            continue
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "").strip()
            if not call_id:
                raise ValueError("Native Responses tool result omitted its call ID.")
            output = message.get("content")
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
            result.append({"type": "function_call_output", "call_id": call_id, "output": output})
            continue
        if role != "assistant":
            raise ValueError("Native Responses history contains an unsupported role.")

        if "responses_output" in message:
            response_items = _decode_responses_output(
                message.get("responses_output"),
                assistant_message=message,
                namespace=namespace,
                require_encrypted_reasoning=require_encrypted_reasoning,
            )
            result.extend(response_items)
            continue
        content = message.get("content")
        if content is not None:
            result.append({"role": "assistant", "content": _responses_content(content)})
        calls = message.get("tool_calls") or []
        if not isinstance(calls, list):
            raise ValueError("Native Responses assistant tool calls are invalid.")
        for call in calls:
            function = call.get("function") if isinstance(call, Mapping) else None
            if not isinstance(function, Mapping):
                raise ValueError("Native Responses assistant tool call is invalid.")
            call_id = str(call.get("id") or "").strip()
            name = str(function.get("name") or "").strip()
            arguments = function.get("arguments")
            if not call_id or not name or not isinstance(arguments, str):
                raise ValueError("Native Responses assistant tool call is incomplete.")
            result.append({
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": arguments,
            })
        if content is None and not calls:
            raise ValueError("Native Responses assistant history is empty.")
    return result


def flat_function_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert nested or flat function definitions to Responses function tools."""
    converted: list[dict[str, Any]] = []
    names: set[str] = set()
    for tool in tools:
        if (not isinstance(tool, Mapping) or str(tool.get("type") or "") != "function"
                or "namespace" in tool):
            raise ValueError("Responses tool definition must be a function.")
        function = tool.get("function")
        source = function if isinstance(function, Mapping) else tool
        name = str(source.get("name") or "").strip()
        parameters = source.get("parameters", {})
        if not name or name in names or not isinstance(parameters, Mapping) or "namespace" in source:
            raise ValueError("Responses function tool definition is incomplete.")
        names.add(name)
        item: dict[str, Any] = {
            "type": "function",
            "name": name,
            "parameters": copy.deepcopy(dict(parameters)),
        }
        if "description" in source:
            item["description"] = copy.deepcopy(source["description"])
        if "strict" in source:
            item["strict"] = copy.deepcopy(source["strict"])
        converted.append(item)
    return converted


def parse_native_response(
    response: Any,
    tools: Sequence[Mapping[str, Any]],
    namespace: str | None = "vrcforge",
    require_encrypted_reasoning: bool = True,
) -> NativeResponse:
    output = _value(response, "output")
    if not isinstance(output, list):
        raise RuntimeError("Responses returned an invalid output list.")
    allowed_names = _tool_names(tools)
    text_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    response_items: list[dict[str, Any]] = []
    summaries: list[Any] = []
    seen_ids: set[str] = set()
    unexposed = False
    for item in output:
        kind = str(_value(item, "type") or "")
        if kind == "reasoning":
            summary = _value(item, "summary")
            if summary:
                summaries.append(summary)
            if require_encrypted_reasoning:
                if not isinstance(_value(item, "encrypted_content"), str):
                    raise RuntimeError("Responses omitted encrypted reasoning required for stateless continuation.")
            else:
                _validate_reasoning_content(item, RuntimeError)
            response_items.append(_model_mapping(item))
            continue
        if kind == "message":
            if _value(item, "role") != "assistant":
                raise RuntimeError("Responses returned a non-assistant message in its output.")
            phase = _value(item, "phase")
            if phase is not None and phase not in {"commentary", "final_answer"}:
                raise RuntimeError("Responses returned an unsupported assistant phase.")
            _validate_message_output(item)
            response_items.append(_model_mapping(item))
            text_parts.append(_message_text(item))
            continue
        if kind == "function_call":
            call_id = str(_value(item, "call_id") or "").strip()
            name = str(_value(item, "name") or "").strip()
            arguments = _value(item, "arguments")
            if not call_id or call_id in seen_ids or not name or not isinstance(arguments, str):
                raise RuntimeError("Responses returned an invalid native tool call.")
            if not _namespace_matches(item, namespace):
                raise RuntimeError("Responses returned a tool call from an unexpected namespace.")
            if name not in allowed_names:
                unexposed = True
            seen_ids.add(call_id)
            response_items.append(_model_mapping(item))
            calls.append({
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            })
            continue
        raise RuntimeError("Responses returned an unsupported native output item.")

    text = "".join(text_parts)
    if not text and not calls:
        raise RuntimeError("Responses completed without a message or tool call.")
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": text or None,
        "tool_calls": calls,
    }
    if response_items:
        assistant_message["responses_output"] = response_items
    receipt = NativeResponse(
        text=text,
        assistant_message=assistant_message,
        finish_reason="tool_calls" if calls else "stop",
        usage=_normalized_usage(response),
        reasoning_summary=summaries,
        model=str(_value(response, "model") or "").strip(),
    )
    if unexposed:
        raise NativeToolRejection(receipt)
    return receipt


def public_assistant_messages(receipt: Mapping[str, Any]) -> list[dict[str, str]]:
    """Project only public message text; preserve private replay items untouched.

    Phase-less receipts retain the existing legacy content behavior.
    """
    if "publicMessages" in receipt:
        return copy.deepcopy(receipt["publicMessages"])
    items = receipt.get("responses_output") or []
    if not any(item.get("type") == "message" and item.get("phase") is not None for item in items):
        return []
    projected = []
    for index, item in enumerate(items):
        if item.get("type") != "message":
            continue
        text = _message_text(item).strip()
        if not text:
            continue
        message = {"id": str(item.get("id") or f"message-{index}")[:180], "text": text[:32_000]}
        if item.get("phase") is not None:
            message["phase"] = item["phase"]
        projected.append(message)
    return projected


def stream_response(
    client: Any,
    payload: Mapping[str, Any],
    cancel_event: Any | None,
    stream_callback: Callable[[str], None] | None,
    activity_callback: Callable[[dict[str, Any]], None] | None,
    *,
    error_factory: Callable[[str, Any], Exception],
    provider_label: str = "Responses",
    request_observer: Callable[[dict[str, Any]], None] | None = None,
    response_headers_callback: Callable[[Mapping[str, str]], None] | None = None,
) -> Any:
    """Consume a completed Responses stream and own its client and watcher."""
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

    if cancel_event is not None and cancel_event.is_set():
        close_client()
        raise RuntimeError(f"{provider_label} request cancelled.")

    watcher: threading.Thread | None = None
    stream: Any = None
    completed_items: dict[int, dict[str, Any]] = {}
    terminal_response: Any = None
    metrics = None
    request_succeeded = False
    request_failure = ""
    stream_started = False
    if cancel_event is not None:
        def close_on_cancel() -> None:
            while not watcher_stop.wait(0.05):
                if cancel_event.is_set():
                    close_client()
                    return

        watcher = threading.Thread(
            target=close_on_cancel,
            name="responses-native-cancel",
            daemon=True,
        )
        watcher.start()
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError(f"{provider_label} request cancelled.")
        if request_observer is not None:
            from provider_request_metrics import RequestMetrics
            metrics = RequestMetrics(request_observer, payload)
        stream = client.responses.create(**dict(payload))
        if response_headers_callback is not None:
            response_headers_callback(getattr(getattr(stream, "response", None), "headers", {}))
        for event in stream:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError(f"{provider_label} request cancelled.")
            event_type = str(_value(event, "type") or "")
            if event_type in {"response.completed", "response.failed", "response.incomplete"}:
                terminal_response = _value(event, "response")
            if "reasoning" in event_type or "summary" in event_type:
                stream_started = True
                if activity_callback is not None:
                    activity_callback({"kind": "reasoning_activity"})
            if event_type in {"error", "response.error", "response.failed", "response.incomplete"}:
                raise _stream_failure(event, provider_label)
            if event_type == "response.output_text.delta":
                delta = str(_value(event, "delta") or "")
                if delta:
                    stream_started = True
                    if stream_callback is not None:
                        stream_callback(delta)
            elif event_type in {"response.output_item.added", "response.output_item.done"}:
                item = _value(event, "item")
                if event_type == "response.output_item.done":
                    index = _value(event, "output_index")
                    if type(index) is not int or index < 0 or item is None:
                        raise RuntimeError(f"{provider_label} output item index is invalid.")
                    final_item = _model_mapping(item)
                    if index in completed_items and completed_items[index] != final_item:
                        raise RuntimeError(f"{provider_label} output item changed after completion.")
                    completed_items[index] = final_item
                item_type = str(_value(item, "type") or "")
                if item_type == "function_call" and activity_callback is not None:
                    stream_started = True
                    activity_callback({"kind": "tool_call_activity"})
            if event_type == "response.completed":
                response = _value(event, "response")
                if response is None:
                    raise RuntimeError(f"{provider_label} completed event omitted its response.")
                status = str(_value(response, "status") or "completed").lower()
                if status != "completed":
                    raise RuntimeError(f"{provider_label} stream did not complete successfully.")
                result = _model_mapping(response)
                output = result.get("output")
                if not isinstance(output, list):
                    raise RuntimeError(f"{provider_label} returned an invalid output list.")
                if output:
                    if any(index >= len(output) or output[index] != item
                           for index, item in completed_items.items()):
                        raise RuntimeError(f"{provider_label} terminal output disagrees with completed items.")
                elif completed_items:
                    if sorted(completed_items) != list(range(len(completed_items))):
                        raise RuntimeError(f"{provider_label} completed output has missing items.")
                    result["output"] = [completed_items[index] for index in range(len(completed_items))]
                # Execution and telemetry must consume the same reconciled
                # receipt when the terminal event omits streamed output items.
                terminal_response = result
                request_succeeded = True
                return result
        raise RuntimeError(f"{provider_label} stream ended before response.completed.")
    except Exception as exc:
        request_failure = type(exc).__name__
        try:
            setattr(exc, "stream_started", stream_started)
            response_headers = _value(terminal_response, "headers") if terminal_response is not None else None
            no_more = response_headers.get("x-no-more-retry") if hasattr(response_headers, "get") else None
            if str(no_more).casefold() in {"1", "true", "yes"}:
                setattr(exc, "no_more_retry", True)
        except Exception:
            pass
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError(f"{provider_label} request cancelled.") from None
        if terminal_response is not None and isinstance(exc, (ValueError, RuntimeError)):
            wrapped = error_factory(str(exc), terminal_response)
            try:
                setattr(wrapped, "stream_started", stream_started)
                if getattr(exc, "no_more_retry", False):
                    setattr(wrapped, "no_more_retry", True)
                for attribute in (
                    "stream_error_code", "stream_error_message", "stream_error_status",
                    "stream_error_status_code",
                    "status_code", "headers",
                ):
                    value = getattr(exc, attribute, None)
                    if value is not None:
                        setattr(wrapped, attribute, value)
            except Exception:
                pass
            raise wrapped from exc
        raise
    finally:
        if metrics is not None:
            output_items = _value(terminal_response, "output")
            tool_calls = []
            if isinstance(output_items, list):
                for item in output_items:
                    if str(_value(item, "type") or "") != "function_call":
                        continue
                    call_name = _value(item, "name")
                    action = ""
                    arguments = _value(item, "arguments")
                    if isinstance(arguments, str):
                        try:
                            decoded = json.loads(arguments)
                        except (TypeError, ValueError):
                            decoded = None
                        if isinstance(decoded, Mapping):
                            action = str(decoded.get("action") or "").strip()[:80]
                            call_name = decoded.get("write_tool") or decoded.get("skill_tool") or decoded.get("tool") or call_name
                    tool_calls.append({
                        "id": _value(item, "call_id") or _value(item, "id"),
                        "name": call_name,
                        "action": action,
                    })
            metrics.finish(
                _normalized_usage(terminal_response),
                success=request_succeeded,
                error=request_failure,
                service_tier=_value(terminal_response, "service_tier"),
                provider_request_id=_value(terminal_response, "id"),
                tool_calls=tool_calls,
                output={
                    "functionCallCount": len(tool_calls),
                    "messageCount": sum(_value(item, "type") == "message" for item in output_items),
                } if isinstance(output_items, list) else {},
            )
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


def _decode_responses_output(
    value: Any,
    *,
    assistant_message: Mapping[str, Any],
    namespace: str | None,
    require_encrypted_reasoning: bool,
) -> list[dict[str, Any]]:
    if value is None:
        raise ValueError("Native Responses output history is invalid.")
    if not isinstance(value, list) or not value or len(value) > 256:
        raise ValueError("Native Responses output history is invalid.")
    items = copy.deepcopy(value)
    response_calls: dict[str, tuple[str, str]] = {}
    output_text: list[str] = []
    has_assistant_output = False
    seen_item_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Native Responses output history is invalid.")
        item_id = item.get("id")
        if item_id is not None:
            if not isinstance(item_id, str) or not item_id or item_id in seen_item_ids:
                raise ValueError("Native Responses output item ID is invalid.")
            seen_item_ids.add(item_id)
        kind = item.get("type")
        if kind == "reasoning":
            if require_encrypted_reasoning:
                if not isinstance(item.get("encrypted_content"), str):
                    raise ValueError("Native Responses encrypted reasoning history is invalid.")
            else:
                _validate_reasoning_content(item, ValueError)
        elif kind == "message":
            if item.get("role") != "assistant" or not isinstance(item.get("content"), list):
                raise ValueError("Native Responses assistant output history is invalid.")
            phase = item.get("phase")
            if phase is not None and phase not in {"commentary", "final_answer"}:
                raise ValueError("Native Responses assistant output phase is invalid.")
            has_assistant_output = True
            _validate_message_output(item)
            output_text.append(_message_text(item))
        elif kind == "function_call":
            if not all(isinstance(item.get(key), str) and item[key] for key in ("call_id", "name", "arguments")):
                raise ValueError("Native Responses tool output history is invalid.")
            if not _namespace_matches(item, namespace):
                raise ValueError("Native Responses tool output namespace is invalid.")
            call_id = item["call_id"]
            if call_id in response_calls:
                raise ValueError("Native Responses tool output call ID is duplicate.")
            response_calls[call_id] = (item["name"], item["arguments"])
            has_assistant_output = True
        else:
            raise ValueError("Native Responses output history has an unsupported item.")
    if not has_assistant_output:
        raise ValueError("Native Responses output history has no assistant output.")
    receipt_calls: dict[str, tuple[str, str]] = {}
    calls = assistant_message.get("tool_calls") or []
    if not isinstance(calls, list):
        raise ValueError("Native Responses assistant tool calls are invalid.")
    for call in calls:
        function = call.get("function") if isinstance(call, Mapping) else None
        if not isinstance(function, Mapping):
            raise ValueError("Native Responses assistant tool call is invalid.")
        call_id = str(call.get("id") or "").strip()
        name = str(function.get("name") or "").strip()
        arguments = function.get("arguments")
        if not call_id or call_id in receipt_calls or not name or not isinstance(arguments, str):
            raise ValueError("Native Responses assistant tool call is incomplete.")
        receipt_calls[call_id] = (name, arguments)
    if response_calls != receipt_calls:
        raise ValueError("Native Responses output calls do not match the assistant receipt.")
    content = assistant_message.get("content")
    if content is not None and "".join(output_text) != content:
        raise ValueError("Native Responses output text does not match the assistant receipt.")
    return items


def _responses_content(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ValueError("Native Responses message content is invalid.")
    converted: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("Native Responses message content is invalid.")
        kind = str(item.get("type") or "")
        if kind in {"text", "input_text"}:
            converted.append({"type": "input_text", "text": str(item.get("text") or "")})
        elif kind == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, Mapping):
                image_url = image_url.get("url")
            if not isinstance(image_url, str) or not image_url:
                raise ValueError("Native Responses image content is invalid.")
            converted.append({"type": "input_image", "image_url": image_url})
        elif kind == "input_image":
            converted.append(copy.deepcopy(dict(item)))
        else:
            raise ValueError("Native Responses message content has an unsupported item.")
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
        raise RuntimeError("Responses returned an invalid assistant message content list.")
    for part in content:
        kind = str(_value(part, "type") or "")
        if kind == "output_text":
            if not isinstance(_value(part, "text"), str):
                raise RuntimeError("Responses returned invalid assistant output text.")
        elif kind == "refusal":
            refusal = _value(part, "refusal")
            if refusal is not None and not isinstance(refusal, str):
                raise RuntimeError("Responses returned invalid refusal text.")
            if refusal is None and not isinstance(_value(part, "text"), str):
                raise RuntimeError("Responses returned invalid refusal text.")
        else:
            raise RuntimeError("Responses returned an unsupported assistant content item.")


def _validate_reasoning_content(item: Any, error_type: type[Exception]) -> None:
    content = _value(item, "content")
    encrypted_content = _value(item, "encrypted_content")
    if not isinstance(encrypted_content, str) and not isinstance(content, list):
        raise error_type("Responses reasoning item has neither encrypted content nor reasoning content.")
    if content is None:
        return
    if not isinstance(content, list):
        raise error_type("Responses reasoning content is invalid.")
    for part in content:
        if str(_value(part, "type") or "") != "reasoning_text" or not isinstance(_value(part, "text"), str):
            raise error_type("Responses reasoning text content is invalid.")


def _namespace_matches(item: Any, namespace: str | None) -> bool:
    value = _value(item, "namespace")
    if namespace is not None:
        return value == namespace
    if isinstance(item, Mapping):
        return "namespace" not in item
    fields_set = getattr(item, "model_fields_set", None)
    if fields_set is not None:
        return "namespace" not in fields_set
    return value is None


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


def _normalized_usage_details(value: Any) -> dict[str, Any]:
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


def _stream_failure(event: Any, provider_label: str) -> RuntimeError:
    response = _value(event, "response")
    error = _value(response, "error") or _value(event, "error") or {}
    code = str(_value(error, "code") or "").strip()[:100]
    message = str(_value(error, "message") or "").strip()[:240]
    detail = ": ".join(part for part in (code, message) if part)
    suffix = f" ({detail})" if detail else ""
    failure = RuntimeError(f"{provider_label} stream did not complete successfully{suffix}.")
    # Stream-level error codes are provider metadata, not HTTP status.  Keep
    # them separate so an HTTP 200 response.failed event cannot become a
    # fabricated 429/503 in the planner classifier.
    if code:
        setattr(failure, "stream_error_code", code)
    if message:
        setattr(failure, "stream_error_message", message)
    response_status = str(_value(response, "status") or "").strip()[:40]
    if response_status:
        setattr(failure, "stream_error_status", response_status)
    for source in (event, response):
        status_code = _value(source, "status_code")
        if type(status_code) is int and status_code > 0:
            # A status embedded in a stream event is provider metadata. It is
            # not the HTTP response status returned by the SDK transport.
            setattr(failure, "stream_error_status_code", status_code)
            break
    headers = _value(response, "headers") or _value(event, "headers")
    if isinstance(headers, Mapping):
        safe_headers = {}
        for key in ("retry-after", "x-no-more-retry", "x-retry-metadata"):
            value = headers.get(key)
            if value is not None:
                safe_headers[key] = str(value)[:80]
        if safe_headers:
            setattr(failure, "headers", safe_headers)
    return failure


def _value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _usage_detail_mapping(value: Any) -> dict[str, Any]:
    return _normalized_usage_details(value)
