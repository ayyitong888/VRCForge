"""Isolated real-dashboard import/ASGI smoke; no listener or external requests."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys


def test_shared_approval_port_filters_general_project_and_session():
    from channel_dashboard_integration import DashboardChannelApprovals

    async def scenario():
        rows = [{"id": "general", "sessionId": "S", "projectRoot": ""},
                {"id": "project", "sessionId": "S", "arguments": {"projectRoot": "P"}},
                {"id": "other", "sessionId": "other", "projectRoot": ""}]
        port = DashboardChannelApprovals(list_pending=lambda _project: rows, approve=None, reject=None)
        assert [row["id"] for row in await port.list_pending("", "S")] == ["general"]
        assert [row["id"] for row in await port.list_pending("P", "S")] == ["project"]
        assert await port.list_pending("", "") == []
    asyncio.run(scenario())


def test_real_dashboard_telegram_wiring_in_isolated_process(tmp_path):
    # This test owns the child (45-second budget), all files are under tmp_path,
    # and requests use a synthetic app token with real application auth enabled.
    env = os.environ.copy()
    for name in list(env):
        if name.startswith("VRCFORGE_"):
            env.pop(name)
    env.update(VRCFORGE_USER_DATA_DIR=str(tmp_path), VRCFORGE_CONFIG_DIR=str(tmp_path / "config"),
               VRCFORGE_DATA_DIR=str(tmp_path / "data"), VRCFORGE_LOG_DIR=str(tmp_path / "logs"),
               VRCFORGE_ARTIFACTS_DIR=str(tmp_path / "artifacts"),
               VRCFORGE_APP_SESSION_TOKEN="telegram-smoke-local-only-0123456789")
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-6000:]
    assert "TELEGRAM_DASHBOARD_SMOKE_OK" in result.stdout


async def _worker(root):
    import socket
    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("external network forbidden in dashboard wiring smoke")
    socket.socket.connect = forbidden_network
    socket.create_connection = forbidden_network
    import httpx
    import dashboard_server as server
    from dashboard_api_models import AgentRuntimeMessageRequest
    assert server.APP_AUTH_REQUIRED
    assert server.runtime_paths.USER_DATA_DIR == root
    # The remote permission selector must share the real desktop authority.
    permission_port = server.TELEGRAM_SERVICE.permission_port
    before_permission = await permission_port.read()
    for mode in ("approval", "auto", "roslyn_full_auto"):
        changed = await permission_port.update(mode)
        assert changed["ok"] is True
        actual = server.AGENT_GATEWAY.approval_transactions.permission_state()
        assert actual["executionMode"] == mode
        assert (await permission_port.read())["executionMode"] == mode
    await permission_port.update(before_permission["executionMode"])
    project = root / "project"
    project.mkdir()
    # Synthetic ledger entries exercise the actual dashboard port and scope
    # guard without creating an execution handler or touching project assets.
    for approval_id, scope, session in (("general-fixture", "", "approval-session"),
                                        ("project-fixture", str(project), "approval-session"),
                                        ("other-fixture", "", "other-session")):
        server.AGENT_GATEWAY._approvals[approval_id] = {
            "id": approval_id, "status": "pending", "projectRoot": scope,
            "sessionId": session, "createdAt": "2026-10-12T00:00:00+00:00"}
    approval_port = server.TELEGRAM_SERVICE.approval_port
    assert [row["id"] for row in await approval_port.list_pending("", "approval-session")] == ["general-fixture"]
    assert [row["id"] for row in await approval_port.list_pending(str(project), "approval-session")] == ["project-fixture"]
    try:
        await approval_port.decide("project-fixture", "", False)
        raise AssertionError("general scope accepted a project approval")
    except server.HTTPException as error:
        assert error.status_code == 409
    rejected = await approval_port.decide("general-fixture", "", False)
    assert rejected["ok"] is True
    assert server.AGENT_GATEWAY._approvals["general-fixture"]["status"] == "rejected"
    auth = {"Authorization": "Bearer " + os.environ["VRCFORGE_APP_SESSION_TOKEN"]}
    # No lifespan is entered: this proves real import/route/glue wiring without
    # acquiring backend ownership, starting schedulers, or probing Unity.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app,
                                 client=("127.0.0.1", 54321)), base_url="http://127.0.0.1") as client:
        for method, path in (("GET", "/api/telegram/status"), ("PUT", "/api/telegram/config")):
            kwargs = {"json": {"ownerId": 12345, "projects": [str(project)]}} if method == "PUT" else {}
            response = await client.request(method, path, **kwargs)
            assert response.status_code in (401, 403), (path, response.status_code, response.text)
        response = await client.get("/api/telegram/status", headers=auth)
        assert response.status_code == 200, response.text
        assert response.json()["configured"] is False
        response = await client.put("/api/telegram/config", headers=auth, json={
            "token": "123456:" + "x" * 24, "ownerId": 12345, "projects": [str(project)]})
        assert response.status_code == 200, response.text
        assert response.json()["configured"] is True
        assert "token" not in response.json()
        assert response.headers.get("cache-control") == "no-store"
        question_scope = "dashboard-question-session"
        response = await client.post("/api/app/agent/questions", headers=auth, json={
            "header": "Dashboard wiring",
            "question": "Use the isolated fixture?",
            "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
            "sessionId": question_scope,
            "projectRoot": str(project),
        })
        assert response.status_code == 200, response.text
        created = response.json()["question"]
        question_id = created["questionId"]
        assert created["sessionId"] == question_scope
        response = await client.get("/api/app/agent/questions", headers=auth, params={
            "sessionId": question_scope, "projectRoot": str(project)})
        assert response.status_code == 200, response.text
        assert any(row["questionId"] == question_id and row["status"] == "pending"
                   for row in response.json()["questions"])
        pending = await server.TELEGRAM_SERVICE.questions.list(str(project), question_scope)
        assert [row["questionId"] for row in pending] == [question_id]
        answer = await server.TELEGRAM_SERVICE.questions.answer(question_id, "yes", str(project), question_scope)
        assert answer["question"]["status"] == "answered"
        response = await client.get("/api/app/agent/questions", headers=auth, params={
            "sessionId": question_scope, "projectRoot": str(project), "includeAnswered": True})
        assert response.status_code == 200, response.text
        assert any(row["questionId"] == question_id and row["status"] == "answered"
                   for row in response.json()["questions"])
    snapshot = server._telegram_chat_snapshot()
    assert snapshot["sourceRevisions"] == snapshot["sources"]
    assert isinstance(snapshot["sourceRevisions"], list)
    observed = []
    async def capture_validated_request(request):
        assert isinstance(request, AgentRuntimeMessageRequest)
        observed.append(request)
        return {"ok": True, "response": "isolated wiring response", "status": "completed"}
    server.app_agent_runtime_message = capture_validated_request
    runtime = server.TELEGRAM_SERVICE.runtime_factory(lambda _event: None)
    try:
        chat = await runtime.create_session(str(project), "Wiring smoke")
        await runtime.submit(chat["id"], "wiring-turn-1", "inspect", str(project))
        for _ in range(100):
            if observed:
                break
            await asyncio.sleep(0.01)
        assert observed, "dashboard message glue failed before validated native request"
        request = observed[0]
        assert request.chat_id == chat["id"]
        assert request.session_id == chat["sessionId"]
        assert request.client_turn_id == "wiring-turn-1"
        assert request.project_root == str(project)
        assert request.project_type == "unity"
        assert request.max_agentic_turns == 64
        assert request.plan_mode is False
    finally:
        await runtime.shutdown()
        await server.TELEGRAM_SERVICE.stop()
    print("TELEGRAM_DASHBOARD_SMOKE_OK")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    asyncio.run(_worker(Path(sys.argv[2]).resolve()))
