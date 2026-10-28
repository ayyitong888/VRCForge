"""App-authenticated ChatGPT connection UI; inference is never started here.

The app owns the lazy OAuth service and closes its temporary loopback callback
listener on shutdown. Its browser callback is separately authenticated by PKCE
and single-use state, never by weakening dashboard authentication.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from provider_configuration_service import ProviderApiConfig, ProviderConfigurationBusyError
from .service import ChatGPTAuthError
from .reasoning import direct_reasoning_effort


class ChatGPTAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(default="", max_length=256)


class ChatGPTActivationRequest(ChatGPTAccountRequest):
    model: str = Field(min_length=1, max_length=256)
    thinking_level: str = Field(default="", max_length=32)


def build_chatgpt_oauth_router(
    *,
    authorize: Callable[[Request], None],
    activate: Callable[[ProviderApiConfig], Awaitable[dict[str, Any]]],
    service_factory: Callable[[], Any] | None = None,
    current_config: Callable[[], ProviderApiConfig] | None = None,
    deactivate: Callable[[str], Awaitable[None]] | None = None,
    configuration_change: Callable[[], Any] = nullcontext,
) -> APIRouter:
    router = APIRouter(prefix="/api/chatgpt")
    service: Any = None

    def owner() -> Any:
        nonlocal service
        if service is None:
            if service_factory is None:
                from .service import get_chatgpt_auth_service

                service = get_chatgpt_auth_service()
            else:
                service = service_factory()
        return service

    def check(request: Request, response: Response) -> None:
        authorize(request)
        response.headers["Cache-Control"] = "no-store"

    async def call(method: str, *args: str) -> Any:
        try:
            return await asyncio.to_thread(getattr(owner(), method), *args)
        except ChatGPTAuthError as exc:
            # ChatGPTAuthError messages are deliberately fixed, credential-free
            # diagnostics owned by the OAuth service. Preserve those messages;
            # every other provider exception remains generic below.
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            # Provider exceptions may carry response bodies or credential URLs.
            raise HTTPException(400, "ChatGPT connection operation failed. Check connection status or sign in again.") from exc

    @router.get("/status")
    async def status(request: Request, response: Response) -> Any:
        check(request, response)
        payload = await call("status")
        config = current_config() if current_config is not None else None
        active_account_id = (
            config.api_key
            if config and config.provider == "chatgpt"
            and any(item.get("id") == config.api_key for item in payload.get("accounts", []))
            else ""
        )
        return {
            **payload,
            "activeAccountId": active_account_id,
            "activeModel": config.model if active_account_id else "",
            "activeThinkingLevel": config.thinking_level if active_account_id else "",
        }

    @router.post("/login")
    async def login(body: ChatGPTAccountRequest, request: Request, response: Response) -> Any:
        check(request, response)
        return await call("begin", body.account_id)

    @router.post("/cancel")
    async def cancel(request: Request, response: Response) -> Any:
        check(request, response)
        await call("cancel")
        return {"ok": True}

    @router.post("/logout")
    async def logout(body: ChatGPTAccountRequest, request: Request, response: Response) -> Any:
        check(request, response)
        try:
            with configuration_change():
                result = await call("logout", body.account_id)
                if deactivate is not None and result.get("localCleared"):
                    try:
                        await deactivate(body.account_id)
                    except Exception as exc:
                        raise HTTPException(500, "ChatGPT sign-out cleared the account locally, but the active provider configuration could not be cleared.") from exc
                return result
        except ProviderConfigurationBusyError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/models")
    async def models(body: ChatGPTAccountRequest, request: Request, response: Response) -> Any:
        check(request, response)
        return {"models": await call("models", body.account_id)}

    @router.post("/activate")
    async def select(body: ChatGPTActivationRequest, request: Request, response: Response) -> Any:
        check(request, response)
        catalog = await call("models", body.account_id)
        selected = next((item for item in catalog if item.get("id") == body.model), None)
        if selected is None:
            raise HTTPException(400, "Select a model from this ChatGPT account's catalog.")
        raw_levels = selected.get("supportedReasoningLevels")
        supported_levels = [
            entry.get("effort") for entry in raw_levels
            if isinstance(entry, dict) and isinstance(entry.get("effort"), str)
        ] if isinstance(raw_levels, list) else []
        requested_level = body.thinking_level.strip().lower()
        try:
            direct_reasoning_effort(requested_level)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if supported_levels and requested_level not in supported_levels:
            raise HTTPException(400, "Choose a supported reasoning level for this ChatGPT model.")
        if requested_level and not supported_levels:
            raise HTTPException(400, "This ChatGPT model does not advertise configurable reasoning levels.")
        config = ProviderApiConfig(
            provider="chatgpt", api_key=body.account_id,
            base_url="https://api.openai.com/v1", model=body.model,
            api_type="responses", thinking_level=requested_level,
        )
        try:
            with configuration_change():
                return await activate(config)
        except ProviderConfigurationBusyError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.on_event("shutdown")
    async def close() -> None:
        if service_factory is None:
            from .service import close_chatgpt_auth_service

            await asyncio.to_thread(close_chatgpt_auth_service)
        elif service is not None:
            await asyncio.to_thread(service.close)

    return router
