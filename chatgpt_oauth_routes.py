"""App-authenticated ChatGPT connection UI; inference is never started here.

The app owns the lazy OAuth service and closes its temporary loopback callback
listener on shutdown. Its browser callback is separately authenticated by PKCE
and single-use state, never by weakening dashboard authentication.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from provider_configuration_service import ProviderApiConfig


class ChatGPTAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(default="", max_length=256)


class ChatGPTActivationRequest(ChatGPTAccountRequest):
    model: str = Field(min_length=1, max_length=256)


def build_chatgpt_oauth_router(
    *,
    authorize: Callable[[Request], None],
    activate: Callable[[ProviderApiConfig], Awaitable[dict[str, Any]]],
    service_factory: Callable[[], Any] | None = None,
    current_config: Callable[[], ProviderApiConfig] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/chatgpt")
    service: Any = None

    def owner() -> Any:
        nonlocal service
        if service is None:
            if service_factory is None:
                from chatgpt_oauth_service import get_chatgpt_auth_service

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
        except Exception as exc:
            # Provider exceptions may carry response bodies or credential URLs.
            raise HTTPException(400, "ChatGPT connection operation failed. Check connection status or sign in again.") from exc

    @router.get("/status")
    async def status(request: Request, response: Response) -> Any:
        check(request, response)
        payload = await call("status")
        config = current_config() if current_config is not None else None
        return {
            **payload,
            "activeAccountId": config.api_key if config and config.provider == "chatgpt" else "",
            "activeModel": config.model if config and config.provider == "chatgpt" else "",
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
        return await call("logout", body.account_id)

    @router.post("/models")
    async def models(body: ChatGPTAccountRequest, request: Request, response: Response) -> Any:
        check(request, response)
        return {"models": await call("models", body.account_id)}

    @router.post("/activate")
    async def select(body: ChatGPTActivationRequest, request: Request, response: Response) -> Any:
        check(request, response)
        catalog = await call("models", body.account_id)
        if body.model not in {item["id"] for item in catalog}:
            raise HTTPException(400, "Select a model from this ChatGPT account's catalog.")
        return await activate(ProviderApiConfig(
            provider="chatgpt", api_key=body.account_id,
            base_url="https://api.openai.com/v1", model=body.model,
            api_type="responses",
        ))

    @router.on_event("shutdown")
    async def close() -> None:
        if service_factory is None:
            from chatgpt_oauth_service import close_chatgpt_auth_service

            await asyncio.to_thread(close_chatgpt_auth_service)
        elif service is not None:
            await asyncio.to_thread(service.close)

    return router
