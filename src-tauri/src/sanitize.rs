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
    remove_secret_response_fields_with_memory_candidate_mode(value, false);
}

fn remove_secret_response_fields_with_memory_candidate_mode(
    value: &mut serde_json::Value,
    allow_memory_candidate_text: bool,
) {
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
            for (key, child) in object.iter_mut() {
                if allow_memory_candidate_text && key == "proposedText" {
                    if let serde_json::Value::String(text) = child {
                        *text = sanitize_memory_candidate_text_for_webview(text);
                        continue;
                    }
                }
                remove_secret_response_fields_with_memory_candidate_mode(
                    child,
                    allow_memory_candidate_text,
                );
            }
        }
        serde_json::Value::String(text) => {
            *text = sanitize_text_for_webview(text);
        }
        serde_json::Value::Array(items) => {
            for item in items {
                remove_secret_response_fields_with_memory_candidate_mode(
                    item,
                    allow_memory_candidate_text,
                );
            }
        }
        _ => {}
    }
}

pub(crate) fn sanitize_webview_response(mut value: serde_json::Value) -> serde_json::Value {
    remove_secret_response_fields(&mut value);
    value
}

pub(crate) fn sanitize_memory_review_response(mut value: serde_json::Value) -> serde_json::Value {
    remove_secret_response_fields_with_memory_candidate_mode(&mut value, true);
    value
}

pub(crate) fn sanitize_text_for_webview(value: &str) -> String {
    let lower = value.to_ascii_lowercase();
    let has_sensitive_assignment = has_extended_sensitive_assignment(&lower);
    if lower.contains("sk-")
        || lower.contains("api_key")
        || lower.contains("apikey")
        || lower.contains("configpath")
        || lower.contains("backuppath")
        || has_sensitive_assignment
        || (lower.contains("bearer ") && !is_env_placeholder_template(value))
        || (lower.contains("authorization") && !is_env_placeholder_template(value))
    {
        return "[redacted]".to_string();
    }
    value.to_string()
}

fn has_extended_sensitive_assignment(value: &str) -> bool {
    [
        "client_secret",
        "access_token",
        "password",
        "credential",
        "token",
        "secret",
        "session",
        "cookie",
    ]
    .iter()
    .any(|key| contains_key_value_assignment(value, key))
}

fn contains_key_value_assignment(value: &str, key: &str) -> bool {
    let mut offset = 0usize;
    while let Some(relative) = value[offset..].find(key) {
        let end = offset + relative + key.len();
        let suffix = value[end..].trim_start_matches(|character: char| {
            character.is_ascii_whitespace() || matches!(character, '\'' | '"')
        });
        if matches!(suffix.as_bytes().first(), Some(b'=') | Some(b':')) {
            return true;
        }
        offset = end;
    }
    false
}

fn sanitize_memory_candidate_text_for_webview(value: &str) -> String {
    let sanitized = sanitize_text_for_webview(value);
    if sanitized != "[redacted]" {
        return sanitized;
    }
    if is_harmless_memory_api_key_reference(value) {
        return value.to_string();
    }
    sanitized
}

fn is_harmless_memory_api_key_reference(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    if lower.contains("sk-")
        || lower.contains("bearer ")
        || lower.contains("authorization")
        || lower.contains("configpath")
        || lower.contains("backuppath")
        || has_extended_sensitive_assignment(&lower)
    {
        return false;
    }
    ["api_key", "apikey"]
        .iter()
        .all(|key| memory_api_key_occurrences_are_metadata_references(&lower, key))
}

fn memory_api_key_occurrences_are_metadata_references(value: &str, key: &str) -> bool {
    let mut offset = 0usize;
    while let Some(relative) = value[offset..].find(key) {
        let start = offset + relative;
        let end = start + key.len();
        if start > 0 {
            let before = value.as_bytes()[start - 1];
            if before.is_ascii_alphanumeric() || before == b'_' {
                return false;
            }
        }
        if let Some(after) = value.as_bytes().get(end) {
            if after.is_ascii_alphanumeric() || *after == b'_' {
                return false;
            }
        }
        let suffix = value[end..].trim_start_matches(char::is_whitespace);
        if suffix.is_empty()
            || matches!(
                suffix.as_bytes().first(),
                Some(b'.') | Some(b',') | Some(b';') | Some(b')') | Some(b']')
            )
        {
            offset = end;
            continue;
        }
        if matches!(
            suffix.as_bytes().first(),
            Some(b'=') | Some(b':') | Some(b'\'') | Some(b'"')
        ) {
            return false;
        }
        let next_word = suffix
            .split(|character: char| !character.is_ascii_alphanumeric() && character != '_')
            .next()
            .unwrap_or_default();
        if !matches!(
            next_word,
            "variable"
                | "variables"
                | "name"
                | "naming"
                | "field"
                | "fields"
                | "label"
                | "labels"
                | "placeholder"
                | "placeholders"
        ) {
            return false;
        }
        offset = end;
    }
    true
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

#[cfg(test)]
mod memory_review_sanitizer_tests {
    use super::{
        sanitize_memory_review_response, sanitize_text_for_webview, sanitize_webview_response,
    };

    #[test]
    fn harmless_candidate_keywords_do_not_split_webview_and_backend_state() {
        let text = "Remember API_KEY variable naming and credential rotation guidance.";
        assert_eq!(sanitize_text_for_webview(text), "[redacted]");
        let snapshot = serde_json::json!({
            "candidates": [{
                "candidateId": "memcand_safe",
                "proposedText": text,
                "state": "proposed"
            }],
            "api_key": "must-not-cross",
        });
        let sanitized = sanitize_memory_review_response(snapshot);
        assert_eq!(
            sanitized["candidates"][0]["proposedText"],
            serde_json::Value::String(text.to_string())
        );
        assert!(sanitized.get("api_key").is_none());
    }

    #[test]
    fn actual_assignments_and_authorization_values_are_redacted() {
        assert_eq!(
            sanitize_text_for_webview(&["sk", "-private-value"].concat()),
            "[redacted]"
        );
        for text in [
            "api_key=private-value",
            "{\"token\":\"private-value\"}",
            "https://probe.invalid/path?session=private-value",
            "Authorization: Bearer private-value",
        ] {
            assert_eq!(sanitize_text_for_webview(text), "[redacted]");
        }
    }

    #[test]
    fn whitespace_separated_secrets_and_paths_stay_redacted_globally_and_for_memory() {
        for text in [
            "api_key private-value",
            r"configPath C:\Users\Private\config.json",
            "Remember API_KEY variable naming and password=private-value",
        ] {
            assert_eq!(sanitize_text_for_webview(text), "[redacted]");
            let sanitized = sanitize_memory_review_response(serde_json::json!({
                "candidates": [{"proposedText": text}],
            }));
            assert_eq!(
                sanitized["candidates"][0]["proposedText"],
                serde_json::Value::String("[redacted]".to_string())
            );
        }
    }

    #[test]
    fn ordinary_webview_responses_keep_the_strong_keyword_fallback() {
        let sanitized = sanitize_webview_response(serde_json::json!({
            "detail": "Remember API_KEY variable naming."
        }));
        assert_eq!(sanitized["detail"], "[redacted]");
    }
}
