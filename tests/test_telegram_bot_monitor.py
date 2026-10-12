import asyncio

from telegram_bot_monitor import TelegramBotMonitor, format_monitor_status


def run(coro):
    return asyncio.run(coro)


def test_general_monitor_keeps_binding_and_reports_unity_not_applicable():
    async def snapshot(project, session):
        return {}
    monitor = TelegramBotMonitor(snapshot, lambda event: None,
        lambda: [{"route": "7", "project_root": "", "session_id": "general-session"}])
    assert monitor._read_bindings() == [{"route": "7", "project": "", "session": "general-session"}]
    text = format_monitor_status({"taskStatus": "running", "unityStatus": "not_applicable"})
    assert "未选择工程（不适用）" in text and "未连接" not in text


def test_context_status_uses_latest_peak_and_does_not_fake_unknown():
    text = format_monitor_status({
        "taskStatus": "completed", "unityStatus": "not_applicable",
        "contextUsage": {"status": "available", "usedTokens": 1234,
                          "limitTokens": 8000, "occupancySource": "peakInputTokens"},
    })
    assert "上下文：1,234 / 8,000 tokens（15% · 最近回合峰值）" in text
    unknown = format_monitor_status({
        "taskStatus": "completed", "unityStatus": "not_applicable",
        "contextUsage": {"status": "unavailable", "unavailableReason": "model_changed"},
    })
    assert "模型已切换" in unknown
    assert "0 /" not in unknown
    assert len(format_monitor_status({"taskStatus": "running", "unityStatus": "connected"}).splitlines()) == 3


def test_progress_changes_are_bounded_deduplicated_and_baseline_silent():
    async def case():
        state = {"runId": "r1", "taskStatus": "running", "unityStatus": "connected", "progressSummary": "initial"}
        sent = []
        async def snapshot(project, session):
            return dict(state)
        monitor = TelegramBotMonitor(snapshot, sent.append, lambda: [])
        entry = {"route": "7", "project": "p", "session": "s"}
        semaphore = asyncio.Semaphore(1)
        await monitor._sample_one(entry, semaphore)
        assert sent == []
        state["progressSummary"] = "progress-" + "x" * 500
        await monitor._sample_one(entry, semaphore)
        assert len(sent) == 1
        assert sent[0]["result"]["reply"].startswith("任务进度：progress-")
        assert len(sent[0]["result"]["reply"]) <= 205
        await monitor._sample_one(entry, semaphore)
        assert len(sent) == 1
        state["taskStatus"] = "completed"
        state["progressSummary"] = "finished"
        await monitor._sample_one(entry, semaphore)
        assert len(sent) == 2 and sent[-1]["result"]["status"] == "completed"
        await monitor.close()
    run(case())


def test_first_snapshot_is_silent_and_changes_emit_safe_projection():
    async def case():
        states = [{"runId": "r1", "clientTurnId": "t1", "taskStatus": "running", "unityStatus": "connected"},
                  {"runId": "r1", "clientTurnId": "t1", "taskStatus": "completed", "unityStatus": "connected"}]
        sent = []
        calls = 0

        async def snapshot(project, session):
            nonlocal calls
            value = states[min(calls, 1)]
            calls += 1
            return value

        monitor = TelegramBotMonitor(snapshot, sent.append, lambda: [{"route": "7", "project_root": "p", "session_id": "s"}], interval=0.05)
        await monitor.start()
        await asyncio.sleep(0.01)
        assert sent == []
        await asyncio.sleep(0.06)
        await monitor.close()
        assert sent and sent[0]["event"] == "telegram_task_status_changed"
        assert "status" in sent[0]["result"]

    run(case())


def test_waiting_approval_and_failed_are_distinct_and_errors_are_isolated():
    async def case():
        sent = []
        calls = {}

        async def snapshot(project, session):
            calls[session] = calls.get(session, 0) + 1
            if session == "bad":
                raise RuntimeError("private details")
            return {"runId": "r1", "clientTurnId": "t1", "taskStatus": "running", "unityStatus": "connected"} if calls[session] == 1 else {"runId": "r1", "clientTurnId": "t1", "taskStatus": "waiting_approval", "unityStatus": "connected"}

        monitor = TelegramBotMonitor(snapshot, sent.append, lambda: [
            {"route": "7", "project_root": "p", "session_id": "good"},
            {"route": "7", "project_root": "p", "session_id": "bad"},
        ], interval=0.03)
        await monitor.start()
        await asyncio.sleep(0.08)
        await monitor.close()
        assert any(item["event"] == "telegram_task_status_changed" and item["result"]["status"] == "waiting_approval" for item in sent)
        assert all("private details" not in repr(item) for item in sent)

    run(case())


def test_close_cancels_bounded_snapshot_without_blocking():
    async def case():
        started = asyncio.Event()

        async def snapshot(project, session):
            started.set()
            await asyncio.sleep(60)
            return {}

        monitor = TelegramBotMonitor(snapshot, lambda event: None, lambda: [{"route": "7", "project_root": "p", "session_id": "s"}], interval=5)
        await monitor.start()
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(monitor.close(), 1)

    run(case())
