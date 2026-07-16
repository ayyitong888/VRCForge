#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
#![allow(unused_imports)]

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

mod backend;
#[cfg(windows)]
mod capture_helper;
mod commands;
mod event_bridge;
mod sanitize;

use backend::*;
use commands::*;
use event_bridge::*;
use sanitize::*;

const WEBVIEW2_ACCESSIBILITY_ARG: &str = "--force-renderer-accessibility";

fn webview2_args_with_accessibility(existing: Option<&str>) -> String {
    let existing = existing.unwrap_or_default().trim();
    if existing
        .split_ascii_whitespace()
        .any(|argument| argument == WEBVIEW2_ACCESSIBILITY_ARG)
    {
        existing.to_string()
    } else if existing.is_empty() {
        WEBVIEW2_ACCESSIBILITY_ARG.to_string()
    } else {
        format!("{existing} {WEBVIEW2_ACCESSIBILITY_ARG}")
    }
}

#[cfg(windows)]
fn configure_webview2_accessibility() {
    const KEY: &str = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS";
    let existing = env::var(KEY).ok();
    env::set_var(KEY, webview2_args_with_accessibility(existing.as_deref()));
}

fn main() {
    #[cfg(windows)]
    if let Some(exit_code) = capture_helper::try_run_from_args() {
        std::process::exit(exit_code);
    }
    #[cfg(windows)]
    configure_webview2_accessibility();
    tauri::Builder::default()
        .manage(BackendState::new())
        .setup(|app| {
            let open_chat_item =
                MenuItem::with_id(app, "open_chat", "打开对话", true, None::<&str>)?;
            let show_item = MenuItem::with_id(app, "show", "打开窗口", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出 VRCForge", true, None::<&str>)?;
            let menu =
                Menu::with_items(app, &[&open_chat_item, &show_item, &separator, &quit_item])?;
            let mut tray = TrayIconBuilder::new()
                .tooltip("VRCForge")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open_chat" => {
                        show_main_window(app);
                        let _ = app.emit("vrcforge-tray-open-chat", ());
                    }
                    "show" => show_main_window(app),
                    "quit" => {
                        shutdown_managed_backend(app);
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            acknowledge_sub_agent_handoff,
            apply_adjustment_checkpoint,
            answer_agent_question,
            approve_agent_approval,
            begin_developer_options_challenge,
            bind_agent_goal_owner,
            block_skill_package,
            cancel_developer_options_challenge,
            cancel_sub_agent,
            check_skills,
            clear_agent_memory,
            cancel_agent_desktop_action,
            compact_agent_history,
            create_adjustment_checkpoint,
            create_agent_goal,
            create_agent_memory,
            create_agent_progress,
            create_agent_question,
            create_skill,
            create_sub_agent,
            delete_adjustment_checkpoint,
            delete_agent_memory,
            delete_agent_progress,
            delete_skill,
            desktop_runtime_snapshot,
            export_interrupted_apply_incident_bundle,
            export_skill_package,
            export_support_bundle,
            fetch_agent_approvals,
            fetch_agent_desktop_actions,
            fetch_agent_goals,
            fetch_recoverable_agent_goal_deliveries,
            fetch_agent_memory,
            fetch_due_agent_goals,
            fetch_agent_notes,
            fetch_agent_progress,
            fetch_agent_questions,
            fetch_agent_runs,
            fetch_adjustment_checkpoints,
            fetch_advanced_settings,
            fetch_app_bootstrap,
            fetch_app_health,
            fetch_avatars,
            fetch_chats,
            fetch_diagnostics,
            fetch_doctor,
            fetch_external_agent_connectors,
            fetch_optimization_proof,
            fetch_optimization_proofs,
            fetch_optimization_plan,
            fetch_provider_models,
            fetch_checkpoints,
            fetch_interrupted_apply_recoveries,
            fetch_project_prefs,
            fetch_skill_packages,
            fetch_skills,
            fetch_sub_agent,
            fetch_sub_agents,
            fetch_workspace_diff,
            import_skill_package,
            install_external_agent_connector,
            issue_computer_use_turn_grant,
            merge_sub_agent,
            materialize_agent_goal_delivery,
            overwrite_adjustment_checkpoint,
            plan_avatar_encryption,
            plan_outfit_import,
            preflight_skill_package,
            preview_path_to_skill,
            preview_interrupted_apply_recovery,
            preview_adjustment_checkpoint,
            preview_restore_checkpoint,
            record_agent_run_queued,
            reject_agent_approval,
            refresh_projects,
            refresh_unity_readiness,
            repair_unity_mcp_bridge,
            request_avatar_encryption_apply,
            request_agent_desktop_action,
            request_agent_run_cancel,
            request_approval_revision,
            request_optimization_apply,
            request_outfit_import,
            request_package_install,
            request_restore_checkpoint,
            request_restore_interrupted_apply_recovery,
            resolve_interrupted_apply_recovery,
            retry_sub_agent,
            replace_agent_progress,
            revoke_skill_package_signer,
            save_agent_notes,
            save_chats,
            select_adjustment_checkpoint,
            set_skill_package_enabled,
            set_skill_package_safe_mode,
            send_agent_message,
            test_provider_capability,
            trust_skill_package_signer,
            uninstall_skill_package,
            uninstall_external_agent_connector,
            update_adjustment_checkpoint,
            update_advanced_settings,
            update_agent_goal,
            update_agent_progress,
            wake_agent_goal,
            update_api_config,
            update_diagnostics,
            update_external_agent_gateway,
            update_permission_mode,
            update_vision_config,
            update_skill,
            save_project_prefs,
            scan_project_index,
            start_backend,
            write_path_to_skill,
            ensure_agent_notes_file,
            open_folder,
            open_local_folder,
            open_logs_folder,
            select_folder
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let app = window.app_handle().clone();
                shutdown_managed_backend(&app);
                app.exit(0);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running VRCForge");
}

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

#[cfg(test)]
mod tests {
    use super::{
        advanced_settings_update_body, app_session_challenge_signature,
        app_session_challenge_signature_matches, developer_options_challenge_path,
        diagnostics_update_body, extract_challenge_signature, hmac_sha256_hex,
        percent_encode_query_component, prepare_runtime_files, provider_config_body,
        resolve_logs_folder, runtime_session_verification_error, sanitize_backend_event,
        sanitize_text_for_webview, sanitize_webview_response, try_ensure_agent_notes_file,
        validate_local_folder_to_open, validate_project_folder_to_open,
        webview2_args_with_accessibility, webview_error_message,
        DesktopAdvancedSettingsUpdateRequest, DesktopDiagnosticsUpdateRequest,
        DESKTOP_AGENT_MESSAGE_TIMEOUT_MS,
    };
    use std::{
        env, fs,
        path::{Path, PathBuf},
        process,
        time::{SystemTime, UNIX_EPOCH},
    };

    fn test_dir(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock should be after Unix epoch")
            .as_nanos();
        env::temp_dir().join(format!("vrcforge-{label}-{}-{nonce}", process::id()))
    }

    fn create_dashboard(root: &Path) {
        let dashboard = root.join("dashboard");
        fs::create_dir_all(&dashboard).expect("dashboard directory should be created");
        fs::write(dashboard.join("index.html"), "test").expect("dashboard index should be created");
    }

    #[test]
    fn agent_notes_creation_creates_missing_parent_directories() {
        let base = test_dir("agent-notes-parent");
        let user_data = base.join("missing").join("nested");

        let path = try_ensure_agent_notes_file(&user_data)
            .expect("optional AGENTS.md should be created when its parent is missing");

        assert_eq!(path, user_data.join("AGENTS.md"));
        assert!(path.is_file());
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn runtime_preparation_ignores_optional_agent_notes_failure() {
        let base = test_dir("optional-agent-notes");
        let root = base.join("app");
        let user_data = base.join("user-data");
        create_dashboard(&root);
        fs::create_dir_all(user_data.join("AGENTS.md"))
            .expect("conflicting AGENTS.md directory should be created");

        let result = prepare_runtime_files(&root, &user_data);

        assert!(result.is_ok(), "optional AGENTS.md failure blocked runtime");
        assert!(user_data.join("config").join("settings.json").is_file());
        assert!(user_data.join("logs").is_dir());
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn runtime_preparation_reports_required_directory_failure() {
        let base = test_dir("required-runtime-dir");
        let root = base.join("app");
        let user_data = base.join("user-data");
        create_dashboard(&root);
        fs::create_dir_all(&user_data).expect("user data directory should be created");
        let logs_path = user_data.join("logs");
        fs::write(&logs_path, "not a directory").expect("logs conflict should be created");

        let error = prepare_runtime_files(&root, &user_data)
            .expect_err("required logs directory failure must block runtime");

        assert!(error.contains(&logs_path.display().to_string()));
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn hmac_sha256_matches_known_vector() {
        // RFC-style known vector: proves the RustCrypto-backed implementation
        // is byte-identical to the previous hand-rolled HMAC.
        assert_eq!(
            hmac_sha256_hex(b"key", b"The quick brown fox jumps over the lazy dog"),
            "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8",
        );
        assert_ne!(
            app_session_challenge_signature("session-token", "nonce-value"),
            app_session_challenge_signature("session-token", "other-nonce"),
        );
    }

    #[test]
    fn session_challenge_signature_match_uses_valid_hex_hmac() {
        let signature = app_session_challenge_signature("session-token", "nonce-value");

        assert!(app_session_challenge_signature_matches(
            "session-token",
            "nonce-value",
            &signature,
        ));
        assert!(app_session_challenge_signature_matches(
            "session-token",
            "nonce-value",
            &signature.to_uppercase(),
        ));
        assert!(!app_session_challenge_signature_matches(
            "session-token",
            "other-nonce",
            &signature,
        ));
        assert!(!app_session_challenge_signature_matches(
            "session-token",
            "nonce-value",
            "not-a-valid-hmac",
        ));
    }

    #[test]
    fn session_challenge_signature_extraction_requires_string_field() {
        assert_eq!(
            extract_challenge_signature(&serde_json::json!({"signature": "abc123"})).as_deref(),
            Some("abc123"),
        );
        assert!(extract_challenge_signature(&serde_json::json!({"signature": 42})).is_none());
        assert!(extract_challenge_signature(&serde_json::json!({})).is_none());
        assert!(extract_challenge_signature(&serde_json::json!(null)).is_none());
    }

    #[test]
    fn runtime_session_verification_error_stays_frontend_detectable() {
        let error = runtime_session_verification_error();

        assert!(error.contains("runtime session verification failed"));
        assert!(error.contains("local runtime was replaced"));
    }

    #[test]
    fn backend_event_sanitizer_only_forwards_safe_desktop_events() {
        let safe = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeRuns",
            "payload": {
                "runs": [],
                "secret": "should-not-reach-webview",
                "state": {"path": "C:\\Users\\Example\\AppData\\Local\\VRCForge"},
                "config": {"apiKey": "hidden"},
                "configPath": "C:\\Users\\Example\\AppData\\Local\\VRCForge\\settings.json",
                "logsDir": "C:\\Users\\Example\\AppData\\Local\\VRCForge\\logs"
            },
            "timestamp": 1.0
        }));
        assert_eq!(
            safe,
            Some(serde_json::json!({"type": "agentRuntimeRuns", "timestamp": 1.0}))
        );

        let delta = sanitize_backend_event(serde_json::json!({
            "type": "agentRuntimeDelta",
            "sessionId": "sess-1",
            "turnId": "turn-1",
            "clientTurnId": "client-1",
            "textDelta": "hello",
            "done": false,
            "secret": "should-not-reach-webview",
            "configPath": "C:\\Users\\Example\\AppData\\Local\\VRCForge\\settings.json"
        }));
        assert_eq!(
            delta,
            Some(serde_json::json!({
                "type": "agentRuntimeDelta",
                "sessionId": "sess-1",
                "turnId": "turn-1",
                "clientTurnId": "client-1",
                "textDelta": "hello",
                "done": false
            }))
        );

        for event_type in ["config", "log", "state", "agentNotesUpdated"] {
            assert!(sanitize_backend_event(serde_json::json!({
                "type": event_type,
                "payload": {"secret": "should-not-reach-webview"}
            }))
            .is_none());
        }

        assert!(sanitize_backend_event(serde_json::json!({"payload": {}})).is_none());
    }

    #[test]
    fn agent_message_timeout_keeps_long_turns_usable() {
        assert!(DESKTOP_AGENT_MESSAGE_TIMEOUT_MS >= 600_000);
    }

    #[test]
    fn webview_accessibility_flag_preserves_existing_arguments() {
        assert_eq!(
            webview2_args_with_accessibility(Some("--remote-debugging-port=9343")),
            "--remote-debugging-port=9343 --force-renderer-accessibility"
        );
        assert_eq!(
            webview2_args_with_accessibility(Some("--force-renderer-accessibility")),
            "--force-renderer-accessibility"
        );
    }

    #[test]
    fn webview_response_sanitizer_removes_secrets_before_webview() {
        let sanitized = sanitize_webview_response(serde_json::json!({
            "configPath": "local-config-path",
            "apiConfig": {
                "provider": "gemini",
                "api_key": "main-secret",
                "apiKeyPresent": true,
                "Authorization": "Bearer main-secret"
            },
            "visionConfig": {
                "provider": "openai",
                "apiKey": "vision-secret",
                "apiKeyPresent": true
            },
            "lastConnectorAction": {
                "backupPath": "Q:/private/AppData/Local/VRCForge/backup.json"
            },
            "clientConfigs": {
                "claudeCode": {
                    "text": "{\"headers\":{\"Authorization\":\"Bearer ${CUSTOM_VRCFORGE_TOKEN}\"}}"
                }
            },
            "effective": {
                "model": "test-model",
                "message": "api_key=main-secret",
                "backupMarker": "backupPath=Q:/private/AppData/Local/VRCForge/backup.json",
                "markerText": "configPath=Q:/private/AppData/Local/VRCForge/config.json",
                "projectPath": "Q:/projects/avatar"
            }
        }));

        assert!(sanitized
            .get("apiConfig")
            .and_then(|value| value.get("Authorization"))
            .is_none());
        let text = sanitized.to_string();
        assert!(!text.contains("main-secret"));
        assert!(!text.contains("vision-secret"));
        assert!(!text.contains("configPath"));
        assert!(!text.contains("backupPath"));
        assert!(!text.contains("Q:/private"));
        assert!(text.contains("[redacted]"));
        assert!(text.contains("Q:/projects/avatar"));
        assert!(text.contains("apiKeyPresent"));
        assert!(text.contains("CUSTOM_VRCFORGE_TOKEN"));
        assert!(text.contains("Bearer"));
    }

    #[test]
    fn webview_text_sanitizer_redacts_error_details() {
        assert_eq!(
            sanitize_text_for_webview(
                "request failed with Authorization: Bearer real-secret and api_key=real-secret"
            ),
            "[redacted]"
        );
        assert_eq!(
            sanitize_text_for_webview("request failed with Bearer real-secret"),
            "[redacted]"
        );
        assert_eq!(
            sanitize_text_for_webview(
                "{\"headers\":{\"Authorization\":\"Bearer ${CUSTOM_VRCFORGE_TOKEN}\"}}"
            ),
            "{\"headers\":{\"Authorization\":\"Bearer ${CUSTOM_VRCFORGE_TOKEN}\"}}"
        );
        assert_eq!(
            webview_error_message(&serde_json::json!({
                "detail": {
                    "message": "Unity MCP bridge is offline",
                    "configPath": "Q:/private/AppData/Local/VRCForge/config.json"
                }
            })),
            "Unity MCP bridge is offline"
        );
        assert_eq!(
            webview_error_message(&serde_json::json!({
                "detail": {
                    "message": "request failed with Bearer real-secret"
                }
            })),
            "[redacted]"
        );
    }

    #[test]
    fn provider_config_body_preserves_omitted_key_semantics() {
        let without_key = provider_config_body(
            "gemini".to_string(),
            None,
            Some("https://example.test".to_string()),
            Some("model-a".to_string()),
        );
        assert!(without_key.get("api_key").is_none());

        let with_blank_key =
            provider_config_body("gemini".to_string(), Some(String::new()), None, None);
        assert_eq!(
            with_blank_key
                .get("api_key")
                .and_then(|value| value.as_str()),
            Some("")
        );
    }

    #[test]
    fn query_component_encoding_handles_windows_paths() {
        assert_eq!(
            percent_encode_query_component(r"D:\VR Projects\Karin FT"),
            "D%3A%5CVR%20Projects%5CKarin%20FT",
        );
        assert_eq!(
            percent_encode_query_component("session_123-abc"),
            "session_123-abc",
        );
    }

    #[test]
    fn diagnostics_update_body_preserves_independent_optional_fields() {
        assert_eq!(
            diagnostics_update_body(Some("DEBUG".to_string()), None),
            serde_json::json!({"logLevel": "DEBUG"}),
        );
        assert_eq!(
            diagnostics_update_body(None, Some(false)),
            serde_json::json!({"debugLogging": false}),
        );
        assert_eq!(
            diagnostics_update_body(Some("WARNING".to_string()), Some(true)),
            serde_json::json!({"logLevel": "WARNING", "debugLogging": true}),
        );

        let _: DesktopDiagnosticsUpdateRequest =
            serde_json::from_value(serde_json::json!({"logLevel": "INFO"}))
                .expect("log-level-only requests must remain valid");
        let _: DesktopDiagnosticsUpdateRequest =
            serde_json::from_value(serde_json::json!({"debugLogging": true}))
                .expect("legacy debug-logging-only requests must remain valid");
    }

    #[test]
    fn diagnostics_commands_run_backend_io_off_the_ui_thread() {
        let source = include_str!("commands.rs");
        for signature in [
            "pub async fn fetch_diagnostics(",
            "pub async fn update_diagnostics(",
            "pub async fn export_support_bundle(",
        ] {
            assert!(
                source.contains(signature),
                "diagnostics backend I/O must stay asynchronous"
            );
        }
    }

    #[test]
    fn advanced_settings_body_only_adds_challenge_when_present() {
        assert_eq!(
            advanced_settings_update_body(true, false, Some("challenge-123".to_string())),
            serde_json::json!({
                "developerOptionsEnabled": true,
                "computerUseEnabled": false,
                "developerChallengeId": "challenge-123",
            }),
        );
        assert_eq!(
            advanced_settings_update_body(false, false, None),
            serde_json::json!({
                "developerOptionsEnabled": false,
                "computerUseEnabled": false,
            }),
        );

        let _: DesktopAdvancedSettingsUpdateRequest = serde_json::from_value(serde_json::json!({
            "developerOptionsEnabled": false,
            "computerUseEnabled": false,
        }))
        .expect("existing advanced-settings requests must not require a challenge ID");
    }

    #[test]
    fn developer_challenge_path_percent_encodes_opaque_ids() {
        assert_eq!(
            developer_options_challenge_path("challenge/with ?#")
                .expect("opaque challenge ID should be accepted"),
            "/api/app/advanced-settings/developer-challenge/challenge%2Fwith%20%3F%23",
        );
        assert!(developer_options_challenge_path("").is_err());
        assert!(developer_options_challenge_path(&"x".repeat(513)).is_err());
    }

    #[test]
    fn packaged_backend_launch_discards_raw_standard_streams() {
        let source = include_str!("backend.rs");
        for forbidden in [
            ["backend", "_stdout", ".log"].concat(),
            ["backend", "_stderr", ".log"].concat(),
            ["Stdio", "::from("].concat(),
        ] {
            assert!(
                !source.contains(&forbidden),
                "packaged backend launch must not use raw file redirection"
            );
        }
        assert!(source.contains(".stdout(Stdio::null())"));
        assert!(source.contains(".stderr(Stdio::null())"));
    }

    #[test]
    fn open_folder_validation_rejects_empty_and_relative_paths() {
        assert!(validate_project_folder_to_open("").is_err());
        assert!(validate_project_folder_to_open("Documents").is_err());
    }

    #[test]
    fn open_folder_validation_requires_unity_project_root() {
        let base = test_dir("open-folder-validation");
        let not_project = base.join("not-project");
        fs::create_dir_all(&not_project).expect("test directory should be created");

        let error = validate_project_folder_to_open(&not_project.display().to_string())
            .expect_err("non-project folder must be rejected");
        assert!(error.contains("Not a Unity project root"));

        let project = base.join("unity-project");
        fs::create_dir_all(project.join("Assets")).expect("Assets directory should be created");
        fs::create_dir_all(project.join("Packages")).expect("Packages directory should be created");
        fs::create_dir_all(project.join("ProjectSettings"))
            .expect("ProjectSettings directory should be created");

        let resolved = validate_project_folder_to_open(&project.display().to_string())
            .expect("Unity project root should be accepted");
        assert!(resolved.ends_with("unity-project"));
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn local_folder_validation_accepts_non_project_directory() {
        let base = test_dir("local-folder-validation");
        let folder = base.join("archives");
        fs::create_dir_all(&folder).expect("archive directory should be created");

        let resolved = validate_local_folder_to_open(&folder.display().to_string())
            .expect("non-project archive directory should be accepted");

        assert_eq!(resolved, folder.canonicalize().unwrap());
        assert!(validate_local_folder_to_open("").is_err());
        assert!(validate_local_folder_to_open("relative\\archives").is_err());
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn logs_folder_resolution_is_internal_and_does_not_launch_shell() {
        let base = test_dir("logs-folder-resolution");
        let user_data = base.join("nested").join("user-data");

        let resolved = resolve_logs_folder(&user_data)
            .expect("logs folder should be created below the user data root");

        assert!(resolved.is_dir());
        assert_eq!(resolved, user_data.join("logs").canonicalize().unwrap());
        assert!(resolved.starts_with(user_data.canonicalize().unwrap()));
        let _ = fs::remove_dir_all(base);
    }

    #[test]
    fn logs_folder_resolution_errors_do_not_expose_local_paths() {
        let base = test_dir("logs-folder-error");
        let user_data = base.join("user-data");
        fs::create_dir_all(&user_data).expect("user data directory should be created");
        fs::write(user_data.join("logs"), "not a directory")
            .expect("logs path conflict should be created");

        let error = resolve_logs_folder(&user_data)
            .expect_err("a file at the logs path must block folder resolution");

        assert_eq!(error, "Unable to prepare the logs folder.");
        assert!(!error.contains(&base.display().to_string()));
        let _ = fs::remove_dir_all(base);
    }
}
