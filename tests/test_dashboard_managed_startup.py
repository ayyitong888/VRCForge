"""The managed listener serves its handshake while recovery owns runtime state."""
import asyncio
import hashlib
import hmac
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException


@pytest.fixture
def managed_runtime(monkeypatch):
    import dashboard_server as dashboard

    server = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(dashboard, "CURRENT_UVICORN_SERVER", server)
    monkeypatch.setattr(dashboard, "DASHBOARD_RUNTIME_STARTUP_TASK", None)
    monkeypatch.setattr(dashboard, "APP_AUTH_REQUIRED", True)
    monkeypatch.setattr(dashboard, "APP_SESSION_TOKEN", "offline-startup-token")
    monkeypatch.setattr(dashboard, "record_debug_interaction", lambda *args, **kwargs: None)
    monkeypatch.setattr(dashboard, "emit_log", lambda *args, **kwargs: None)
    return dashboard, server


def test_handshake_and_auth_work_while_stateful_requests_wait(managed_runtime, monkeypatch):
    dashboard, _ = managed_runtime

    async def scenario():
        recovery_started = asyncio.Event()
        finish_recovery = asyncio.Event()
        reads = []
        mcp_auth = []

        async def recover():
            recovery_started.set()
            await finish_recovery.wait()

        def read_bootstrap(**_options):
            reads.append("bootstrap")
            return {"ok": True, "runtimeContinuations": ["recovered"]}

        def authenticate_mcp(*_args, **_options):
            mcp_auth.append("checked")
            raise HTTPException(status_code=401, detail="Fixture MCP token rejected")

        monkeypatch.setattr(dashboard, "on_startup", recover)
        monkeypatch.setattr(dashboard, "build_agentic_app_bootstrap_payload", read_bootstrap)
        monkeypatch.setattr(dashboard, "authenticate_agent_request", authenticate_mcp)
        await asyncio.wait_for(dashboard.start_dashboard_runtime(), 1)
        await asyncio.wait_for(recovery_started.wait(), 1)
        task = dashboard.DASHBOARD_RUNTIME_STARTUP_TASK
        headers = {"Authorization": "Bearer offline-startup-token", "Origin": "tauri://localhost"}
        transport = httpx.ASGITransport(app=dashboard.app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            waiting = asyncio.create_task(client.get("/api/app/bootstrap", headers=headers))
            waiting_mcp = asyncio.create_task(client.get("/mcp"))
            try:
                challenge = await asyncio.wait_for(client.get(
                    "/api/app/session-challenge?nonce=offline-probe-123",
                    headers={"Origin": "tauri://localhost"},
                ), 1)
                expected = hmac.new(b"offline-startup-token", b"vrcforge.app-session.v1\noffline-probe-123", hashlib.sha256).hexdigest()
                assert challenge.status_code == 200
                assert challenge.json()["signature"] == expected
                assert (await asyncio.wait_for(client.get("/api/health"), 1)).status_code == 200
                assert (await asyncio.wait_for(client.get("/api/app/bootstrap"), 1)).status_code == 401
                assert not waiting.done() and not reads
                assert not waiting_mcp.done() and not mcp_auth
                waiting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting
                assert not task.done()
                waiting = asyncio.create_task(client.get("/api/app/bootstrap", headers=headers))
            finally:
                finish_recovery.set()
                await task
            response = await asyncio.wait_for(waiting, 1)
            assert response.json() == {"ok": True, "runtimeContinuations": ["recovered"]}
            assert reads == ["bootstrap"]
            assert (await asyncio.wait_for(waiting_mcp, 1)).status_code == 401
            assert mcp_auth == ["checked"]

    asyncio.run(scenario())


def test_authenticated_shutdown_blocks_work_waiting_for_recovery(managed_runtime, monkeypatch):
    dashboard, server = managed_runtime

    async def scenario():
        finish_recovery = asyncio.Event()
        request_is_waiting = asyncio.Event()
        startup_error = dashboard.dashboard_runtime_startup_error

        async def recover():
            await finish_recovery.wait()

        async def observe_wait():
            request_is_waiting.set()
            return await startup_error()

        def unexpected_read(**_options):
            pytest.fail("Closing runtime admitted a request queued during recovery")

        monkeypatch.setattr(dashboard, "on_startup", recover)
        monkeypatch.setattr(dashboard, "dashboard_runtime_startup_error", observe_wait)
        monkeypatch.setattr(dashboard, "build_agentic_app_bootstrap_payload", unexpected_read)
        await dashboard.start_dashboard_runtime()
        task = dashboard.DASHBOARD_RUNTIME_STARTUP_TASK
        headers = {"Authorization": "Bearer offline-startup-token"}
        path = dashboard.APP_INTERNAL_SHUTDOWN_PATH
        proof = hmac.new(b"offline-startup-token", f"vrcforge.tauri-ipc-bridge.v1\nPOST\n{path}".encode(), hashlib.sha256).hexdigest()
        transport = httpx.ASGITransport(app=dashboard.app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            waiting = asyncio.create_task(client.get("/api/app/bootstrap", headers=headers))
            try:
                await asyncio.wait_for(request_is_waiting.wait(), 1)
                response = await asyncio.wait_for(client.post(path, headers={
                    **headers, "X-VRCForge-Transport": "tauri-ipc-bridge", "X-VRCForge-Transport-Proof": proof,
                }), 1)
                assert response.status_code == 202
                assert server.should_exit and not task.done()
            finally:
                finish_recovery.set()
                await task
            assert (await asyncio.wait_for(waiting, 1)).json()["error"]["code"] == "runtime_shutting_down"

    asyncio.run(scenario())


def test_recovery_failure_stops_listener_and_rejects_runtime_requests(managed_runtime, monkeypatch):
    dashboard, server = managed_runtime

    async def scenario():
        async def recover():
            raise RuntimeError("fixture private exception")

        def unexpected_read(**_options):
            pytest.fail("Failed recovery admitted a stateful request")

        monkeypatch.setattr(dashboard, "on_startup", recover)
        monkeypatch.setattr(dashboard, "build_agentic_app_bootstrap_payload", unexpected_read)
        await dashboard.start_dashboard_runtime()
        transport = httpx.ASGITransport(app=dashboard.app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            response = await asyncio.wait_for(client.get(
                "/api/app/bootstrap", headers={"Authorization": "Bearer offline-startup-token"},
            ), 1)
        assert server.should_exit
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "runtime_startup_failed"
        assert "fixture private exception" not in response.text

    asyncio.run(scenario())
