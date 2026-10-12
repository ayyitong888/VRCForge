import asyncio

import pytest

from telegram_bot_approvals import TelegramApprovalController, TelegramApprovalError


class Port:
    def __init__(self):
        self.rows = [{"approvalId": "a1", "status": "pending", "targetTool": "write", "reason": "change", "projectRoot": "P", "sessionId": "S", "arguments": {"objectPath": "Avatar/Body"}}]
        self.decisions = []

    async def list_pending(self, project_root, session_id):
        return [dict(row) for row in self.rows]

    async def decide(self, approval_id, project_root, approve):
        self.decisions.append((approval_id, project_root, approve))
        return {"ok": True, "status": "approved" if approve else "rejected"}


def test_general_approvals_are_exact_scope_and_keep_session_identity():
    async def scenario():
        port = Port()
        base = port.rows[0]
        port.rows = [dict(base, approvalId="general", projectRoot=""),
                     dict(base, approvalId="project"),
                     dict(base, approvalId="other-session", projectRoot="", sessionId="other")]
        controller = TelegramApprovalController(port)
        messages = await controller.pending_messages(owner_id=7, chat_id=7, project_root="", session_id="S")
        assert [row["approvalId"] for row in messages] == ["general"]
        result = await controller.consume_callback(messages[0]["buttons"][0]["callback_data"], owner_id=7, chat_id=7)
        assert result["status"] == "decided"
        assert port.decisions == [("general", "", True)]
        project_messages = await controller.pending_messages(owner_id=7, chat_id=7, project_root="P", session_id="S")
        assert [row["approvalId"] for row in project_messages] == ["project"]
        with pytest.raises(TelegramApprovalError, match="approval_scope_invalid"):
            await controller.pending_messages(owner_id=7, chat_id=7, project_root="", session_id="")
        messages = await controller.pending_messages(owner_id=7, chat_id=7, project_root="", session_id="S")
        port.rows[0]["projectRoot"] = "P"
        with pytest.raises(TelegramApprovalError, match="approval_state_changed"):
            await controller.consume_callback(messages[0]["buttons"][0]["callback_data"], owner_id=7, chat_id=7)
        assert len(port.decisions) == 1
    asyncio.run(scenario())


def test_render_and_consume_is_owner_chat_bound_and_one_shot():
    async def scenario():
        port = Port()
        controller = TelegramApprovalController(port, ttl_seconds=30)
        messages = await controller.pending_messages(owner_id=7, chat_id=8, project_root="P", session_id="S")
        callback = messages[0]["buttons"][0]["callback_data"]
        assert len(callback) <= 64
        with pytest.raises(TelegramApprovalError) as wrong:
            await controller.consume_callback(callback, owner_id=7, chat_id=9)
        assert wrong.value.code == "approval_callback_scope"
        assert len(port.decisions) == 0
        result = await controller.consume_callback(callback, owner_id=7, chat_id=8)
        assert result["status"] == "decided"
        with pytest.raises(TelegramApprovalError) as repeated:
            await controller.consume_callback(callback, owner_id=7, chat_id=8)
        assert repeated.value.code == "approval_callback_expired"
        assert port.decisions == [("a1", "P", True)]
    asyncio.run(scenario())


def test_expiry_state_change_and_rejection_do_not_decide():
    async def scenario():
        now = [10.0]
        port = Port()
        controller = TelegramApprovalController(port, ttl_seconds=2, clock=lambda: now[0])
        callback = (await controller.pending_messages(owner_id=1, chat_id=2, project_root="P", session_id="S"))[0]["buttons"][1]["callback_data"]
        now[0] = 13
        with pytest.raises(TelegramApprovalError) as expired:
            await controller.consume_callback(callback, owner_id=1, chat_id=2)
        assert expired.value.code == "approval_callback_expired"
        messages = await controller.pending_messages(owner_id=1, chat_id=2, project_root="P", session_id="S")
        callback = messages[0]["buttons"][1]["callback_data"]
        port.rows[0]["status"] = "approved"
        with pytest.raises(TelegramApprovalError) as changed:
            await controller.consume_callback(callback, owner_id=1, chat_id=2)
        assert changed.value.code == "approval_state_changed"
        assert not port.decisions
    asyncio.run(scenario())


def test_different_session_and_project_are_filtered_and_nonce_capacity_bounded():
    async def scenario():
        port = Port()
        port.rows[0]["sessionId"] = "S2"
        controller = TelegramApprovalController(port, max_pending_nonces=1)
        assert await controller.pending_messages(owner_id=1, chat_id=1, project_root="P", session_id="S1") == []
        port.rows[0]["sessionId"] = "S1"
        await controller.pending_messages(owner_id=1, chat_id=1, project_root="P", session_id="S1")
        with pytest.raises(TelegramApprovalError) as full:
            await controller.pending_messages(owner_id=1, chat_id=1, project_root="P", session_id="S1")
        assert full.value.code == "approval_nonce_capacity"
    asyncio.run(scenario())


def test_missing_scope_and_argument_drift_fail_closed_and_false_decision_is_not_success():
    async def scenario():
        port = Port()
        controller = TelegramApprovalController(port)
        port.rows[0].pop("projectRoot")
        assert await controller.pending_messages(owner_id=1, chat_id=2, project_root="P", session_id="S") == []
        port.rows[0]["projectRoot"] = "P"
        messages = await controller.pending_messages(owner_id=1, chat_id=2, project_root="P", session_id="S")
        callback = messages[0]["buttons"][0]["callback_data"]
        port.rows[0]["arguments"]["objectPath"] = "Avatar/Other"
        with pytest.raises(TelegramApprovalError) as drift:
            await controller.consume_callback(callback, owner_id=1, chat_id=2)
        assert drift.value.code == "approval_state_changed"
        messages = await controller.pending_messages(owner_id=1, chat_id=2, project_root="P", session_id="S")
        callback = messages[0]["buttons"][0]["callback_data"]
        class FalsePort(Port):
            async def decide(self, approval_id, project_root, approve):
                self.decisions.append((approval_id, project_root, approve))
                return {"ok": False, "status": "rejected"}
        # Preserve the live row while exercising a false decision result.
        false_port = FalsePort()
        false_controller = TelegramApprovalController(false_port)
        callback = (await false_controller.pending_messages(owner_id=1, chat_id=2, project_root="P", session_id="S"))[0]["buttons"][0]["callback_data"]
        result = await false_controller.consume_callback(callback, owner_id=1, chat_id=2)
        assert result["status"] == "notaccepted"
    asyncio.run(scenario())


def test_notification_refresh_reuses_live_nonce_before_capacity_is_exhausted():
    async def scenario():
        port = Port()
        controller = TelegramApprovalController(port, max_pending_nonces=1)
        first = await controller.pending_messages(owner_id=1, chat_id=2, project_root="P", session_id="S")
        callback = first[0]["buttons"][0]["callback_data"]
        for _ in range(300):
            refreshed = await controller.pending_messages(owner_id=1, chat_id=2, project_root="P",
                session_id="S", reuse_existing=True)
            assert refreshed[0]["buttons"][0]["callback_data"] == callback
        assert len(controller._bindings) == 1
    asyncio.run(scenario())
