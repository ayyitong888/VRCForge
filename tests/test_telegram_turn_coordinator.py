import asyncio

from channel_turn_coordinator import ChannelTurnCoordinator, ChannelTurnMetadata


class FakeRuntime:
    def __init__(self):
        self.started = {}
        self.steers = []
        self.cancels = []
        self.fail = set()

    async def start_turn(self, session_id, client_turn_id, message):
        event = asyncio.Event()
        self.started[(session_id, client_turn_id)] = event
        if client_turn_id in self.fail:
            raise RuntimeError("boom")
        await event.wait()
        return {"reply": message}

    async def steer(self, session_id, target_client_turn_id, client_turn_id, message):
        self.steers.append((session_id, target_client_turn_id, client_turn_id, message))
        return {"accepted": True}

    async def cancel(self, session_id, target_client_turn_id):
        self.cancels.append((session_id, target_client_turn_id))


class RejectingSteerRuntime(FakeRuntime):
    async def steer(self, *_args):
        return {}


class StringSteerRuntime(FakeRuntime):
    async def steer(self, *_args):
        return "rejected"


class LateAcceptRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.allow_steer = asyncio.Event()

    async def steer(self, *_args):
        await self.allow_steer.wait()
        return {"accepted": True}


def test_idle_start_is_nonblocking_and_busy_message_steers():
    async def scenario():
        runtime = FakeRuntime()
        events = []
        coordinator = ChannelTurnCoordinator(runtime, events.append)
        assert coordinator.submit("s", "t1", "first")["status"] == "started"
        assert coordinator.submit("s", "t2", "change")["status"] == "steer_submitted"
        for _ in range(5):
            await asyncio.sleep(0)
        assert runtime.steers == [("s", "t1", "t2", "change")]
        assert not any(item.get("event") == "turn_returned" for item in events)
        runtime.started[("s", "t1")].set()
        for _ in range(5):
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert any(item.get("event") == "turn_returned" for item in events)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_stop_reports_requested_and_does_not_claim_completion():
    async def scenario():
        runtime = FakeRuntime()
        events = []
        coordinator = ChannelTurnCoordinator(runtime, events.append)
        coordinator.submit("s", "t1", "first", metadata=ChannelTurnMetadata(route="r"))
        result = coordinator.stop("s")
        assert result["status"] == "requested"
        for _ in range(5):
            await asyncio.sleep(0)
        assert runtime.cancels == [("s", "t1")]
        assert not any(item.get("event") == "turn_returned" for item in events)
        runtime.started[("s", "t1")].set()
        await asyncio.sleep(0)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_sessions_are_independent_failure_isolated_and_capacity_bounded():
    async def scenario():
        runtime = FakeRuntime()
        runtime.fail.add("bad")
        events = []
        coordinator = ChannelTurnCoordinator(runtime, events.append, max_active_sessions=2)
        coordinator.submit("a", "a1", "a")
        coordinator.submit("b", "bad", "b")
        assert coordinator.submit("c", "c1", "c")["reason"] == "max_active_sessions"
        for _ in range(8):
            await asyncio.sleep(0)
        assert any(item.get("event") == "turn_failed" for item in events)
        assert coordinator.active_sessions == 1
        runtime.started[("a", "a1")].set()
        for _ in range(5):
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_shutdown_requests_cancel_and_joins_owned_tasks():
    async def scenario():
        runtime = FakeRuntime()
        coordinator = ChannelTurnCoordinator(runtime, lambda _event: None)
        coordinator.submit("s", "t1", "first")
        await coordinator.shutdown()
        assert runtime.cancels == [("s", "t1")]
        assert coordinator.active_sessions == 0
        assert coordinator.submit("s", "t2", "later")["reason"] == "coordinator_closing"

    asyncio.run(scenario())


def test_steer_requires_explicit_acceptance_and_auxiliary_capacity():
    async def scenario():
        runtime = RejectingSteerRuntime()
        events = []
        coordinator = ChannelTurnCoordinator(runtime, events.append, max_auxiliary_tasks=1)
        coordinator.submit("s", "t1", "first")
        assert coordinator.submit("s", "t2", "second")["status"] == "steer_submitted"
        assert coordinator.submit("s", "t3", "third")["reason"] == "auxiliary_capacity"
        for _ in range(8):
            await asyncio.sleep(0)
        assert any(item.get("event") == "steer_notaccepted" for item in events)
        runtime.started[("s", "t1")].set()
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_duplicate_stop_is_merged_and_shutdown_cancel_is_bounded():
    async def scenario():
        class StuckRuntime(FakeRuntime):
            async def cancel(self, *_args):
                await asyncio.Event().wait()

        runtime = StuckRuntime()
        coordinator = ChannelTurnCoordinator(runtime, lambda _event: None, shutdown_timeout_seconds=0.01)
        coordinator.submit("s", "t1", "first")
        assert coordinator.stop("s")["status"] == "requested"
        assert coordinator.stop("s")["merged"] is True
        await coordinator.shutdown()
        assert coordinator.active_sessions == 0

    asyncio.run(scenario())


def test_none_and_string_steer_results_are_notaccepted():
    async def scenario():
        for runtime in (RejectingSteerRuntime(), StringSteerRuntime()):
            events = []
            coordinator = ChannelTurnCoordinator(runtime, events.append)
            coordinator.submit("s", "t1", "first")
            coordinator.submit("s", "t2", "second")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert any(item.get("event") == "steer_notaccepted" for item in events)
            runtime.started[("s", "t1")].set()
            await coordinator.shutdown()

    asyncio.run(scenario())


def test_runtime_acceptance_wins_if_turn_ends_before_steer_returns():
    async def scenario():
        runtime = LateAcceptRuntime()
        events = []
        coordinator = ChannelTurnCoordinator(runtime, events.append)
        coordinator.submit("s", "t1", "first")
        coordinator.submit("s", "t2", "second")
        for _ in range(5):
            await asyncio.sleep(0)
        runtime.started[("s", "t1")].set()
        await asyncio.sleep(0)
        runtime.allow_steer.set()
        for _ in range(8):
            await asyncio.sleep(0)
        assert any(item.get("event") == "steer_accepted" for item in events)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_waiting_approval_is_returned_for_upper_layer_interpretation():
    async def scenario():
        class WaitingRuntime(FakeRuntime):
            async def start_turn(self, session_id, client_turn_id, message):
                return {"status": "waiting_approval", "approvalId": "a1"}

        events = []
        coordinator = ChannelTurnCoordinator(WaitingRuntime(), events.append)
        coordinator.submit("s", "t1", "write")
        for _ in range(8):
            await asyncio.sleep(0)
        returned = [item for item in events if item.get("event") == "turn_returned"]
        assert returned and returned[0]["result"]["status"] == "waiting_approval"
        assert not any(item.get("event") == "turn_completed" for item in events)
        await coordinator.shutdown()

    asyncio.run(scenario())
