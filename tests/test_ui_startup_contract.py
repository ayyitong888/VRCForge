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


def test_project_chat_uses_a_lazy_workbench_while_quick_chat_keeps_the_status_surface() -> None:
    source = _read("src/components/runtime/runtime-sidebar.tsx")
    workbench = _read("src/components/runtime/project-workbench-sections.tsx")
    app = _read("src/App.tsx")

    for required in (
        'data-vrcforge-environment-status',
        'data-vrcforge-project-workbench',
    ):
        assert required in source

    for required in (
        'title={t("workspace.todo")}',
        'title={t("workspace.subAgents")}',
        'title={t("workspace.environment")}',
        'title={t("workspace.userAttachmentSources")}',
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
        "data-vrcforge-project-workbench-activity",
    ):
        assert required in workbench

    for deliberately_still_excluded_from_the_lazy_rail in (
        "RuntimeRunRow",
        "RuntimeReviewEvidenceRow",
        "RuntimeFileReferenceRow",
        "RuntimeDiffFileRow",
        "RuntimeScheduleRow",
        't("workspace.runLedger")',
        't("workspace.desktopActions")',
        't("workspace.reviewEvidence")',
        't("workspace.filesSeen")',
        "providerComponent",
        "providerCompactLabel",
        "fetch(",
        "useRuntimeWorkspace",
    ):
        assert deliberately_still_excluded_from_the_lazy_rail not in source

    assert "authoritativeSelectedProjectPath" in app
    assert "workspaceProjectLabel = authoritativeSelectedProjectPath" in app
    assert 'const AsyncRightRuntimeSidebar = lazy' in app
    assert 'const projectChatWorkspace = activeView === "chat" && Boolean(activeChat?.projectPath)' in app
    assert "activityPanel={projectChatWorkspace ? undefined : runtimeActivityPanel}" in app
    assert "projectWorkspace={projectChatWorkspace}" in app
    assert "subAgentPanel={projectChatWorkspace ? subAgentActivityPanel : undefined}" in app
    assert "subAgentTaskCount={activeSubAgentTasks.length}" in app
    assert "userAttachmentSources={userAttachmentSources}" in app
    assert 'const backendStatus = backendComponent?.status || "unknown"' in source
    assert 'runtimeConnected ? t("workspace.online") : t("workspace.notLoaded")' in source

    assert "subAgentPanel={projectChatWorkspace ? undefined : subAgentActivityPanel}" in app
    assert "subAgentPanel={projectChatWorkspace ? subAgentActivityPanel : undefined}" in app


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
    assert "bootstrap.approvalsState?.ok !== false" in app
    assert "? bootstrap.approvals ?? []" in app
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


def test_runtime_and_subagent_details_move_to_the_project_workbench_without_duplication() -> None:
    app = _read("src/App.tsx")
    chat = _read("src/components/chat/chat-workspace.tsx")
    activity = _read("src/components/runtime/runtime-activity-panel.tsx")
    sidebar = _read("src/components/runtime/runtime-sidebar.tsx")

    assert "<RuntimeActivityPanel" in app
    assert "<SubAgentPanel" in app
    assert "activityPanel={" in app
    assert "subAgentPanel={" in app
    assert "{activityPanel}" in chat
    assert "{subAgentPanel}" in chat
    assert "{activityPanel}" not in sidebar
    assert "activityPanel={projectChatWorkspace ? undefined : runtimeActivityPanel}" in app
    assert "subAgentPanel={projectChatWorkspace ? undefined : subAgentActivityPanel}" in app
    assert "subAgentPanel={projectChatWorkspace ? subAgentActivityPanel : undefined}" in app
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


def test_startup_paints_static_shell_before_loading_app_and_records_visible_shell() -> None:
    index_source = _read("index.html")
    startup_shell_source = _read("public/vrcforge-startup-shell.js")
    main_source = _read("src/main.tsx")
    app_source = _read("src/App.tsx")
    probe_source = _read("scripts/diagnose_packaged_latency.mjs")

    assert 'const appModule = startupShellPainted.then(() => import("./App"))' in main_source
    assert "Promise.all([initializeI18n(), appModule])" in main_source
    assert "data-vrcforge-startup-shell" in index_source
    assert "Local AI Workbench for VRChat Avatar Editing" in index_source
    assert index_source.index("/vrcforge-startup-shell.js") < index_source.index("/src/main.tsx")
    assert "__vrcforgeStartupShellPaintedPromise" in startup_shell_source
    assert "startupShellPaintedMs" in startup_shell_source
    assert startup_shell_source.count("window.requestAnimationFrame") == 1
    assert "window.setTimeout" in startup_shell_source
    assert "flushSync" not in main_source
    assert main_source.index("startupShellPainted.then") < main_source.index("await Promise.all")
    assert main_source.index("await Promise.all") < main_source.index("ReactDOM.createRoot")
    assert "__vrcforgeStartupShellPaintedPromise" in main_source
    assert "const AsyncAppSidebar = lazy" in app_source
    assert "const AsyncRightRuntimeSidebar = lazy" in app_source
    assert "const AsyncSubAgentWorkspaceSurface = lazy" in app_source
    assert 'import { AppSidebar }' not in app_source
    assert 'import { RightRuntimeSidebar }' not in app_source
    assert 'import { SubAgentWorkspaceSurface }' not in app_source
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


