"""Small shared guards for provider adapter inputs."""

from __future__ import annotations


class ProviderCredentialError(ValueError):
    """A provider credential cannot safely be placed in an HTTP header."""


_CREDENTIAL_ERROR = "API key is invalid. Re-enter the API key."
_API_TYPE_ERROR = "The selected provider API type is not supported for this model."

API_TYPES = frozenset({"auto", "chat_completions", "responses", "generate_content", "messages"})
_LEGACY_API_TYPES = {
    "deepseek": "chat_completions",
    "openai": "chat_completions",
    "openrouter": "chat_completions",
    "ollama": "chat_completions",
    "custom": "chat_completions",
    "gemini": "generate_content",
    "vertexai": "generate_content",
    "anthropic": "messages",
}
_FIXED_PROVIDER_TYPES = {
    "gemini": "generate_content",
    "vertexai": "generate_content",
    "anthropic": "messages",
}


class ProviderApiTypeError(ValueError):
    """A provider/model API transport selection is unsupported."""


def legacy_provider_api_type(provider: str) -> str:
    """Return the transport used by configurations created before MCP2."""

    return _LEGACY_API_TYPES.get(str(provider).strip().lower(), "chat_completions")


def normalize_provider_api_type(provider: str, model: str, api_type: object) -> tuple[str, str]:
    """Validate requested transport and return (requested, resolved).

    ``None`` is deliberately a legacy signal, rather than ``auto``: existing
    saved configurations must retain their established provider transport.
    """

    provider_id = str(provider).strip().lower()
    # DeepSeek model identifiers are protocol values, not display labels.  Do
    # not silently canonicalize a near-match onto a different transport.
    model_id = str(model).strip()
    requested = legacy_provider_api_type(provider_id) if api_type is None else str(api_type).strip().lower()
    if requested not in API_TYPES:
        raise ProviderApiTypeError(_API_TYPE_ERROR)

    fixed = _FIXED_PROVIDER_TYPES.get(provider_id)
    if fixed:
        if requested not in {"auto", fixed}:
            raise ProviderApiTypeError(_API_TYPE_ERROR)
        return requested, fixed

    if requested == "auto":
        if provider_id == "deepseek" and model_id == "deepseek-v4-flash":
            return requested, "responses"
        return requested, "chat_completions"
    if requested == "responses":
        if provider_id != "deepseek" or model_id != "deepseek-v4-flash":
            raise ProviderApiTypeError(_API_TYPE_ERROR)
        return requested, "responses"
    if requested != "chat_completions":
        raise ProviderApiTypeError(_API_TYPE_ERROR)
    return requested, "chat_completions"


def provider_model_descriptor(provider: str, model: str, api_type: object) -> dict[str, object]:
    """Return conservative capability metadata without guessing unknown models."""

    requested, resolved = normalize_provider_api_type(provider, model, api_type)
    provider_id = str(provider).strip().lower()
    model_id = str(model).strip()
    descriptor: dict[str, object] = {
        "api_type": requested,
        "apiType": requested,
        "resolvedApiType": resolved,
        "supportedApiTypes": [resolved],
        "capabilities": [],
        "capabilitySource": "unknown",
        "modelRegistrySchema": "vrcforge.provider-model-registry.v1",
    }
    if provider_id == "deepseek" and model_id == "deepseek-v4-flash":
        descriptor.update(
            supportedApiTypes=["responses", "chat_completions"],
            capabilities=["text", "structured_json", "reasoning", "tools"],
            capabilitySource="official_registry",
        )
    elif provider_id == "deepseek" and model_id == "deepseek-v4-pro":
        descriptor.update(
            supportedApiTypes=["chat_completions"],
            capabilities=["text", "structured_json", "reasoning", "tools"],
            capabilitySource="official_registry",
        )
    return descriptor


def validate_provider_api_key(value: object) -> str:
    """Return an unchanged empty/printable-ASCII credential, or fail safely.

    Provider keys are opaque, so this deliberately has no provider-specific
    prefix or length policy.  Non-empty values must be safe Bearer/header text.
    """

    key = "" if value is None else str(value)
    if not key:
        return ""
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in key):
        raise ProviderCredentialError(_CREDENTIAL_ERROR)
    return key
