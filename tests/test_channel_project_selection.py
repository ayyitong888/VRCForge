import asyncio
from pathlib import Path
import json
import pytest
from channel_project_selection import register_channel_project_selection, TOOL_NAME

class G:
    def __init__(self): self.tools={}
    def register_tool(self,n,d,c,h): self.tools[n]=(d,c,h)
    def consume_runtime_task_link(self,p):
        if p.pop("ok",False): return ({"sessionId":"s","clientTurnId":"t","projectRoot":""},"s")
        return None,""
    def request_runtime_project_selection(self,p,cb): return cb({"sessionId":"s","chatId":"c","clientTurnId":"t","projectRoot":"","requestText":"pick"})

def test_register_and_scope():
    g=G(); seen=[]; register_channel_project_selection(g,lambda scope: seen.append(scope) or {"status":"needs_user_action"})
    assert TOOL_NAME in g.tools
    assert g.tools[TOOL_NAME][1]=="plan/preview"
    assert g.tools[TOOL_NAME][2]({"ok":True})["status"]=="needs_user_action"
    assert seen[0]["requestText"]=="pick"


def test_real_gateway_registration_trusted_scope_and_external_rejection(tmp_path):
    from agent_gateway import AgentGateway, _RUNTIME_TASK_LINK_AUTHORITY

    seen = []
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit",
        project_selection_request=lambda scope: seen.append(scope) or {
            "status": "needs_user_action", "projectSelectionRequested": True,
            "outcome": {"kind": "project_selection"},
        })
    assert TOOL_NAME in gateway._tools
    assert any(item.get("name") == TOOL_NAME for item in gateway.build_tool_registry()["tools"])
    from agent_gateway import AgentGatewayError
    gateway.config_path.parent.mkdir(parents=True, exist_ok=True)
    gateway.config_path.write_text(json.dumps({"enabled": True, "require_token": False}), encoding="utf-8")
    external = gateway.call_tool(TOOL_NAME, {"sessionId": "forged", "chatId": "chat", "clientTurnId": "turn"})
    assert external["status"] == "failed" and external["errorCode"] == "runtime_scope_untrusted"
    assert not seen
    result = gateway._tools[TOOL_NAME].handler({
        "_runtimeTaskLinkAuthority": _RUNTIME_TASK_LINK_AUTHORITY,
        "_runtimeSessionId": "real-session",
        "_taskSeed": {"sessionId": "real-session", "clientTurnId": "real-turn", "projectRoot": ""},
        "chatId": "real-chat", "requestText": "inspect the project",
        "projectRoot": "forged-model-path",
    })
    assert result["status"] == "needs_user_action"
    assert seen[0] == {"sessionId": "real-session", "chatId": "real-chat",
                       "clientTurnId": "real-turn", "projectRoot": "", "requestText": "inspect the project"}


def _native_gateway(tmp_path, monkeypatch, callback, model_arguments=None):
    """Use the App's real catalog and runtime, replacing only the provider port."""
    import copy
    import dashboard_server as host
    from agent_gateway import AgentGateway
    from runtime_planner_service import PlannerModelResult, PlannerTurnMetadata, RuntimePlannerService
    from tests.test_runtime_planner_service import FakeDesktop, FakeTurn, native_tool_call

    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit",
                           project_selection_request=callback)
    # App bootstrap owns this standard tool; the registry always adds its Unity alias.
    def unexpected_shell(_params):
        raise AssertionError("project selection must not execute a shell")
    gateway.register_tool("vrcforge_execute_shell", "Test-only shell sentinel.", "read/debug", unexpected_shell)
    gateway.config_path.write_text(json.dumps({"enabled": True, "require_token": False}), encoding="utf-8")
    monkeypatch.setattr(host, "AGENT_GATEWAY", gateway)
    monkeypatch.setattr(host, "_RUNTIME_PROFILED_TOOL_REGISTRY", host._build_runtime_profiled_tool_registry())
    catalog = host._RuntimePlannerCatalog()
    from channel_project_selection import bind_channel_project_selection
    with bind_channel_project_selection():
        selection = next(tool for tool in catalog.read("planning", project_context_active=False).visible_tools
                         if tool.runtime_name == TOOL_NAME)
    assert selection.block == "core", "first general turn must not need a tool-block discovery round trip"
    assert not selection.write
    assert not selection.input_schema.get("required"), "selection accepts no model-owned project path or scope"

    class Model:
        def __init__(self):
            self.requests = []

        def plan(self, prompt):
            raise AssertionError("runtime must use native provider transport")

        def plan_native(self, request):
            self.requests.append(copy.deepcopy(request))
            if len(self.requests) == 1:
                message = native_tool_call(selection.name, model_arguments or {}, "call-project-selection")
            else:
                message = {"role": "assistant", "content": "Selected project is ready for inspection."}
            return PlannerModelResult(text=message.get("content") or "", assistant_message=message,
                                      finish_reason="tool_calls" if message.get("tool_calls") else "stop")

    model = Model()
    gateway.bind_runtime_planner(RuntimePlannerService(
        catalog=catalog, desktop=FakeDesktop(), model=model,
        turn=FakeTurn(PlannerTurnMetadata(native_binding="synthetic-project-selection-provider")),
    ))
    return gateway, model, selection


