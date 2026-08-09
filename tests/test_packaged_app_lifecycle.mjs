import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import { requestPackagedAppQuit } from "../scripts/lib/packaged_app_lifecycle.mjs";

test("awaits the exact Tauri Quit receipt before app exit", async () => {
  const calls = [];
  const result = await requestPackagedAppQuit({
    async send(method, params) {
      calls.push({ method, params });
      return calls.length === 1
        ? { result: { value: { accepted: true } } }
        : { result: { value: { dispatched: true } } };
    },
  });

  assert.deepEqual(result, {
    accepted: true,
    confirmAttempted: true,
    confirmDispatched: true,
  });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].method, "Runtime.evaluate");
  assert.equal(calls[0].params.returnByValue, true);
  assert.equal(calls[0].params.awaitPromise, true);
  assert.match(calls[0].params.expression, /await invoke\("prepare_app_quit"\)/);
  assert.doesNotMatch(calls[0].params.expression, /token|Stop-Process|\.kill\(/i);
  assert.equal(calls[1].method, "Runtime.evaluate");
  assert.equal(calls[1].params.returnByValue, true);
  assert.equal(calls[1].params.awaitPromise, false);
  assert.match(calls[1].params.expression, /invoke\("confirm_app_quit"\)/);
  assert.doesNotMatch(calls[1].params.expression, /token|Stop-Process|\.kill\(/i);
});

test("keeps the prepare receipt when confirmation closes the CDP target", async () => {
  let calls = 0;
  const result = await requestPackagedAppQuit({
    async send() {
      calls += 1;
      if (calls === 1) return { result: { value: { accepted: true } } };
      throw new Error("Target closed during confirmed Quit");
    },
  });
  assert.equal(calls, 2);
  assert.deepEqual(result, {
    accepted: true,
    confirmAttempted: true,
    confirmDispatched: false,
    confirmResponseLost: true,
    error: "Target closed during confirmed Quit",
  });
});

test("fails closed when confirmation explicitly was not dispatched", async () => {
  let calls = 0;
  const result = await requestPackagedAppQuit({
    async send() {
      calls += 1;
      return calls === 1
        ? { result: { value: { accepted: true } } }
        : { result: { value: { dispatched: false, error: "invoke disappeared" } } };
    },
  });
  assert.deepEqual(result, {
    accepted: false,
    confirmAttempted: true,
    confirmDispatched: false,
    error: "invoke disappeared",
  });
});

test("bounds a CDP adapter that never settles during Quit", async () => {
  const realSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (callback, _delay, ...args) => realSetTimeout(callback, 1, ...args);
  try {
    const prepareTimeout = await requestPackagedAppQuit({
      async send() { return new Promise(() => {}); },
    });
    assert.equal(prepareTimeout.accepted, false);
    assert.match(prepareTimeout.error, /prepare_app_quit timed out/);

    let calls = 0;
    const confirmTimeout = await requestPackagedAppQuit({
      async send() {
        calls += 1;
        return calls === 1
          ? { result: { value: { accepted: true } } }
          : new Promise(() => {});
      },
    });
    assert.equal(calls, 2);
    assert.equal(confirmTimeout.accepted, true);
    assert.equal(confirmTimeout.confirmAttempted, true);
    assert.equal(confirmTimeout.confirmResponseLost, true);
    assert.match(confirmTimeout.error, /confirm_app_quit timed out/);
  } finally {
    globalThis.setTimeout = realSetTimeout;
  }
});

test("fails closed when the packaged WebView cannot accept Quit", async () => {
  const unavailable = await requestPackagedAppQuit({
    async send() {
      return { result: { value: { accepted: false, error: "Tauri invoke is unavailable." } } };
    },
  });
  assert.deepEqual(unavailable, {
    accepted: false,
    error: "Tauri invoke is unavailable.",
  });

  const malformed = await requestPackagedAppQuit({ async send() { return {}; } });
  assert.equal(malformed.accepted, false);
  assert.match(malformed.error, /did not accept/);

  const rejected = await requestPackagedAppQuit({
    async send() {
      throw new Error("CDP target closed before evaluation");
    },
  });
  assert.deepEqual(rejected, {
    accepted: false,
    error: "CDP target closed before evaluation",
  });

  const missing = await requestPackagedAppQuit(null);
  assert.deepEqual(missing, {
    accepted: false,
    error: "CDP Runtime transport is unavailable.",
  });
});

