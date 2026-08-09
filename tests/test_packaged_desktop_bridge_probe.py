from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_packaged_desktop_bridge.mjs"


def test_desktop_probe_has_exact_strict_and_local_package_binding() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'const allowUnpushed = process.argv.includes("--allow-unpushed");' in source
    assert 'const selfTest = process.argv.includes("--self-test");' in source
    assert 'new Set(["--allow-unpushed", "--self-test", "--help", "-h"])' in source
    assert "Unknown packaged Desktop/Computer Use probe option." in source
    assert "isStrictBuildPolicy" in source
    assert "isLocalAcceptanceBuildPolicy" in source
    assert 'policy.mode === "local-acceptance"' in source
    assert "policy.releaseEligible === false" in source
    assert "policy.allowDirty === false" in source
    assert "policy.allowUnpushed === true" in source
    assert "!worktreeClean || !localAcceptanceBuildPolicy" in source
    assert "manifestCommit !== headCommit" in source
    assert "Release manifest version" in source
    assert "Portable package digest did not match release-manifest.json" in source
    assert "if ($main.Count -ne 1)" in source
    assert "if ($backend.Count -ne 1)" in source
    assert "innerExeSha256" in source
    assert "extractedExeSha256" in source
    assert "innerBackendSha256" in source
    assert "extractedBackendSha256" in source
    assert "Extracted package executables did not match their manifest-bound ZIP entries." in source
    assert 'mode: allowUnpushed ? "local-preacceptance" : "strict-release"' in source
    assert 'strictReleaseBinding: false' in source
    assert 'releaseEligible: false' in source
    assert "non-release local-preacceptance only" in source


def test_desktop_probe_isolates_runtime_credentials_and_owned_lifetimes() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '!key.toUpperCase().startsWith("VRCFORGE_")' in source
    assert "inheritedEnvironmentIsSensitive" in source
    assert '"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"' in source
    assert "OPENAI|ANTHROPIC|GOOGLE|GEMINI|DEEPSEEK|OPENROUTER|XAI|OLLAMA" in source
    assert 'VRCFORGE_USER_DATA_DIR: userDataRoot' in source
    assert 'VRCFORGE_CONFIG_DIR: configRoot' in source
    assert 'VRCFORGE_CONFIG_PATH: resolve(configRoot, "config.json")' in source
    assert 'VRCFORGE_SETTINGS_PATH: resolve(configRoot, "settings.json")' in source
    assert 'VRCFORGE_LOG_DIR: resolve(userDataRoot, "logs")' in source
    assert 'VRCFORGE_ARTIFACTS_DIR: resolve(userDataRoot, "artifacts")' in source
    assert 'VRCFORGE_DESKTOP_EXECUTOR: "1"' in source
    assert "APPDATA: hostProfileRoot" in source
    assert "LOCALAPPDATA: hostProfileRoot" in source
    assert "WEBVIEW2_USER_DATA_FOLDER: webviewDataRoot" in source
    assert 'const tokenPath = resolve(configRoot, "app-session-token");' in source
    assert "const desktopActionLedger = resolve(\n      userDataRoot," in source
    assert "env: isolatedLaunchEnvironment()" in source
    assert "...process.env" not in source

    assert "Preflight found an existing VRCForge process or occupied backend/CDP port; nothing was terminated" in source
    assert "Launch preflight found an existing VRCForge process or occupied backend/CDP port; nothing was terminated" in source
    assert "$_ .LocalPort" not in source
    assert "$_.LocalPort -eq 8757 -or $_.LocalPort -eq ${port}" in source
    assert "async function forceCloseTrackedLaunch(identity)" in source
    assert "identity?.id" in source
    assert "identity?.startedAt" in source
    assert "$rootStart.Equals($expectedStart" in source
    assert "forceCloseTrackedLaunch(packagedProcessIdentity)" in source
    assert "$ids.Contains([int]$candidate.ParentProcessId)" in source
    assert "$path.Equals($exe, [StringComparison]::OrdinalIgnoreCase)" in source
    assert "$path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)" in source
    assert "process.kill(child.pid)" not in source
    assert source.count("process.kill(") == 1
    assert "child?.pid && snapshotHasResidue" in source
    assert "embedded Desktop worker is owned by the packaged backend lifetime" in source
    assert "stopTrackedExternalProcess(notepadProcessIdentity)" in source
    assert "stopTrackedExternalProcess(uiaFixtureProcessIdentity)" in source
    assert "protectedSecrets.add(appSessionToken)" in source
    assert 'join("<redacted-probe-secret>")' in source
    assert "scanTreeForProtectedSecrets(resolve(userDataRoot, \"logs\"))" in source
    assert "scanTreeForProtectedSecrets(resolve(userDataRoot, \"artifacts\"))" in source
    assert "the in-memory probe report contained the exact app-session token before final redaction" in source
    assert "the exact app-session token leaked into isolated logs or audit artifacts" in source
    assert "const selfTestSecret = \"desktop-probe-self-test-secret\";" in source
    assert "containsProtectedSecret(sanitized)" in source