def test_app_bootstrap_defers_heavy_catalog_and_hydrates_skills_asynchronously() -> None:
    app = _read("src/App.tsx")
    api = _read("src/lib/api/app.ts")
    skills = _read("src/hooks/use-skills-workspace-controller.ts")
    commands = _read("src-tauri/src/commands.rs")

    startup_refresh = app[
        app.index("async function refreshStartupWithMetrics") : app.index("function resolveBackendReady")
    ]
    assert "await refreshWithRetry(target);" in startup_refresh
    assert "refreshWithRetry(target, options)" not in startup_refresh
    assert "void refreshProjectList(target, { allowDuringStartup: true });" in startup_refresh
    assert 'fetchBootstrap(target, { deferAgentCatalog: true })' in app
    assert "bootstrap?.agentManifest?.skills ?? []" in app
    assert "const bootstrapRequestSequenceRef = useRef(0)" in app
    assert "const bootstrapForegroundRequestRef = useRef(0)" in app
    assert app.count("const sequence = ++bootstrapRequestSequenceRef.current") >= 2
    assert app.count("sequence !== bootstrapRequestSequenceRef.current") >= 2
    refresh_start = app.index("async function refresh(target = endpoint")
    silent_start = app.index("async function refreshSilently", refresh_start)
    refresh_body = app[refresh_start:silent_start]
    assert "bootstrapForegroundRequestRef.current = sequence" in refresh_body
    assert "bootstrapForegroundRequestRef.current === sequence" in refresh_body
    silent_end = app.index("async function refreshFullHealth", silent_start)
    silent_body = app[silent_start:silent_end]
    assert silent_body.index("if (bootstrapForegroundRequestRef.current !== 0)") < silent_body.index(
        "fetchBootstrap(target"
    )
    assert "deferAgentCatalog: Boolean(options.deferAgentCatalog)" in api
    assert 'url.searchParams.set("deferAgentCatalog", "true")' in api
    assert "defer_agent_catalog: Option<bool>" in commands
    assert 'query.push("deferAgentCatalog=true")' in commands
    assert "useEffect(() =>" in skills
    assert "SKILL_REGISTRY_BACKGROUND_REFRESH_MS = 30_000" in skills
    assert "const refreshRegistry = () =>" in skills
    assert "void fetchSkills(endpoint)" in skills
    assert "setSkillRegistry(payload)" in skills
    assert "window.setInterval(refreshRegistry, SKILL_REGISTRY_BACKGROUND_REFRESH_MS)" in skills
    assert "new LatestForegroundRequestGate()" in skills
    assert "skillRegistryRequestGateRef.current.beginBackground()" in skills
    assert skills.count("skillRegistryRequestGateRef.current.beginForeground()") == 2
    assert skills.count("skillRegistryRequestGateRef.current.beginAuthoritative()") == 5
    assert skills.count("skillRegistryRequestGateRef.current.commitAuthoritative(registryToken)") == 5
    assert "skillRegistryRequestGateRef.current.isCurrent" in skills
    assert "skillRegistryRequestGateRef.current.endForeground" in skills
    assert "bootstrap.approvalsState?.ok !== false" in app
    project_refresh = app[
        app.index("async function refreshProjectList") : app.index("async function refreshWithRetry")
    ]
    assert "projectRefreshInFlightRef.current" in project_refresh
    assert "!options.allowDuringStartup" in project_refresh


def test_native_setup_starts_the_owned_backend_before_webview_hydration() -> None:
    main_rs = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    backend_rs = (ROOT / "src-tauri" / "src" / "backend.rs").read_text(encoding="utf-8")
    helper = main_rs[
        main_rs.index("fn start_managed_backend_early") : main_rs.index("fn main()")
    ]
    setup = main_rs[main_rs.index(".setup(|app|") : main_rs.index(".invoke_handler(")]
    start_command = backend_rs[
        backend_rs.index("pub fn start_backend(") : backend_rs.index("pub(crate) fn begin_backend_start")
    ]

    assert "app.state::<BackendState>()" in helper
    assert "begin_backend_start(&state)?" in helper
    assert "thread::spawn(move || run_backend_start_worker(app_handle))" in helper
    assert setup.index("start_managed_backend_early(app.handle())") < setup.index(
        'app.get_webview_window("main")'
    )
    assert start_command.index("ready_backend_start_result(") < start_command.index(
        "begin_backend_start(&state)?"
    )
    assert "backend_session_verify_cache_valid()" in start_command


