#![allow(unused_imports)]

use crate::backend::*;
use crate::commands::*;
use crate::sanitize::*;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::{
    env, fs,
    net::{TcpStream, ToSocketAddrs},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, State,
};
use tungstenite::client::IntoClientRequest;
use tungstenite::http::HeaderValue;

pub(crate) fn start_backend_event_bridge_once(
    app_handle: tauri::AppHandle,
    state: &BackendState,
    app_session_token: String,
) -> Result<(), String> {
    let mut started = state
        .event_bridge_started
        .lock()
        .map_err(|_| "backend event bridge state lock poisoned".to_string())?;
    if *started {
        return Ok(());
    }
    *started = true;
    thread::spawn(move || backend_event_bridge_loop(app_handle, app_session_token));
    Ok(())
}

pub(crate) fn backend_event_bridge_loop(app_handle: tauri::AppHandle, app_session_token: String) {
    loop {
        match connect_backend_event_socket(&app_session_token) {
            Ok(mut socket) => {
                let _ = app_handle.emit(
                    "vrcforge-backend-event-status",
                    serde_json::json!({"ok": true, "status": "connected"}),
                );
                loop {
                    match socket.read() {
                        Ok(message) if message.is_text() => match message.into_text() {
                            Ok(text) => match serde_json::from_str::<serde_json::Value>(&text) {
                                Ok(payload) => {
                                    if let Some(event) = sanitize_backend_event(payload) {
                                        let _ = app_handle.emit("vrcforge-backend-event", event);
                                    }
                                }
                                Err(error) => {
                                    let _ = app_handle.emit(
                                        "vrcforge-backend-event-status",
                                        serde_json::json!({
                                            "ok": false,
                                            "status": "invalid_event",
                                            "error": error.to_string()
                                        }),
                                    );
                                }
                            },
                            Err(error) => {
                                let _ = app_handle.emit(
                                    "vrcforge-backend-event-status",
                                    serde_json::json!({
                                        "ok": false,
                                        "status": "invalid_text",
                                        "error": error.to_string()
                                    }),
                                );
                            }
                        },
                        Ok(message) if message.is_close() => break,
                        Ok(_) => {}
                        Err(error) => {
                            let _ = app_handle.emit(
                                "vrcforge-backend-event-status",
                                serde_json::json!({
                                    "ok": false,
                                    "status": "disconnected",
                                    "error": error.to_string()
                                }),
                            );
                            break;
                        }
                    }
                }
            }
            Err(error) => {
                let _ = app_handle.emit(
                    "vrcforge-backend-event-status",
                    serde_json::json!({
                        "ok": false,
                        "status": "connect_failed",
                        "error": error
                    }),
                );
            }
        }
        thread::sleep(Duration::from_millis(1500));
    }
}

pub(crate) fn connect_backend_event_socket(
    app_session_token: &str,
) -> Result<tungstenite::WebSocket<tungstenite::stream::MaybeTlsStream<TcpStream>>, String> {
    let url = format!("ws://{BACKEND_HOST}:{BACKEND_PORT}/ws");
    let mut request = url
        .into_client_request()
        .map_err(|error| format!("unable to build backend event socket request: {error}"))?;
    request
        .headers_mut()
        .insert("Origin", HeaderValue::from_static("tauri://localhost"));
    request.headers_mut().insert(
        "Authorization",
        HeaderValue::from_str(&format!("Bearer {app_session_token}"))
            .map_err(|error| error.to_string())?,
    );
    request.headers_mut().insert(
        "X-VRCForge-Transport",
        HeaderValue::from_static("tauri-ipc-bridge"),
    );
    request.headers_mut().insert(
        "X-VRCForge-Transport-Proof",
        HeaderValue::from_str(&tauri_ipc_bridge_proof(app_session_token, "GET", "/ws"))
            .map_err(|error| error.to_string())?,
    );
    let (socket, _) = tungstenite::connect(request)
        .map_err(|error| format!("unable to connect backend event socket: {error}"))?;
    Ok(socket)
}

