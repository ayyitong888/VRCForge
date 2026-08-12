from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_packaged_skill_probe_observes_projected_write_skills_in_execution_layer() -> None:
    source = (
        ROOT / "scripts" / "diagnose_packaged_skill_ecosystem.mjs"
    ).read_text(encoding="utf-8")

    assert 'agentApi("/api/agent/skills")' not in source
    assert 'agentApi("/api/agent/skills",' not in source
    assert source.count(
        'agentApi("/api/agent/skills?exposure_layer=execution"'
    ) == 8


def test_agent_skill_route_remains_default_planning_for_product_callers() -> None:
    source = (ROOT / "dashboard_server.py").read_text(encoding="utf-8")

    assert (
        'exposure_layer: Literal["planning", "execution"] = "planning"'
        in source
    )
    assert "AGENT_GATEWAY.skills.build_skill_registry(exposure_layer=exposure_layer)" in source


def test_packaged_skill_probe_keeps_approval_packages_request_only() -> None:
    source = (
        ROOT / "scripts" / "diagnose_packaged_skill_ecosystem.mjs"
    ).read_text(encoding="utf-8")

    entrypoint_block = source.split(
        "const requiredPackageEntrypoints = new Map([", 1
    )[1].split("]);", 1)[0]
    request_only_block = source.split(
        "const requestOnlyPackageSlugs = new Set([", 1
    )[1].split("]);", 1)[0]
    assert '"outfit-naming-helper"' not in entrypoint_block
    assert '"material-preset-pack"' in request_only_block
    assert '"outfit-naming-helper"' in request_only_block
    assert '"vrcforge_unity_mcp_write"' in source
    assert '"vrc_atomic_reference_rename"' in source
    assert '"post-safe-mode request-only outfit package"' in source


def test_packaged_skill_probe_uses_explicit_app_quit_for_normal_shutdown() -> None:
    source = (
        ROOT / "scripts" / "diagnose_packaged_skill_ecosystem.mjs"
    ).read_text(encoding="utf-8")

    close_start = source.index("async function closePackagedApp")
    close_end = source.index("async function waitForFileJson", close_start)
    close = source[close_start:close_end]
    assert "await assertOwnedCdpListener()" in close
    assert "requestPackagedAppQuit(launch.cdp)" in close
    assert close.index("requestPackagedAppQuit") < close.index("waitForPackagedClear(30000)")
    assert "quit.accepted && cleared.ok" in close
    assert close.index("waitForPackagedClear(30000)") < close.index("forceCloseLaunch")

    launch_start = source.index("async function launchPackagedApp")
    launch_end = source.index("async function readAppToken", launch_start)
    launch = source[launch_start:launch_end]
    assert launch.count("await assertOwnedCdpListener()") >= 2
    assert launch.index("await assertOwnedCdpListener()") < launch.index("connectCdp(page.webSocketDebuggerUrl)")
    assert "cdpListenerOwnershipRejectsForeignRoot" in source
    assert "launch.childProcess?.kill" not in source
    assert "no unverified process was terminated" in source
    assert "CloseMainWindow" not in close
    assert "requestManagedBackendShutdown" not in close


def test_packaged_skill_probe_seeds_current_mcp2_fixture_and_waits_for_audit_rows() -> None:
    source = (
        ROOT / "scripts" / "diagnose_packaged_skill_ecosystem.mjs"
    ).read_text(encoding="utf-8")

    assert "requiredUnityMcpFixtureRelativePaths" in source
    for required in (
        "VRCForgeCommandAttribute.cs",
        "VRCForgeInputAttribute.cs",
        "VRCForgeToolRegistry.cs",
        "VRCForgeToolResult.cs",
        "VRCForgeMcpCoreServer.cs",
    ):
        assert required in source
    assert "auditRowsReady" in source
    assert "rowElements().length === 10" in source
    assert "eventValues().length === expectedGovernanceRows.length" in source


def test_packaged_skill_probe_uses_authenticated_loopback_provider_after_local_planner_removal() -> None:
    source = (
        ROOT / "scripts" / "diagnose_packaged_skill_ecosystem.mjs"
    ).read_text(encoding="utf-8")

    assert 'from "node:http"' in source
    assert "createAuthenticatedLoopbackPlannerProvider" in source
    assert 'server.listen(0, "127.0.0.1"' in source
    assert 'authorization !== `Bearer ${plannerProviderApiKey}`' in source
    assert "beginToolSelection(skillName, effectiveParams)" in source
    assert 'action: "enter_execution"' in source
    assert 'action: "skill"' in source
    assert 'action: "reply"' in source
    assert 'provider: "custom"' in source
    assert 'api_type: "chat_completions"' in source
    assert "await plannerProvider.close()" in source


def test_contextual_path_to_skill_runtime_owns_a_provider_selection() -> None:
    source = (
        ROOT / "scripts" / "diagnose_packaged_skill_ecosystem.mjs"
    ).read_text(encoding="utf-8")

    start = source.index("async function invokeContextualReadinessRuntime")
    end = source.index("async function exerciseContextualPathToSkillUi", start)
    contextual_runtime = source[start:end]

    assert 'plannerProvider.beginToolSelection("vrcforge_build_test_readiness"' in contextual_runtime
    assert "plannerProvider.finishToolSelection(selection)" in contextual_runtime
    assert contextual_runtime.index("beginToolSelection") < contextual_runtime.index(
        'appApi("/api/app/agent/message"'
    )
    assert contextual_runtime.index('appApi("/api/app/agent/message"') < contextual_runtime.index(
        "finishToolSelection"
    )
