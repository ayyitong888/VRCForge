"""Telegram channel policy wired to shared VRCForge runtime ports."""
from typing import Any, Callable
from channel_dashboard_integration import DashboardChannelRuntime, DashboardChannelChatStore
from telegram_runtime_adapter import TelegramRuntimeAdapter


def make_telegram_runtime_factory(*, load_chats: Callable, save_chats: Callable,
                                   message: Callable, steer: Callable, cancel: Callable,
                                   current_config: Callable, recovery: Any = None,
                                   model_catalog: Callable[[str], Any] | None = None,
                                   compactor: Callable[[dict[str, Any], str, str], Any] | None = None,
                                   unresolved: Callable[[dict[str, Any], str], Any] | None = None) -> Callable:
    def factory(emit: Callable) -> TelegramRuntimeAdapter:
        return TelegramRuntimeAdapter(
            DashboardChannelRuntime(message=message, steer=steer, cancel=cancel, current_config=current_config,
                agent_name="telegram-agent", stop_reason="telegram_user_stop"),
            DashboardChannelChatStore(load_chats, save_chats), emit, recovery=recovery,
            model_catalog=model_catalog, compactor=compactor, unresolved=unresolved,
        )
    return factory
