#![allow(unused_imports)]

use crate::backend::*;
use crate::commands::*;
use crate::event_bridge::*;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
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

/// Sanitize the dedicated compaction response without rewriting the producer's
/// already-redacted recovery payload or its matching top-level summary.
///
/// This is intentionally scoped to the exact response shape emitted by
/// `/api/app/agent/compact`; callers for ordinary backend responses must keep
/// using `sanitize_webview_response`.
pub(crate) fn sanitize_compaction_response(mut value: serde_json::Value) -> serde_json::Value {
    let retained = value.as_object().and_then(|object| {
        let recovery = object.get("recovery")?.as_object()?;
        let required = [
            "schema",
            "authority",
            "sourceEntries",
            "sourceDigest",
            "summary",
            "summaryDigest",
            "summarySource",
        ];
        let source_entries = recovery.get("sourceEntries")?.as_array()?;
        if recovery.len() != required.len()
            || !required.iter().all(|key| recovery.contains_key(*key))
            || recovery.get("schema")?.as_str()? != "vrcforge.context_compaction_recovery.v1"
            || recovery.get("authority")?.as_str()? != "historical_context_data"
            || source_entries.is_empty()
            || !source_entries.iter().all(|entry| {
                entry.as_object().is_some_and(|entry| {
                    entry.len() == 2
                        && matches!(entry.get("role").and_then(|value| value.as_str()), Some("user" | "assistant" | "system" | "tool"))
                        && entry.get("text").and_then(|value| value.as_str()).is_some_and(|value| !value.is_empty())
                })
            })
            || recovery.get("sourceDigest")?.as_str()?.len() != 64
            || recovery.get("summaryDigest")?.as_str()?.len() != 64
            || !matches!(recovery.get("summarySource")?.as_str()?, "provider" | "fallback")
        {
            return None;
        }
        let summary = recovery.get("summary")?.as_str()?.to_string();
        if summary.is_empty() {
            return None;
        }
        let canonical_entries: Vec<_> = source_entries
            .iter()
            .map(|entry| {
                let object = entry.as_object().expect("validated recovery entry");
                serde_json::json!({
                    "role": object.get("role").and_then(|value| value.as_str()).unwrap_or_default(),
                    "text": object.get("text").and_then(|value| value.as_str()).unwrap_or_default(),
                })
            })
            .collect();
        let source_digest = Sha256::digest(serde_json::to_vec(&canonical_entries).ok()?);
        let source_digest = source_digest.iter().map(|byte| format!("{byte:02x}")).collect::<String>();
        if recovery.get("sourceDigest")?.as_str()? != source_digest {
            return None;
        }
        let summary_digest = Sha256::digest(summary.as_bytes());
        let summary_digest = summary_digest.iter().map(|byte| format!("{byte:02x}")).collect::<String>();
        if recovery.get("summaryDigest")?.as_str()? != summary_digest {
            return None;
        }
        let matching_summary = object
            .get("summary")
            .and_then(|value| value.as_str())
            .filter(|value| *value == summary)
            .map(str::to_string);
        Some((recovery.clone(), matching_summary))
    });
    remove_secret_response_fields(&mut value);
    if let Some((recovery, summary)) = retained {
        if let Some(object) = value.as_object_mut() {
            object.insert("recovery".to_string(), serde_json::Value::Object(recovery));
            if let Some(summary) = summary {
                object.insert("summary".to_string(), serde_json::Value::String(summary));
            }
        }
    }
    value
}

#[cfg(test)]
mod compaction_sanitizer_tests {
    use super::sanitize_compaction_response;
    use serde_json::json;
    use sha2::{Digest, Sha256};

