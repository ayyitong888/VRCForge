"""Responses transport for ChatGPT subscription OAuth accounts."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from model_provider_adapters import validate_provider_api_key
from provider_runtime_adapters import (
    OpenAIResponsesAdapter,
    ProviderNativeToolRejection,
    ProviderRuntimeRequest,
    ProviderRuntimeResponse,
    _TOOL_SCHEMA,
)
from responses_native_protocol import (
    NativeResponse,
    NativeToolRejection,
    build_responses_input as _build_responses_input,
    _normalized_usage,
    parse_native_response as _parse_responses_native,
    stream_response as _stream_native_response,
)


CHATGPT_RESPONSES_BASE_URL = "https://api.openai.com/v1"
_NAMESPACE = "vrcforge"


class ChatGPTResponseError(RuntimeError):
    """Response failure with numeric usage, never raw response or credentials."""

    def __init__(self, message: str, response: Any) -> None:
        super().__init__(message)
        self.usage = _normalized_usage(response)


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
        try:
            return self._planner_parser.parse_response(
                response,
                planner_mode=request.mode == "planner",
            )
        except (ValueError, RuntimeError) as exc:
            raise ChatGPTResponseError(str(exc), response) from exc

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
        try:
            return _parse_native_response(response, tools)
        except NativeToolRejection as exc:
            raise ProviderNativeToolRejection(response, exc.receipt.assistant_message) from exc
        except (ValueError, RuntimeError) as exc:
            raise ChatGPTResponseError(str(exc), response) from exc

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
                from .service import get_chatgpt_auth_service
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
        return _stream_native_response(
            client,
            payload,
            cancel_event,
            stream_callback,
            activity_callback,
            error_factory=lambda message, response: ChatGPTResponseError(message, response),
            provider_label="ChatGPT Responses",
        )


def build_responses_input(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert ChatGPT history with its existing strict continuation defaults."""
    return _build_responses_input(
        messages,
        namespace=_NAMESPACE,
        require_encrypted_reasoning=True,
    )


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
    parsed: NativeResponse = _parse_responses_native(
        response,
        tools,
        namespace=_NAMESPACE,
        require_encrypted_reasoning=True,
    )
    return ChatGPTNativeResponse(
        text=parsed.text,
        assistant_message=parsed.assistant_message,
        finish_reason=parsed.finish_reason,
        usage=parsed.usage,
        reasoning_summary=parsed.reasoning_summary,
    )
