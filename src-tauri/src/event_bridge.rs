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
        if let Some(consumed) = runtime_delta.get("consumedSteerInputIds").and_then(|item| item.as_array()) {
            let ids: Vec<serde_json::Value> = consumed.iter().take(256).filter_map(|item| {
                let id = item.as_str()?;
                (!id.is_empty() && id.len() <= 240 && id.chars().all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.' | ':')))
                    .then(|| serde_json::Value::String(id.to_string()))
            }).collect();
            if !ids.is_empty() { event["consumedSteerInputIds"] = serde_json::Value::Array(ids); }
        }
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
        if let Some(retry) = runtime_delta
            .get("providerRetry")
            .and_then(|value| value.as_object())
        {
            let attempt = retry
                .get("attempt")
                .and_then(|value| value.as_u64())
                .filter(|value| (1..=100).contains(value));
            let max = retry
                .get("max")
                .and_then(|value| value.as_u64())
                .filter(|value| (1..=100).contains(value));
            if let (Some(attempt), Some(max)) = (attempt, max) {
                if attempt <= max {
                    let mut safe_retry = serde_json::Map::new();
                    safe_retry.insert("attempt".to_string(), serde_json::Value::from(attempt));
                    safe_retry.insert("max".to_string(), serde_json::Value::from(max));
                    if let Some(reason) = retry
                        .get("reason")
                        .and_then(|value| value.as_str())
                        .filter(|value| {
                            matches!(*value, "connection" | "timeout" | "rate_limit" | "capacity")
                        })
                    {
                        safe_retry.insert(
                            "reason".to_string(),
                            serde_json::Value::String(reason.to_string()),
                        );
                    }
                    if let Some(status_code) = retry
                        .get("statusCode")
                        .and_then(|value| value.as_u64())
                        .filter(|value| (100..=999).contains(value))
                    {
                        safe_retry.insert(
                            "statusCode".to_string(),
                            serde_json::Value::from(status_code),
                        );
                    }
                    if retry.get("terminal").and_then(|value| value.as_str())
                        == Some("NO_MORE_RETRY")
                    {
                        safe_retry.insert(
                            "terminal".to_string(),
                            serde_json::Value::String("NO_MORE_RETRY".to_string()),
                        );
                    }
                    event["providerRetry"] = serde_json::Value::Object(safe_retry);
                }
            }
        }
    }
    if event_type == "agentRuntimeTurn" {
        let runtime_turn = payload
            .get("payload")
            .and_then(sanitize_runtime_turn_event)?;
        event["payload"] = runtime_turn;
    }
    if event_type == "chatStorageChanged" {
        if let Some(origin_id) = payload
            .get("payload")
            .and_then(|value| value.get("originId"))
            .and_then(|value| value.as_str())
            .filter(|value| value.len() <= 64)
        {
            event["originId"] = serde_json::Value::String(origin_id.to_string());
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
        ("actionId", 96usize),
        ("messageId", 180usize),
    ] {
        let bounded = bounded_event_text(source.and_then(|item| item.get(key)), limit);
        if !bounded.is_empty() {
            payload.insert(key.to_string(), serde_json::Value::String(bounded));
        }
    }
    let phase = bounded_event_text(source.and_then(|item| item.get("phase")), 80);
    if !phase.is_empty()
        && (kind != "assistant" || matches!(phase.as_str(), "commentary" | "final_answer"))
    {
        payload.insert("phase".to_string(), serde_json::Value::String(phase));
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

fn sanitize_public_messages(value: Option<&serde_json::Value>) -> Option<serde_json::Value> {
    let messages = value?.as_array()?;
    let projected: Vec<serde_json::Value> = messages
        .iter()
        .take(64)
        .filter_map(|message| {
            let object = message.as_object()?;
            let id = bounded_event_text(object.get("id"), 180);
            let text = bounded_event_text(object.get("text"), 32000);
            if id.is_empty() || text.is_empty() {
                return None;
            }
            let mut safe = serde_json::Map::new();
            safe.insert("id".to_string(), serde_json::Value::String(id));
            safe.insert("text".to_string(), serde_json::Value::String(text));
            if let Some(phase) = object.get("phase").and_then(|item| item.as_str()) {
                if !matches!(phase, "commentary" | "final_answer") {
                    return None;
                }
                safe.insert(
                    "phase".to_string(),
                    serde_json::Value::String(phase.to_string()),
                );
            }
            Some(serde_json::Value::Object(safe))
        })
        .collect();
    Some(serde_json::Value::Array(projected))
}

const PLANNER_FAILURE_IDENTIFIER_LIMIT: usize = 64;
const PLANNER_FAILURE_RETRY_ATTEMPTS_LIMIT: u64 = 100;

fn bounded_safe_identifier(value: Option<&serde_json::Value>) -> Option<String> {
    let value = value?.as_str()?;
    if value.is_empty() || value.len() > PLANNER_FAILURE_IDENTIFIER_LIMIT {
        return None;
    }
    let mut chars = value.chars();
    if !chars
        .next()
        .is_some_and(|character| character.is_ascii_alphanumeric())
    {
        return None;
    }
    if !chars.all(|character| {
        character.is_ascii_alphanumeric()
            || matches!(character, '_' | '-' | '.' | ':')
    }) {
        return None;
    }
    Some(value.to_string())
}

fn sanitize_planner_failure(value: Option<&serde_json::Value>) -> Option<serde_json::Value> {
    let object = value?.as_object()?;
    let mut result = serde_json::Map::new();
    for key in ["code", "phase", "transportPhase"] {
        if let Some(identifier) = bounded_safe_identifier(object.get(key)) {
            result.insert(key.to_string(), serde_json::Value::String(identifier));
        }
    }
    if let Some(retryable) = object.get("retryable").and_then(|item| item.as_bool()) {
        result.insert(
            "retryable".to_string(),
            serde_json::Value::Bool(retryable),
        );
    }
    if let Some(retry_attempts) = object
        .get("retryAttempts")
        .and_then(|item| item.as_u64())
        .filter(|attempts| *attempts <= PLANNER_FAILURE_RETRY_ATTEMPTS_LIMIT)
    {
        result.insert(
            "retryAttempts".to_string(),
            serde_json::Value::from(retry_attempts),
        );
    }
    if let Some(retry_decision) = object
        .get("retryDecision")
        .and_then(|item| item.as_str())
        .filter(|decision| matches!(*decision, "NO_MORE_RETRY" | "CANCELLED"))
    {
        result.insert(
            "retryDecision".to_string(),
            serde_json::Value::String(retry_decision.to_string()),
        );
    }
    if let Some(provider_error) = object.get("providerError").and_then(|item| item.as_object()) {
        let mut projected = serde_json::Map::new();
        for key in ["type", "code"] {
            if let Some(identifier) = bounded_safe_identifier(provider_error.get(key)) {
                projected.insert(key.to_string(), serde_json::Value::String(identifier));
            }
        }
        if let Some(status_code) = provider_error
            .get("statusCode")
            .and_then(|item| item.as_u64())
            .filter(|status| (100..=599).contains(status))
        {
            projected.insert(
                "statusCode".to_string(),
                serde_json::Value::from(status_code),
            );
        }
        if let Some(stream_started) = provider_error
            .get("streamStarted")
            .and_then(|item| item.as_bool())
        {
            projected.insert(
                "streamStarted".to_string(),
                serde_json::Value::Bool(stream_started),
            );
        }
        if !projected.is_empty() {
            result.insert(
                "providerError".to_string(),
                serde_json::Value::Object(projected),
            );
        }
    }
    (!result.is_empty()).then_some(serde_json::Value::Object(result))
}

const CONTEXT_USAGE_INTEGER_FIELDS: &[&str] = &[
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "cumulativeInputTokens",
    "cumulativeOutputTokens",
    "cumulativeTotalTokens",
    "lastInputTokens",
    "lastOutputTokens",
    "lastTotalTokens",
    "peakInputTokens",
    "peakTotalTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "cacheUsageRequestCount",
    "cacheWriteUsageRequestCount",
    "requestCount",
    "sentHistoryEntryCount",
    "sentHistoryCharacterCount",
    "promptCharacterCount",
    "lastPromptCharacterCount",
];

const CONTEXT_USAGE_BOOLEAN_FIELDS: &[&str] = &[
    "taskTotalAvailable",
    "exact",
    "cacheUsageComplete",
    "cacheWriteUsageComplete",
    "partial",
];

const CONTEXT_USAGE_TEXT_FIELDS: &[&str] = &[
    "schema",
    "source",
    "scope",
    "provider",
    "providerLabel",
    "model",
    "unavailableReason",
];

fn sanitize_usage_coverage_bucket(value: &serde_json::Value) -> Option<serde_json::Value> {
    let object = value.as_object()?;
    let mut result = serde_json::Map::new();
    for key in ["requestCount", "reportedRequestCount"] {
        if let Some(number) = object
            .get(key)
            .and_then(|item| item.as_u64())
            .filter(|number| *number <= 1_000_000_000_000_000)
        {
            result.insert(key.to_string(), serde_json::Value::from(number));
        }
    }
    if let Some(complete) = object.get("complete").and_then(|item| item.as_bool()) {
        result.insert("complete".to_string(), serde_json::Value::Bool(complete));
    }
    (!result.is_empty()).then_some(serde_json::Value::Object(result))
}

fn sanitize_usage_coverage(value: &serde_json::Value) -> Option<serde_json::Value> {
    let object = value.as_object()?;
    let mut result = serde_json::Map::new();
    for (name, bucket) in object.iter().take(16) {
        let bounded_name: String = name.chars().take(80).collect();
        if bounded_name.is_empty() {
            continue;
        }
        if let Some(projected) = sanitize_usage_coverage_bucket(bucket) {
            result.insert(bounded_name, projected);
        }
    }
    (!result.is_empty()).then_some(serde_json::Value::Object(result))
}

fn sanitize_cache_usage_coverage(value: &serde_json::Value) -> Option<serde_json::Value> {
    let object = value.as_object()?;
    let mut result = serde_json::Map::new();
    if let Some(number) = object
        .get("requestCount")
        .and_then(|item| item.as_u64())
        .filter(|number| *number <= 1_000_000_000_000_000)
    {
        result.insert("requestCount".to_string(), serde_json::Value::from(number));
    }
    for key in ["read", "write"] {
        if let Some(projected) = object.get(key).and_then(sanitize_usage_coverage_bucket) {
            result.insert(key.to_string(), projected);
        }
    }
    (!result.is_empty()).then_some(serde_json::Value::Object(result))
}

fn sanitize_context_usage(value: &serde_json::Value) -> Option<serde_json::Value> {
    let object = value.as_object()?;
    let mut result = serde_json::Map::new();
    for key in CONTEXT_USAGE_INTEGER_FIELDS {
        if let Some(number) = object
            .get(*key)
            .and_then(|item| item.as_u64())
            .filter(|number| *number <= 1_000_000_000_000_000)
        {
            result.insert((*key).to_string(), serde_json::Value::from(number));
        }
    }
    for key in CONTEXT_USAGE_BOOLEAN_FIELDS {
        if let Some(value) = object.get(*key).and_then(|item| item.as_bool()) {
            result.insert((*key).to_string(), serde_json::Value::Bool(value));
        }
    }
    for key in CONTEXT_USAGE_TEXT_FIELDS {
        if let Some(value) = object.get(*key).and_then(|item| item.as_str()) {
            let bounded: String = value.chars().take(160).collect();
            result.insert((*key).to_string(), serde_json::Value::String(bounded));
        }
    }
    if let Some(projected) = object.get("coverage").and_then(sanitize_usage_coverage) {
        result.insert("coverage".to_string(), projected);
    }
    if let Some(projected) = object
        .get("cacheCoverage")
        .and_then(sanitize_cache_usage_coverage)
    {
        result.insert("cacheCoverage".to_string(), projected);
    }
    (!result.is_empty()).then_some(serde_json::Value::Object(result))
}

fn sanitize_runtime_turn_event(payload: &serde_json::Value) -> Option<serde_json::Value> {
    let continuation_source = payload.get("continuationSource")?.as_str()?;
    if payload.get("schema")?.as_str()? != "vrcforge.runtime_turn_event.v1"
        || !matches!(
            continuation_source,
            "approval_finished"
                | "shell_process_finished"
                | "sub_agent_finished"
                | "question_answered"
                | "foreground_completed"
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
    let mut result = serde_json::json!({
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
    });
    if let Some(reason) = bounded_safe_identifier(plan.and_then(|item| item.get("reason"))) {
        result["plan"]["reason"] = serde_json::Value::String(reason);
    }
    if let Some(public_messages) = sanitize_public_messages(
        plan.and_then(|item| item.get("publicMessages")),
    ) {
        result["plan"]["publicMessages"] = public_messages;
    }
    if let Some(planner_failure) = plan
        .and_then(|item| item.get("plannerFailure"))
        .and_then(|item| sanitize_planner_failure(Some(item)))
    {
        result["plan"]["plannerFailure"] = planner_failure;
    }
    if let Some(context_usage) = payload.get("contextUsage").and_then(sanitize_context_usage) {
        result["contextUsage"] = context_usage;
    }
    if let Some(consumed) = payload.get("consumedSteerInputIds").and_then(|item| item.as_array()) {
        let ids: Vec<serde_json::Value> = consumed.iter().take(256).filter_map(|item| {
            let id = item.as_str()?;
            (!id.is_empty() && id.len() <= 240 && id.chars().all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.' | ':')))
                .then(|| serde_json::Value::String(id.to_string()))
        }).collect();
        if !ids.is_empty() { result["consumedSteerInputIds"] = serde_json::Value::Array(ids); }
    }
    if let Some(timeline) = payload.get("timeline").and_then(|item| item.as_array()) {
        let projected: Vec<serde_json::Value> = timeline
            .iter()
            .take(256)
            .filter_map(sanitize_runtime_timeline_event)
            .collect();
        if !projected.is_empty() {
            result["timeline"] = serde_json::Value::Array(projected);
        }
    }
    Some(result)
}

#[cfg(test)]
mod steer_consumption_delta_tests {
    #[test]
    fn live_delta_keeps_only_bounded_confirmed_ids_without_terminal_signal() {
        let event = super::sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeDelta", "payload": {
                "sessionId": "session", "clientTurnId": "active-turn",
                "consumedSteerInputIds": ["confirmed:one", "", "bad value", {"private": true}]
            }
        })).unwrap();
        assert_eq!(event["consumedSteerInputIds"], serde_json::json!(["confirmed:one"]));
        assert!(event.get("done").is_none());
        assert!(event.get("phase").is_none());
    }
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
            | "chatStorageChanged"
            | "config"
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
    fn provider_config_change_forwards_only_a_refresh_signal() {
        assert!(desktop_backend_event_allowed("config"));
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "config",
            "timestamp": "2026-10-19T11:01:00Z",
            "payload": {
                "apiConfig": {"provider": "deepseek", "api_key": "private-key"},
                "visionConfig": {"api_key": "private-vision-key"}
            }
        }))
        .expect("provider configuration changes must refresh desktop bootstrap");
        assert_eq!(sanitized, serde_json::json!({
            "type": "config",
            "timestamp": "2026-10-19T11:01:00Z"
        }));
    }

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
    fn chat_storage_changed_signal_crosses_the_desktop_bridge_without_payload() {
        assert!(desktop_backend_event_allowed("chatStorageChanged"));
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "chatStorageChanged",
            "timestamp": "2026-10-13T00:00:00Z",
            "source": "app",
            "payload": {"chats": "must-not-cross"}
        }))
        .expect("chat storage change event should be forwarded");
        assert_eq!(sanitized["type"], "chatStorageChanged");
        assert_eq!(sanitized["timestamp"], "2026-10-13T00:00:00Z");
        assert!(sanitized.get("source").is_none());
        assert!(sanitized.get("payload").is_none());
    }

    #[test]
    fn chat_storage_changed_preserves_only_bounded_origin_identity() {
        let origin_id = "00000000-0000-4000-8000-000000000001";
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "chatStorageChanged",
            "payload": {"source": "app", "originId": origin_id, "chats": "private"}
        }))
        .expect("chat storage change event should be forwarded");
        assert_eq!(sanitized["originId"], origin_id);
        assert!(sanitized.get("payload").is_none());

        let oversized = sanitize_backend_event(serde_json::json!({
            "type": "chatStorageChanged",
            "payload": {"originId": "x".repeat(65)}
        }))
        .expect("chat storage change event should still be forwarded");
        assert!(oversized.get("originId").is_none());
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
    fn question_cancellation_forwards_context_usage_and_timeline() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "question_answered",
                "sessionId": "session-turn-1791931182719-bf0fba057b852",
                "turnId": "turn_20261013_225152_044760_997a7b",
                "clientTurnId": "turn-1791931881875-548ff55bc86e28:question:question_20261013_225144_296544_34ac82",
                "contextUsage": {
                    "schema": "vrcforge.context_usage.v1",
                    "source": "provider_usage",
                    "scope": "task_total_context_usage",
                    "exact": true,
                    "provider": "chatgpt",
                    "model": "gpt-6.1-sol",
                    "inputTokens": 38310,
                    "outputTokens": 84,
                    "totalTokens": 38394,
                    "requestCount": 2,
                    "cacheReadTokens": 22528,
                    "coverage": {
                        "planner": {
                            "requestCount": 2,
                            "reportedRequestCount": 2,
                            "complete": true,
                            "private": "drop"
                        }
                    },
                    "cacheCoverage": {
                        "requestCount": 2,
                        "read": {"complete": true},
                        "write": {"requestCount": 0, "private": "drop"}
                    },
                    "unknownObject": {"secret": "must-not-cross"}
                },
                "plan": {
                    "summary": "Runtime turn was cancelled by the user.",
                    "reply": "Request cancelled.",
                    "planner": "runtime",
                    "nextStep": "cancelled"
                },
                "timeline": [{
                    "id": "timeline-turn-cancel-0",
                    "sequence": 0,
                    "timestamp": "2026-10-14T07:52:17.604154+09:00",
                    "kind": "assistant",
                    "payload": {"summary": "Request cancelled.", "private": "drop"}
                }]
            }
        }))
        .expect("question cancellation should reach the desktop event bridge");

        assert_eq!(
            sanitized["payload"]["continuationSource"],
            "question_answered"
        );
        assert_eq!(sanitized["payload"]["contextUsage"]["totalTokens"], 38394);
        assert_eq!(sanitized["payload"]["contextUsage"]["requestCount"], 2);
        assert_eq!(
            sanitized["payload"]["contextUsage"]["coverage"]["planner"]["reportedRequestCount"],
            2
        );
        assert_eq!(
            sanitized["payload"]["contextUsage"]["cacheCoverage"]["read"]["complete"],
            true
        );
        assert!(
            sanitized["payload"]["contextUsage"]["cacheCoverage"]["write"]
                .get("private")
                .is_none()
        );
        assert!(sanitized["payload"]["contextUsage"]
            .get("unknownObject")
            .is_none());
        assert_eq!(sanitized["payload"]["timeline"][0]["kind"], "assistant");
        assert_eq!(
            sanitized["payload"]["timeline"][0]["payload"]["summary"],
            "Request cancelled."
        );
        assert!(sanitized["payload"]["timeline"][0]["payload"]
            .get("private")
            .is_none());
    }

    #[test]
    fn runtime_turn_bridge_allows_existing_continuation_sources_and_rejects_unknown() {
        for source in [
            "approval_finished",
            "shell_process_finished",
            "sub_agent_finished",
            "question_answered",
            "foreground_completed",
        ] {
            let sanitized = sanitize_backend_event(serde_json::json!({
                "type": "agentRuntimeTurn",
                "payload": {
                    "schema": "vrcforge.runtime_turn_event.v1",
                    "continuationSource": source,
                    "sessionId": "session-owner",
                    "turnId": "turn-terminal",
                    "plan": {"reply": "done"}
                }
            }));
            assert!(sanitized.is_some(), "source {source} should be allowed");
        }

        let unknown = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "unknown_source",
                "sessionId": "session-owner",
                "turnId": "turn-terminal",
                "plan": {"reply": "must be rejected"}
            }
        }));
        assert!(unknown.is_none());
    }

    #[test]
    fn runtime_turn_bridge_omits_unknown_context_usage_without_dropping_event() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "foreground_completed",
                "sessionId": "session-owner",
                "turnId": "turn-terminal",
                "contextUsage": {"untrusted": {"credential": "drop"}},
                "plan": {"reply": "done"}
            }
        }))
        .expect("unknown usage fields must not invalidate the safe event");

        assert!(sanitized["payload"].get("contextUsage").is_none());
    }

    #[test]
    fn runtime_turn_bridge_preserves_cache_write_usage_zero_and_false_and_rejects_wrong_types() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "foreground_completed",
                "sessionId": "session-owner",
                "turnId": "turn-cache-write",
                "contextUsage": {
                    "schema": "vrcforge.context_usage.v1",
                    "cacheWriteUsageComplete": false,
                    "cacheWriteUsageRequestCount": 0,
                    "cacheWriteTokens": 0
                },
                "plan": {"reply": "done"}
            }
        }))
        .expect("cache write usage event should be forwarded");

        let usage = &sanitized["payload"]["contextUsage"];
        assert_eq!(usage["cacheWriteUsageComplete"], false);
        assert_eq!(usage["cacheWriteUsageRequestCount"], 0);
        assert_eq!(usage["cacheWriteTokens"], 0);

        let sanitized_wrong_types = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "foreground_completed",
                "sessionId": "session-owner",
                "turnId": "turn-cache-write-invalid",
                "contextUsage": {
                    "schema": "vrcforge.context_usage.v1",
                    "cacheWriteUsageComplete": "false",
                    "cacheWriteUsageRequestCount": "0",
                    "cacheWriteTokens": 0
                },
                "plan": {"reply": "done"}
            }
        }))
        .expect("invalid cache write usage types must not invalidate the event");

        let usage = &sanitized_wrong_types["payload"]["contextUsage"];
        assert!(usage.get("cacheWriteUsageComplete").is_none());
        assert!(usage.get("cacheWriteUsageRequestCount").is_none());
        assert_eq!(usage["cacheWriteTokens"], 0);
    }

    #[test]
    fn runtime_turn_bridge_preserves_controlled_planner_failure() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "foreground_completed",
                "sessionId": "session-owner",
                "turnId": "turn-rate-limit",
                "plan": {
                    "reply": "retry exhausted",
                    "plannerFailure": {
                        "code": "provider_request_failed",
                        "phase": "post_tool",
                        "transportPhase": "first_byte",
                        "retryable": true,
                        "retryAttempts": 5,
                        "retryDecision": "NO_MORE_RETRY",
                        "providerError": {
                            "statusCode": 429,
                            "type": "rate_limit_error",
                            "code": "rate_limit_exceeded",
                            "streamStarted": false,
                            "message": "must not cross",
                            "body": {"secret": "must not cross"},
                            "headers": {"authorization": "must not cross"},
                            "key": "must not cross",
                            "stack": "must not cross"
                        }
                    }
                }
            }
        }))
        .expect("controlled planner failure should reach the desktop event bridge");

        assert_eq!(
            sanitized["payload"]["plan"]["plannerFailure"]["code"],
            "provider_request_failed"
        );
        assert_eq!(
            sanitized["payload"]["plan"]["plannerFailure"]["retryAttempts"],
            5
        );
        assert_eq!(
            sanitized["payload"]["plan"]["plannerFailure"]["retryDecision"],
            "NO_MORE_RETRY"
        );
        assert_eq!(
            sanitized["payload"]["plan"]["plannerFailure"]["providerError"]["statusCode"],
            429
        );
        assert!(sanitized["payload"]["plan"]["plannerFailure"]["providerError"]
            .get("message")
            .is_none());
        assert!(sanitized["payload"]["plan"]["plannerFailure"]["providerError"]
            .get("body")
            .is_none());
        assert!(sanitized["payload"]["plan"]["plannerFailure"]["providerError"]
            .get("headers")
            .is_none());
    }

    #[test]
    fn runtime_turn_bridge_keeps_stream_error_without_fabricating_status() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "question_answered",
                "sessionId": "session-owner",
                "turnId": "turn-stream-error",
                "plan": {
                    "reply": "stream failed",
                    "plannerFailure": {
                        "code": "provider_stream_error",
                        "phase": "provider_request",
                        "retryable": false,
                        "retryAttempts": 0,
                        "retryDecision": "CANCELLED",
                        "providerError": {
                            "type": "stream_error",
                            "code": "stream_reset",
                            "streamStarted": true
                        }
                    }
                }
            }
        }))
        .expect("stream failure should reach the desktop event bridge");

        let error = &sanitized["payload"]["plan"]["plannerFailure"]["providerError"];
        assert_eq!(error["streamStarted"], true);
        assert!(error.get("statusCode").is_none());
    }

    #[test]
    fn runtime_turn_bridge_keeps_only_confirmed_bounded_steer_ids() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": { "schema": "vrcforge.runtime_turn_event.v1", "continuationSource": "question_answered",
                "sessionId": "owner-session", "turnId": "answer-turn",
                "consumedSteerInputIds": ["turn-confirmed:input-1", "bad value", "", {"message": "private"}],
                "plan": { "reply": "Selected file read", "nextStep": "done" }
            }
        })).expect("question continuation should be projected");
        assert_eq!(sanitized["payload"]["consumedSteerInputIds"], serde_json::json!(["turn-confirmed:input-1"]));
    }

    #[test]
    fn runtime_turn_bridge_drops_invalid_planner_failure_fields() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "foreground_completed",
                "sessionId": "session-owner",
                "turnId": "turn-hostile",
                "plan": {
                    "plannerFailure": {
                        "code": "bad value with spaces",
                        "phase": "<script>",
                        "transportPhase": "first_byte",
                        "retryable": "yes",
                        "retryAttempts": -1,
                        "retryDecision": "RETRY",
                        "providerError": {
                            "statusCode": 700,
                            "type": "bad type with spaces",
                            "code": "<secret>",
                            "streamStarted": "yes",
                            "message": "must not cross",
                            "body": {"secret": "must not cross"},
                            "headers": {"authorization": "must not cross"},
                            "key": "must not cross",
                            "stack": "must not cross"
                        }
                    }
                }
            }
        }))
        .expect("invalid planner failure fields should not drop the event");

        assert_eq!(
            sanitized["payload"]["plan"]["plannerFailure"]["transportPhase"],
            "first_byte"
        );
        assert!(sanitized["payload"]["plan"]["plannerFailure"]
            .get("code")
            .is_none());
        assert!(sanitized["payload"]["plan"]["plannerFailure"]
            .get("retryAttempts")
            .is_none());
        assert!(sanitized["payload"]["plan"]["plannerFailure"]
            .get("retryDecision")
            .is_none());
        assert!(sanitized["payload"]["plan"]["plannerFailure"]
            .get("providerError")
            .is_none());
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
                "providerRetry": {
                    "attempt": 2,
                    "max": 5,
                    "reason": "capacity",
                    "statusCode": 503,
                    "terminal": "NO_MORE_RETRY",
                    "secret": "must-not-cross"
                },
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
            sanitized["providerRetry"],
            serde_json::json!({
                "attempt": 2,
                "max": 5,
                "reason": "capacity",
                "statusCode": 503,
                "terminal": "NO_MORE_RETRY"
            })
        );
        assert_eq!(
            sanitized["timelineEvent"]["payload"]["actionId"],
            "action-live"
        );
        assert!(sanitized["timelineEvent"]["payload"]
            .get("arguments")
            .is_none());
        assert!(sanitized.get("payload").is_none());
    }

    #[test]
    fn runtime_timeline_bridge_preserves_bounded_assistant_phase_and_message_id() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeDelta",
            "payload": {
                "timelineEvent": {
                    "id": "timeline-assistant-1",
                    "sequence": 1,
                    "timestamp": "2026-10-14T01:02:03Z",
                    "kind": "assistant",
                    "payload": {
                        "phase": "commentary",
                        "messageId": "public-message-1",
                        "summary": "Reading the requested file.",
                        "private": "must-not-cross"
                    }
                }
            }
        }))
        .expect("assistant timeline event should be forwarded");

        let timeline = &sanitized["timelineEvent"]["payload"];
        assert_eq!(timeline["phase"], "commentary");
        assert_eq!(timeline["messageId"], "public-message-1");
        assert_eq!(timeline["summary"], "Reading the requested file.");
        assert!(timeline.get("private").is_none());

        let hidden_phase = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeDelta",
            "payload": {
                "timelineEvent": {
                    "id": "timeline-assistant-hidden",
                    "sequence": 3,
                    "timestamp": "2026-10-14T01:02:05Z",
                    "kind": "assistant",
                    "payload": {
                        "phase": "hidden_reasoning",
                        "summary": "must not cross"
                    }
                }
            }
        }))
        .expect("unknown assistant phase should not invalidate the event");
        assert!(hidden_phase["timelineEvent"]["payload"]
            .get("phase")
            .is_none());

        let overlong = "x".repeat(181);
        let bounded = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeDelta",
            "payload": {
                "timelineEvent": {
                    "id": "timeline-assistant-2",
                    "sequence": 2,
                    "timestamp": "2026-10-14T01:02:04Z",
                    "kind": "assistant",
                    "payload": {
                        "phase": "final_answer",
                        "messageId": overlong
                    }
                }
            }
        }))
        .expect("bounded assistant timeline event should be forwarded");
        assert_eq!(bounded["timelineEvent"]["payload"]["messageId"].as_str().unwrap().len(), 180);
    }

    #[test]
    fn runtime_turn_bridge_serializes_public_message_phase_and_reason_without_private_fields() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "foreground_completed",
                "sessionId": "session-owner",
                "turnId": "turn-public-messages",
                "plan": {
                    "summary": "",
                    "reply": "",
                    "nextStep": "needs_user_action",
                    "reason": "native_final_answer_missing",
                    "publicMessages": [
                        {
                            "id": "public-1",
                            "text": "I am checking the requested file.",
                            "phase": "commentary",
                            "private": "drop"
                        },
                        {
                            "id": "public-2",
                            "text": "The requested file is ready.",
                            "phase": "final_answer",
                            "secret": {"token": "drop"}
                        },
                        {
                            "id": "bad-entry",
                            "text": "drop this unsupported phase",
                            "phase": "hidden_reasoning"
                        }
                    ],
                    "privatePlanField": "drop"
                },
                "timeline": [{
                    "id": "timeline-public-1",
                    "sequence": 0,
                    "timestamp": "2026-10-14T01:02:05Z",
                    "kind": "assistant",
                    "payload": {
                        "messageId": "public-1",
                        "phase": "commentary",
                        "status": "done",
                        "summary": "I am checking the requested file."
                    }
                }],
                "privateTopLevel": "drop"
            }
        }))
        .expect("runtime turn should be forwarded");

        let serialized = serde_json::to_string(&sanitized).expect("event should serialize");
        let round_trip: serde_json::Value =
            serde_json::from_str(&serialized).expect("serialized event should parse");
        let plan = &round_trip["payload"]["plan"];
        assert_eq!(plan["reason"], "native_final_answer_missing");
        assert_eq!(plan["publicMessages"].as_array().unwrap().len(), 2);
        assert_eq!(plan["publicMessages"][0]["id"], "public-1");
        assert_eq!(plan["publicMessages"][0]["phase"], "commentary");
        assert_eq!(plan["publicMessages"][1]["phase"], "final_answer");
        assert!(plan["publicMessages"][0].get("private").is_none());
        assert!(plan.get("privatePlanField").is_none());
        assert_eq!(round_trip["payload"]["timeline"][0]["payload"]["messageId"], "public-1");
        assert!(round_trip["payload"].get("privateTopLevel").is_none());

        let long_text = "x".repeat(7001);
        let long_event = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeTurn",
            "payload": {
                "schema": "vrcforge.runtime_turn_event.v1",
                "continuationSource": "foreground_completed",
                "sessionId": "session-owner",
                "turnId": "turn-public-message-long",
                "plan": {
                    "publicMessages": [{
                        "id": "public-long",
                        "text": long_text,
                        "phase": "final_answer"
                    }]
                }
            }
        }))
        .expect("long public message should remain bounded but intact");
        let long_serialized = serde_json::to_string(&long_event).expect("long event should serialize");
        let long_round_trip: serde_json::Value =
            serde_json::from_str(&long_serialized).expect("long event should parse");
        assert_eq!(long_round_trip["payload"]["plan"]["publicMessages"][0]["text"].as_str().unwrap().len(), 7001);
    }

    #[test]
    fn runtime_delta_rejects_unbounded_or_malformed_provider_retry() {
        let sanitized = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeDelta",
            "payload": {
                "providerRetry": {
                    "attempt": 0,
                    "max": 1000,
                    "reason": "private",
                    "statusCode": 9999,
                    "terminal": "private"
                }
            }
        }))
        .expect("runtime delta should remain a valid event");
        assert!(sanitized.get("providerRetry").is_none());
    }
}