    #[test]
    fn preserves_valid_backend_recovery_and_matching_summary_only() {
        let source_entries = json!([{"role": "user", "text": "api_key=[REDACTED_SECRET]"}]);
        let source_digest = Sha256::digest(serde_json::to_vec(&source_entries).unwrap());
        let source_digest = source_digest.iter().map(|byte| format!("{byte:02x}")).collect::<String>();
        let summary = "Discussed api_key=[REDACTED_SECRET]";
        let summary_digest = Sha256::digest(summary.as_bytes());
        let summary_digest = summary_digest.iter().map(|byte| format!("{byte:02x}")).collect::<String>();
        let recovery = json!({
            "schema": "vrcforge.context_compaction_recovery.v1",
            "authority": "historical_context_data",
            "sourceEntries": source_entries,
            "sourceDigest": source_digest,
            "summary": summary,
            "summaryDigest": summary_digest,
            "summarySource": "fallback"
        });
        let actual = sanitize_compaction_response(json!({
            "ok": true,
            "recovery": recovery.clone(),
            "summary": recovery["summary"].clone(),
            "detail": "api_key=private-value"
        }));
        assert_eq!(actual["recovery"], recovery);
        assert_eq!(actual["summary"], recovery["summary"]);
        assert_eq!(actual["detail"], "[redacted]");
    }

    #[test]
    fn malformed_or_mismatched_recovery_keeps_normal_redaction() {
        let recovery = json!({
            "schema": "vrcforge.context_compaction_recovery.v1",
            "authority": "historical_context_data",
            "sourceEntries": [{"role": "user", "text": "api_key=private-value"}],
            "sourceDigest": "a".repeat(64),
            "summary": "safe summary",
            "summaryDigest": "b".repeat(64),
            "summarySource": "fallback"
        });
        let actual = sanitize_compaction_response(json!({
            "recovery": recovery,
            "summary": "other api_key=private-value"
        }));
        assert_eq!(actual["recovery"]["sourceEntries"][0]["text"], "[redacted]");
        assert_eq!(actual["summary"], "[redacted]");
    }
}

/// Only for the authenticated, digest-assembled chat snapshot response.
/// The backend recomputes this allowlist after validating both privacy and
/// recovery digests. It is not accepted from stored chats or caller input.
pub(crate) fn sanitize_chat_snapshot_response(mut value: serde_json::Value) -> serde_json::Value {
    let chat_digests = value
        .as_object_mut()
        .and_then(|object| object.remove("chatDigests"))
        .and_then(|digests| validated_chat_digests(&digests));
    let chat_metadata_shapes = value.as_object_mut()
        .and_then(|object| object.remove("chatMetadataShapes"))
        .and_then(|shapes| validated_chat_metadata_shapes(&shapes));
    let paths = value.as_object_mut()
        .and_then(|object| object.remove("_validatedCompactionRecoveryPaths"))
        .and_then(|paths| paths.as_array().cloned())
        .unwrap_or_default();
    let retained: Vec<_> = paths.iter().filter_map(|path| {
        let path = path.as_str()?;
        let parts: Vec<_> = path.split('/').collect();
        let index = |part: &str| part.parse::<usize>().is_ok();
        let known = parts.len() >= 6 && parts[0].is_empty()
            && parts[1] == "chats" && index(parts[2])
            && parts[3] == "items" && index(parts[4])
            && match &parts[5..] {
                ["compactionRecovery"] => true,
                ["compactionRecovery", item] => index(item),
                ["response", "contextCompaction", "recovery"] => true,
                ["response", "contextCompaction", "recovery", item] => index(item),
                _ => false,
            };
        if !known { return None; }
        let recovery = value.pointer(path)?;
        recovery.is_object().then(|| (path.to_string(), recovery.clone()))
    }).collect();
    let text_paths = value.as_object_mut()
        .and_then(|object| object.remove("_validatedChatTextPaths"))
        .and_then(|paths| paths.as_array().cloned())
        .unwrap_or_default();
    let retained_text: Vec<_> = text_paths.iter().filter_map(|path| {
        validated_plain_chat_text_path(&value, path.as_str()?)
    }).collect();
    remove_secret_response_fields(&mut value);
    for (path, recovery) in retained {
        if let Some(target) = value.pointer_mut(&path) {
            *target = recovery;
        }
    }
    for (path, text) in retained_text {
        if let Some(target) = value.pointer_mut(&path) {
            *target = serde_json::Value::String(text);
        }
    }
    if let Some(digests) = chat_digests {
        if let Some(object) = value.as_object_mut() {
            object.insert("chatDigests".to_string(), digests);
        }
    }
    if let Some(shapes) = chat_metadata_shapes {
        if let Some(object) = value.as_object_mut() {
            object.insert("chatMetadataShapes".to_string(), shapes);
        }
    }
    value
}