def test_desktop_probe_fails_closed_before_input_and_binds_each_input_step() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "const notepadLaunchStartedAt = Date.now();" in source
    assert "notepadProcessIdentity = await processIdentity(launchedAppPid);" in source
    assert '.toLowerCase().endsWith("\\\\notepad.exe")' in source
    assert "Notepad ownership was not proven; no desktop input was attempted." in source
    assert "const fixtureLaunchStartedAt = Date.now();" in source
    assert "uiaFixtureProcessIdentity = await processIdentity(launchedFixturePid);" in source
    assert "fixtureExePath.toLowerCase()" in source
    assert "Native fixture ownership was not proven; no fixture input was attempted." in source

    for operation in ("type", "press_key", "type_text", "drag", "scroll"):
        assert f'{{ operation: "{operation}", window: notepadTarget' in source
    assert 'operation: "click",\n              window: notepadTarget,' in source
    assert 'operation: "set_value",\n                window: fixtureTarget,' in source
    assert 'operation: "click",\n                window: fixtureTarget,' in source
    assert 'operation: "perform_secondary_action",\n                window: fixtureTarget,' in source
    assert 'key: "Win+r"' in source
    assert source.index("Notepad ownership was not proven; no desktop input was attempted.") < source.index(
        'prompt: "Controlled packaged Notepad input proof"'
    )
    assert source.index("Native fixture ownership was not proven; no fixture input was attempted.") < source.index(
        'prompt: "Bring the native fixture over Notepad before passive capture"'
    )


def test_desktop_probe_completes_isolated_first_run_before_waiting_for_the_composer() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "async function prepareComposerAfterFirstRun(cdp, timeoutMs = 30000)" in source
    assert "[data-vrcforge-onboarding-language-gate='true']" in source
    assert "button[data-vrcforge-onboarding-language-option][aria-pressed='true']" in source
    assert "button[data-vrcforge-onboarding-language-continue]" in source
    assert "[data-vrcforge-onboarding='true']" in source
    assert "button[data-vrcforge-onboarding-skip]" in source
    assert 'actions.push("language-continue")' in source
    assert 'actions.push("onboarding-skip")' in source
    assert "function composerIsReadyAfterOnboarding(state)" in source
    assert "state?.composerReady && !state.languageGate && !state.onboarding" in source
    assert "if (composerIsReadyAfterOnboarding(lastState))" in source
    assert "composer readiness bypassed an active first-run overlay" in source
    assert "composerReady: Boolean(textarea && submit)" in source
    assert "composerDisabled: Boolean(textarea?.disabled)" in source
    assert "composerDisabled: lastState.composerDisabled" in source
    assert "output.ready = await prepareComposerAfterFirstRun(cdp);" in source
    assert source.index("output.ready = await prepareComposerAfterFirstRun(cdp);") < source.index(
        'output.bootstrap = await appApi("/api/app/bootstrap")'
    )


def test_desktop_probe_owns_an_authenticated_fake_provider_before_real_composer_use() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'import { createServer } from "node:http";' in source
    assert 'server.listen(0, "127.0.0.1", resolveListen)' in source
    assert 'String(request?.headers?.authorization || "") === `Bearer ${token}`' in source
    assert "fakeProviderRequestIsAuthorized(request, token)" in source
    assert 'response.writeHead(401, { "Content-Type": "application/json" })' in source
    assert 'provider: "custom"' in source
    assert 'base_url: `http://127.0.0.1:${fakeProviderPort}/v1`' in source
    assert 'model: "vrcforge-desktop-probe"' in source
    assert 'const send = document.querySelector("[data-composer-send]");' in source
    assert 'textarea instanceof HTMLTextAreaElement && !textarea.disabled' in source
    assert 'send instanceof HTMLButtonElement,' in source
    assert 'output.providerComposerReady.sendDisabled !== true' in source
    assert 'the empty Provider-backed composer unexpectedly enabled submission without input' in source
    assert "waitForFakeProviderRequest(fakeProvider, `${marker} frontend gate probe`)" in source
    assert "waitForFakeProviderCancellation(observedProviderRequest)" in source
    assert "currentUserMarkerObserved" in source
    assert "observedProviderRequest.authorized === true" in source
    assert "!observedProviderRequest.providerFinished && !observedProviderRequest.responseClosed" in source
    assert "the fake Provider request completed before the real Stop path was exercised" in source
    assert "output.providerCancellationAfterStop.providerFinished" in source
    assert "real composer Stop did not cancel the still-pending fake Provider request" in source
    assert "server.closeAllConnections?.()" in source
    assert "await fakeProvider.close()" in source
    assert "proveLoopbackPortReleased(fakeProviderPort)" in source
    assert 'protectedSecrets.add(fakeProviderToken)' in source
    first_run_ready = source.index("output.ready = await prepareComposerAfterFirstRun(cdp);")
    production_provider = source.index('fakeProvider = createFakeProvider(fakeProviderToken);', first_run_ready)
    assert first_run_ready < production_provider
    assert source.index("output.providerComposerReady = await waitForEval(") < source.index(
        "output.frontendDesktopGateSetup = await evalValue("
    )
    assert source.index("output.frontendDesktopGateClick = await evalValue(") < source.index(
        "waitForFakeProviderRequest(fakeProvider, `${marker} frontend gate probe`)"
    )


def test_desktop_probe_self_test_and_unknown_option_are_side_effect_free() -> None:
    self_test = subprocess.run(
        ["node", str(SCRIPT), "--self-test"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert self_test.returncode == 0, self_test.stderr
    assert "Desktop/Computer Use probe self-test passed" in self_test.stdout

    unknown = subprocess.run(
        ["node", str(SCRIPT), "--unexpected-option"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert unknown.returncode == 2
    assert "Unknown packaged Desktop/Computer Use probe option." in unknown.stderr

    source = SCRIPT.read_text(encoding="utf-8")
    self_test_body = source[
        source.index("function runSelfTest()") : source.index("if (selfTest)")
    ]
    assert "createFakeProvider(" not in self_test_body
    assert ".listen(" not in self_test_body
    assert "fetch(" not in self_test_body
    assert source.index("if (selfTest)") < source.index("async function prepareManifestBoundPackage")
    assert source.index("if (selfTest)") < source.index("async function main()")
