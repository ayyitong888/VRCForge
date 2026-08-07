from __future__ import annotations

from typing import Any


class ProviderConfigurationService:
    """Own Provider API/Vision document handling behind Dashboard facades.

    The Dashboard remains the lifetime owner for configuration globals, locks,
    paths and startup.  This migration-time service reaches those facilities
    only through its host so existing Dashboard contracts stay patchable until
    the final 1.5 composition pass replaces these facades with typed ports.
    """

    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl_load_initial_dashboard_api_config(self) -> DashboardApiConfig:
        settings_path = self._host.RUNTIME_SETTINGS_PATH
        settings = self._host.load_runtime_settings_safely(settings_path, loader=self._host.load_settings)
        config_document = self._host.load_config_document()
        raw_api_section = config_document.get("api")
        api_section = raw_api_section if isinstance(raw_api_section, dict) else {}

        def build(section: dict[str, Any]) -> DashboardApiConfig:
            provider = self._host.normalize_provider_name(
                section.get("provider") or settings.llm_provider or self._host.DEFAULT_LLM_PROVIDER
            )
            defaults = self._host.get_provider_defaults(provider)
            api_key = str(section.get("api_key") or settings.llm_api_key).strip()
            base_url = self._host.normalize_base_url(section.get("base_url"), provider, defaults["base_url"])
            model = str(section.get("model") or settings.llm_model or defaults["model"]).strip() or defaults["model"]
            raw_thinking_level = (
                section.get("thinking_level")
                if "thinking_level" in section
                else settings.gemini_thinking_level
            )
            return self._host.DashboardApiConfig(
                provider=provider,
                api_key=api_key,
                base_url=base_url,
                model=model,
                api_type=self._host.normalize_provider_api_type(provider, model, section.get("api_type"))[0],
                thinking_level=self._host.normalize_reasoning_effort(raw_thinking_level),
            )

        try:
            return build(api_section)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            # Preserve the malformed document for later diagnostics; keep the backend alive
            # with the already-safe runtime settings or provider defaults.
            try:
                return build({})
            except (AttributeError, RuntimeError, TypeError, ValueError):
                defaults = self._host.get_provider_defaults(self._host.DEFAULT_LLM_PROVIDER)
                return self._host.DashboardApiConfig(
                    provider=self._host.DEFAULT_LLM_PROVIDER,
                    api_key="",
                    base_url=defaults["base_url"],
                    model=defaults["model"],
                )

    def _impl_load_initial_dashboard_vision_config(self) -> DashboardVisionConfig:
        vision_section = self._host.load_config_document().get("vision") or {}
        if not isinstance(vision_section, dict):
            return self._host.DashboardVisionConfig()
        provider = str(vision_section.get("provider") or "").strip()
        if not provider:
            return self._host.DashboardVisionConfig()
        try:
            provider = self._host.normalize_provider_name(provider)
            defaults = self._host.get_provider_defaults(provider)
            return self._host.DashboardVisionConfig(
                provider=provider,
                api_key=str(vision_section.get("api_key") or "").strip(),
                base_url=self._host.normalize_base_url(vision_section.get("base_url"), provider, defaults["base_url"]),
                model=str(vision_section.get("model") or "").strip(),
                enabled=bool(vision_section.get("enabled", True)),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return self._host.DashboardVisionConfig()

    def _impl_normalize_vision_config_request(self, request: VisionConfigRequest) -> DashboardVisionConfig:
        provider = str(request.provider or "").strip()
        if not provider:
            return self._host.DashboardVisionConfig()
        provider = self._host.normalize_provider_name(provider)
        defaults = self._host.get_provider_defaults(provider)
        return self._host.DashboardVisionConfig(
            provider=provider,
            api_key=self._host.validate_provider_api_key(request.api_key),
            base_url=self._host.normalize_base_url(request.base_url, provider, defaults["base_url"]),
            model=str(request.model or "").strip(),
            enabled=bool(request.enabled),
        )

    def _impl_load_config_document(self) -> dict[str, Any]:
        with self._host.CONFIG_DOCUMENT_LOCK:
            if not self._host.CONFIG_PATH.exists():
                return {}
            try:
                payload = self._host.json.loads(self._host.CONFIG_PATH.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError, self._host.json.JSONDecodeError):
                return {}
            return payload if isinstance(payload, dict) else {}

    def _impl_normalize_api_config_request(self, request: ApiConfigRequest) -> DashboardApiConfig:
        provider = self._host.normalize_provider_name(request.provider)
        defaults = self._host.get_provider_defaults(provider)
        model = str(request.model or defaults["model"]).strip() or defaults["model"]
        base_url = self._host.normalize_base_url(request.base_url, provider, defaults["base_url"])
        requested_api_type, _resolved_api_type = self._host.normalize_provider_api_type(provider, model, request.api_type)

        thinking_level = self._host.normalize_reasoning_effort(request.thinking_level)
        raw_thinking_level = str(request.thinking_level or "").strip()
        if raw_thinking_level and raw_thinking_level.lower() not in {"off", "default"}:
            supported = self._host.reasoning_effort_variants(provider, model, request.api_type)
            if not thinking_level or thinking_level not in supported:
                supported_text = ", ".join(supported) if supported else "provider default only"
                raise ValueError(
                    f"Reasoning variant '{raw_thinking_level}' is not supported by {provider}/{model}; "
                    f"supported: {supported_text}."
                )

        return self._host.DashboardApiConfig(
            provider=provider,
            api_key=self._host.validate_provider_api_key(request.api_key),
            base_url=base_url,
            model=model,
            api_type=requested_api_type,
            thinking_level=thinking_level,
        )

    def _impl_save_dashboard_config_document(
        self,
        *,
        api_config: DashboardApiConfig | None = None,
        vision_config: DashboardVisionConfig | None = None,
    ) -> None:
        "Persist both the api and vision sections in one atomic write.\n\n    单文件双段：任何一段保存都重写整个文档，避免旧的\"只写 api 段\"行为把\n    vision 段冲掉。\n    "

        with self._host.CONFIG_DOCUMENT_LOCK:
            committed_api = self._host.DASHBOARD_API_CONFIG or self._host.load_initial_dashboard_api_config()
            committed_vision = self._host.DASHBOARD_VISION_CONFIG or self._host.load_initial_dashboard_vision_config()
            api = api_config if api_config is not None else committed_api
            vision = vision_config if vision_config is not None else committed_vision
            api_descriptor = self._host.provider_config_descriptor(api)
            payload: dict[str, Any] = {
                "api": {
                    "provider": api.provider,
                    "api_key": api.api_key,
                    "base_url": api.base_url,
                    "model": api.model,
                    "api_type": api_descriptor["api_type"],
                    "thinking_level": api.thinking_level,
                }
            }
            if vision.provider:
                payload["vision"] = {
                    "provider": vision.provider,
                    "api_key": vision.api_key,
                    "base_url": vision.base_url,
                    "model": vision.model,
                    "enabled": vision.enabled,
                }
            if self._host.CONFIG_PATH.is_file():
                original = self._host.CONFIG_PATH.read_bytes()
                digest = self._host.hashlib.sha256(original).hexdigest()
                backup = self._host.CONFIG_PATH.with_name(f"{self._host.CONFIG_PATH.name}.backup-{digest}.bak")
                if backup.exists() or backup.is_symlink():
                    if self._host._path_is_reparse_or_link(backup) or not backup.is_file() or backup.read_bytes() != original:
                        raise OSError("Provider configuration backup collision or verification failure.")
                else:
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    with backup.open("xb") as handle:
                        handle.write(original)
                        handle.flush()
                        self._host.os.fsync(handle.fileno())
                    if self._host._path_is_reparse_or_link(backup) or not backup.is_file() or backup.read_bytes() != original:
                        raise OSError("Provider configuration backup verification failed.")
            self._host.atomic_write_json(self._host.CONFIG_PATH, payload)
            self._host.DASHBOARD_API_CONFIG = api
            self._host.DASHBOARD_VISION_CONFIG = vision

    def _impl_save_dashboard_api_config(self, config: DashboardApiConfig) -> None:
        self._host.save_dashboard_config_document(api_config=config)

    def _impl_save_dashboard_vision_config(self, config: DashboardVisionConfig) -> None:
        self._host.save_dashboard_config_document(vision_config=config)

    def _impl_serialize_api_config(self, include_secret: bool) -> dict[str, Any]:
        config = self._host.DASHBOARD_API_CONFIG or self._host.load_initial_dashboard_api_config()
        return {
            "provider": config.provider,
            "providerLabel": self._host.provider_display_name(config.provider),
            "api_key": config.api_key if include_secret else self._host.mask_secret(config.api_key),
            "apiKeyPresent": bool(config.api_key),
            "base_url": config.base_url,
            "model": config.model,
            **self._host.provider_config_descriptor(config),
            "thinking_level": config.thinking_level,
            "usesBaseUrl": config.provider not in {"anthropic", "gemini"},
            "authHeader": self._host.provider_auth_label(config.provider),
            "apiKeyRequired": self._host.provider_requires_api_key(config.provider),
        }

    def _impl_serialize_vision_config(self, include_secret: bool) -> dict[str, Any]:
        config = self._host.DASHBOARD_VISION_CONFIG or self._host.load_initial_dashboard_vision_config()
        return {
            "provider": config.provider,
            "providerLabel": self._host.provider_display_name(config.provider) if config.provider else "",
            "api_key": config.api_key if include_secret else self._host.mask_secret(config.api_key),
            "apiKeyPresent": bool(config.api_key),
            "base_url": config.base_url,
            "model": config.model,
            "enabled": config.enabled,
            "configured": config.configured,
            "apiKeyRequired": self._host.provider_requires_api_key(config.provider) if config.provider else False,
        }

    def _impl_serialize_app_api_config(self) -> dict[str, Any]:
        config = self._host.serialize_api_config(include_secret=False)
        config.pop("api_key", None)
        return config

    def _impl_serialize_app_vision_config(self) -> dict[str, Any]:
        config = self._host.serialize_vision_config(include_secret=False)
        config.pop("api_key", None)
        return config

    def _impl_build_effective_model_summary(self) -> dict[str, Any]:
        config = self._host.DASHBOARD_API_CONFIG or self._host.load_initial_dashboard_api_config()
        return {
            "provider": config.provider,
            "providerLabel": self._host.provider_display_name(config.provider),
            "model": config.model,
            "baseUrl": config.base_url,
            **self._host.provider_config_descriptor(config),
            "authHeader": self._host.provider_auth_label(config.provider),
            "apiKeyRequired": self._host.provider_requires_api_key(config.provider),
        }

    def _impl_mask_secret(self, value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * max(len(value) - 8, 4)}{value[-4:]}"
