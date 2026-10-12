"""App-authenticated configuration endpoints for the optional Telegram owner."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt


class TelegramConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = SecretStr("")
    ownerId: StrictInt = Field(gt=0, lt=2**53)
    projects: list[str] = Field(default_factory=list, max_length=32)
    proxyMode: str = Field(default="environment")
    proxyUrl: str = Field(default="")


def build_telegram_router(*, authorize: Callable[[Request], None], service: Any) -> APIRouter:
    router = APIRouter(prefix="/api/telegram")

    def check(request: Request, response: Response) -> None:
        authorize(request)
        response.headers["Cache-Control"] = "no-store"

    @router.get("/status")
    async def status(request: Request, response: Response) -> dict:
        check(request, response)
        return service.status()

    @router.put("/config")
    async def configure(payload: TelegramConfigurationRequest, request: Request, response: Response) -> dict:
        check(request, response)
        try:
            await service.configure(token=payload.token.get_secret_value(), owner_id=payload.ownerId,
                                    projects=payload.projects, proxy_mode=payload.proxyMode,
                                    proxy_url=payload.proxyUrl)
        except Exception:
            raise HTTPException(400, "Telegram configuration could not be saved. Check owner, token and project paths.") from None
        return service.status()

    @router.post("/start")
    async def start(request: Request, response: Response) -> dict:
        check(request, response)
        try:
            await service.start()
        except Exception:
            raise HTTPException(400, "Telegram connection failed. Check the local connection status.") from None
        return service.status()

    @router.post("/test-connection")
    async def test_connection(request: Request, response: Response) -> dict:
        check(request, response)
        try:
            return await service.test_connection()
        except Exception:
            raise HTTPException(400, "Telegram connection test failed. Check bot token and proxy settings.") from None

    @router.post("/stop")
    async def stop(request: Request, response: Response) -> dict:
        check(request, response)
        await service.stop()
        return service.status()

    return router
