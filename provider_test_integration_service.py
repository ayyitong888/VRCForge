"""Typed Provider connectivity-test ownership for app composition."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from provider_configuration_service import (
    ProviderApiConfig,
    ProviderApiConfigRequestPort,
)
from provider_endpoint_policy import endpoint_for_protocol, normalize_provider_endpoint
from provider_protocol_negotiation import (
    is_explicit_protocol_compatibility_error,
    provider_protocol_candidates,
)


class ProviderTestRequestPort(ProviderApiConfigRequestPort, Protocol):
    capability: str


class ResponsesAdapterPort(Protocol):
    def send_request(self, request: Any) -> Any: ...


class ProbeSettingsFactoryPort(Protocol):
    def __call__(
        self,
        *,
        llm_provider: str,
        llm_api_key: str,
        llm_base_url: str,
        llm_model: str,
        llm_api_key_env: str,
        gemini_thinking_level: str,
        unity_mcp_command: list[str],
        unity_mcp_host: str,
        unity_mcp_port: int,
        unity_mcp_instance: str,
        unity_mcp_retries: int,
        unity_mcp_retry_backoff_seconds: float,
        unity_mcp_timeout_seconds: int,
        export_tool_name: str,
        execute_tool_name: str,
        export_path: Path,
        min_confidence: float,
        llm_api_type: str | None,
    ) -> Any: ...


class RuntimeRequestFactoryPort(Protocol):
    def __call__(
        self,
        *,
        model: str,
        prompt: str,
        instructions: str,
        reasoning_effort: str,
        max_output_tokens: int,
        mode: str,
        structured_output: bool,
    ) -> Any: ...


class ProviderTextProbePort(Protocol):
    def probe(
        self,
        config: ProviderApiConfig,
        prompt: str,
        *,
        structured: bool = False,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ProviderTestServicePorts:
    """Configuration and display policy used by the high-level test owner."""

    resolve_api_request: Callable[[ProviderApiConfigRequestPort], ProviderApiConfig]
    normalize_provider_name: Callable[[str], str]
    provider_display_name: Callable[[str], str]
    provider_config_descriptor: Callable[[ProviderApiConfig], dict[str, Any]]
    provider_requires_api_key: Callable[[str], bool]
    extract_json_block: Callable[[str], str]


@dataclass(frozen=True, slots=True)
class ProviderProbePolicyPorts:
    """Production request-shaping policy used by the bounded text probe."""

    validate_provider_api_key: Callable[[str], None]
    normalize_provider_api_type: Callable[[str, str, str | None], tuple[str, str]]
    resolve_vertex_project_location: Callable[[str], tuple[str, str]]
    build_gemini_generate_config: Callable[[Any, Any], Any]
    build_anthropic_request_payload: Callable[[Any, str], dict[str, Any]]
    build_openai_compatible_request_payload: Callable[[Any, str], dict[str, Any]]
    model_rejects_fixed_temperature: Callable[[str], bool]
    settings_factory: ProbeSettingsFactoryPort
    runtime_request_factory: RuntimeRequestFactoryPort


@dataclass(frozen=True, slots=True)
class ProviderProbeSdkPorts:
    """Lazy SDK factories; unit tests inject fakes and never use credentials."""

    responses_adapter: Callable[[str, str], ResponsesAdapterPort]
    google_client: Callable[[ProviderApiConfig, tuple[str, str] | None], Any]
    google_types: Callable[[], Any]
    anthropic_client: Callable[[str, str], Any]
    openai_client: Callable[[str, str | None, float], Any]


class ProviderTestIntegrationService:
    """Return the existing bounded connectivity-test result envelope."""

    def __init__(
        self,
        ports: ProviderTestServicePorts,
        probe: ProviderTextProbePort,
    ) -> None:
        self._ports = ports
        self._probe = probe

    def run(self, request: ProviderTestRequestPort) -> dict[str, Any]:
        capability = request.capability
        try:
            config = self._ports.resolve_api_request(request)
        except ValueError as exc:
            provider = self._ports.normalize_provider_name(request.provider)
            return {
                "ok": False,
                "status": "error",
                "capability": capability,
                "provider": provider,
                "providerLabel": self._ports.provider_display_name(provider),
                "model": str(request.model or ""),
                "message": str(exc),
            }
        provider_label = self._ports.provider_display_name(config.provider)
        descriptor = self._ports.provider_config_descriptor(config)
        if (
            self._ports.provider_requires_api_key(config.provider)
            and not config.api_key.strip()
        ):
            return {
                "ok": False,
                "status": "error",
                "capability": capability,
                "provider": config.provider,
                "providerLabel": provider_label,
                "model": config.model,
                "apiType": descriptor["apiType"],
                "resolvedApiType": descriptor["resolvedApiType"],
                "message": f"{provider_label} API key is empty.",
            }
        if capability == "vision":
            return {
                "ok": True,
                "status": "skipped",
                "skipped": True,
                "capability": capability,
                "provider": config.provider,
                "providerLabel": provider_label,
                "model": config.model,
                "apiType": descriptor["apiType"],
                "resolvedApiType": descriptor["resolvedApiType"],
                "message": (
                    "Vision test requires an explicit user-selected image; no Unity "
                    "screenshot or project asset was sent."
                ),
            }
        prompt = (
            "Return exactly: VRCForge provider test OK"
            if capability == "text"
            else 'Return compact JSON exactly like {"ok":true,"name":"vrcforge"}.'
        )
        candidates = provider_protocol_candidates(
            config.provider,
            config.model,
            config.api_type,
        )
        attempts: list[dict[str, Any]] = []
        successful: list[tuple[Any, str, bool]] = []
        for candidate in candidates:
            candidate_config = replace(
                config,
                provider=candidate.provider,
                model=candidate.model,
                api_type=candidate.api_type,
            )
            try:
                text = self._probe.probe(
                    candidate_config,
                    prompt,
                    structured=capability == "structured",
                )
                structured_ok = self._structured_ok(text) if capability == "structured" else True
                attempts.append(
                    {
                        "model": candidate.model,
                        "apiType": candidate.api_type,
                        "status": "verified" if structured_ok else "failed",
                    }
                )
                if structured_ok:
                    successful.append((candidate, text, structured_ok))
            except Exception as exc:  # noqa: BLE001
                attempts.append(
                    {
                        "model": candidate.model,
                        "apiType": candidate.api_type,
                        "status": (
                            "unsupported"
                            if is_explicit_protocol_compatibility_error(exc)
                            else "failed"
                        ),
                        "message": self._bounded_probe_error(exc, config.api_key),
                    }
                )
        if not successful:
            first_message = next(
                (str(item.get("message") or "") for item in attempts if item.get("message")),
                "Provider responded, but structured JSON did not validate.",
            )
            return {
                "ok": False,
                "status": "error" if any(item.get("message") for item in attempts) else "warning",
                "capability": capability,
                "provider": config.provider,
                "providerLabel": provider_label,
                "model": config.model,
                "apiType": descriptor["apiType"],
                "resolvedApiType": descriptor["resolvedApiType"],
                "attempts": attempts,
                "message": first_message,
            }
        recommended, text, structured_ok = successful[0]
        return {
            "ok": structured_ok,
            "status": "ok" if structured_ok else "warning",
            "capability": capability,
            "provider": config.provider,
            "providerLabel": provider_label,
            "model": config.model,
            "apiType": descriptor["apiType"],
            "resolvedApiType": recommended.api_type,
            "recommendedModel": recommended.model,
            "recommendedApiType": recommended.api_type,
            "attempts": attempts,
            "message": (
                "Provider test succeeded."
                if structured_ok
                else "Provider responded, but structured JSON did not validate."
            ),
            "responsePreview": text[:240],
        }

    def _structured_ok(self, text: str) -> bool:
        try:
            parsed = json.loads(self._ports.extract_json_block(text) or text)
            return isinstance(parsed, dict) and bool(parsed.get("ok"))
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _bounded_probe_error(error: BaseException, api_key: str) -> str:
        message = str(error)[:500]
        if api_key:
            message = message.replace(api_key, "[redacted]")
        return message

    def probe_text(
        self,
        config: ProviderApiConfig,
        prompt: str,
        *,
        structured: bool = False,
    ) -> str:
        return self._probe.probe(config, prompt, structured=structured)


class ProviderTextProbeRunner:
    """Perform one Provider text request through injected SDK factories."""

    def __init__(
        self,
        policy: ProviderProbePolicyPorts,
        sdk: ProviderProbeSdkPorts | None = None,
    ) -> None:
        self._policy = policy
        self._sdk = sdk or default_provider_probe_sdk_ports()

    def probe(
        self,
        config: ProviderApiConfig,
        prompt: str,
        *,
        structured: bool = False,
    ) -> str:
        self._policy.validate_provider_api_key(config.api_key)
        _requested_api_type, resolved_api_type = self._policy.normalize_provider_api_type(
            config.provider,
            config.model,
            config.api_type,
        )
        if resolved_api_type == "responses":
            adapter = self._sdk.responses_adapter(config.api_key, config.base_url)
            response = adapter.send_request(
                self._policy.runtime_request_factory(
                    model=config.model,
                    prompt=prompt,
                    instructions=(
                        "You are a provider connectivity probe. Return only the "
                        "requested result."
                    ),
                    reasoning_effort=config.thinking_level,
                    max_output_tokens=512 if structured else 64,
                    mode="probe",
                    structured_output=structured,
                )
            )
            return response.text
        if resolved_api_type == "generate_content":
            vertex_location: tuple[str, str] | None = None
            if config.provider == "vertexai":
                vertex_location = self._policy.resolve_vertex_project_location(
                    config.base_url
                )
            client = self._sdk.google_client(config, vertex_location)
            try:
                generate_kwargs: dict[str, Any] = {
                    "model": config.model,
                    "contents": prompt,
                }
                if config.thinking_level:
                    settings = self.probe_settings(config)
                    generate_config = self._policy.build_gemini_generate_config(
                        settings,
                        self._sdk.google_types(),
                    )
                    if generate_config is not None:
                        generate_kwargs["config"] = generate_config
                response = client.models.generate_content(**generate_kwargs)
                return str(getattr(response, "text", "") or response)
            finally:
                _close_sdk_client(client)
        if resolved_api_type == "messages":
            client = self._sdk.anthropic_client(config.api_key, config.base_url)
            try:
                request_payload = self._policy.build_anthropic_request_payload(
                    self.probe_settings(config),
                    prompt,
                )
                response = client.messages.create(**request_payload)
                parts = getattr(response, "content", []) or []
                texts = [str(getattr(part, "text", "") or "") for part in parts]
                return "\n".join(text for text in texts if text).strip()
            finally:
                _close_sdk_client(client)
        if not config.base_url.strip() and config.provider not in {"openai"}:
            raise RuntimeError("Base URL is empty.")
        kwargs = self._policy.build_openai_compatible_request_payload(
            self.probe_settings(config),
            prompt,
        )
        if self._policy.model_rejects_fixed_temperature(config.model):
            kwargs["max_completion_tokens"] = 512
        else:
            kwargs["max_tokens"] = 64
        if structured:
            kwargs["response_format"] = {"type": "json_object"}
        client = self._sdk.openai_client(
            config.api_key or "ollama",
            config.base_url or None,
            30.0,
        )
        try:
            response = client.chat.completions.create(**kwargs)
            choices = getattr(response, "choices", []) or []
            if not choices:
                return ""
            message = getattr(choices[0], "message", None)
            return str(getattr(message, "content", "") or "")
        finally:
            _close_sdk_client(client)

    def probe_settings(self, config: ProviderApiConfig) -> Any:
        return self._policy.settings_factory(
            llm_provider=config.provider,
            llm_api_key=config.api_key,
            llm_base_url=config.base_url,
            llm_model=config.model,
            llm_api_key_env="",
            gemini_thinking_level=config.thinking_level,
            unity_mcp_command=[],
            unity_mcp_host="127.0.0.1",
            unity_mcp_port=0,
            unity_mcp_instance="",
            unity_mcp_retries=0,
            unity_mcp_retry_backoff_seconds=0.0,
            unity_mcp_timeout_seconds=0,
            export_tool_name="",
            execute_tool_name="",
            export_path=Path("Assets/VRCForge/blendshapes_export.json"),
            min_confidence=0.0,
            llm_api_type=config.api_type,
        )


def default_provider_probe_sdk_ports() -> ProviderProbeSdkPorts:
    return ProviderProbeSdkPorts(
        responses_adapter=_default_responses_adapter,
        google_client=_default_google_client,
        google_types=_default_google_types,
        anthropic_client=_default_anthropic_client,
        openai_client=_default_openai_client,
    )


def _default_responses_adapter(api_key: str, base_url: str) -> ResponsesAdapterPort:
    from provider_runtime_adapters import OpenAIResponsesAdapter

    return OpenAIResponsesAdapter(api_key=api_key, base_url=base_url)


def _default_google_client(
    config: ProviderApiConfig,
    vertex_location: tuple[str, str] | None,
) -> Any:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("The google-genai package is not installed.") from exc
    if config.provider == "vertexai":
        if vertex_location is None:
            raise RuntimeError("Vertex AI project and location are unavailable.")
        project, location = vertex_location
        return genai.Client(vertexai=True, project=project, location=location)
    if config.provider == "custom":
        try:
            from google.genai import types as genai_types
        except ImportError as exc:
            raise RuntimeError("The installed google-genai package does not expose custom endpoint options.") from exc
        import httpx

        return genai.Client(
            api_key=config.api_key,
            http_options=genai_types.HttpOptions(
                baseUrl=normalize_provider_endpoint(config.base_url),
                httpxClient=httpx.Client(follow_redirects=False),
            ),
        )
    return genai.Client(api_key=config.api_key)


def _default_google_types() -> Any:
    try:
        from google.genai import types as genai_types
    except ImportError as exc:
        raise RuntimeError(
            "The installed google-genai package does not expose thinking "
            "configuration."
        ) from exc
    return genai_types


def _default_anthropic_client(api_key: str, base_url: str) -> Any:
    try:
        import anthropic
        import httpx
    except ImportError as exc:
        raise RuntimeError("The anthropic package is not installed.") from exc
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "http_client": httpx.Client(follow_redirects=False),
    }
    if base_url:
        kwargs["base_url"] = endpoint_for_protocol(base_url, "messages")
    return anthropic.Anthropic(**kwargs)


def _default_openai_client(
    api_key: str,
    base_url: str | None,
    timeout: float,
) -> Any:
    try:
        from openai import OpenAI
        import httpx
    except ImportError as exc:
        raise RuntimeError("The openai package is not installed.") from exc
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        http_client=httpx.Client(follow_redirects=False),
    )


def _close_sdk_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()