def _channel_message(gateway, params):
    from channel_project_selection import bind_channel_project_selection

    async def dispatch():
        # Mirror the App async wrapper -> to_thread context propagation boundary.
        with bind_channel_project_selection():
            return await asyncio.to_thread(gateway.runtime_message, {
                **params, "_projectContextActive": bool(params.get("projectRoot")),
            })

    return asyncio.run(dispatch())


def test_native_general_runtime_requests_selection_and_releases_turn_for_continuation(tmp_path, monkeypatch):
    seen = []
    # Match TelegramBotService's actual callback shape: it has no Question ID or outcome wrapper.
    def request_selection(scope):
        seen.append(scope)
        return {"ok": True, "status": "needs_user_action", "projectSelectionRequested": True,
                "summary": "Choose a project before continuing; selection does not approve edits."}

    gateway, model, selection = _native_gateway(tmp_path, monkeypatch, request_selection)
    first = _channel_message(gateway, {"message": "Inspect my avatar project", "sessionId": "runtime-session",
                                     "chatId": "chat-session", "clientTurnId": "turn-before-selection",
                                     "projectRoot": "", "maxAgenticTurns": 3})
    assert seen == [{"sessionId": "runtime-session", "chatId": "chat-session",
                     "clientTurnId": "turn-before-selection", "projectRoot": "",
                     "requestText": "Inspect my avatar project"}]
    assert len(model.requests) == 1, "project selection must park without asking the model again"
    assert first["plan"]["nextStep"] == "needs_user_action"
    assert first["plan"]["continueLoop"] is False
    step = next(step for step in first["steps"] if step.get("tool") == TOOL_NAME)
    assert step["outcome"]["status"] == "needs_user_action"
    assert gateway.questions.list(session_id="runtime-session")["questions"] == []
    runs = gateway.runtime_runs.list_runs(limit=10)["runs"]
    assert next(run for run in runs if run["clientTurnId"] == "turn-before-selection")["status"] == "blocked"

    async def bind_and_continue():
        import copy
        from channel_dashboard_integration import DashboardChannelRuntime
        from channel_project_selection import bind_channel_project_selection
        from channel_runtime_recovery import ChannelRuntimeRecovery
        from telegram_runtime_adapter import TelegramRuntimeAdapter

        recovery = ChannelRuntimeRecovery(gateway.runtime_runs)
        fresh = await recovery.check("runtime-session", "turn-after-selection", "")
        assert fresh.allowed and fresh.status == "absent"
        duplicate = await recovery.check("runtime-session", "turn-before-selection", "")
        assert not duplicate.allowed and duplicate.status == "terminal"
        assert duplicate.reason == "client_turn_already_recorded"

        class CASStore:
            def __init__(self):
                self.revision = 0
                self.chats = [{"id": "chat-session", "sessionId": "runtime-session", "title": "General",
                               "projectPath": "", "projectType": "general", "items": [
                                   {"id": "turn-before-selection", "type": "user", "text": "Inspect my avatar project"},
                                   {"id": first["turnId"], "type": "agent", "response": copy.deepcopy(first)},
                               ]}]

            def load(self):
                return {"chats": copy.deepcopy(self.chats), "sourceRevisions": [self.revision]}

            def save(self, chats, revisions):
                assert revisions == [self.revision], "chat writes must use the loaded CAS revision"
                self.chats = copy.deepcopy(chats)
                self.revision += 1
                return {"ok": True}

        async def message(params):
            with bind_channel_project_selection():
                return await asyncio.to_thread(gateway.runtime_message, {
                    **params, "_projectContextActive": bool(params.get("projectRoot")),
                })

        runtime = DashboardChannelRuntime(message=message, steer=gateway.submit_runtime_steer,
                                          cancel=gateway.request_runtime_cancel,
                                          current_config=lambda: {}, agent_name="test-channel", stop_reason="test stop")
        store = CASStore()
        original_items = copy.deepcopy(store.chats[0]["items"])
        returned = asyncio.Event()
        results = []

        def on_event(event):
            if event.get("event") == "turn_returned":
                results.append(event["result"])
                returned.set()

        adapter = TelegramRuntimeAdapter(runtime, store, on_event, recovery=recovery)
        try:
            history_before = await adapter.history("chat-session", "")
            bound = await adapter.bind_project("chat-session", str(tmp_path))
            assert bound["id"] == "chat-session" and bound["sessionId"] == "runtime-session"
            assert bound["projectType"] == "unity" and bound["projectPath"] == str(tmp_path)
            assert store.chats[0]["items"] == original_items
            assert await adapter.history("chat-session", str(tmp_path)) == history_before
            submitted = await adapter.submit("chat-session", "turn-after-selection",
                                             "Continue with the selected project", str(tmp_path))
            assert submitted["status"] == "started"
            await asyncio.wait_for(returned.wait(), timeout=5)
            assert store.chats[0]["items"][:2] == original_items
            assert store.chats[0]["items"][-1]["response"] == results[0]
            return results[0]
        finally:
            await adapter.shutdown()

    resumed = asyncio.run(bind_and_continue())
    assert len(model.requests) == 2
    assert resumed["plan"]["nextStep"] == "done"
    assert len(seen) == 1
    runs = gateway.runtime_runs.list_runs(limit=10)["runs"]
    assert next(run for run in runs if run["clientTurnId"] == "turn-after-selection")["status"] == "completed"