fn validated_chat_metadata_shapes(value: &serde_json::Value) -> Option<serde_json::Value> {
    fn valid_shape(value: &serde_json::Value) -> bool {
        match value {
            serde_json::Value::Bool(true) => true,
            serde_json::Value::Object(fields) => fields.values().all(valid_shape),
            _ => false,
        }
    }
    let object = value.as_object()?;
    let mut retained = serde_json::Map::new();
    for (chat_id, shape) in object {
        if !chat_id.is_empty() && valid_shape(shape) {
            retained.insert(chat_id.clone(), shape.clone());
        }
    }
    Some(serde_json::Value::Object(retained))
}

fn validated_chat_digests(value: &serde_json::Value) -> Option<serde_json::Value> {
    let object = value.as_object()?;
    let mut retained = serde_json::Map::new();
    for (chat_id, digest) in object {
        let Some(digest) = digest.as_str() else {
            continue;
        };
        if chat_id.is_empty()
            || digest.len() != 64
            || !digest.chars().all(|character| character.is_ascii_hexdigit())
        {
            continue;
        }
        retained.insert(chat_id.clone(), serde_json::Value::String(digest.to_ascii_lowercase()));
    }
    Some(serde_json::Value::Object(retained))
}

fn canonical_array_index(value: &str) -> bool {
    value.parse::<usize>().is_ok_and(|index| index.to_string() == value)
}

