from __future__ import annotations

from typing import Any


class ProviderTestIntegrationService:
    """Own provider connectivity probes behind Dashboard late-bound facades."""

    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl_run_provider_test_sync(self, request: ProviderTestRequest) -> dict[str, Any]:
        capability = request.capability
        try:
            config = self._host.normalize_api_config_request(request)
        except ValueError as exc:
            provider = self._host.normalize_provider_name(request.provider)
            return {
                "ok": False,
                "status": "error",
                "capability": capability,
                "provider": provider,
                "providerLabel": self._host.provider_display_name(provider),
                "model": str(request.model or ""),
                "message": str(exc),
            }
        provider_label = self._host.provider_display_name(config.provider)
        descriptor = self._host.provider_config_descriptor(config)
        if self._host.provider_requires_api_key(config.provider) and not config.api_key.strip():
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
                "message": "Vision test requires an explicit user-selected image; no Unity screenshot or project asset was sent.",
            }
        prompt = (
            "Return exactly: VRCForge provider test OK"
            if capability == "text"
            else 'Return compact JSON exactly like {"ok":true,"name":"vrcforge"}.'
        )
        try:
            text = self._host._run_provider_text_probe(config, prompt, structured=capability == "structured")
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "status": "error",
                "capability": capability,
                "provider": config.provider,
                "providerLabel": provider_label,
                "model": config.model,
                "apiType": descriptor["apiType"],
                "resolvedApiType": descriptor["resolvedApiType"],
                "message": str(exc),
            }
        structured_ok = True
        if capability == "structured":
            try:
                parsed = self._host.json.loads(self._host.extract_json_block(text) or text)
                structured_ok = isinstance(parsed, dict) and bool(parsed.get("ok"))
            except Exception:  # noqa: BLE001
                structured_ok = False
        return {
            "ok": structured_ok,
            "status": "ok" if structured_ok else "warning",
            "capability": capability,
            "provider": config.provider,
            "providerLabel": provider_label,
            "model": config.model,
            "apiType": descriptor["apiType"],
            "resolvedApiType": descriptor["resolvedApiType"],
            "message": "Provider test succeeded." if structured_ok else "Provider responded, but structured JSON did not validate.",
            "responsePreview": text[:240],
        }

    def _impl__run_provider_text_probe(self, config: DashboardApiConfig, prompt: str, structured: bool = False) -> str:
        self._host.validate_provider_api_key(config.api_key)
        _requested_api_type, resolved_api_type = self._host.normalize_provider_api_type(
            config.provider, config.model, config.api_type
        )
        if resolved_api_type == "responses":
            adapter = self._host.DeepSeekResponsesAdapter(api_key=config.api_key, base_url=config.base_url)
            response = adapter.send_request(
                self._host.ProviderRuntimeRequest(
                    model=config.model,
                    prompt=prompt,
                    instructions="You are a provider connectivity probe. Return only the requested result.",
                    reasoning_effort=config.thinking_level,
                    # Structured selection needs room for a final JSON message even
                    # when the provider emits bounded reasoning before the answer.
                    max_output_tokens=512 if structured else 64,
                    mode="probe",
                    structured_output=structured,
                )
            )
            return response.text
        if config.provider in {"gemini", "vertexai"}:
            try:
                from google import genai
            except ImportError as exc:
                raise RuntimeError("The google-genai package is not installed.") from exc
            if config.provider == "vertexai":
                project, location = self._host.resolve_vertex_project_location(config.base_url)
                client = genai.Client(vertexai=True, project=project, location=location)
            else:
                client = genai.Client(api_key=config.api_key)
            generate_kwargs: dict[str, Any] = {"model": config.model, "contents": prompt}
            if config.thinking_level:
                try:
                    from google.genai import types as genai_types
                except ImportError as exc:
                    raise RuntimeError("The installed google-genai package does not expose thinking configuration.") from exc
                settings = self._host._provider_probe_settings(config)
                generate_config = self._host.build_gemini_generate_config(settings, genai_types)
                if generate_config is not None:
                    generate_kwargs["config"] = generate_config
            response = client.models.generate_content(**generate_kwargs)
            return str(getattr(response, "text", "") or response)
        if config.provider == "anthropic":
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError("The anthropic package is not installed.") from exc
            client = anthropic.Anthropic(api_key=config.api_key)
            request_payload = self._host.build_anthropic_request_payload(self._host._provider_probe_settings(config), prompt)
            response = client.messages.create(**request_payload)
            parts = getattr(response, "content", []) or []
            texts = [str(getattr(part, "text", "") or "") for part in parts]
            return "\n".join(text for text in texts if text).strip()
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package is not installed.") from exc
        if not config.base_url.strip() and config.provider not in {"openai"}:
            raise RuntimeError("Base URL is empty.")
        kwargs = self._host.build_openai_compatible_request_payload(self._host._provider_probe_settings(config), prompt)
        if self._host.model_rejects_fixed_temperature(config.model):
            kwargs["max_completion_tokens"] = 512
        else:
            kwargs["max_tokens"] = 64
        if structured:
            kwargs["response_format"] = {"type": "json_object"}
        client = OpenAI(api_key=config.api_key or "ollama", base_url=config.base_url or None, timeout=30.0)
        response = client.chat.completions.create(**kwargs)
        choices = getattr(response, "choices", []) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        return str(getattr(message, "content", "") or "")

    def _impl__provider_probe_settings(self, config: DashboardApiConfig) -> Settings:
        """Build an in-memory Settings object so provider tests use production request policy."""

        return self._host.Settings(
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
            export_path=self._host.Path("Assets/VRCForge/blendshapes_export.json"),
            min_confidence=0.0,
            llm_api_type=config.api_type,
        )
