import ast
import asyncio
from pathlib import Path

from channel_runtime_adapter import ChannelRuntimeAdapter


class Store:
    def __init__(self):
        self.chats = []
        self.revisions = [{"storeId": "shared", "digest": "d"}]

    async def load(self):
        return {"chats": list(self.chats), "sourceRevisions": list(self.revisions)}

    async def save(self, chats, revisions):
        self.chats = list(chats)
        self.revisions = list(revisions)
        return {"ok": True}


class Runtime:
    def __init__(self):
        self.started = {}
        self.cancelled = []

    async def start_turn(self, session_id, client_turn_id, message, context):
        gate = asyncio.Event()
        self.started[(session_id, client_turn_id)] = gate
        await gate.wait()
        return {"turnId": f"turn-{client_turn_id}", "reply": message}

    async def steer(self, session_id, target, client_turn_id, message, context):
        return {"accepted": True}

    async def cancel(self, session_id, target, context):
        self.cancelled.append((session_id, target))


class PrefixAdapter(ChannelRuntimeAdapter):
    def __init__(self, prefix, *args, **kwargs):
        self.prefix = prefix
        super().__init__(*args, **kwargs)

    def _stable_identity(self, project_root, request_id, stable):
        return f"{self.prefix}_{stable}", f"{self.prefix}_sess_{stable}"

    def _random_identity(self):
        return super()._random_identity()

    def _default_title(self):
        return f"{self.prefix} chat"


def test_channel_adapters_isolate_identity_runtime_shutdown_and_events():
    async def scenario():
        store = Store()
        events_a, events_b = [], []
        runtime_a, runtime_b = Runtime(), Runtime()
        adapter_a = PrefixAdapter("discord", runtime_a, store, events_a.append)
        adapter_b = PrefixAdapter("slack", runtime_b, store, events_b.append)
        try:
            first = await adapter_a.create_session("/project", request_id="same")
            second = await adapter_b.create_session("/project", request_id="same")
            assert first["id"] != second["id"]
            assert first["sessionId"] != second["sessionId"]

            result_a = await adapter_a.submit(first["id"], "turn-a", "hello", "/project",
                                              metadata={"route": "discord-route"})
            result_b = await adapter_b.submit(second["id"], "turn-b", "hello", "/project",
                                              metadata={"route": "slack-route"})
            assert result_a["status"] == result_b["status"] == "started"
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert any(event.get("metadata", {}).get("route") == "discord-route" for event in events_a)
            assert any(event.get("metadata", {}).get("route") == "slack-route" for event in events_b)
            assert not any(event.get("metadata", {}).get("route") == "slack-route" for event in events_a)
            assert not any(event.get("metadata", {}).get("route") == "discord-route" for event in events_b)

            await adapter_a.shutdown()
            assert adapter_b._coordinator.has_active_session(second["sessionId"])
            assert not adapter_a._coordinator.has_active_session(first["sessionId"])
        finally:
            await adapter_b.shutdown()

    asyncio.run(scenario())


def test_channel_runtime_modules_do_not_import_telegram_modules():
    paths = [
        "channel_runtime_adapter.py",
        "channel_turn_coordinator.py",
        "channel_runtime_recovery.py",
        "channel_dashboard_integration.py",
    ]
    for name in paths:
        tree = ast.parse(Path(name).read_text(encoding="utf-8"), filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            assert all("telegram" not in value.lower() for value in imported), (name, imported)