fn validated_plain_chat_text_path(
    value: &serde_json::Value,
    path: &str,
) -> Option<(String, String)> {
    let parts: Vec<_> = path.split('/').collect();
    let text = if parts.len() == 4
        && parts[0].is_empty()
        && parts[1] == "chats"
        && parts[3] == "title"
        && canonical_array_index(parts[2])
    {
        let chat_path = format!("/chats/{}", parts[2]);
        if !value.pointer(&chat_path)?.is_object() {
            return None;
        }
        value.pointer(path)?.as_str()?
    } else if parts.len() == 6
        && parts[0].is_empty()
        && parts[1] == "chats"
        && parts[3] == "items"
        && canonical_array_index(parts[2])
        && canonical_array_index(parts[4])
        && (parts[5] == "text" || parts[5] == "detail")
    {
        let item_path = format!("/chats/{}/items/{}", parts[2], parts[4]);
        let item = value.pointer(&item_path)?.as_object()?;
        let item_type = item.get("type")?.as_str()?;
        let valid = if parts[5] == "text" {
            matches!(item_type, "user" | "streaming" | "error" | "compact")
        } else {
            item_type == "compact"
        };
        if !valid {
            return None;
        }
        value.pointer(path)?.as_str()?
    } else if parts.len() == 8
        && parts[0].is_empty()
        && parts[1] == "chats"
        && parts[3] == "items"
        && parts[5] == "response"
        && parts[6] == "plan"
        && canonical_array_index(parts[2])
        && canonical_array_index(parts[4])
    {
        let item_path = format!("/chats/{}/items/{}", parts[2], parts[4]);
        let item = value.pointer(&item_path)?.as_object()?;
        if item.get("type").and_then(|item_type| item_type.as_str()) != Some("agent") {
            return None;
        }
        if parts[7] != "reply" && parts[7] != "summary" {
            return None;
        }
        value.pointer(path)?.as_str()?
    } else if parts.len() == 8
        && parts[0].is_empty()
        && parts[1] == "chats"
        && parts[3] == "items"
        && parts[5] == "response"
        && parts[6] == "task"
        && parts[7] == "objective"
        && canonical_array_index(parts[2])
        && canonical_array_index(parts[4])
    {
        let item_path = format!("/chats/{}/items/{}", parts[2], parts[4]);
        let item = value.pointer(&item_path)?.as_object()?;
        if item.get("type").and_then(|item_type| item_type.as_str()) != Some("agent") {
            return None;
        }
        value.pointer(path)?.as_str()?
    } else if parts.len() == 9
        && parts[0].is_empty()
        && parts[1] == "chats"
        && parts[3] == "items"
        && parts[5] == "response"
        && parts[6] == "plan"
        && parts[7] == "task"
        && parts[8] == "objective"
        && canonical_array_index(parts[2])
        && canonical_array_index(parts[4])
    {
        let item_path = format!("/chats/{}/items/{}", parts[2], parts[4]);
        let item = value.pointer(&item_path)?.as_object()?;
        if item.get("type").and_then(|item_type| item_type.as_str()) != Some("agent") {
            return None;
        }
        value.pointer(path)?.as_str()?
    } else if parts.len() == 10
        && parts[0].is_empty()
        && parts[1] == "chats"
        && parts[3] == "items"
        && parts[5] == "response"
        && parts[6] == "timeline"
        && parts[8] == "payload"
        && parts[9] == "summary"
        && canonical_array_index(parts[2])
        && canonical_array_index(parts[4])
        && canonical_array_index(parts[7])
    {
        let item_path = format!("/chats/{}/items/{}", parts[2], parts[4]);
        let item = value.pointer(&item_path)?.as_object()?;
        if item.get("type").and_then(|item_type| item_type.as_str()) != Some("agent") {
            return None;
        }
        let event_path = format!(
            "/chats/{}/items/{}/response/timeline/{}",
            parts[2], parts[4], parts[7]
        );
        let event = value.pointer(&event_path)?.as_object()?;
        if event.get("kind").and_then(|kind| kind.as_str()) != Some("assistant") {
            return None;
        }
        value.pointer(path)?.as_str()?
    } else if parts.len() == 9
        && parts[0].is_empty()
        && parts[1] == "chats"
        && parts[3] == "items"
        && parts[5] == "timeline"
        && parts[7] == "payload"
        && parts[8] == "summary"
        && canonical_array_index(parts[2])
        && canonical_array_index(parts[4])
        && canonical_array_index(parts[6])
    {
        let item_path = format!("/chats/{}/items/{}", parts[2], parts[4]);
        let item = value.pointer(&item_path)?.as_object()?;
        if item.get("type").and_then(|item_type| item_type.as_str()) != Some("agent") {
            return None;
        }
        let event_path = format!(
            "/chats/{}/items/{}/timeline/{}",
            parts[2], parts[4], parts[6]
        );
        let event = value.pointer(&event_path)?.as_object()?;
        if event.get("kind").and_then(|kind| kind.as_str()) != Some("assistant") {
            return None;
        }
        value.pointer(path)?.as_str()?
    } else {
        return None;
    };
    is_validated_plain_chat_text(text).then(|| (path.to_string(), text.to_string()))
}

fn is_validated_plain_chat_text(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    if lower.contains("bearer ") {
        return false;
    }
    if sanitize_text_for_webview(value) == value {
        return true;
    }
    if contains_secret_key_prefix(value)
        || has_extended_sensitive_assignment(&lower)
    {
        return false;
    }
    ["api_key", "apikey"].iter().all(|key| {
        chat_api_key_occurrences_are_metadata(&lower, key)
    }) && (lower.contains("api_key") || lower.contains("apikey"))
}

fn chat_api_key_occurrences_are_metadata(value: &str, key: &str) -> bool {
    let mut offset = 0usize;
    while let Some(relative) = value[offset..].find(key) {
        let start = offset + relative;
        let end = start + key.len();
        if start > 0 && is_ascii_identifier_character(value.as_bytes()[start - 1]) {
            return false;
        }
        if value.as_bytes().get(end).is_some_and(|character| is_ascii_identifier_character(*character)) {
            return false;
        }
        let suffix = value[end..].trim_start_matches(char::is_whitespace);
        if matches!(suffix.as_bytes().first(), Some(b'=') | Some(b':') | Some(b'\'') | Some(b'"')) {
            return false;
        }
        offset = end;
    }
    true
}

#[cfg(test)]
mod chat_snapshot_sanitizer_tests {
    use super::sanitize_chat_snapshot_response;
    use serde_json::json;

