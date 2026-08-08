"""Typed Provider configuration ownership for app composition.

The owner keeps the API and Vision sections behind one app-lifetime reentrant
lock.  A save writes both sections atomically and publishes the new in-memory
state only after the disk commit succeeds.  Callers inject persistence and
provider policy explicitly; this module has no Dashboard or route dependency.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


class ReentrantLockPort(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> bool | None: ...


class ProviderRuntimeSettingsPort(Protocol):
    llm_provider: str
    llm_api_key: str
    llm_model: str
    gemini_thinking_level: str


class ProviderApiConfigRequestPort(Protocol):
    provider: str
    api_key: str
    base_url: str | None
    model: str | None
    api_type: str | None
    thinking_level: str


class ProviderVisionConfigRequestPort(Protocol):
    provider: str
    api_key: str
    base_url: str | None
    model: str | None
    enabled: bool


@dataclass(frozen=True, slots=True)
class ProviderApiConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    api_type: str | None = None
    thinking_level: str = ""


@dataclass(frozen=True, slots=True)
class ProviderVisionConfig:
    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    enabled: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.model)


@dataclass(frozen=True, slots=True)
class ProviderConfigurationPersistencePorts:
    """Durable Provider document capabilities owned by the application."""

    config_path: Path
    load_runtime_settings: Callable[[], ProviderRuntimeSettingsPort]
    atomic_write_json: Callable[[Path, Mapping[str, Any]], None]
    path_is_reparse_or_link: Callable[[Path], bool]
    fsync: Callable[[int], None] = os.fsync


@dataclass(frozen=True, slots=True)
class ProviderConfigurationPolicyPorts:
    """Provider registry and request-normalization policy."""

    default_provider: str
    normalize_provider_name: Callable[[str], str]
    get_provider_defaults: Callable[[str], Mapping[str, str]]
    normalize_base_url: Callable[[str | None, str, str], str]
    normalize_provider_api_type: Callable[[str, str, str | None], tuple[str, str]]
    normalize_reasoning_effort: Callable[[str | None], str]
    reasoning_effort_variants: Callable[[str, str, str | None], list[str]]
    validate_provider_api_key: Callable[[str], None]
    provider_display_name: Callable[[str], str]
    provider_auth_label: Callable[[str], str]
    provider_requires_api_key: Callable[[str], bool]
    provider_config_descriptor: Callable[[ProviderApiConfig], dict[str, Any]]


class ProviderConfigurationService:
    """Own current Provider configuration, persistence and projections."""

    def __init__(
        self,
        persistence: ProviderConfigurationPersistencePorts,
        policy: ProviderConfigurationPolicyPorts,
        lock: ReentrantLockPort,
    ) -> None:
        self._persistence = persistence
        self._policy = policy
        self._lock = lock
        self._api_config: ProviderApiConfig | None = None
        self._vision_config: ProviderVisionConfig | None = None

    @property
    def config_path(self) -> Path:
        return self._persistence.config_path

    def load_config_document(self) -> dict[str, Any]:
        with self._lock:
            return self._load_config_document_locked()

    def _load_config_document_locked(self) -> dict[str, Any]:
        path = self._persistence.config_path
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def current_api_config(self) -> ProviderApiConfig:
        with self._lock:
            if self._api_config is None:
                self._api_config = self._load_initial_api_config_locked()
            return self._api_config

    def current_vision_config(self) -> ProviderVisionConfig:
        with self._lock:
            if self._vision_config is None:
                self._vision_config = self._load_initial_vision_config_locked()
            return self._vision_config

    def reload_from_disk(self) -> tuple[ProviderApiConfig, ProviderVisionConfig]:
        """Reload both sections under the same lock after an external repair."""

        with self._lock:
            document = self._load_config_document_locked()
            api_config = self._load_initial_api_config_locked(document)
            vision_config = self._load_initial_vision_config_locked(document)
            self._api_config = api_config
            self._vision_config = vision_config
            return api_config, vision_config

    def _load_initial_api_config_locked(
        self,
        document: dict[str, Any] | None = None,
    ) -> ProviderApiConfig:
        settings = self._persistence.load_runtime_settings()
        config_document = document if document is not None else self._load_config_document_locked()
        raw_api_section = config_document.get("api")
        api_section = raw_api_section if isinstance(raw_api_section, dict) else {}

        def build(section: dict[str, Any]) -> ProviderApiConfig:
            provider = self._policy.normalize_provider_name(
                section.get("provider")
                or settings.llm_provider
                or self._policy.default_provider
            )
            defaults = self._policy.get_provider_defaults(provider)
            api_key = str(section.get("api_key") or settings.llm_api_key).strip()
            base_url = self._policy.normalize_base_url(
                section.get("base_url"),
                provider,
                defaults["base_url"],
            )
            model = (
                str(section.get("model") or settings.llm_model or defaults["model"]).strip()
                or defaults["model"]
            )
            raw_thinking_level = (
                section.get("thinking_level")
                if "thinking_level" in section
                else settings.gemini_thinking_level
            )
            return ProviderApiConfig(
                provider=provider,
                api_key=api_key,
                base_url=base_url,
                model=model,
                api_type=self._policy.normalize_provider_api_type(
                    provider,
                    model,
                    section.get("api_type"),
                )[0],
                thinking_level=self._policy.normalize_reasoning_effort(raw_thinking_level),
            )

        try:
            return build(api_section)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            try:
                return build({})
            except (AttributeError, RuntimeError, TypeError, ValueError):
                defaults = self._policy.get_provider_defaults(self._policy.default_provider)
                return ProviderApiConfig(
                    provider=self._policy.default_provider,
                    api_key="",
                    base_url=defaults["base_url"],
                    model=defaults["model"],
                )

    def _load_initial_vision_config_locked(
        self,
        document: dict[str, Any] | None = None,
    ) -> ProviderVisionConfig:
        config_document = document if document is not None else self._load_config_document_locked()
        vision_section = config_document.get("vision") or {}
        if not isinstance(vision_section, dict):
            return ProviderVisionConfig()
        provider = str(vision_section.get("provider") or "").strip()
        if not provider:
            return ProviderVisionConfig()
        try:
            provider = self._policy.normalize_provider_name(provider)
            defaults = self._policy.get_provider_defaults(provider)
            return ProviderVisionConfig(
                provider=provider,
                api_key=str(vision_section.get("api_key") or "").strip(),
                base_url=self._policy.normalize_base_url(
                    vision_section.get("base_url"),
                    provider,
                    defaults["base_url"],
                ),
                model=str(vision_section.get("model") or "").strip(),
                enabled=bool(vision_section.get("enabled", True)),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ProviderVisionConfig()

    def normalize_api_request(
        self,
        request: ProviderApiConfigRequestPort,
    ) -> ProviderApiConfig:
        provider = self._policy.normalize_provider_name(request.provider)
        defaults = self._policy.get_provider_defaults(provider)
        model = str(request.model or defaults["model"]).strip() or defaults["model"]
        base_url = self._policy.normalize_base_url(
            request.base_url,
            provider,
            defaults["base_url"],
        )
        requested_api_type, _resolved_api_type = self._policy.normalize_provider_api_type(
            provider,
            model,
            request.api_type,
        )
        thinking_level = self._policy.normalize_reasoning_effort(request.thinking_level)
        raw_thinking_level = str(request.thinking_level or "").strip()
        if raw_thinking_level and raw_thinking_level.lower() not in {"off", "default"}:
            supported = self._policy.reasoning_effort_variants(
                provider,
                model,
                request.api_type,
            )
            if not thinking_level or thinking_level not in supported:
                supported_text = ", ".join(supported) if supported else "provider default only"
                raise ValueError(
                    f"Reasoning variant '{raw_thinking_level}' is not supported by "
                    f"{provider}/{model}; supported: {supported_text}."
                )
        return ProviderApiConfig(
            provider=provider,
            api_key=self._validated_key(request.api_key),
            base_url=base_url,
            model=model,
            api_type=requested_api_type,
            thinking_level=thinking_level,
        )

    def normalize_vision_request(
        self,
        request: ProviderVisionConfigRequestPort,
    ) -> ProviderVisionConfig:
        provider = str(request.provider or "").strip()
        if not provider:
            return ProviderVisionConfig()
        provider = self._policy.normalize_provider_name(provider)
        defaults = self._policy.get_provider_defaults(provider)
        return ProviderVisionConfig(
            provider=provider,
            api_key=self._validated_key(request.api_key),
            base_url=self._policy.normalize_base_url(
                request.base_url,
                provider,
                defaults["base_url"],
            ),
            model=str(request.model or "").strip(),
            enabled=bool(request.enabled),
        )

    def resolve_api_request(
        self,
        request: ProviderApiConfigRequestPort,
    ) -> ProviderApiConfig:
        """Normalize a request, reuse only a same-provider key, then revalidate."""

        config = self.normalize_api_request(request)
        if not config.api_key.strip():
            saved = self.current_api_config()
            if saved.provider == config.provider and saved.api_key.strip():
                config = replace(config, api_key=saved.api_key)
        self._policy.validate_provider_api_key(config.api_key)
        return config

    def resolve_vision_request(
        self,
        request: ProviderVisionConfigRequestPort,
    ) -> ProviderVisionConfig:
        """Normalize a Vision request with the same saved-key contract."""

        config = self.normalize_vision_request(request)
        if config.provider and not config.api_key.strip():
            saved = self.current_vision_config()
            if saved.provider == config.provider and saved.api_key.strip():
                config = replace(config, api_key=saved.api_key)
        self._policy.validate_provider_api_key(config.api_key)
        return config

    def _validated_key(self, value: str) -> str:
        self._policy.validate_provider_api_key(value)
        return value

    def save_api_config(self, config: ProviderApiConfig) -> None:
        self.save_config_document(api_config=config)

    def save_vision_config(self, config: ProviderVisionConfig) -> None:
        self.save_config_document(vision_config=config)

    def save_config_document(
        self,
        *,
        api_config: ProviderApiConfig | None = None,
        vision_config: ProviderVisionConfig | None = None,
    ) -> None:
        """Persist both sections, then publish both in-memory values."""

        with self._lock:
            committed_api = self._api_config or self._load_initial_api_config_locked()
            committed_vision = self._vision_config or self._load_initial_vision_config_locked()
            api = api_config if api_config is not None else committed_api
            vision = vision_config if vision_config is not None else committed_vision
            api_descriptor = self._policy.provider_config_descriptor(api)
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
            self._write_backup_if_needed_locked()
            self._persistence.atomic_write_json(self._persistence.config_path, payload)
            self._api_config = api
            self._vision_config = vision

    def _write_backup_if_needed_locked(self) -> None:
        path = self._persistence.config_path
        if not path.is_file():
            return
        original = path.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        backup = path.with_name(f"{path.name}.backup-{digest}.bak")
        if backup.exists() or backup.is_symlink():
            if (
                self._persistence.path_is_reparse_or_link(backup)
                or not backup.is_file()
                or backup.read_bytes() != original
            ):
                raise OSError("Provider configuration backup collision or verification failure.")
            return
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("xb") as handle:
            handle.write(original)
            handle.flush()
            self._persistence.fsync(handle.fileno())
        if (
            self._persistence.path_is_reparse_or_link(backup)
            or not backup.is_file()
            or backup.read_bytes() != original
        ):
            raise OSError("Provider configuration backup verification failed.")

    def serialize_api_config(self, include_secret: bool) -> dict[str, Any]:
        config = self.current_api_config()
        return {
            "provider": config.provider,
            "providerLabel": self._policy.provider_display_name(config.provider),
            "api_key": config.api_key if include_secret else self.mask_secret(config.api_key),
            "apiKeyPresent": bool(config.api_key),
            "base_url": config.base_url,
            "model": config.model,
            **self._policy.provider_config_descriptor(config),
            "thinking_level": config.thinking_level,
            "usesBaseUrl": config.provider not in {"anthropic", "gemini"},
            "authHeader": self._policy.provider_auth_label(config.provider),
            "apiKeyRequired": self._policy.provider_requires_api_key(config.provider),
        }

    def serialize_vision_config(self, include_secret: bool) -> dict[str, Any]:
        config = self.current_vision_config()
        return {
            "provider": config.provider,
            "providerLabel": (
                self._policy.provider_display_name(config.provider)
                if config.provider
                else ""
            ),
            "api_key": config.api_key if include_secret else self.mask_secret(config.api_key),
            "apiKeyPresent": bool(config.api_key),
            "base_url": config.base_url,
            "model": config.model,
            "enabled": config.enabled,
            "configured": config.configured,
            "apiKeyRequired": (
                self._policy.provider_requires_api_key(config.provider)
                if config.provider
                else False
            ),
        }

    def serialize_app_api_config(self) -> dict[str, Any]:
        config = self.serialize_api_config(include_secret=False)
        config.pop("api_key", None)
        return config

    def serialize_app_vision_config(self) -> dict[str, Any]:
        config = self.serialize_vision_config(include_secret=False)
        config.pop("api_key", None)
        return config

    def build_effective_model_summary(self) -> dict[str, Any]:
        config = self.current_api_config()
        return {
            "provider": config.provider,
            "providerLabel": self._policy.provider_display_name(config.provider),
            "model": config.model,
            "baseUrl": config.base_url,
            **self._policy.provider_config_descriptor(config),
            "authHeader": self._policy.provider_auth_label(config.provider),
            "apiKeyRequired": self._policy.provider_requires_api_key(config.provider),
        }

    @staticmethod
    def mask_secret(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * max(len(value) - 8, 4)}{value[-4:]}"
