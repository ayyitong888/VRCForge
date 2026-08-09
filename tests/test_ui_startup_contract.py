from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_startup_restores_project_without_activating_historical_chat() -> None:
    source = _read("src/hooks/use-chat-sessions.ts")
    start = source.index("restoredChatPathKeyRef.current = restorePathKey;")
    end = source.index("const reloadChatStorageState", start)
    restore_effect = source[start:end]

    assert "fetchChats<unknown>" in restore_effect
    assert "reconcileFetchedChatStorage" in restore_effect
    assert "setActiveChatId" not in restore_effect
    assert "expandProjectGroup" not in restore_effect


def test_right_sidebar_is_a_status_surface_not_an_activity_history() -> None:
    source = _read("src/components/runtime/runtime-sidebar.tsx")
    app = _read("src/App.tsx")

    for required in (
        'data-vrcforge-environment-status',
        'data-vrcforge-status="project"',
        'data-vrcforge-status="core"',
        'data-vrcforge-status="mcp-core"',
        'data-vrcforge-status="mcp-bridge"',
        'data-vrcforge-status="unity"',
        'data-vrcforge-status="tools"',
        'data-vrcforge-status="approval"',
        "selectedProjectComponent",
        "mcpPackageComponent",
        "unityBridgeComponent",
        "unityInstanceComponent",
        "unityToolsComponent",
        "!approvalsLoaded ? \"unknown\"",
    ):
        assert required in source

    for removed in (
        "RuntimeRunRow",
        "RuntimeReviewEvidenceRow",
        "RuntimeFileReferenceRow",
        "RuntimeDiffFileRow",
        "RuntimeScheduleRow",
        't("workspace.progress")',
        't("workspace.runLedger")',
        't("workspace.desktopActions")',
        't("workspace.reviewEvidence")',
        't("workspace.filesSeen")',
        't("workspace.subAgents")',
        "providerComponent",
        "providerCompactLabel",
    ):
        assert removed not in source

    assert "authoritativeSelectedProjectPath" in app
    assert "workspaceProjectLabel = authoritativeSelectedProjectPath" in app
    assert 'const backendStatus = backendComponent?.status || "unknown"' in source
    assert 'runtimeConnected ? t("workspace.online") : t("workspace.notLoaded")' in source


def test_project_without_a_session_does_not_load_or_poll_historical_runtime_activity() -> None:
    source = _read("src/hooks/use-runtime-workspace.ts")
    app = _read("src/App.tsx")

    assert source.count("!sessionId.trim()") >= 2
    assert "rightSidebarCollapsed" not in source
    assert "if (!requestSessionId.trim())" in source
    no_session = source.index("if (!requestSessionId.trim())")
    fetch_snapshot = source.index("const snapshot = await fetchRuntimeSnapshotOnce", no_session)
    assert no_session < fetch_snapshot
    assert "setAgentApprovals(null)" not in source[no_session:fetch_snapshot]
    assert "setAgentApprovals(null);\n  }, [activeRuntimeProjectPath]" not in app
    assert "setAgentApprovals(runtimeConnected && bootstrap ? bootstrap.approvals ?? [] : null)" in app
    assert "clearScopedRuntimeProjection();" in source
    clear_definition = source.index("function clearScopedRuntimeProjection()")
    clear_end = source.index("useEffect(() =>", clear_definition)
    clear_body = source[clear_definition:clear_end]
    for setter in (
        "setRuntimeRuns([])",
        "setAgentGoals([])",
        "setAgentProgress([])",
        "setAgentQuestions([])",
        "setAgentMemory([])",
    ):
        assert setter in clear_body
    assert "setActiveDesktopActions([])" not in clear_body
    scope_layout = source.index("useLayoutEffect(() =>")
    synchronous_clear = source.index("clearScopedRuntimeProjection();", scope_layout)
    session_guard = source.index("if (!runtimeConnected || !sessionId.trim())", synchronous_clear)
    assert synchronous_clear < session_guard


def test_active_desktop_safety_state_survives_scope_changes_without_loading_history() -> None:
    source = _read("src/hooks/use-runtime-workspace.ts")

    clear_definition = source.index("function clearScopedRuntimeProjection()")
    clear_end = source.index("useEffect(() =>", clear_definition)
    clear_body = source[clear_definition:clear_end]
    assert "setActiveDesktopActions([])" not in clear_body

    scope_layout = source.index("useLayoutEffect(() =>")
    scope_end = source.index("useEffect(() =>", scope_layout)
    scope_body = source[scope_layout:scope_end]
    assert "if (!runtimeConnected)" in scope_body
    assert "setActiveDesktopActions([])" in scope_body
    assert "if (!sessionId.trim())" in scope_body
    assert "refreshActiveDesktopActionState()" in scope_body

    active_refresh = source.index("async function refreshActiveDesktopActionState")
    runtime_refresh = source.index("async function refreshRuntimeRuns", active_refresh)
    active_body = source[active_refresh:runtime_refresh]
    assert "fetchActiveAgentDesktopActions(target)" in active_body
    assert "fetchRuntimeSnapshotOnce" not in active_body

    no_session = source.index("if (!requestSessionId.trim())", runtime_refresh)
    snapshot_fetch = source.index("const snapshot = await fetchRuntimeSnapshotOnce", no_session)
    no_session_body = source[no_session:snapshot_fetch]
    assert "await refreshActiveDesktopActionState(showError, target)" in no_session_body
    assert "setActiveDesktopActions([])" not in no_session_body


