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
        event["timestamp"] = timestamp.clone();
    }
    if event_type == "agentRuntimeDelta" {
        if let Some(value) = payload.get("sessionId").and_then(|value| value.as_str()) {
            event["sessionId"] = serde_json::Value::String(value.chars().take(160).collect());
        }
        if let Some(value) = payload.get("turnId").and_then(|value| value.as_str()) {
            event["turnId"] = serde_json::Value::String(value.chars().take(160).collect());
        }
        if let Some(value) = payload.get("clientTurnId").and_then(|value| value.as_str()) {
            event["clientTurnId"] = serde_json::Value::String(value.chars().take(160).collect());
        }
        if let Some(value) = payload.get("textDelta").and_then(|value| value.as_str()) {
            event["textDelta"] = serde_json::Value::String(value.chars().take(1000).collect());
        }
        if let Some(value) = payload.get("done").and_then(|value| value.as_bool()) {
            event["done"] = serde_json::Value::Bool(value);
        }
    }
    Some(event)
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
}
