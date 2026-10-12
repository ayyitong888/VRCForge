"""Offline complete session-control path, with isolated real transcript storage."""
import asyncio
import copy
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


def test_session_controls_through_real_dashboard_storage(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("VRCFORGE_")}
    env.update(PYTHONIOENCODING="utf-8", VRCFORGE_USER_DATA_DIR=str(tmp_path), VRCFORGE_CONFIG_DIR=str(tmp_path / "config"),
               VRCFORGE_DATA_DIR=str(tmp_path / "data"), VRCFORGE_LOG_DIR=str(tmp_path / "logs"),
               VRCFORGE_ARTIFACTS_DIR=str(tmp_path / "artifacts"),
               VRCFORGE_APP_SESSION_TOKEN="synthetic-session-controls-test-only")
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-6000:]
    assert "SESSION_CONTROLS_CHAIN_OK" in result.stdout


async def worker(root):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError("external network forbidden")
    socket.socket.connect = socket.create_connection = forbidden
    import dashboard_server as server
    from dashboard_api_models import ChatTranscriptsRequest
    from channel_runtime_adapter import ChannelRuntimeAdapter
    from context_compaction import compact_context, normalize_compaction_recovery
    from telegram_dashboard_integration import make_telegram_runtime_factory
    from telegram_bot_commands import TelegramCommandDispatcher
    from telegram_bot_store import TelegramBotStore
    from telegram_bot_host import TelegramBotHost
    from test_telegram_service_integration import LocalTransport, OWNER, until
    assert server.runtime_paths.USER_DATA_DIR == root
    project = root / "project"
    project.mkdir()
    calls, results, compact_diagnostics = [], [], []
    # Only provider configuration and summarization I/O are synthetic. The
    # production dashboard compactor, recovery builder and projection run intact.
    server.PROVIDER_CONFIGURATION.resolve_session_config = lambda **kwargs: SimpleNamespace(
        provider=kwargs.get("provider") or "openai", model=kwargs.get("model") or "chosen-model",
        api_key="synthetic-provider-key", base_url="http://127.0.0.1:1", api_type="openai", thinking_level="")
    server.request_llm_plan = lambda settings, prompt: {"summary": "Preserve the blue outfit goal and completed inspection."}
    def capture_compact(*args, **kwargs):
        result = compact_context(*args, **kwargs)
        results.append(result)
        assert normalize_compaction_recovery(result["recovery"]) == result["recovery"]
        return result
    server.compact_context = capture_compact
    compact_entered = asyncio.Event()
    hold_compact = [False]
    async def native(params):
        calls.append(copy.deepcopy(params))
        return {"ok": True, "plan": {"reply": "NATIVE_RESPONSE", "summary": "Completed", "planner": "runtime", "shellNeeded": False}}
    async def compactor(chat, scope, instructions):
        if hold_compact[0]:
            compact_entered.set()
            await asyncio.Event().wait()
        try:
            result = await server._telegram_compact_chat(chat, scope, instructions)
        except Exception as exc:
            compact_diagnostics.append((type(exc).__name__, str(exc)))
            raise
        compact_diagnostics.append({key: value for key, value in result.items() if key != "chat"})
        assert result.get("ok") or result.get("reason") == "compact_no_reduction", result
        return result
    runtime = make_telegram_runtime_factory(load_chats=server._telegram_chat_snapshot,
        save_chats=server._telegram_save_chats, message=native, steer=lambda params: {}, cancel=lambda params: {},
        current_config=lambda: SimpleNamespace(provider="default-provider", model="default-model"),
        model_catalog=lambda scope: [{"provider": "openai", "id": "chosen-model"}],
        compactor=compactor)(lambda event: None)
    store = TelegramBotStore(root / "telegram-controls.sqlite3", "fixture-bot")
    dispatcher = TelegramCommandDispatcher(owner_id=OWNER, projects=(str(project),), store=store, runtime=runtime)
    def update(number, text):
        return {"update_id": number, "message": {"from": {"id": OWNER}, "chat": {"id": OWNER, "type": "private"}, "text": text}}
    def stored(chat_id):
        return next(chat for chat in server._telegram_chat_snapshot()["chats"] if chat["id"] == chat_id)
    host = None
    try:
        short_chat = {"id": "short", "sessionId": "short-session", "projectPath": "", "items": [
            {"id": "short-user", "type": "user", "text": "blue outfit " * 150},
            {"id": "short-agent", "type": "agent", "response": {"ok": True, "plan": {
                "reply": "inspection " * 150, "summary": "Complete", "planner": "runtime", "shellNeeded": False}}}]}
        before_short = copy.deepcopy(short_chat)
        short_result = await compactor(short_chat, "", "")
        assert short_result["ok"] is False and short_result["reason"] == "compact_no_reduction"
        assert short_chat == before_short
        for index, scope in enumerate(("", str(project))):
            chat_id = "controls-" + str(index)
            chat = {"id": chat_id, "sessionId": "original-" + str(index), "title": "Controls fixture",
                    "projectPath": scope, "projectType": "unity" if scope else "general", "items": []}
            for turn in range(8):
                chat["items"].extend([{"id": f"u{turn}", "type": "user", "text": "blue outfit goal " * 90},
                    {"id": f"a{turn}", "type": "agent", "response": {"ok": True, "plan": {"reply": "inspection complete " * 90, "summary": "Completed", "planner": "runtime", "shellNeeded": False}}}])
            snapshot = server._telegram_chat_snapshot()
            existing = snapshot["chats"]
            saved, _, _ = server.write_chat_transcripts_storage(ChatTranscriptsRequest(chats=[*existing, chat], sourceRevisions=snapshot["sourceRevisions"]))
            assert saved["ok"]
            store.set_binding(str(OWNER), scope, chat_id)
            store.watch_session(str(OWNER), scope, chat["sessionId"])
            choice = await dispatcher.dispatch(update(100 + index * 10, "/model"))
            data = choice["messages"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            await dispatcher.dispatch({"update_id": 101 + index * 10, "callback_query": {"from": {"id": OWNER},
                "message": {"message_id": 5, "chat": {"id": OWNER, "type": "private"}}, "data": data}})
            assert stored(chat_id)["settings"] == {"provider": "openai", "model": "chosen-model"}
            await dispatcher.dispatch(update(102 + index * 10, "next using chosen model"))
            await until(lambda: len(calls) == 1 + index * 2 and not runtime._coordinator.has_active_session(chat["sessionId"]) and not runtime._event_tasks, "model turn did not finish")
            before = calls[-1]
            assert (before["provider"], before["model"], before["chatId"], (before["projectRoot"] or "")) == ("openai", "chosen-model", chat_id, scope), {key: before[key] for key in ("provider", "model", "chatId", "projectRoot")}
            compact = await dispatcher.dispatch(update(103 + index * 10, "/compact keep outfit goal"))
            assert "已压缩" in compact["text"], (compact, compact_diagnostics)
            updated = stored(chat_id)
            assert updated["sessionId"] and updated["sessionId"] != chat["sessionId"]
            assert any(w["session_id"] == updated["sessionId"] for w in store.watches())
            await dispatcher.dispatch(update(104 + index * 10, "continue compacted session"))
            await until(lambda: len(calls) == 2 + index * 2 and not runtime._coordinator.has_active_session(updated["sessionId"]) and not runtime._event_tasks, "compact turn did not finish")
            after = calls[-1]
            assert after["session_id"] == updated["sessionId"]
            assert after["chatId"] == chat_id and (after["projectRoot"] or "") == scope
            assert after["provider"] == "openai" and after["model"] == "chosen-model"
            assert sum(len(row["text"]) for row in after["history"]) < sum(len(row["text"]) for row in before["history"])
            assert after["compactionRecovery"] == [results[-1]["recovery"]]
        # Same host dispatcher worker must survive cancellation of compact.
        transport = LocalTransport()
        host = TelegramBotHost(transport, store, dispatcher.dispatch, OWNER)
        await host.start()
        hold_compact[0] = True
        original_session = stored(chat_id)["sessionId"]
        transport.push(300, "/compact")
        await asyncio.wait_for(compact_entered.wait(), 2)
        transport.push(301, "/stop")
        await until(lambda: any("取消上下文压缩" in row["text"] for row in transport.messages), "compact cancellation response missing")
        assert stored(chat_id)["sessionId"] == original_session
        transport.push(302, "/model")
        await until(lambda: any(row.get("reply_markup", {}).get("inline_keyboard") for row in transport.messages), "dispatcher worker died after compact cancellation")
        assert not runtime._compact_tasks
        print("SESSION_CONTROLS_CHAIN_OK")
    finally:
        if host:
            await host.close()
        await runtime.shutdown()
        store.close()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    asyncio.run(worker(Path(sys.argv[2]).resolve()))
