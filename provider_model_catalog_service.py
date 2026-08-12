"""Typed Provider model-catalog ownership for app composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from provider_configuration_service import ProviderApiConfig


@dataclass(frozen=True, slots=True)
class ProviderModelCatalogPolicyPorts:
    validate_provider_api_key: Callable[[str], None]
    provider_requires_api_key: Callable[[str], bool]
    provider_display_name: Callable[[str], str]
    provider_model_descriptor: Callable[[str, str, str | None], dict[str, Any]]
    resolve_vertex_project_location: Callable[[str], tuple[str, str]]


@dataclass(frozen=True, slots=True)
class ProviderModelCatalogSdkPorts:
    """Lazy SDK client factories; unit tests inject fakes with no network."""

    openai_client: Callable[[str, str, float], Any]
    google_ai_studio_client: Callable[[str], Any]
    google_vertex_client: Callable[[str, str], Any]
    anthropic_client: Callable[[str], Any]


class ProviderModelCatalogService:
    """List and shape provider models through explicit policy and SDK ports."""

    def __init__(
        self,
        policy: ProviderModelCatalogPolicyPorts,
        sdk: ProviderModelCatalogSdkPorts | None = None,
    ) -> None:
        self._policy = policy
        self._sdk = sdk or default_provider_model_catalog_sdk_ports()

    def provider_config_descriptor(self, config: ProviderApiConfig) -> dict[str, Any]:
        return self._policy.provider_model_descriptor(
            config.provider,
            config.model,
            config.api_type,
        )

    def enrich_provider_model_item(
        self,
        config: ProviderApiConfig,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = dict(item)
        model_id = str(enriched.get("id") or "").strip()
        descriptor = self._policy.provider_model_descriptor(
            config.provider,
            model_id,
            config.api_type,
        )
        enriched.update(
            provider=config.provider,
            apiType=descriptor["apiType"],
            resolvedApiType=descriptor["resolvedApiType"],
            supportedApiTypes=descriptor["supportedApiTypes"],
            capabilities=descriptor["capabilities"],
            capabilitySource=descriptor["capabilitySource"],
        )
        for key in ("modelContextWindow", "maxOutputTokens", "modelVersion"):
            value = descriptor.get(key)
            if value is not None:
                enriched[key] = value
        return enriched

    def fetch_provider_models(self, config: ProviderApiConfig) -> list[dict[str, Any]]:
        self._policy.validate_provider_api_key(config.api_key)
        if self._policy.provider_requires_api_key(config.provider) and not config.api_key.strip():
            raise RuntimeError(
                f"{self._policy.provider_display_name(config.provider)} API key is empty. "
                "Enter an API key before loading models."
            )
        if config.provider == "gemini":
            return self.fetch_google_ai_studio_models(config)
        if config.provider == "vertexai":
            return self.fetch_vertex_ai_models(config)
        if config.provider == "anthropic":
            return self.fetch_anthropic_models(config)
        return self.fetch_openai_compatible_models(config)

    def fetch_openai_compatible_models(
        self,
        config: ProviderApiConfig,
    ) -> list[dict[str, Any]]:
        self._policy.validate_provider_api_key(config.api_key)
        if not config.base_url.strip():
            raise RuntimeError(
                "Base URL is empty. Enter a provider API endpoint before loading models."
            )
        client = self._sdk.openai_client(
            config.api_key or "ollama",
            config.base_url,
            20.0,
        )
        try:
            response = client.models.list()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"{self._policy.provider_display_name(config.provider)} "
                f"model list request failed: {exc}"
            ) from exc
        return self.normalize_provider_model_list(
            response,
            self._policy.provider_display_name(config.provider),
        )

    def fetch_google_ai_studio_models(
        self,
        config: ProviderApiConfig,
    ) -> list[dict[str, Any]]:
        self._policy.validate_provider_api_key(config.api_key)
        client = self._sdk.google_ai_studio_client(config.api_key)
        try:
            response = client.models.list()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Google AI Studio model list request failed: {exc}"
            ) from exc
        return self.normalize_provider_model_list(response, "Google AI Studio")

    def fetch_vertex_ai_models(
        self,
        config: ProviderApiConfig,
    ) -> list[dict[str, Any]]:
        project, location = self._policy.resolve_vertex_project_location(config.base_url)
        try:
            client = self._sdk.google_vertex_client(project, location)
            response = client.models.list()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "Google Vertex AI model list request failed for project "
                f"'{project}' / location '{location}': {exc}"
            ) from exc
        return self.normalize_provider_model_list(response, "Google Vertex AI")

    def fetch_anthropic_models(
        self,
        config: ProviderApiConfig,
    ) -> list[dict[str, Any]]:
        self._policy.validate_provider_api_key(config.api_key)
        client = self._sdk.anthropic_client(config.api_key)
        models_api = getattr(client, "models", None)
        list_models = getattr(models_api, "list", None)
        if not callable(list_models):
            raise RuntimeError(
                "The installed Anthropic SDK does not expose models.list(). "
                "Use manual model input."
            )
        try:
            try:
                response = list_models(limit=100)
            except TypeError:
                response = list_models()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Anthropic model list request failed: {exc}") from exc
        return self.normalize_provider_model_list(response, "Anthropic")

    def normalize_provider_model_list(
        self,
        response: Any,
        provider_label: str,
    ) -> list[dict[str, Any]]:
        raw_items: Any = response
        if isinstance(response, dict):
            raw_items = response.get("data") or response.get("models") or []
        else:
            raw_items = getattr(response, "data", response)
        try:
            items = list(raw_items or [])
        except TypeError:
            items = []

        models_by_id: dict[str, dict[str, Any]] = {}
        for item in items:
            if isinstance(item, dict):
                model_id = item.get("id") or item.get("name")
            else:
                model_id = getattr(item, "id", None) or getattr(item, "name", None)
            if not model_id:
                continue
            normalized_id = str(model_id).strip()
            if normalized_id:
                models_by_id.setdefault(
                    normalized_id,
                    self.build_provider_model_info(item, normalized_id),
                )
        models = sorted(
            models_by_id.values(),
            key=lambda model: model["id"].casefold(),
        )
        if not models:
            raise RuntimeError(f"{provider_label} returned no models.")
        return models

    @staticmethod
    def read_model_attr(item: Any, *names: str) -> Any:
        for name in names:
            if isinstance(item, dict) and name in item:
                return item.get(name)
            value = getattr(item, name, None)
            if value is not None:
                return value
        return None

    @staticmethod
    def coerce_positive_int(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def build_provider_model_info(self, item: Any, model_id: str) -> dict[str, Any]:
        label = str(
            self.read_model_attr(
                item,
                "label",
                "display_name",
                "displayName",
                "name",
            )
            or model_id
        ).strip() or model_id
        info: dict[str, Any] = {"id": model_id, "label": label}
        field_map = {
            "contextWindow": (
                "contextWindow",
                "context_window",
                "contextLength",
                "context_length",
                "maxContextTokens",
                "max_context_tokens",
            ),
            "inputTokenLimit": (
                "inputTokenLimit",
                "input_token_limit",
                "inputTokenCountLimit",
                "input_token_count_limit",
            ),
            "maxInputTokens": ("maxInputTokens", "max_input_tokens"),
            "outputTokenLimit": ("outputTokenLimit", "output_token_limit"),
            "maxOutputTokens": ("maxOutputTokens", "max_output_tokens"),
        }
        for out_key, candidates in field_map.items():
            value = self.coerce_positive_int(self.read_model_attr(item, *candidates))
            if value is not None:
                info[out_key] = value
        return info


def default_provider_model_catalog_sdk_ports() -> ProviderModelCatalogSdkPorts:
    return ProviderModelCatalogSdkPorts(
        openai_client=_default_openai_client,
        google_ai_studio_client=_default_google_ai_studio_client,
        google_vertex_client=_default_google_vertex_client,
        anthropic_client=_default_anthropic_client,
    )


def _default_openai_client(api_key: str, base_url: str, timeout: float) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "The openai package is not installed, so OpenAI-compatible model "
            "listing is unavailable."
        ) from exc
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


def _default_google_ai_studio_client(api_key: str) -> Any:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "The google-genai package is not installed, so Google AI Studio "
            "model listing is unavailable."
        ) from exc
    return genai.Client(api_key=api_key)


def _default_google_vertex_client(project: str, location: str) -> Any:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "The google-genai package is not installed, so Google Vertex AI "
            "model listing is unavailable."
        ) from exc
    return genai.Client(vertexai=True, project=project, location=location)


def _default_anthropic_client(api_key: str) -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The anthropic package is not installed, so Anthropic model "
            "listing is unavailable."
        ) from exc
    return anthropic.Anthropic(api_key=api_key)
