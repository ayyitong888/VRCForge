from __future__ import annotations

from typing import Any


class ProviderVisionIntegrationService:
    """Own provider Vision routing and SDK adaptation behind Dashboard facades.

    The Dashboard remains the owner of Gateway hook registration, configuration
    lifetime and routes.  This temporary host lookup keeps current monkeypatch
    contracts stable until the final 1.5 composition pass replaces it with
    explicit typed ports.
    """

    __slots__ = ("_host",)

    def __init__(self, host: Any) -> None:
        self._host = host

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def _impl__agent_gateway_vision_analyze(self, message: str, images: list[dict[str, Any]]) -> dict[str, Any]:
        """Vision-analysis hook for the agent gateway (visionProfile routing).

    Routing matrix (docs/ROADMAP.local.md):
    1. Main model is vision-capable -> analyze with the main provider config
       (source "main").
    2. Otherwise, a configured + enabled vision profile -> delegate to it
       (source "visionProfile").
    3. Otherwise -> return status "unconfigured" so the gateway stays honest.
       Image bytes are NEVER sent to a text-only model.

    The returned usage belongs to the labeled vision run step only; the
    gateway must never merge it into the chat context meter.
    Provider errors are raised; the gateway converts them into an honest
    "error" vision step.
    """
        main = self._host.DASHBOARD_API_CONFIG or self._host.load_initial_dashboard_api_config()
        main_key_ok = bool(main.api_key.strip()) or not self._host.provider_requires_api_key(main.provider)
        if main.model and main_key_ok and self._host.provider_model_supports_vision(main.provider, main.model):
            config = main
            source = "main"
        else:
            vision = self._host.DASHBOARD_VISION_CONFIG or self._host.load_initial_dashboard_vision_config()
            if not vision.configured:
                return {
                    "status": "unconfigured",
                    "reason": "Main model is not vision-capable and no vision profile is configured.",
                }
            if not vision.enabled:
                return {
                    "status": "unconfigured",
                    "reason": "The configured vision profile is disabled in settings.",
                }
            if self._host.provider_requires_api_key(vision.provider) and not vision.api_key.strip():
                return {
                    "status": "unconfigured",
                    "reason": f"The vision profile ({self._host.provider_display_name(vision.provider)}) has no API key.",
                }
            # 用户显式配置的视觉档案视为“用户声明可识图”，不再套用主模型的
            # 保守启发式（避免误伤自定义端点上的多模态模型）。
            config = self._host.DashboardApiConfig(
                provider=vision.provider,
                api_key=vision.api_key,
                base_url=vision.base_url,
                model=vision.model,
            )
            source = "visionProfile"

        prompt = self._host.build_vision_analysis_prompt(message, images)
        text, usage = self._host._run_provider_vision_analysis(config, prompt, images)
        if not text.strip():
            raise RuntimeError(f"{self._host.provider_display_name(config.provider)} returned an empty vision analysis.")
        return {
            "status": "analyzed",
            "text": text,
            "provider": config.provider,
            "providerLabel": self._host.provider_display_name(config.provider),
            "model": config.model,
            "source": source,
            "usage": usage,
        }

    def _impl_provider_model_supports_vision(self, provider: str, model: str) -> bool:
        provider = self._host.normalize_provider_name(provider)
        model_id = str(model or "").strip().lower()
        if provider in {"gemini", "vertexai"}:
            # Gemini 系列原生多模态。
            return True
        if provider == "anthropic":
            # Claude 3 起全系支持图像输入。
            return True
        if provider == "deepseek":
            return False
        return any(marker in model_id for marker in self._host.VISION_CAPABLE_MODEL_MARKERS) or bool(
            self._host.VISION_CAPABLE_MODEL_RE.search(model_id)
        )

    def _impl_split_image_data_url(self, data_url: str) -> tuple[str, str]:
        """Split `data:image/png;base64,...` into (mime, base64_payload)."""
        value = str(data_url or "")
        if not value.startswith("data:"):
            raise RuntimeError("Attachment payload is not a data URL.")
        header, _, payload = value.partition(",")
        if not payload:
            raise RuntimeError("Attachment data URL has no payload.")
        mime = header[5:].split(";", 1)[0].strip().lower() or "image/png"
        if not mime.startswith("image/"):
            raise RuntimeError(f"Attachment data URL is not an image ({mime}).")
        if "base64" not in header:
            raise RuntimeError("Attachment data URL is not base64-encoded.")
        return mime, payload

    def _impl_build_vision_analysis_prompt(self, message: str, images: list[dict[str, Any]]) -> str:
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

    def _impl__extract_openai_usage(self, response: Any) -> dict[str, Any]:
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

    def _impl__run_provider_vision_analysis(
        self,
        config: DashboardApiConfig,
        prompt: str,
        images: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        """One multimodal request: bounded image payloads + analysis prompt.

    Returns (analysis_text, usage). Usage belongs to the vision run step only
    and must never be merged into the chat context meter.
    """
        self._host.validate_provider_api_key(config.api_key)
        decoded: list[tuple[str, str]] = []
        for item in images:
            mime, payload = self._host.split_image_data_url(str(item.get("dataUrl") or ""))
            decoded.append((mime, payload))
        if not decoded:
            raise RuntimeError("No image payloads to analyze.")

        if config.provider in {"gemini", "vertexai"}:
            try:
                from google import genai
                from google.genai import types as genai_types
            except ImportError as exc:
                raise RuntimeError("The google-genai package is not installed.") from exc
            if config.provider == "vertexai":
                project, location = self._host.resolve_vertex_project_location(config.base_url)
                client = genai.Client(vertexai=True, project=project, location=location)
            else:
                client = genai.Client(api_key=config.api_key)
            contents: list[Any] = [
                genai_types.Part.from_bytes(data=self._host.base64.b64decode(payload), mime_type=mime)
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

        if config.provider == "anthropic":
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError("The anthropic package is not installed.") from exc
            client = anthropic.Anthropic(api_key=config.api_key)
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
                usage = {"exact": False, "unavailableReason": "provider_usage_missing"}
            else:
                usage = {"exact": True}
                if input_tokens is not None:
                    usage["inputTokens"] = int(input_tokens)
                if output_tokens is not None:
                    usage["outputTokens"] = int(output_tokens)
                if input_tokens is not None and output_tokens is not None:
                    usage["totalTokens"] = int(input_tokens) + int(output_tokens)
            return text, usage

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package is not installed.") from exc
        if not config.base_url.strip() and config.provider not in {"openai"}:
            raise RuntimeError("Base URL is empty.")
        message_content: list[dict[str, Any]] = [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{payload}"}}
            for mime, payload in decoded
        ]
        message_content.append({"type": "text", "text": prompt})
        client = OpenAI(api_key=config.api_key or "ollama", base_url=config.base_url or None, timeout=60.0)
        vision_kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": [{"role": "user", "content": message_content}],
        }
        if self._host.model_rejects_fixed_temperature(config.model):
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
        return text, self._host._extract_openai_usage(response)
