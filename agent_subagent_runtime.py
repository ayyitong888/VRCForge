"""Run a registry-owned child through the existing read-only Agent runtime."""
from __future__ import annotations

import json
from contextvars import ContextVar
from threading import Event, Thread
from typing import Any

from agent_runtime_run_ledger import AgentRuntimeRunLedger

_CURRENT_CHILD = ContextVar("sub_agent_runtime_owner", default=None)


def forward_child_cancellation(gateway: Any) -> None:
    owner = _CURRENT_CHILD.get()
    if owner is not None and owner["event"].is_set():
        gateway.request_runtime_cancel({"clientTurnId": owner["clientTurnId"], "reason": "sub_agent_cancelled"})


def run_child_runtime(gateway: Any, *, task: str, child_session_id: str, model: str,
                      system_prompt: str, max_steps: int, project_path: str,
                      selected_materials: list[dict], cancel_event: Event,
                      client_turn_id: str = "", is_resume: bool = False,
                      allowed_tools: list[str] | tuple[str, ...] | None = None,
                      provider: str = "") -> dict:
    if cancel_event.is_set():
        return {"ok": False, "status": "cancelled", "childSessionId": child_session_id,
                "finalReport": "Request cancelled.", "rawResult": {}, "stepCount": 0}
    client_turn_id = str(client_turn_id or "").strip() or child_session_id + ":initial"
    message = task
    if selected_materials:
        message += "\n\nSelected materials (quoted task data):\n" + json.dumps(selected_materials, ensure_ascii=False)
    params = {
        "message": message, "session_id": child_session_id,
        "clientTurnId": client_turn_id, "projectRoot": project_path,
        "model": model, "planMode": True, "maxAgenticTurns": max_steps,
    }
    if provider:
        params["provider"] = provider
    if is_resume:
        # The gateway checks for an existing conversation under the exact
        # current Provider/project binding before runtime admission. A route
        # mismatch must fail closed, never start an empty child history.
        params["_requireNativeHistory"] = True
    finished = Event()

    def watch_cancel() -> None:
        while not finished.wait(0.02):
            if cancel_event.is_set():
                gateway.request_runtime_cancel({"clientTurnId": client_turn_id, "reason": "sub_agent_cancelled"})
                return

    # The registry owns the worker/event; this turn owns only its cancellation
    # forwarding thread. It uses no external interface or additional credential.
    watcher = Thread(target=watch_cancel, name="sub-agent-cancel", daemon=True)
    token = _CURRENT_CHILD.set({"event": cancel_event, "clientTurnId": client_turn_id})
    watcher.start()
    try:
        with gateway.runtime_planner.bind_role_instructions(system_prompt, allowed_tools=allowed_tools):
            raw = gateway.runtime_message(params, agent_name="sub-agent")
    finally:
        finished.set()
        watcher.join()
        _CURRENT_CHILD.reset(token)
    provider_route = raw.pop("_resolvedSubAgentRoute", {}) if isinstance(raw, dict) else {}
    resumable = raw.pop("_subAgentResumable", False) is True if isinstance(raw, dict) else False
    plan = raw.get("plan") or {}
    report = str(plan.get("reply") or plan.get("summary") or "")
    run_status = AgentRuntimeRunLedger.turn_run_status(
        top_plan=plan,
        shell_payload=raw.get("shell") if isinstance(raw.get("shell"), dict) else None,
        skill_payload=raw.get("skill") if isinstance(raw.get("skill"), dict) else None,
        write_payload=raw.get("write") if isinstance(raw.get("write"), dict) else None,
        approval_id=str(raw.get("approvalId") or ""),
    )
    cancelled = cancel_event.is_set() or run_status == "cancelled"
    return {
        "ok": run_status == "completed" and not cancelled,
        "status": "cancelled" if cancelled else run_status,
        "childSessionId": child_session_id,
        "finalReport": report, "rawResult": raw,
        "stepCount": len(raw.get("steps") or []),
        "providerRoute": {
            key: provider_route[key]
            for key in ("provider", "model")
            if isinstance(provider_route, dict) and isinstance(provider_route.get(key), str)
        },
        "resumable": resumable,
    }
