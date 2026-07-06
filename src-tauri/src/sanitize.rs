#![allow(unused_imports)]

use crate::backend::*;
use crate::commands::*;
use crate::event_bridge::*;
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

pub(crate) fn remove_secret_response_fields(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::Object(object) => {
            for key in [
                "api_key",
                "apiKey",
                "authorization",
                "Authorization",
                "configPath",
                "backupPath",
            ] {
                object.remove(key);
            }
            for child in object.values_mut() {
                remove_secret_response_fields(child);
            }
        }
        serde_json::Value::String(text) => {
            *text = sanitize_text_for_webview(text);
        }
        serde_json::Value::Array(items) => {
            for item in items {
                remove_secret_response_fields(item);
            }
        }
        _ => {}
    }
}

pub(crate) fn sanitize_webview_response(mut value: serde_json::Value) -> serde_json::Value {
    remove_secret_response_fields(&mut value);
    value
}

pub(crate) fn sanitize_text_for_webview(value: &str) -> String {
    let lower = value.to_ascii_lowercase();
    if lower.contains("api_key")
        || lower.contains("apikey")
        || lower.contains("configpath")
        || lower.contains("backuppath")
        || (lower.contains("bearer ") && !is_env_placeholder_template(value))
        || (lower.contains("authorization") && !is_env_placeholder_template(value))
    {
        return "[redacted]".to_string();
    }
    value.to_string()
}

pub(crate) fn webview_error_message(body: &serde_json::Value) -> String {
    let Some(detail) = body.get("detail") else {
        return "VRCForge runtime request failed.".to_string();
    };
    match detail {
        serde_json::Value::String(text) => sanitize_text_for_webview(text),
        serde_json::Value::Object(_) => {
            let sanitized = sanitize_webview_response(detail.clone());
            for key in ["error", "message", "detail", "reason"] {
                if let Some(text) = sanitized.get(key).and_then(|value| value.as_str()) {
                    return sanitize_text_for_webview(text);
                }
            }
            if let Some(status) = sanitized.get("status") {
                if status.is_string() || status.is_number() || status.is_boolean() {
                    return format!("VRCForge runtime request failed: {status}");
                }
            }
            "VRCForge runtime request failed.".to_string()
        }
        _ => "VRCForge runtime request failed.".to_string(),
    }
}

pub(crate) fn is_env_placeholder_template(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    lower.contains("${")
        && lower.contains('}')
        && (lower.contains("token") || lower.contains("api_key") || lower.contains("apikey"))
}

pub(crate) fn sanitize_error_message(message: String, secrets: &[&str]) -> String {
    let mut sanitized = sanitize_text_for_webview(&message);
    for secret in secrets {
        let secret = secret.trim();
        if !secret.is_empty() {
            sanitized = sanitized.replace(secret, "[redacted]");
        }
    }
    sanitized
}

pub(crate) fn sanitize_provider_result(
    result: Result<serde_json::Value, String>,
    secrets: &[&str],
) -> Result<serde_json::Value, String> {
    result
        .map(sanitize_webview_response)
        .map_err(|message| sanitize_error_message(message, secrets))
}

pub(crate) fn provider_config_body(
    provider: String,
    api_key: Option<String>,
    base_url: Option<String>,
    model: Option<String>,
) -> serde_json::Value {
    let mut body = serde_json::Map::new();
    body.insert("provider".to_string(), serde_json::Value::String(provider));
    if let Some(api_key) = api_key {
        body.insert("api_key".to_string(), serde_json::Value::String(api_key));
    }
    body.insert(
        "base_url".to_string(),
        base_url.map_or(serde_json::Value::Null, serde_json::Value::String),
    );
    body.insert(
        "model".to_string(),
        model.map_or(serde_json::Value::Null, serde_json::Value::String),
    );
    serde_json::Value::Object(body)
}