    #[test]
    fn preserves_only_host_validated_recoveries_in_known_chat_locations() {
        let recovery = json!({
            "schema": "vrcforge.context_compaction_recovery.v1",
            "authority": "historical_context_data",
            "sourceEntries": [{"role":"user", "text":"api_key=[REDACTED_SECRET]"}],
            "sourceDigest":"source-digest-fixture",
            "summary":"Discussed api_key configuration",
            "summaryDigest":"summary-digest-fixture",
            "summarySource":"provider"
        });
        let value = json!({
            "_validatedCompactionRecoveryPaths": [
                "/chats/0/items/0/compactionRecovery/0",
                "/chats/0/items/0/response/contextCompaction/recovery",
                "/chats/0/items/0/text",
                "/other"
            ],
            "chats":[{"titleSource":"auto", "isTestChat":true, "projectPath":"Q:/Milltina", "items":[{
                "type":"agent", "text":"api_key=private-value",
                "compactionRecovery":[recovery.clone(), recovery.clone()],
                "response":{"contextCompaction":{"recovery":recovery.clone()}}
            }]}],
            "other": recovery.clone(),
            "api_key":"must-not-cross"
        });
        let actual = sanitize_chat_snapshot_response(value);
        assert_eq!(actual["chats"][0]["titleSource"], "auto");
        assert_eq!(actual["chats"][0]["isTestChat"], true);
        assert_eq!(actual["chats"][0]["projectPath"], "Q:/Milltina");
        assert_eq!(actual["chats"][0]["items"][0]["compactionRecovery"][0], recovery);
        assert_eq!(actual["chats"][0]["items"][0]["response"]["contextCompaction"]["recovery"], recovery);
        assert_eq!(actual["chats"][0]["items"][0]["compactionRecovery"][1]["summary"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][0]["text"], "[redacted]");
        assert_eq!(actual["other"]["summary"], "[redacted]");
        assert!(actual.get("_validatedCompactionRecoveryPaths").is_none());
        assert!(actual.get("api_key").is_none());
    }

    #[test]
    fn unmarked_and_malformed_paths_keep_normal_redaction() {
        for marker in [serde_json::Value::Null, json!(["/chats/x/items/0/compactionRecovery", "/chats/0/items/0/compactionRecovery/sourceEntries"])] {
            let actual = sanitize_chat_snapshot_response(json!({
                "_validatedCompactionRecoveryPaths":marker,
                "chats":[{"items":[{"compactionRecovery":{"summary":"api_key=private-value"}}]}]
            }));
            assert_eq!(actual["chats"][0]["items"][0]["compactionRecovery"]["summary"], "[redacted]");
            assert!(actual.get("_validatedCompactionRecoveryPaths").is_none());
        }
    }

    #[test]
    fn preserves_only_scanned_plain_chat_text_and_rejects_forged_paths() {
        let actual = sanitize_chat_snapshot_response(json!({
            "_validatedChatTextPaths": [
                "/chats/0/items/0/text",
                "/chats/0/items/1/text",
                "/chats/0/items/2/text",
                "/chats/0/items/3/text/../../other",
                "/other/text",
            ],
            "chats": [{"items": [
                {"type": "user", "text": "配置字段名 api_key 仅用于说明。"},
                {"type": "user", "text": "The api_key field is documented in the configuration reference."},
                {"type": "user", "text": "api_key=secret-value"},
                {"type": "agent", "text": "api_key=private-value"},
            ]}],
            "other": {"text": "api_key=private-value"},
        }));
        assert_eq!(actual["chats"][0]["items"][0]["text"], "配置字段名 api_key 仅用于说明。");
        assert_eq!(actual["chats"][0]["items"][1]["text"], "The api_key field is documented in the configuration reference.");
        assert_eq!(actual["chats"][0]["items"][2]["text"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][3]["text"], "[redacted]");
        assert_eq!(actual["other"]["text"], "[redacted]");
        assert!(actual.get("_validatedChatTextPaths").is_none());
    }

