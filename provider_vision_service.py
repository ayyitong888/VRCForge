"""Typed provider-Vision routing and SDK adaptation for app composition.

This module owns no configuration persistence, route, Gateway, process, file,
or approval lifecycle.  Callers provide the app-lifetime configuration and
provider-policy ports explicitly.  Image bytes prefer an enabled independent
Vision profile; without one they use the configured main model as the default
image channel.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence


VISION_CAPABLE_MODEL_MARKERS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-5",
    "chatgpt-4o",
    "omni",
    "vision",
    "llava",
    "gemini",
    "claude",
    "pixtral",
    "internvl",
    "minicpm-v",
    "moondream",
    "glm-4v",
    "glm-4.5v",
    "gemma-3",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "kimi-vl",
    "step-1v",
    "yi-vision",
    "phi-3-vision",
    "phi-3.5-vision",
    "llama-3.2-11b",
    "llama-3.2-90b",
    "llama-4",
)
VISION_CAPABLE_MODEL_RE = re.compile(r"(^|[-_/.])(o[134])([-_.]|$)|(^|[-_/.])vl([-_.]|$)")
VISION_IMAGE_MAX_ITEMS = 8
VISION_IMAGE_MAX_BYTES = 10 * 1024 * 1024


class VisionInputError(RuntimeError):
    """The local image envelope is invalid and must never reach a Provider SDK."""


@dataclass(frozen=True, slots=True)
class VisionModelConfig:
    provider: str
    api_key: str
    base_url: str
    model: str


@dataclass(frozen=True, slots=True)
class VisionProfileConfig(VisionModelConfig):
    enabled: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.model)


@dataclass(frozen=True, slots=True)
class ProviderVisionStatePorts:
    """Read the current app-owned provider configuration without persisting it."""

    main_config: Callable[[], VisionModelConfig]
    profile_config: Callable[[], VisionProfileConfig]


@dataclass(frozen=True, slots=True)
class ProviderVisionPolicyPorts:
    """Existing provider policy required by routing and SDK request shaping."""

    normalize_provider_name: Callable[[str], str]
    provider_requires_api_key: Callable[[str], bool]
    provider_display_name: Callable[[str], str]
    validate_provider_api_key: Callable[[str], None]
    resolve_vertex_project_location: Callable[[str], tuple[str, str]]
    model_rejects_fixed_temperature: Callable[[str], bool]


class VisionProviderRunnerPort(Protocol):
    def run(
        self,
        config: VisionModelConfig,
        prompt: str,
        images: Sequence[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class ProviderVisionSdkPorts:
    """Lazy SDK client factories; tests inject local fakes with no network."""

    google_client: Callable[[VisionModelConfig, tuple[str, str] | None], Any]
    google_part_from_bytes: Callable[[bytes, str], Any]
    anthropic_client: Callable[[str], Any]
    openai_client: Callable[[str, str | None, float], Any]


class ProviderVisionService:
    """Select the honest Vision lane and return the existing Gateway payload."""

    def __init__(
        self,
        state: ProviderVisionStatePorts,
        policy: ProviderVisionPolicyPorts,
        runner: VisionProviderRunnerPort,
    ) -> None:
        self._state = state
        self._policy = policy
        self._runner = runner

    def _resolve_route(
        self,
    ) -> tuple[VisionModelConfig | None, str, str]:
        profile = self._state.profile_config()
        if profile.configured and profile.enabled:
            if self._policy.provider_requires_api_key(profile.provider) and not profile.api_key.strip():
                return (
                    None,
                    "",
                    (
                        f"The vision profile ({self._policy.provider_display_name(profile.provider)}) "
                        "has no API key."
                    ),
                )
            return (
                VisionModelConfig(
                    provider=profile.provider,
                    api_key=profile.api_key,
                    base_url=profile.base_url,
                    model=profile.model,
                ),
                "visionProfile",
                "",
            )

        main = self._state.main_config()
        if main.provider and main.model:
            # Model-name capability hints are informational only.  The selected
            # provider owns the authoritative answer: always send the image
            # through its multimodal request channel and return any provider
            # rejection as the bounded visual-action failure.
            if self._policy.provider_requires_api_key(main.provider) and not main.api_key.strip():
                return (
                    None,
                    "",
                    (
                        f"The main model ({self._policy.provider_display_name(main.provider)}) "
                        "has no API key."
                    ),
                )
            return main, "main", ""

        if profile.configured:
            return None, "", "The configured vision profile is disabled in settings."
        return (
            None,
            "",
            "No enabled vision profile or main model is configured.",
        )

    def capability(self) -> dict[str, Any]:
        """Return bounded provider-neutral availability without exposing secrets."""

        config, source, reason = self._resolve_route()
        if config is None:
            return {"available": False, "reason": reason}
        return {
            "available": True,
            "provider": config.provider,
            "providerLabel": self._policy.provider_display_name(config.provider),
            "model": config.model,
            "source": source,
        }

    def analyze(self, message: str, images: list[dict[str, Any]]) -> dict[str, Any]:
        return self.analyze_prompt(build_vision_analysis_prompt(message, images), images)

    def analyze_prompt(self, prompt: str, images: list[dict[str, Any]]) -> dict[str, Any]:
        """Run one exact caller-owned prompt through the selected visual provider."""

        config, source, reason = self._resolve_route()
        if config is None:
            return {"status": "unconfigured", "reason": reason}

        try:
            text, usage = self._runner.run(config, prompt, images)
            if not text.strip():
                raise RuntimeError(
                    f"{self._policy.provider_display_name(config.provider)} returned an empty vision analysis."
                )
        except Exception as exc:  # noqa: BLE001 - provider failures are a typed visual result.
            error_type, retryable, retain_images = classify_vision_provider_error(exc)
            return {
                "status": "error",
                "error": bounded_provider_error_text(exc),
                "errorType": error_type,
                "retryable": retryable,
                "retainImages": retain_images,
                "provider": config.provider,
                "providerLabel": self._policy.provider_display_name(config.provider),
                "model": config.model,
                "source": source,
            }
        return {
            "status": "analyzed",
            "text": text,
            "provider": config.provider,
            "providerLabel": self._policy.provider_display_name(config.provider),
            "model": config.model,
            "source": source,
            "usage": usage,
        }

    def model_supports_vision(self, provider: str, model: str) -> bool:
        """Return an informational name hint; routing never rejects unknown models."""

        provider = self._policy.normalize_provider_name(provider)
        model_id = str(model or "").strip().lower()
        if provider in {"gemini", "vertexai"}:
            return True
        if provider == "anthropic":
            return True
        if provider == "deepseek":
            return False
        return any(marker in model_id for marker in VISION_CAPABLE_MODEL_MARKERS) or bool(
            VISION_CAPABLE_MODEL_RE.search(model_id)
        )


def bounded_provider_error_text(exc: Exception, limit: int = 500) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def classify_vision_provider_error(exc: Exception) -> tuple[str, bool, bool]:
    """Classify whether the exact image payload may be retained for retry."""

    if isinstance(exc, VisionInputError):
        return "input_invalid", False, False

    status_code: int | None = None
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        try:
            status_code = int(candidate)
        except (TypeError, ValueError):
            continue
        break

    message = bounded_provider_error_text(exc).casefold()
    class_name = type(exc).__name__.casefold()
    transient_markers = (
        "timeout",
        "timed out",
        "connection",
        "rate limit",
        "temporarily unavailable",
        "service unavailable",
        "gateway timeout",
        "connection reset",
    )
    if (
        isinstance(exc, (TimeoutError, ConnectionError))
        or status_code in {408, 429}
        or (status_code is not None and 500 <= status_code <= 599)
        or any(marker in class_name or marker in message for marker in transient_markers)
    ):
        return "transient_provider_failure", True, True

    rejection_markers = (
        "image input is not supported",
        "does not support image",
        "images are not supported",
        "unsupported image",
        "vision is not supported",
        "multimodal is not supported",
        "unsupported content type",
    )
    if (
        (status_code is not None and 400 <= status_code <= 499)
        or any(marker in message for marker in rejection_markers)
    ):
        return "provider_rejected", False, False

    return "provider_failure", False, False


class ProviderVisionSdkRunner:
    """Perform one bounded multimodal SDK request for the selected provider."""

    def __init__(
        self,
        policy: ProviderVisionPolicyPorts,
        sdk: ProviderVisionSdkPorts | None = None,
    ) -> None:
        self._policy = policy
        self._sdk = sdk or default_provider_vision_sdk_ports()

    def run(
        self,
        config: VisionModelConfig,
        prompt: str,
        images: Sequence[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        self._policy.validate_provider_api_key(config.api_key)
        if len(images) > VISION_IMAGE_MAX_ITEMS:
            raise VisionInputError(
                f"Visual analysis accepts at most {VISION_IMAGE_MAX_ITEMS} image attachments."
            )
        if any(bool(item.get("truncated")) for item in images):
            raise VisionInputError("Truncated image attachments cannot be sent for visual analysis.")
        decoded = [split_image_data_url(str(item.get("dataUrl") or "")) for item in images]
        if not decoded:
            raise RuntimeError("No image payloads to analyze.")

        if config.provider in {"gemini", "vertexai"}:
            return self._run_google(config, prompt, decoded)
        if config.provider == "anthropic":
            return self._run_anthropic(config, prompt, decoded)
        return self._run_openai_compatible(config, prompt, decoded)

    def _run_google(
        self,
        config: VisionModelConfig,
        prompt: str,
        decoded: Sequence[tuple[str, str]],
    ) -> tuple[str, dict[str, Any]]:
        vertex_location: tuple[str, str] | None = None
        if config.provider == "vertexai":
            vertex_location = self._policy.resolve_vertex_project_location(config.base_url)
        client = self._sdk.google_client(config, vertex_location)
        contents: list[Any] = [
            self._sdk.google_part_from_bytes(base64.b64decode(payload), mime)
            for mime, payload in decoded
        ]
        contents.append(prompt)
        response = client.models.generate_content(model=config.model, contents=contents)
        text = str(getattr(response, "text", "") or "").strip()
        metadata = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(metadata, "prompt_token_count", None)
        output_tokens = getattr(metadata, "candidates_token_count", None)
        total_tokens = getattr(metadata, "total_token_count", None)
        if prompt_tokens is None and output_tokens is None and total_tokens is None:
            usage: dict[str, Any] = {"exact": False, "unavailableReason": "provider_usage_missing"}
        else:
            usage = {"exact": True}
            if prompt_tokens is not None:
                usage["inputTokens"] = int(prompt_tokens)
            if output_tokens is not None:
                usage["outputTokens"] = int(output_tokens)
            if total_tokens is not None:
                usage["totalTokens"] = int(total_tokens)
        return text, usage

    def _run_anthropic(
        self,
        config: VisionModelConfig,
        prompt: str,
        decoded: Sequence[tuple[str, str]],
    ) -> tuple[str, dict[str, Any]]:
        client = self._sdk.anthropic_client(config.api_key)
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": payload},
            }
            for mime, payload in decoded
        ]
        content.append({"type": "text", "text": prompt})
        response = client.messages.create(
            model=config.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )
        parts = getattr(response, "content", []) or []
        text = "\n".join(str(getattr(part, "text", "") or "") for part in parts).strip()
        usage_obj = getattr(response, "usage", None)
        input_tokens = getattr(usage_obj, "input_tokens", None)
        output_tokens = getattr(usage_obj, "output_tokens", None)
        if input_tokens is None and output_tokens is None:
            usage: dict[str, Any] = {"exact": False, "unavailableReason": "provider_usage_missing"}
        else:
            usage = {"exact": True}
            if input_tokens is not None:
                usage["inputTokens"] = int(input_tokens)
            if output_tokens is not None:
                usage["outputTokens"] = int(output_tokens)
            if input_tokens is not None and output_tokens is not None:
                usage["totalTokens"] = int(input_tokens) + int(output_tokens)
        return text, usage

    def _run_openai_compatible(
        self,
        config: VisionModelConfig,
        prompt: str,
        decoded: Sequence[tuple[str, str]],
    ) -> tuple[str, dict[str, Any]]:
        if not config.base_url.strip() and config.provider not in {"openai"}:
            raise RuntimeError("Base URL is empty.")
        message_content: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{payload}"}}
            for mime, payload in decoded
        ]
        message_content.append({"type": "text", "text": prompt})
        client = self._sdk.openai_client(config.api_key or "ollama", config.base_url or None, 60.0)
        vision_kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": [{"role": "user", "content": message_content}],
        }
        # Ollama's OpenAI-compatible endpoint otherwise lets thinking-capable
        # local models spend the entire bounded completion budget on hidden
        # reasoning and return an empty visual result. This is a request-local
        # output control; it does not alter the saved model or other providers.
        if config.provider == "ollama":
            vision_kwargs["reasoning_effort"] = "none"
        if self._policy.model_rejects_fixed_temperature(config.model):
            vision_kwargs["max_completion_tokens"] = 1024
        else:
            vision_kwargs["temperature"] = 0
            vision_kwargs["max_tokens"] = 1024
        response = client.chat.completions.create(**vision_kwargs)
        choices = getattr(response, "choices", []) or []
        text = ""
        if choices:
            message_obj = getattr(choices[0], "message", None)
            text = str(getattr(message_obj, "content", "") or "").strip()
        return text, extract_openai_usage(response)


def default_provider_vision_sdk_ports() -> ProviderVisionSdkPorts:
    return ProviderVisionSdkPorts(
        google_client=_default_google_client,
        google_part_from_bytes=_default_google_part_from_bytes,
        anthropic_client=_default_anthropic_client,
        openai_client=_default_openai_client,
    )


def _default_google_client(
    config: VisionModelConfig,
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
    return genai.Client(api_key=config.api_key)


def _default_google_part_from_bytes(data: bytes, mime_type: str) -> Any:
    try:
        from google.genai import types as genai_types
    except ImportError as exc:
        raise RuntimeError("The google-genai package is not installed.") from exc
    return genai_types.Part.from_bytes(data=data, mime_type=mime_type)


def _default_anthropic_client(api_key: str) -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("The anthropic package is not installed.") from exc
    return anthropic.Anthropic(api_key=api_key)


def _default_openai_client(api_key: str, base_url: str | None, timeout: float) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The openai package is not installed.") from exc
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


def split_image_data_url(data_url: str) -> tuple[str, str]:
    """Split one base64 image data URL without decoding or sending it."""

    value = str(data_url or "")
    if not value.startswith("data:"):
        raise VisionInputError("Attachment payload is not a data URL.")
    header, _, payload = value.partition(",")
    if not payload:
        raise VisionInputError("Attachment data URL has no payload.")
    mime = header[5:].split(";", 1)[0].strip().lower() or "image/png"
    if not mime.startswith("image/"):
        raise VisionInputError(f"Attachment data URL is not an image ({mime}).")
    if "base64" not in header:
        raise VisionInputError("Attachment data URL is not base64-encoded.")
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VisionInputError("Image attachment base64 payload is invalid.") from exc
    if len(decoded) > VISION_IMAGE_MAX_BYTES:
        raise VisionInputError(
            f"Image attachment exceeds the {VISION_IMAGE_MAX_BYTES // (1024 * 1024)} MB visual input limit."
        )
    return mime, payload


def build_vision_analysis_prompt(message: str, images: Sequence[dict[str, Any]]) -> str:
    names = ", ".join(str(item.get("name") or "image") for item in images[:8])
    user_context = str(message or "").strip()
    lines = [
        "You are the image-analysis assistant of VRCForge, a VRChat avatar tool.",
        f"Describe the attached image(s) ({names}) precisely and concisely for a text-only",
        "planning model that cannot see them. Focus on what is visually present:",
        "UI state, error text, avatar/mesh/material details, colors, layout, and any",
        "readable text (transcribe it exactly). Do not speculate beyond the image.",
        "Answer in the same language as the user request below.",
    ]
    if user_context:
        lines.append(f"\nUser request (for context): {user_context[:2000]}")
    return "\n".join(lines)


def extract_openai_usage(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return {"exact": False, "unavailableReason": "provider_usage_missing"}
    payload: dict[str, Any] = {"exact": True}
    if prompt_tokens is not None:
        payload["inputTokens"] = int(prompt_tokens)
    if completion_tokens is not None:
        payload["outputTokens"] = int(completion_tokens)
    if total_tokens is not None:
        payload["totalTokens"] = int(total_tokens)
    return payload