pub(crate) fn sanitize_backend_event(payload: serde_json::Value) -> Option<serde_json::Value> {
    let event_type = payload.get("type")?.as_str()?;
    if !desktop_backend_event_allowed(event_type) {
        return None;
    }
    let mut event = serde_json::json!({ "type": event_type });
    if let Some(timestamp) = payload.get("timestamp") {
        if event_type != "agentMemoryReview" {
            event["timestamp"] = timestamp.clone();
        } else if timestamp.is_number() {
            event["timestamp"] = timestamp.clone();
        } else if let Some(timestamp) = timestamp.as_str() {
            event["timestamp"] = serde_json::Value::String(timestamp.chars().take(80).collect());
        }
    }
    if event_type == "agentRuntimeDelta" {
        // Backend websocket messages use build_event_message(), so Runtime
        // deltas live inside the single `payload` envelope. Accept the flat
        // form as well for compatibility with direct sanitizer callers.
        let runtime_delta = payload.get("payload").unwrap_or(&payload);
        if let Some(value) = runtime_delta
            .get("sessionId")
            .and_then(|value| value.as_str())
        {
            event["sessionId"] = serde_json::Value::String(value.chars().take(160).collect());
        }
        if let Some(value) = runtime_delta.get("turnId").and_then(|value| value.as_str()) {
            event["turnId"] = serde_json::Value::String(value.chars().take(160).collect());
        }
        if let Some(value) = runtime_delta
            .get("clientTurnId")
            .and_then(|value| value.as_str())
        {
            event["clientTurnId"] = serde_json::Value::String(value.chars().take(160).collect());
        }
        if let Some(value) = runtime_delta
            .get("textDelta")
            .and_then(|value| value.as_str())
        {
            event["textDelta"] = serde_json::Value::String(value.chars().take(1000).collect());
        }
        if let Some(value) = runtime_delta.get("phase").and_then(|value| value.as_str()) {
            if matches!(
                value,
                "preparing"
                    | "waiting_for_model"
                    | "receiving_response"
                    | "running_tool"
                    | "waiting_for_approval"
                    | "verifying"
            ) {
                event["phase"] = serde_json::Value::String(value.to_string());
            }
        }
        if let Some(timeline_event) = runtime_delta
            .get("timelineEvent")
            .and_then(sanitize_runtime_timeline_event)
        {
            event["timelineEvent"] = timeline_event;
        }
        if let Some(value) = runtime_delta.get("done").and_then(|value| value.as_bool()) {
            event["done"] = serde_json::Value::Bool(value);
        }
        if let Some(value) = runtime_delta
            .get("activity")
            .and_then(|value| value.as_bool())
        {
            event["activity"] = serde_json::Value::Bool(value);
        }
    }
    if event_type == "agentRuntimeTurn" {
        if let Some(runtime_turn) = payload.get("payload").and_then(sanitize_runtime_turn_event) {
            event["payload"] = runtime_turn;
        }
    }
    Some(event)
}

fn sanitize_runtime_timeline_event(value: &serde_json::Value) -> Option<serde_json::Value> {
    let kind = value.get("kind")?.as_str()?;
    if !matches!(
        kind,
        "phase"
            | "planner"
            | "tool_call"
            | "tool_result"
            | "file_edit"
            | "command"
            | "subagent"
            | "assistant"
    ) {
        return None;
    }
    let id = bounded_event_text(value.get("id"), 180);
    let timestamp = bounded_event_text(value.get("timestamp"), 80);
    let sequence = value.get("sequence")?.as_u64()?;
    if id.is_empty() || timestamp.is_empty() {
        return None;
    }
    let source = value.get("payload").and_then(|item| item.as_object());
    let mut payload = serde_json::Map::new();
    for (key, limit) in [
        ("label", 160usize),
        ("summary", 1000usize),
        ("status", 80usize),
        ("tool", 160usize),
        ("phase", 80usize),
        ("actionId", 96usize),
    ] {
        let bounded = bounded_event_text(source.and_then(|item| item.get(key)), limit);
        if !bounded.is_empty() {
            payload.insert(key.to_string(), serde_json::Value::String(bounded));
        }
    }
    let subagent_status =
        bounded_event_text(source.and_then(|item| item.get("subagentStatus")), 40);
    if matches!(
        subagent_status.as_str(),
        "created" | "started" | "completed" | "failed"
    ) {
        payload.insert(
            "subagentStatus".to_string(),
            serde_json::Value::String(subagent_status),
        );
    }
    Some(serde_json::json!({
        "id": id,
        "sequence": sequence,
        "timestamp": timestamp,
        "kind": kind,
        "payload": payload,
    }))
}