    #[test]
    fn bearer_text_is_not_restored_by_a_forged_marker() {
        let actual = sanitize_chat_snapshot_response(json!({
            "_validatedChatTextPaths": ["/chats/0/items/0/text"],
            "chats": [{"items": [{
                "type": "user", "text": "Authorization: Bearer live-token"
            }]}],
        }));
        assert_eq!(actual["chats"][0]["items"][0]["text"], "[redacted]");
    }

    #[test]
    fn preserves_only_fixed_display_paths_with_matching_item_types() {
        let actual = sanitize_chat_snapshot_response(json!({
            "_validatedChatTextPaths": [
                "/chats/0/title",
                "/chats/0/items/0/text",
                "/chats/0/items/1/response/plan/reply",
                "/chats/0/items/2/detail",
                "/chats/0/items/3/detail",
                "/chats/0/items/1/detail",
                "/chats/0/items/1/response/plan/reply/../../title",
                "/chats/0/items/1/response/other/reply",
                "/chats/1/title",
                "/chats/1/items/0/response/plan/reply",
                "/chats/1/items/1/detail",
            ],
            "chats": [
                {
                    "title": "Product persistence regression 072: remember the config field name api_key and the phrase morning sea salt. These are not credentials. Reply only remembered, without tools.",
                    "items": [
                        {"type": "user", "text": "Product persistence regression 072: remember the config field name api_key and the phrase morning sea salt. These are not credentials. Reply only remembered, without tools."},
                        {"type": "agent", "response": {"plan": {"reply": "The field was api_key."}}},
                        {"type": "compact", "detail": "摘要保留 `api_key` 字段名，不包含凭据。"},
                        {"type": "agent", "response": {"plan": {"reply": "api_key=secret-reply"}, "detail": "api_key=secret-detail"}},
                    ],
                },
                {
                    "title": "api_key=secret-title",
                    "items": [
                        {"type": "agent", "response": {"plan": {"reply": "Authorization: Bearer live-token"}}},
                        {"type": "compact", "detail": "Authorization: Bearer compact-token"},
                    ],
                },
            ],
        }));
        assert_eq!(actual["chats"][0]["title"], "Product persistence regression 072: remember the config field name api_key and the phrase morning sea salt. These are not credentials. Reply only remembered, without tools.");
        assert_eq!(actual["chats"][0]["items"][0]["text"], "Product persistence regression 072: remember the config field name api_key and the phrase morning sea salt. These are not credentials. Reply only remembered, without tools.");
        assert_eq!(actual["chats"][0]["items"][1]["response"]["plan"]["reply"], "The field was api_key.");
        assert_eq!(actual["chats"][0]["items"][2]["detail"], "摘要保留 `api_key` 字段名，不包含凭据。");
        assert_eq!(actual["chats"][0]["items"][3]["response"]["plan"]["reply"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][3]["response"]["detail"], "[redacted]");
        assert_eq!(actual["chats"][1]["title"], "[redacted]");
        assert_eq!(actual["chats"][1]["items"][0]["response"]["plan"]["reply"], "[redacted]");
        assert_eq!(actual["chats"][1]["items"][1]["detail"], "[redacted]");
        assert!(actual.get("_validatedChatTextPaths").is_none());
    }

