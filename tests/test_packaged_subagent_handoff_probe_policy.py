from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_packaged_subagent_handoff.mjs"


def test_subagent_handoff_probe_has_explicit_local_acceptance_boundary() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'const allowUnpushed = process.argv.includes("--allow-unpushed");' in source
    assert '"--self-test"' in source
    assert "isLocalAcceptanceBuildPolicy" in source
    assert "releaseEligible === false" in source
    assert "policy.allowDirty === false" in source
    assert "!worktreeClean || !localAcceptanceBuildPolicy" in source
    assert "strictReleaseBinding: false" in source
    assert "Unknown packaged sub-agent handoff probe option." in source
    assert "| Select-Object -First 1" not in source
    assert "if ($entry.Count -ne 1)" in source
    assert '!key.toUpperCase().startsWith("VRCFORGE_")' in source
    assert 'VRCFORGE_DESKTOP_EXECUTOR: "0"' in source
    assert "APPDATA: hostProfileRoot" in source
    assert "LOCALAPPDATA: hostProfileRoot" in source
    assert "USERPROFILE: hostProfileRoot" not in source
    assert "HOME: hostProfileRoot" not in source
    assert "proveLoopbackPortReleased" in source
    assert "let providerPort = 0;" in source
    assert "if (report.provider)" in source
    assert "report.provider.portReleased = portReleased" in source
    assert "async function assertNoHostUnityProcesses()" in source
    assert "await assertNoHostUnityProcesses();" in source
    assert "environment-not-isolated" in source
    assert "Timed out waiting for ${url}:" in source
    assert "sourceRevisions: payload.sources || []" in source
    assert "attempt < 2" in source
    assert "attempt === 0 && isChatStoreSnapshotChanged(error)" in source
    assert 'error?.payload?.detail?.code === "chat_store_snapshot_changed"' in source
    assert 'code: "chat_store_recovery_required"' in source
    assert "function sameLaunchIdentity(expected, observed)" in source
    assert "startedAtUtc" in source
    assert "creationDateUtc" in source
    assert "captureLaunchIdentity(child.pid)" in source
    assert "function cdpOwnershipAllowsAction(ownership)" in source
    assert "function closeAuthorizationAction(identityCheck, cdpOwnership)" in source
    assert "closeCalls.quit !== 0 || closeCalls.force !== 1" in source
    assert "waitForOwnedCdpListener(launch)" in source
    assert "$ids.Contains([int]$listeners[0].OwningProcess)" in source
    assert "captureLaunchIdentity(processId, timeoutMs = 5000)" in source
    assert "await sleep(50)" in source
    assert "Tracked packaged PID generation changed; no Quit or forced termination is authorized" in source
    close = source[source.index("async function closePackagedApp(launch)") : source.index("async function waitForJson")]
    assert close.index("validateLaunchIdentity(launch)") < close.index("requestPackagedAppQuit(launch.cdp)")
    assert close.index("inspectCdpListenerOwnership(launch)") < close.index("requestPackagedAppQuit(launch.cdp)")
    assert close.index('if (closeAction === "force-own-root")') < close.index("requestPackagedAppQuit(launch.cdp)")
    assert "forced: true" in close
    assert "evidenceFailure: true" in close
    assert "forceCloseLaunch(launch)" in close
    force = source[source.index("async function forceCloseLaunch(launch)") : source.index("async function closePackagedApp(launch)")]
    assert force.count("Assert-TrackedRootIdentity") == 3
    assert force.rindex("Assert-TrackedRootIdentity") < force.index("Stop-Process -Force")
    launch = source[source.index("async function launchPackagedApp(requireComposerEnabled = true)") : source.index("async function waitForComposer")]
    assert launch.index("waitForOwnedCdpListener(launch)") < launch.index("waitForJson(`http://127.0.0.1:${cdpPort}/json/list`)")
    assert launch.index("waitForJson(`http://127.0.0.1:${cdpPort}/json/list`)") < launch.index("const connectOwnership")
    assert launch.index("const connectOwnership") < launch.index("connectCdp(page.webSocketDebuggerUrl)")


def test_subagent_handoff_probe_self_test_is_side_effect_free() -> None:
    result = subprocess.run(
        ["node", str(SCRIPT), "--self-test"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "sub-agent handoff probe self-test passed" in result.stdout
    assert "PID generation identity was not fail-closed" not in result.stderr
    assert "foreign CDP listener ownership was accepted" not in result.stderr
    assert "did not fail through one owned force cleanup" not in result.stderr


def test_subagent_handoff_probe_rejects_unknown_cli_options_before_package_access() -> None:
    result = subprocess.run(
        ["node", str(SCRIPT), "--unexpected-option"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert "Unknown packaged sub-agent handoff probe option." in result.stderr