fn bounded_event_text(value: Option<&serde_json::Value>, limit: usize) -> String {
    value
        .and_then(|item| item.as_str())
        .unwrap_or_default()
        .chars()
        .take(limit)
        .collect()
}

fn sanitize_runtime_turn_event(payload: &serde_json::Value) -> Option<serde_json::Value> {
    let continuation_source = payload.get("continuationSource")?.as_str()?;
    if payload.get("schema")?.as_str()? != "vrcforge.runtime_turn_event.v1"
        || !matches!(
            continuation_source,
            "shell_process_finished" | "sub_agent_finished"
        )
    {
        return None;
    }
    let session_id = bounded_event_text(payload.get("sessionId"), 180);
    let turn_id = bounded_event_text(payload.get("turnId"), 180);
    if session_id.is_empty() || turn_id.is_empty() {
        return None;
    }
    let plan = payload.get("plan").and_then(|item| item.as_object());
    let completion = plan
        .and_then(|item| item.get("taskCompletion"))
        .and_then(|item| item.as_object());
    let evidence_action_ids: Vec<serde_json::Value> = completion
        .and_then(|item| item.get("evidenceActionIds"))
        .and_then(|item| item.as_array())
        .into_iter()
        .flatten()
        .take(3)
        .filter_map(|item| {
            let bounded: String = item.as_str()?.chars().take(80).collect();
            (!bounded.is_empty()).then(|| serde_json::Value::String(bounded))
        })
        .collect();
    Some(serde_json::json!({
        "schema": "vrcforge.runtime_turn_event.v1",
        "continuationSource": continuation_source,
        "sessionId": session_id,
        "turnId": turn_id,
        "clientTurnId": bounded_event_text(payload.get("clientTurnId"), 240),
        "plan": {
            "summary": bounded_event_text(plan.and_then(|item| item.get("summary")), 1200),
            "reply": bounded_event_text(plan.and_then(|item| item.get("reply")), 6000),
            "planner": bounded_event_text(plan.and_then(|item| item.get("planner")), 80),
            "nextStep": bounded_event_text(plan.and_then(|item| item.get("nextStep")), 80),
            "taskCompletion": {
                "status": bounded_event_text(completion.and_then(|item| item.get("status")), 40),
                "taskId": bounded_event_text(completion.and_then(|item| item.get("taskId")), 80),
                "evidenceActionIds": evidence_action_ids,
            }
        }
    }))
}

pub(crate) fn desktop_backend_event_allowed(event_type: &str) -> bool {
    matches!(
        event_type,
        "advancedSettings"
            | "agentApprovals"
            | "agentDesktopActions"
            | "agentGoalBackground"
            | "agentGoals"
            | "agentMemory"
            | "agentMemoryReview"
            | "agentProgress"
            | "agentQuestions"
            | "agentPermission"
            | "agentRuntimeCancel"
            | "agentRuntimeDelta"
            | "agentRuntimeQueue"
            | "agentRuntimeRuns"
            | "agentRuntimeTurn"
            | "hello"
            | "projects"
            | "subAgentTasks"
            | "unity_status"
    )
}

#[cfg(test)]
mod tests {
    use super::{desktop_backend_event_allowed, sanitize_backend_event};