def test_startup_latency_probe_is_manifest_bound_profile_isolated_and_providerless() -> None:
    source = _read("scripts/diagnose_packaged_latency.mjs")

    assert 'args.includes("--startup-only")' in source
    assert 'args.includes("--allow-unpushed")' in source
    assert 'args.indexOf("--profile-root")' in source
    assert 'args.indexOf("--sample")' in source
    assert 'relative(startupProfilesRoot, profileRoot)' in source
    assert 'profileRelative.startsWith("..") || isAbsolute(profileRelative)' in source
    assert 'startupSample === "cold" && profileExistedBefore' in source
    assert 'startupSample === "warm" && !profileExistedBefore' in source
    assert "requireWarmStartupPairMarker(releaseBinding)" in source
    assert "writeColdStartupPairMarker(releaseBinding)" in source
    assert "manifestCommit: releaseBinding.manifestCommit" in source
    assert "portableSha256: releaseBinding.portableSha256" in source
    assert "profilePreparedForWarm: true" in source
    assert "prepareStartupPackage()" in source
    assert "manifestCommit !== headCommit || !worktreeClean" in source
    assert "portableSha256 !== String(portable.sha256).toLowerCase()" in source
    assert "ExtractToDirectory" in source
    assert "startupLaunchEnvironment()" in source
    for key in (
        "VRCFORGE_USER_DATA_DIR",
        "VRCFORGE_CONFIG_DIR",
        "VRCFORGE_CONFIG_PATH",
        "VRCFORGE_SETTINGS_PATH",
        "VRCFORGE_LOG_DIR",
        "VRCFORGE_ARTIFACTS_DIR",
        "APPDATA",
        "LOCALAPPDATA",
        "WEBVIEW2_USER_DATA_FOLDER",
    ):
        assert key in source
    assert '!key.toUpperCase().startsWith("VRCFORGE_")' in source
    assert "inheritedEnvironmentIsSensitive(key)" in source
    startup_branch = source.index("if (startupOnly) {", source.index("const startupMetrics"))
    input_path = source.index("const inputText =")
    assert startup_branch < input_path
    assert "providerRequestCount: providerRequests.length" in source[startup_branch:input_path]
    assert "firstNativeWindowVisible" in source[startup_branch:input_path]
    assert "nativeVisibilityEvidenceOk(nativeWindow)" in source[startup_branch:input_path]
    assert "visibleAtMs: Date.now() - launchedAt" in source
    assert "readFirstRunUiState(cdp)" in source[startup_branch:input_path]
    assert '"first-run-center-surface-under-onboarding"' in source[startup_branch:input_path]
    assert "prepareColdProfileForWarm(cdp, firstRunUiState)" in source[startup_branch:input_path]
    assert "requestPackagedAppQuit(cdp)" in source[startup_branch:input_path]
    assert "async function waitForOwnedCdpListener" in source
    assert "await waitForOwnedCdpListener(trackedLaunchIdentity)" in source
    assert "forcedCleanupUsed: false" in source[startup_branch:input_path]
    assert "providerRequests.length === 0" in source[startup_branch:input_path]
    assert "sidebarMountsRecorded" in source
    assert "firstContentfulPaintRecorded" in source
    assert 'evaluateStartupBudget(startupMetrics, startupSample)' in source[startup_branch:input_path]
    assert 'sample === "cold"' in source
    assert '"startupShellRecorded"' in source
    assert '"cachedBootstrap"' not in source[source.index('sample === "cold"'):source.index(': Object.keys(checks)')]
    startup_report_write = source.index('await writeFile(outPath, `${JSON.stringify(output, null, 2)}\\n`, "utf8")', startup_branch)
    cold_marker_write = source.index("await writeColdStartupPairMarker(releaseBinding)", startup_branch)
    assert startup_report_write < cold_marker_write < input_path


def test_i18n_fallback_and_selected_locale_load_in_parallel() -> None:
    source = _read("src/i18n.ts")

    assert "const fallbackPromise = loadLocaleMessages(DEFAULT_LOCALE);" in source
    assert "const initialPromise = initialLocale === DEFAULT_LOCALE" in source
    assert "Promise.all([fallbackPromise, initialPromise])" in source