test("ordinary packaged probes use explicit Quit and reserve window close for tray proof", async () => {
  const repoRoot = resolve(import.meta.dirname, "..");
  const ordinary = [
    "diagnose_packaged_context_compaction.mjs",
    "diagnose_packaged_goal_delivery.mjs",
    "diagnose_packaged_latency.mjs",
    "diagnose_packaged_logging.mjs",
    "diagnose_packaged_memory_restart.mjs",
    "diagnose_packaged_progress_questions.mjs",
    "diagnose_packaged_skill_ecosystem.mjs",
    "diagnose_packaged_subagent_handoff.mjs",
    "diagnose_packaged_skill_ecosystem.mjs",
    "diagnose_packaged_workflows.mjs",
  ];
  for (const name of ordinary) {
    const source = await readFile(resolve(repoRoot, "scripts", name), "utf8");
    assert.match(source, /requestPackagedAppQuit/);
    assert.doesNotMatch(source, /CloseMainWindow/);
  }
  const ownershipBound = [
    "diagnose_packaged_context_compaction.mjs",
    "diagnose_packaged_desktop_bridge.mjs",
    "diagnose_packaged_goal_delivery.mjs",
    "diagnose_packaged_latency.mjs",
    "diagnose_packaged_memory_restart.mjs",
    "diagnose_packaged_progress_questions.mjs",
    "diagnose_packaged_subagent_handoff.mjs",
    "diagnose_packaged_workflows.mjs",
  ];
  for (const name of ownershipBound) {
    const source = await readFile(resolve(repoRoot, "scripts", name), "utf8");
    assert.match(source, /listenerOwnedByLaunch|inspectCdpListenerOwnership|assertOwnedCdpListener/);
    assert.match(source, /OwningProcess/);
    assert.match(source, /CDP listener (?:was not owned by|changed owner)/);
  }
  const context = await readFile(resolve(repoRoot, "scripts", "diagnose_packaged_context_compaction.mjs"), "utf8");
  const launchStart = context.indexOf("async function launch()");
  const spawnStart = context.indexOf("spawn(exe", launchStart);
  assert.ok(launchStart >= 0 && spawnStart > launchStart);
  assert.ok(context.indexOf("await assertProbePreflightClear();", launchStart) < spawnStart);
  const preflightStart = context.indexOf("async function assertProbePreflightClear()");
  const preflightEnd = context.indexOf("async function waitClear", preflightStart);
  const preflight = context.slice(preflightStart, preflightEnd);
  assert.match(preflight, /requireClearLaunchSnapshot\(await snapshot\(\)\)/);
  assert.doesNotMatch(preflight, /requestPackagedAppQuit|Stop-Process|\.kill\(/);
  assert.match(context, /nothing was terminated/);
  assert.match(context, /identity = await captureLaunchIdentity\(child\.pid\)/);
  assert.match(context, /launchIdentityValuesMatch/);
  assert.match(context, /startedAtUtc/);
  assert.match(context, /different generation was accepted/);
  assert.match(context, /listenerOwnedByLaunch\(identity, cdpPort\)/);
  assert.match(context, /CDP listener was not owned by the captured launch generation/);
  const contextQuitStart = context.indexOf("async function requestQuit");
  const contextQuitEnd = context.indexOf("function createLoopbackProvider", contextQuitStart);
  const contextQuit = context.slice(contextQuitStart, contextQuitEnd);
  assert.ok(contextQuit.indexOf("await launchIdentityMatches") < contextQuit.indexOf("requestPackagedAppQuit"));
  assert.ok(contextQuit.indexOf("await listenerOwnedByLaunch") < contextQuit.indexOf("requestPackagedAppQuit"));
  assert.match(contextQuit, /forceCloseTrackedLaunch\(launchInfo\.identity\)/);
  assert.match(contextQuit, /listenerOwnershipChanged: true/);
  assert.match(contextQuit, /graceful: false,[\s\S]*forced: true/);
  assert.doesNotMatch(contextQuit, /\.child\.kill\(/);
  assert.match(context, /final packaged cleanup did not complete through accepted explicit Quit without forced termination/);
  assert.match(context, /if \(!report\.cleanup\.graceful \|\| report\.cleanup\.forced\)/);
  const desktop = await readFile(resolve(repoRoot, "scripts", "diagnose_packaged_desktop_bridge.mjs"), "utf8");
  assert.equal((desktop.match(/CloseMainWindow/g) || []).length, 1);
  assert.match(desktop, /requestPackagedAppQuit/);
  assert.match(desktop, /waitForCloseToTray/);
  assert.match(desktop, /forceCloseTrackedLaunch\(packagedProcessIdentity\)/);
  assert.match(desktop, /rootStart\.Equals\(\$expectedStart/);
  assert.match(desktop, /windowVisible/);
  assert.match(desktop, /backendAlive/);
  const closeProof = desktop.indexOf("output.closeToTray");
  const explicitQuit = desktop.indexOf("await requestPackagedAppQuit(cdp)", closeProof);
  assert.ok(closeProof >= 0 && explicitQuit > closeProof);
  assert.match(desktop, /failure cleanup required identity-bound forced termination/);
});

test("force-only packaged probes fail closed and never count forced cleanup as success", async () => {
  const repoRoot = resolve(import.meta.dirname, "..");
  const probes = [
    "diagnose_packaged_latency.mjs",
    "diagnose_packaged_workflows.mjs",
    "diagnose_packaged_goal_delivery.mjs",
  ];
  for (const name of probes) {
    const source = await readFile(resolve(repoRoot, "scripts", name), "utf8");
    assert.match(source, /requestPackagedAppQuit/);
    assert.match(source, /assertProbePreflightClear/);
    assert.match(source, /\.LocalPort -eq 8757 -or \$_\.LocalPort -eq \$\{/);
    assert.match(source, /captureLaunchIdentity/);
    assert.match(source, /startedAtUtc/);
    assert.match(source, /forceCloseTrackedLaunch/);
    assert.match(source, /forcedCleanupUsed/);
    assert.match(source, /identityCaptureFailed/);
    assert.match(source, /Stop-Process -Force/);
    assert.equal((source.match(/(?:trackedChild|app\.child)\.kill\(\)/g) || []).length, 0);
    assert.match(source, /unverifiedProcessPreserved/);
    assert.doesNotMatch(source, /CloseMainWindow|closeExistingVrcforgeProcesses|closePackagedProcesses/);
  }

  const latency = await readFile(resolve(repoRoot, "scripts", probes[0]), "utf8");
  assert.match(latency, /VRCFORGE_PROBE_CLOSE_ON_COMPLETE === "1"/);
  assert.match(latency, /detached: !closeOnComplete/);
  assert.match(latency, /"preserved-for-manual-inspection"/);
  assert.match(latency, /if \(completed && closeOnComplete\) \{/);
  assert.match(latency, /preserved-after-incomplete-probe-for-manual-inspection/);
  assert.match(latency, /forced cleanup cannot count as success/);
  const workflows = await readFile(resolve(repoRoot, "scripts", probes[1]), "utf8");
  assert.match(workflows, /failure cleanup required forced termination of the exact probe-owned launch/);
  const goalDelivery = await readFile(resolve(repoRoot, "scripts", probes[2]), "utf8");
  assert.equal((goalDelivery.match(/shutdownPackagedApp\(app, report, "restart-/g) || []).length, 2);
  assert.match(goalDelivery, /failure cleanup required forced termination of the exact probe-owned launch/);
});
