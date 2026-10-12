"""Dashboard composition callbacks for channel session controls."""
from __future__ import annotations
import asyncio
from dataclasses import replace
from typing import Any

from channel_runtime_adapter import ChannelRuntimeAdapter
from channel_session_controls import compact_chat_items
from channel_session_controls import SessionControlError


async def has_unresolved_state(*, chat: dict[str, Any], project_root: str,
                               approval_port: Any, list_questions: Any) -> bool:
    """Read existing scoped authority before changing a session's context.

    The runtime ledger guard lives in the adapter. These additional sources own
    approvals/questions and materialized desktop queues; no new authority is made.
    """
    session = str(chat.get("sessionId") or "")
    if not session:
        return True
    try:
        approvals, question_snapshot = await asyncio.wait_for(asyncio.gather(
            approval_port.list_pending(project_root, session),
            asyncio.to_thread(list_questions, project_root=project_root, session_id=session,
                              include_answered=True, limit=100),
        ), 2.0)
    except Exception:
        raise SessionControlError("recovery_unavailable") from None
    questions = question_snapshot.get("questions") if isinstance(question_snapshot, dict) else None
    if not isinstance(approvals, list) or not isinstance(questions, list):
        raise SessionControlError("recovery_unavailable")
    if any(row.get("status") == "pending" for row in approvals):
        return True
    # Empty scope in the question service is an unfiltered query; enforce it here.
    if any(str(row.get("projectRoot") or "") == project_root and str(row.get("sessionId") or "") == session
           and (row.get("status") == "pending" or row.get("runtimeContinuationStatus") in {"queued", "claimed"})
           for row in questions):
        return True
    if (chat.get("compaction") or {}).get("status") == "compacting":
        return True
    for item in chat.get("items") or []:
        if item.get("type") == "streaming" or item.get("queueStatus") in {
                "steering", "queued", "waiting_for_resources", "delivery_unverified", "paused"}:
            return True
        task = item.get("task") if item.get("type") == "subagent" else None
        if isinstance(task, dict) and (task.get("status") not in {"completed", "failed", "cancelled", "canceled"}
                or task.get("parentContinuationStatus") in {"pending", "dispatching"}):
            return True
    return False


async def model_catalog(*, configuration: Any, catalog: Any) -> list[dict[str, Any]]:
    current = configuration.current_api_config()
    providers = {current.provider}
    try:
        safe = configuration.serialize_api_config(include_secret=False)
        providers.update(str(item).strip() for item in safe.get("savedKeyProviders", []) if str(item).strip())
    except Exception:
        pass
    options = []
    for provider in sorted(providers):
        try:
            config = configuration.resolve_session_config(provider=provider)
            models = await asyncio.to_thread(catalog.fetch_provider_models, config)
            options.extend(catalog.enrich_provider_model_item(config, item) for item in models)
        except Exception:
            continue
    return options


async def compact_chat(*, chat: dict[str, Any], project_root: str, instructions: str,
                       configuration: Any, load_settings: Any, connection_request: Any,
                       request_plan: Any, provider_requires_key: Any,
                       compact_context: Any) -> dict[str, Any]:
    settings = load_settings(connection_request())
    chosen = ChannelRuntimeAdapter._session_runtime_config(chat)
    provider = str(chosen.get("provider") or settings.llm_provider)
    model = str(chosen.get("model") or settings.llm_model)
    resolved = configuration.resolve_session_config(provider=provider, model=model)
    effective = replace(settings, llm_provider=resolved.provider, llm_api_key=resolved.api_key,
                        llm_base_url=resolved.base_url, llm_model=resolved.model,
                        llm_api_type=resolved.api_type, gemini_thinking_level=resolved.thinking_level)
    history = ChannelRuntimeAdapter._runtime_history(chat.get("items") or [])
    if any(isinstance(item, dict) and item.get("type") == "user" and item.get("attachments")
           for item in chat.get("items") or []):
        return {"ok": False, "reason": "attachments_unsupported"}
    if instructions.strip():
        history.append({"role": "user", "text": instructions.strip()})
    if not history:
        return {"ok": False, "reason": "no_content"}
    if provider_requires_key(provider) and not str(effective.llm_api_key or "").strip():
        return {"ok": False, "reason": "provider_not_configured"}
    result = await asyncio.to_thread(compact_context, history,
        summarizer=lambda prompt: request_plan(effective, prompt), trigger="manual",
        phase="standalone", provider=provider, model=model)
    if not isinstance(result, dict) or not str(result.get("summary") or "").strip():
        return {"ok": False, "reason": "empty_summary"}
    updated = compact_chat_items(chat, {**result, "provider": provider, "model": model},
                                 history_builder=ChannelRuntimeAdapter._runtime_history)
    if updated is None:
        return {"ok": False, "reason": "compact_no_reduction"}
    return {"ok": True, "chat": updated, "provider": provider, "model": model, "projectRoot": project_root}