    #[test]
    fn background_goal_signal_is_allowed_without_forwarding_payload_details() {
        assert!(desktop_backend_event_allowed("agentGoalBackground"));
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentGoalBackground",
            "timestamp": "2026-07-21T00:00:00Z",
            "payload": {"error": "private", "response": "private"}
        }))
        .expect("background goal event should be forwarded");
        assert_eq!(sanitized["type"], "agentGoalBackground");
        assert!(sanitized.get("payload").is_none());
    }

    #[test]
    fn memory_review_signal_is_allowed_without_forwarding_candidate_content() {
        assert!(desktop_backend_event_allowed("agentMemoryReview"));
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentMemoryReview",
            "timestamp": "2026-07-22T00:00:00Z",
            "candidateText": "private",
            "source": "private",
            "revision": 4,
            "prose": "private",
            "payload": {
                "candidateText": "private",
                "sourceText": "private",
                "revision": 4
            }
        }))
        .expect("Memory Review event should be forwarded");
        assert_eq!(
            sanitized,
            serde_json::json!({
                "type": "agentMemoryReview",
                "timestamp": "2026-07-22T00:00:00Z"
            })
        );

        let structured_timestamp = sanitize_backend_event(serde_json::json!({
            "type": "agentMemoryReview",
            "timestamp": {"candidateText": "private"},
            "payload": {"candidateText": "private"}
        }))
        .expect("Memory Review signal should remain allowed");
        assert_eq!(
            structured_timestamp,
            serde_json::json!({"type": "agentMemoryReview"})
        );
    }

    #[test]
    fn shell_task_continuation_forwards_only_the_bounded_chat_projection() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "shell_process_finished",
                "sessionId": "session-owner",
                "turnId": "turn-terminal",
                "clientTurnId": "client-terminal",
                "secret": "must-not-cross",
                "observe": {"path": "C:\\private"},
                "plan": {
                    "summary": "finished",
                    "reply": "The background task finished.",
                    "planner": "runtime",
                    "nextStep": "done",
                    "taskCompletion": {
                        "status": "completed",
                        "taskId": "task-1",
                        "evidenceActionIds": ["action-1"]
                    }
                }
            }
        }))
        .expect("terminal continuation should be forwarded");

        assert_eq!(sanitized["type"], "agentRuntimeTurn");
        assert_eq!(sanitized["payload"]["sessionId"], "session-owner");
        assert_eq!(
            sanitized["payload"]["plan"]["reply"],
            "The background task finished."
        );
        assert!(sanitized["payload"].get("secret").is_none());
        assert!(sanitized["payload"].get("observe").is_none());
    }

    #[test]
    fn sub_agent_task_continuation_uses_the_same_bounded_chat_projection() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "sub_agent_finished",
                "sessionId": "session-owner",
                "turnId": "turn-sub-agent",
                "result": {"privateWorkerState": "must-not-cross"},
                "plan": {
                    "summary": "review finished",
                    "reply": "The delegated review finished.",
                    "planner": "runtime",
                    "nextStep": "done"
                }
            }
        }))
        .expect("sub-agent continuation should be forwarded");

        assert_eq!(
            sanitized["payload"]["continuationSource"],
            "sub_agent_finished"
        );
        assert_eq!(sanitized["payload"]["sessionId"], "session-owner");
        assert!(sanitized["payload"].get("result").is_none());
    }

    #[test]
    fn runtime_delta_reads_the_websocket_payload_envelope_once() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeDelta",
            "timestamp": "2026-08-14T03:37:25Z",
            "payload": {
                "sessionId": "session-live",
                "turnId": "turn-live",
                "clientTurnId": "client-live",
                "phase": "running_tool",
                "activity": true,
                "timelineEvent": {
                    "id": "timeline-turn-live-4",
                    "sequence": 4,
                    "timestamp": "2026-08-14T03:37:25Z",
                    "kind": "tool_call",
                    "payload": {
                        "actionId": "action-live",
                        "tool": "vrcforge_list_directory",
                        "status": "started",
                        "arguments": "must-not-cross"
                    }
                }
            }
        }))
        .expect("runtime delta should be forwarded");

        assert_eq!(sanitized["clientTurnId"], "client-live");
        assert_eq!(sanitized["phase"], "running_tool");
        assert_eq!(sanitized["activity"], true);
        assert_eq!(
            sanitized["timelineEvent"]["payload"]["actionId"],
            "action-live"
        );
        assert!(sanitized["timelineEvent"]["payload"]
            .get("arguments")
            .is_none());
        assert!(sanitized.get("payload").is_none());
    }
}