    #[test]
    fn preserves_only_valid_chat_digests_and_new_fixed_agent_paths() {
        let safe = "The field name api_key is documented; no credential is present.";
        let actual = sanitize_chat_snapshot_response(json!({
            "chatDigests": {
                "chat-safe": "A".repeat(64),
                "bad": "not-a-digest",
                "wrong-type": 7,
            },
            "chatMetadataShapes": {
                "chat-safe": {"id": true, "usage": {"inputTokens": true}},
                "bad-shape": {"private": "must not pass through"},
                "wrong-type": 4,
            },
            "_validatedChatTextPaths": [
                "/chats/0/items/0/response/plan/summary",
                "/chats/0/items/0/response/plan/task/objective",
                "/chats/0/items/0/response/task/objective",
                "/chats/0/items/0/response/timeline/1/payload/summary",
                "/chats/0/items/0/response/timeline/0/payload/summary",
                "/chats/0/items/0/timeline/1/payload/summary",
                "/chats/0/items/0/timeline/2/payload/summary",
                "/chats/0/items/0/timeline/3/payload/summary",
                "/chats/0/items/0/response/plan/summary/../../text",
                "/chats/0/items/1/response/plan/summary",
                "/chats/0/items/1/response/task/objective",
                "/chats/0/items/1/response/timeline/0/payload/summary",
                "/chats/0/items/1/timeline/0/payload/summary",
            ],
            "chats": [{"items": [
                {"type": "agent", "response": {
                    "plan": {"summary": safe, "task": {"objective": safe}},
                    "task": {"objective": safe},
                    "timeline": [
                        {"kind": "tool_result", "payload": {"summary": safe}},
                        {"kind": "assistant", "payload": {"summary": safe}},
                    ],
                }, "timeline": [
                    {"kind": "tool_result", "payload": {"summary": safe}},
                    {"kind": "assistant", "payload": {"summary": safe}},
                    {"kind": "assistant", "payload": {"summary": "Bearer live-token"}},
                    {"kind": "assistant", "payload": {"summary": "api_key=secret-value"}},
                ]},
                {"type": "agent", "response": {
                    "plan": {"summary": "api_key=secret-value"},
                    "task": {"objective": "Authorization: Bearer live-token"},
                    "timeline": [{"kind": "assistant", "payload": {"summary": "Bearer live-token"}}],
                }, "timeline": [
                    {"kind": "assistant", "payload": {"summary": "Bearer live-token"}},
                ]},
            ]}],
        }));
        assert_eq!(actual["chatDigests"]["chat-safe"], "a".repeat(64));
        assert!(actual["chatDigests"].get("bad").is_none());
        assert!(actual["chatDigests"].get("wrong-type").is_none());
        assert_eq!(actual["chatMetadataShapes"]["chat-safe"]["usage"]["inputTokens"], true);
        assert!(actual["chatMetadataShapes"]["bad-shape"].is_null());
        assert!(actual["chatMetadataShapes"]["wrong-type"].is_null());
        assert_eq!(actual["chats"][0]["items"][0]["response"]["plan"]["summary"], safe);
        assert_eq!(actual["chats"][0]["items"][0]["response"]["plan"]["task"]["objective"], safe);
        assert_eq!(actual["chats"][0]["items"][0]["response"]["task"]["objective"], safe);
        assert_eq!(actual["chats"][0]["items"][0]["response"]["timeline"][1]["payload"]["summary"], safe);
        assert_eq!(actual["chats"][0]["items"][0]["response"]["timeline"][0]["payload"]["summary"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][0]["timeline"][1]["payload"]["summary"], safe);
        assert_eq!(actual["chats"][0]["items"][0]["timeline"][0]["payload"]["summary"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][0]["timeline"][2]["payload"]["summary"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][0]["timeline"][3]["payload"]["summary"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][1]["response"]["plan"]["summary"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][1]["response"]["task"]["objective"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][1]["response"]["timeline"][0]["payload"]["summary"], "[redacted]");
        assert_eq!(actual["chats"][0]["items"][1]["timeline"][0]["payload"]["summary"], "[redacted]");
    }
}

/// Preserve backend-redacted support prose so users can review it in the UI.
pub(crate) fn sanitize_support_feedback_response(
    mut value: serde_json::Value,
) -> serde_json::Value {
    fn visit(value: &mut serde_json::Value, preserve_text: bool) {
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
                    visit(child, matches!(key.as_str(), "text" | "preview"));
                }
            }
            serde_json::Value::Array(items) => {
                for item in items {
                    visit(item, preserve_text);
                }
            }
            serde_json::Value::String(text) if !preserve_text => {
                *text = sanitize_text_for_webview(text);
            }
            _ => {}
        }
    }
    visit(&mut value, false);
    value
}

pub(crate) fn sanitize_memory_review_response(mut value: serde_json::Value) -> serde_json::Value {
    remove_secret_response_fields_with_memory_candidate_mode(&mut value, true);
    value
}