def test_native_project_selection_failure_is_not_a_pending_question(tmp_path, monkeypatch):
    gateway, model, _ = _native_gateway(tmp_path, monkeypatch, lambda scope: {
        "ok": False, "status": "failed", "errorCode": "channel_unavailable", "summary": "Channel is offline."})
    result = _channel_message(gateway, {"message": "Inspect my avatar project", "sessionId": "runtime-session",
                                      "chatId": "chat-session", "clientTurnId": "failed-selection",
                                      "projectRoot": "", "maxAgenticTurns": 2})
    from agent_gateway import find_current_pending_question
    assert not find_current_pending_question(result["steps"])
    step = next(step for step in result["steps"] if step.get("tool") == TOOL_NAME)
    assert step["outcome"]["status"] == "failed"
    assert result["plan"]["nextStep"] != "done"
    assert gateway.runtime_runs.list_runs(limit=10)["runs"][0]["status"] == "failed"


def test_native_project_selection_does_not_accept_model_owned_scope(tmp_path, monkeypatch):
    seen = []
    forged = {"sessionId": "forged-session", "chatId": "forged-chat", "clientTurnId": "forged-turn",
              "projectRoot": "forged-project", "requestText": "forged-message",
              "_runtimeSessionId": "forged-session", "_runtimeTaskLinkAuthority": "forged-authority",
              "_taskSeed": {"sessionId": "forged-session", "projectRoot": "forged-project"}}
    gateway, model, _ = _native_gateway(tmp_path, monkeypatch, lambda scope: seen.append(scope) or {
        "ok": True, "status": "needs_user_action", "projectSelectionRequested": True}, forged)
    result = _channel_message(gateway, {"message": "Real project request", "sessionId": "runtime-session",
                                      "chatId": "real-chat", "clientTurnId": "real-turn",
                                      "projectRoot": "", "maxAgenticTurns": 2})
    if seen:
        assert seen == [{"sessionId": "runtime-session", "chatId": "real-chat", "clientTurnId": "real-turn",
                         "projectRoot": "", "requestText": "Real project request"}]
        assert result["plan"]["nextStep"] == "needs_user_action"
    else:
        # A stricter schema may reject extra arguments before reaching the handler.
        assert any(step.get("kind") == "planner_validation" or step.get("argumentValidation")
                   or step.get("result", {}).get("code") == "planner_invalid_response"
                   for step in result.get("steps", [])), result


@pytest.mark.parametrize("layer", ["planning", "execution"])
def test_project_selection_catalog_requires_channel_scope_and_no_selected_project(tmp_path, monkeypatch, layer):
    import dashboard_server as host
    from channel_project_selection import bind_channel_project_selection, channel_project_selection_available

    _native_gateway(tmp_path, monkeypatch, lambda scope: {"status": "needs_user_action"})
    assert channel_project_selection_available() is False
    catalog = host._RuntimePlannerCatalog()
    def names(active):
        snapshot = catalog.read(layer, project_context_active=active)
        return {tool.runtime_name for tool in (*snapshot.visible_tools, *snapshot.routable_tools)}

    assert TOOL_NAME not in names(False), "ordinary desktop general chat must not offer Telegram selection"
    with bind_channel_project_selection():
        assert channel_project_selection_available() is True
        assert TOOL_NAME in names(False)
        assert TOOL_NAME not in names(True), "bound Unity project must not ask to select again"
    assert channel_project_selection_available() is False
    assert TOOL_NAME not in names(False), "channel capability must not leak to subsequent desktop turns"


def test_native_project_selection_invalid_callback_does_not_park(tmp_path, monkeypatch):
    gateway, _, _ = _native_gateway(tmp_path, monkeypatch, lambda scope: None)
    result = _channel_message(gateway, {"message": "Inspect project", "sessionId": "runtime-session",
                                       "chatId": "real-chat", "clientTurnId": "invalid-callback",
                                       "projectRoot": "", "maxAgenticTurns": 2})
    from agent_gateway import find_current_pending_question
    assert not find_current_pending_question(result["steps"])
    step = next(step for step in result["steps"] if step.get("tool") == TOOL_NAME)
    assert step["outcome"]["status"] == "failed"
    assert step["result"]["errorCode"] == "project_selection_invalid_response"
    assert gateway.runtime_runs.list_runs(limit=10)["runs"][0]["status"] == "failed"
