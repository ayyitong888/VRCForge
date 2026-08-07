from __future__ import annotations

from typing import Any


class ProviderModelCatalogService:
    """Own provider-model catalogue adapters behind Dashboard late-bound facades."""

    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl_provider_config_descriptor(self, config: DashboardApiConfig) -> dict[str, Any]:
        """Expose a non-secret, model-aware provider transport descriptor."""

        return self._host.provider_model_descriptor(config.provider, config.model, config.api_type)

    def _impl_enrich_provider_model_item(self, config: DashboardApiConfig, item: dict[str, Any]) -> dict[str, Any]:
        """Keep provider list metadata while adding conservative registry fields."""

        enriched = dict(item)
        model_id = str(enriched.get("id") or "").strip()
        descriptor = self._host.provider_model_descriptor(config.provider, model_id, config.api_type)
        enriched.update(
            provider=config.provider,
            apiType=descriptor["apiType"],
            resolvedApiType=descriptor["resolvedApiType"],
            supportedApiTypes=descriptor["supportedApiTypes"],
            capabilities=descriptor["capabilities"],
            capabilitySource=descriptor["capabilitySource"],
        )
        return enriched

    def _impl_fetch_provider_models(self, config: DashboardApiConfig) -> list[dict[str, Any]]:
        self._host.validate_provider_api_key(config.api_key)
        if self._host.provider_requires_api_key(config.provider) and not config.api_key.strip():
            raise RuntimeError(
                f"{self._host.provider_display_name(config.provider)} API key is empty. Enter an API key before loading models."
            )

        if config.provider == "gemini":
            return self._host.fetch_google_ai_studio_models(config)
        if config.provider == "vertexai":
            return self._host.fetch_vertex_ai_models(config)
        if config.provider == "anthropic":
            return self._host.fetch_anthropic_models(config)
        return self._host.fetch_openai_compatible_models(config)

    def _impl_fetch_openai_compatible_models(self, config: DashboardApiConfig) -> list[dict[str, Any]]:
        self._host.validate_provider_api_key(config.api_key)
        if not config.base_url.strip():
            raise RuntimeError("Base URL is empty. Enter a provider API endpoint before loading models.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package is not installed, so OpenAI-compatible model listing is unavailable.") from exc

        client = OpenAI(api_key=config.api_key or "ollama", base_url=config.base_url, timeout=20.0)
        try:
            response = client.models.list()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"{self._host.provider_display_name(config.provider)} model list request failed: {exc}") from exc

        return self._host.normalize_provider_model_list(response, self._host.provider_display_name(config.provider))

    def _impl_fetch_google_ai_studio_models(self, config: DashboardApiConfig) -> list[dict[str, Any]]:
        self._host.validate_provider_api_key(config.api_key)
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("The google-genai package is not installed, so Google AI Studio model listing is unavailable.") from exc

        client = genai.Client(api_key=config.api_key)
        try:
            response = client.models.list()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Google AI Studio model list request failed: {exc}") from exc

        return self._host.normalize_provider_model_list(response, "Google AI Studio")

    def _impl_fetch_vertex_ai_models(self, config: DashboardApiConfig) -> list[dict[str, Any]]:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("The google-genai package is not installed, so Google Vertex AI model listing is unavailable.") from exc

        project, location = self._host.resolve_vertex_project_location(config.base_url)
        try:
            client = genai.Client(vertexai=True, project=project, location=location)
            response = client.models.list()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Google Vertex AI model list request failed for project '{project}' / location '{location}': {exc}"
            ) from exc

        return self._host.normalize_provider_model_list(response, "Google Vertex AI")

    def _impl_fetch_anthropic_models(self, config: DashboardApiConfig) -> list[dict[str, Any]]:
        self._host.validate_provider_api_key(config.api_key)
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("The anthropic package is not installed, so Anthropic model listing is unavailable.") from exc

        client = anthropic.Anthropic(api_key=config.api_key)
        models_api = getattr(client, "models", None)
        list_models = getattr(models_api, "list", None)
        if not callable(list_models):
            raise RuntimeError("The installed Anthropic SDK does not expose models.list(). Use manual model input.")

        try:
            try:
                response = list_models(limit=100)
            except TypeError:
                response = list_models()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Anthropic model list request failed: {exc}") from exc

        return self._host.normalize_provider_model_list(response, "Anthropic")

    def _impl_normalize_provider_model_list(self, response: Any, provider_label: str) -> list[dict[str, Any]]:
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

            model_id = str(model_id).strip()
            if model_id:
                models_by_id.setdefault(model_id, self._host.build_provider_model_info(item, model_id))

        models = sorted(models_by_id.values(), key=lambda model: model["id"].casefold())
        if not models:
            raise RuntimeError(f"{provider_label} returned no models.")
        return models

    def _impl_read_model_attr(self, item: Any, *names: str) -> Any:
        for name in names:
            if isinstance(item, dict) and name in item:
                return item.get(name)
            value = getattr(item, name, None)
            if value is not None:
                return value
        return None

    def _impl_coerce_positive_int(self, value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    def _impl_build_provider_model_info(self, item: Any, model_id: str) -> dict[str, Any]:
        label = str(self._host.read_model_attr(item, "label", "display_name", "displayName", "name") or model_id).strip() or model_id
        info: dict[str, Any] = {"id": model_id, "label": label}
        field_map = {
            "contextWindow": ("contextWindow", "context_window", "contextLength", "context_length", "maxContextTokens", "max_context_tokens"),
            "inputTokenLimit": ("inputTokenLimit", "input_token_limit", "inputTokenCountLimit", "input_token_count_limit"),
            "maxInputTokens": ("maxInputTokens", "max_input_tokens"),
            "outputTokenLimit": ("outputTokenLimit", "output_token_limit"),
            "maxOutputTokens": ("maxOutputTokens", "max_output_tokens"),
        }
        for out_key, candidates in field_map.items():
            value = self._host.coerce_positive_int(self._host.read_model_attr(item, *candidates))
            if value is not None:
                info[out_key] = value
        return info