pub(crate) fn sanitize_text_for_webview(value: &str) -> String {
    let lower = value.to_ascii_lowercase();
    let has_sensitive_assignment = has_extended_sensitive_assignment(&lower);
    if contains_secret_key_prefix(value)
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

pub(crate) fn contains_secret_key_prefix(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    lower
        .match_indices("sk-")
        .any(|(start, _)| !is_harmless_risk_level_occurrence(&lower, start))
}

fn is_harmless_risk_level_occurrence(value: &str, secret_prefix_start: usize) -> bool {
    const SAFE_IDENTIFIER: &str = "risk-level";
    let Some(identifier_start) = secret_prefix_start.checked_sub(2) else {
        return false;
    };
    let identifier_end = identifier_start + SAFE_IDENTIFIER.len();
    if value.get(identifier_start..identifier_end) != Some(SAFE_IDENTIFIER) {
        return false;
    }
    let bytes = value.as_bytes();
    let left_boundary =
        identifier_start == 0 || !is_ascii_identifier_character(bytes[identifier_start - 1]);
    let right_boundary =
        identifier_end == bytes.len() || !is_ascii_identifier_character(bytes[identifier_end]);
    left_boundary && right_boundary
}

fn is_ascii_identifier_character(value: u8) -> bool {
    value.is_ascii_alphanumeric() || matches!(value, b'_' | b'-')
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
    if contains_secret_key_prefix(value)
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
    api_type: Option<String>,
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
    if let Some(api_type) = api_type {
        body.insert("api_type".to_string(), serde_json::Value::String(api_type));
    }
    serde_json::Value::Object(body)
}

#[cfg(test)]
mod memory_review_sanitizer_tests {
    use super::{
        sanitize_memory_review_response, sanitize_support_feedback_response,
        sanitize_text_for_webview, sanitize_webview_response,
    };

    #[test]
    fn harmless_candidate_keywords_do_not_split_webview_and_backend_state() {
        let text =
            "Remember API_KEY variable naming, risk-level metadata, and credential rotation guidance.";
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
        assert_eq!(
            sanitize_text_for_webview(&["Value ", "sk", "-private-value"].concat()),
            "[redacted]"
        );
        assert_eq!(
            sanitize_text_for_webview(&["prefix", "sk", "-private-value"].concat()),
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
    fn harmless_skill_markdown_is_not_redacted_as_a_secret_key() {
        let markdown = concat!(
            "---\n",
            "name: validation-report-extension\n",
            "permission-mode: read_only\n",
            "risk-level: medium\n",
            "entrypoint-tool: vrcforge_run_validation_report\n",
            "user-invocable: true\n",
            "disable-model-invocation: false\n",
            "allowed-tools:\n",
            "  - vrcforge_run_validation_report\n",
            "disallowed-tools:\n",
            "  - vrcforge_execute_shell\n",
            "  - direct_unity_asset_write\n",
            "---\n",
            "Use the read-only validation report.\n",
        );

        assert_eq!(sanitize_text_for_webview(markdown), markdown);
        let sanitized = sanitize_webview_response(serde_json::json!({
            "skillMarkdown": markdown,
        }));
        assert_eq!(sanitized["skillMarkdown"], markdown);

        let mixed = [markdown, "notes: ", "sk", "-private-value\n"].concat();
        assert_eq!(sanitize_text_for_webview(&mixed), "[redacted]");
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

    #[test]
    fn support_feedback_projection_preserves_backend_redacted_review_text() {
        let sanitized = sanitize_support_feedback_response(serde_json::json!({
            "text": "What happened:\napi_key=[REDACTED]\nBearer [REDACTED]",
            "preview": "member api_key=[REDACTED]",
            "api_key": "must-not-cross",
            "detail": "api_key=private-value",
        }));
        assert_eq!(
            sanitized["text"],
            "What happened:\napi_key=[REDACTED]\nBearer [REDACTED]"
        );
        assert_eq!(sanitized["preview"], "member api_key=[REDACTED]");
        assert!(sanitized.get("api_key").is_none());
        assert_eq!(sanitized["detail"], "[redacted]");
    }
}