def test_runtime_and_subagent_details_are_on_demand_in_the_center_workspace() -> None:
    app = _read("src/App.tsx")
    chat = _read("src/components/chat/chat-workspace.tsx")
    activity = _read("src/components/runtime/runtime-activity-panel.tsx")

    assert "<RuntimeActivityPanel" in app
    assert "<SubAgentPanel" in app
    assert "activityPanel={" in app
    assert "subAgentPanel={" in app
    assert "{activityPanel}" in chat
    assert "{subAgentPanel}" in chat
    assert "data-vrcforge-runtime-activity-panel" in activity
    assert "RuntimeRunRow" in activity


def test_main_window_close_hides_to_tray_while_explicit_quit_stops_backend() -> None:
    source = _read("src-tauri/src/main.rs")
    close_start = source.index(".on_window_event")
    close_end = source.index(".run(tauri::generate_context!())", close_start)
    close_handler = source[close_start:close_end]

    assert "CloseRequested { api, .. }" in close_handler
    assert "api.prevent_close();" in close_handler
    assert "window.hide()" in close_handler
    assert "shutdown_managed_backend" not in close_handler
    assert "app.exit" not in close_handler

    quit_start = source.index('"quit" => {')
    quit_end = source.index("_ => {}", quit_start)
    quit_handler = source[quit_start:quit_end]
    assert "shutdown_and_exit_app(app)" in quit_handler
    prepare_start = source.index("fn prepare_app_quit")
    prepare_end = source.index("fn confirm_app_quit", prepare_start)
    prepare_command = source[prepare_start:prepare_end]
    assert "AppQuitReceipt { accepted: true }" in prepare_command
    assert "shutdown_and_exit_app" not in prepare_command
    confirm_start = prepare_end
    confirm_end = source.index("fn shutdown_and_exit_app", confirm_start)
    confirm_command = source[confirm_start:confirm_end]
    assert "shutdown_and_exit_app(&app)" in confirm_command
    lifecycle_start = source.index("fn shutdown_and_exit_app")
    lifecycle_end = source.index("#[cfg(test)]", lifecycle_start)
    lifecycle = source[lifecycle_start:lifecycle_end]
    assert lifecycle.index("shutdown_managed_backend(app)") < lifecycle.index("app.exit(0)")
    assert "prepare_app_quit," in source
    assert "confirm_app_quit," in source
    quit_commands = source[prepare_start:lifecycle_end]
    assert "Duration::from_millis" not in quit_commands
    assert "thread::sleep" not in quit_commands


def test_startup_loads_app_and_locale_in_parallel_and_records_visible_shell() -> None:
    main_source = _read("src/main.tsx")
    app_source = _read("src/App.tsx")
    probe_source = _read("scripts/diagnose_packaged_latency.mjs")

    assert 'const appModule = import("./App")' in main_source
    assert "Promise.all([initializeI18n(), appModule, startupShellPainted])" in main_source
    assert "<StartupShell />" in main_source
    assert "flushSync(() => root.render(<StartupShell />))" in main_source
    assert "startupShellPaintedMs" in main_source
    assert main_source.count("window.requestAnimationFrame") >= 2
    assert "const AsyncAppSidebar = lazy" in app_source
    assert "const AsyncRightRuntimeSidebar = lazy" in app_source
    assert 'import { AppSidebar }' not in app_source
    assert 'import { RightRuntimeSidebar }' not in app_source
    assert "sidebarsRequestedMs" in app_source
    assert "leftSidebarMountedMs" in app_source
    assert "rightSidebarMountedMs" in app_source
    assert "sidebarsMountedMs" in app_source
    assert "shellCommittedMs" in app_source
    assert "shellPaintedMs" in app_source
    assert "centerUsableMs" in app_source
    assert "sidebarsHydratedMs" in app_source
    assert 'dataset.vrcforgeShell = "ready"' in app_source
    assert 'dataset.vrcforgeCenter = "ready"' in app_source
    assert 'dataset.vrcforgeSidebars = "ready"' in app_source
    assert "window.__vrcforgeStartupMetrics || {}" in probe_source
    assert "performance.timeOrigin" in probe_source
    assert 'performance.getEntriesByType("paint")' in probe_source
    assert "STARTUP_SHELL_BUDGET_MS = 100" in probe_source
    assert "BACKEND_INVOKE_BUDGET_MS = 100" in probe_source
    assert "CACHED_BOOTSTRAP_BUDGET_MS = 100" in probe_source
    assert "metrics.centerUsableMs <= metrics.sidebarsRequestedMs" in probe_source
    assert "metrics.sidebarsRequestedMs <= metrics.sidebarsMountedMs" in probe_source
    assert "process.exitCode = 1" in probe_source


def test_i18n_fallback_and_selected_locale_load_in_parallel() -> None:
    source = _read("src/i18n.ts")

    assert "const fallbackPromise = loadLocaleMessages(DEFAULT_LOCALE);" in source
    assert "const initialPromise = initialLocale === DEFAULT_LOCALE" in source
    assert "Promise.all([fallbackPromise, initialPromise])" in source
