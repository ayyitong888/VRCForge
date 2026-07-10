import { spawn } from "node:child_process";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const packagedRoot = resolve(repoRoot, "dist", "VRCForge_Windows_x64");
const packagedRootPowerShell = packagedRoot.replaceAll("'", "''");
const exe = resolve(packagedRoot, "VRCForge.exe");
const port = Number(process.env.VRCFORGE_CDP_PORT || "9343");
const marker = `DB_PROBE_${Date.now()}`;
const probeSessionId = `${marker}_SESSION`;
const outPath = resolve(repoRoot, "artifacts", "actual-app-desktop-bridge", `desktop-bridge-${marker}.json`);
const fixtureScript = resolve(repoRoot, "scripts", "desktop_executor_fixture.ps1");
const fixtureOutputPath = resolve(repoRoot, "artifacts", "actual-app-desktop-bridge", `fixture-${marker}.json`);
const fixtureWindowMarker = `${marker}_WINDOW`;
const fixtureTypedMarker = `${marker}_TYPED_VALUE`;
const appOrigin = process.env.VRCFORGE_APP_ORIGIN || "http://127.0.0.1:8757";
const appRequestOrigin = "tauri://localhost";
let appSessionToken = "";


function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function runPowerShell(script) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], { windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", rejectRun);
    child.on("close", (code) => {
      if (code === 0) {
        resolveRun(stdout.trim());
      } else {
        rejectRun(new Error(stderr.trim() || stdout.trim() || `PowerShell exited ${code}`));
      }
    });
  });
}

async function waitForPortReleased(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    last = await runPowerShell(`
      $rows = Get-NetTCPConnection -LocalPort 8757 -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq 'Listen' } |
        Select-Object -First 5 LocalAddress,LocalPort,State,OwningProcess
      if ($rows) { $rows | ConvertTo-Json -Compress } else { '' }
    `);
    if (!last) {
      return;
    }
    await sleep(250);
  }
  throw new Error(`Port 8757 still has a listener before launch: ${last}`);
}

async function processSnapshot() {
  const value = await runPowerShell(`
    $packagedRoot = '${packagedRootPowerShell}'
    $processes = Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Path -and $_.Path.StartsWith($packagedRoot, [StringComparison]::OrdinalIgnoreCase) } |
      Select-Object Id,ProcessName,Path
    $ports = Get-NetTCPConnection -LocalPort 8757 -ErrorAction SilentlyContinue |
      Where-Object { $_.State -eq 'Listen' } |
      Select-Object LocalAddress,LocalPort,State,OwningProcess
    [pscustomobject]@{ processes = @($processes); ports = @($ports) } | ConvertTo-Json -Depth 4 -Compress
  `);
  return value ? JSON.parse(value) : { processes: [], ports: [] };
}

async function resourceSnapshot() {
  const value = await runPowerShell(`
    $packagedRoot = '${packagedRootPowerShell}'
    $processes = Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Path -and $_.Path.StartsWith($packagedRoot, [StringComparison]::OrdinalIgnoreCase) } |
      Select-Object Id,ProcessName,@{N='WorkingSetMB';E={[math]::Round($_.WorkingSet64/1MB,1)}},@{N='PrivateMB';E={[math]::Round($_.PrivateMemorySize64/1MB,1)}}
    $os = Get-CimInstance Win32_OperatingSystem
    [pscustomobject]@{
      processes = @($processes)
      appWorkingSetMB = [math]::Round((@($processes) | Measure-Object WorkingSetMB -Sum).Sum,1)
      appPrivateMB = [math]::Round((@($processes) | Measure-Object PrivateMB -Sum).Sum,1)
      systemFreeGB = [math]::Round($os.FreePhysicalMemory/1MB,2)
      systemUsedPercent = [math]::Round((1-$os.FreePhysicalMemory/$os.TotalVisibleMemorySize)*100,1)
    } | ConvertTo-Json -Depth 4 -Compress
  `);
  return value ? JSON.parse(value) : { processes: [], appWorkingSetMB: 0, appPrivateMB: 0 };
}

async function requestMainWindowClose(processId) {
  const value = await runPowerShell(`
    $process = Get-Process -Id ${Number(processId)} -ErrorAction SilentlyContinue
    if ($process) {
      @([pscustomobject]@{ id = $process.Id; closeRequested = $process.CloseMainWindow() }) | ConvertTo-Json -Compress
    } else { '[]' }
  `);
  return value ? JSON.parse(value) : [];
}

async function waitForAppShutdown(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await processSnapshot();
    if (!(latest.processes || []).length && !(latest.ports || []).length) {
      return latest;
    }
    await sleep(200);
  }
  return latest || processSnapshot();
}

async function waitForFixtureWindow(processId, titleMarker, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let latestTitle = "";
  while (Date.now() < deadline) {
    latestTitle = await runPowerShell(`
      $process = Get-Process -Id ${Number(processId)} -ErrorAction SilentlyContinue
      if ($process) { [string]$process.MainWindowTitle } else { '' }
    `);
    if (latestTitle.includes(titleMarker)) {
      return latestTitle;
    }
    await sleep(100);
  }
  throw new Error(`Timed out waiting for fixture window: ${titleMarker}; last=${latestTitle}`);
}

async function jsonFetch(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} for ${url}`);
  }
  return response.json();
}

async function waitForCdpTarget() {
  const deadline = Date.now() + 30000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const targets = await jsonFetch(`http://127.0.0.1:${port}/json/list`);
      const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
      if (page) {
        return page;
      }
    } catch (error) {
      lastError = error;
    }
    await sleep(100);
  }
  throw lastError || new Error("Timed out waiting for WebView2 CDP target.");
}

function connectCdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let nextId = 1;
  const pending = new Map();
  const events = [];
  ws.addEventListener("message", (message) => {
    const payload = JSON.parse(String(message.data));
    if (payload.id && pending.has(payload.id)) {
      const { resolve: resolvePending, reject } = pending.get(payload.id);
      pending.delete(payload.id);
      if (payload.error) {
        reject(new Error(payload.error.message || JSON.stringify(payload.error)));
      } else {
        resolvePending(payload.result);
      }
      return;
    }
    if (payload.method) {
      events.push({ t: Date.now(), method: payload.method, params: payload.params });
    }
  });
  const opened = new Promise((resolveOpen, rejectOpen) => {
    ws.addEventListener("open", resolveOpen, { once: true });
    ws.addEventListener("error", rejectOpen, { once: true });
  });
  return {
    events,
    opened,
    close: () => ws.close(),
    send(method, params = {}) {
      const id = nextId++;
      ws.send(JSON.stringify({ id, method, params }));
      return new Promise((resolveSend, rejectSend) => {
        pending.set(id, { resolve: resolveSend, reject: rejectSend });
      });
    },
  };
}

async function evalValue(cdp, expression, timeout = 15000) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    timeout,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "Runtime.evaluate failed");
  }
  return result.result?.value;
}

async function waitForEval(cdp, expression, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let lastValue = null;
  while (Date.now() < deadline) {
    lastValue = await evalValue(cdp, expression, 5000).catch((error) => ({ error: String(error) }));
    if (lastValue === true || (lastValue && lastValue.ok)) {
      return lastValue;
    }
    await sleep(200);
  }
  throw new Error(`Timed out waiting for expression: ${expression}; last=${JSON.stringify(lastValue)}`);
}

async function reloadAppPage(cdp) {
  const priorTimeOrigin = Number(await evalValue(cdp, "performance.timeOrigin"));
  await cdp.send("Page.reload", { ignoreCache: true });
  return waitForEval(
    cdp,
    `(() => ({
      ok: performance.timeOrigin !== ${JSON.stringify(priorTimeOrigin)} &&
        Boolean(document.querySelector("textarea")) &&
        Boolean(document.querySelector("button[type='submit']")),
      timeOrigin: performance.timeOrigin,
    }))()`,
    30000,
  );
}

function sanitizeProbeValue(value) {
  if (Array.isArray(value)) {
    return value.map(sanitizeProbeValue);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const sanitized = {};
  for (const [key, raw] of Object.entries(value)) {
    if (/token|secret|authorization|apiKey|api_key|password/i.test(key)) {
      sanitized[key] = raw ? "<redacted>" : raw;
    } else {
      sanitized[key] = sanitizeProbeValue(raw);
    }
  }
  return sanitized;
}

async function appApi(path, options = {}) {
  if (!appSessionToken) {
    const tokenPath = resolve(
      process.env.VRCFORGE_CONFIG_DIR || resolve(process.env.LOCALAPPDATA || "", "VRCForge", "agentic-app", "config"),
      "app-session-token",
    );
    try {
      appSessionToken = (await readFile(tokenPath, "utf8")).trim();
    } catch {
      const sessionResponse = await fetch(`${appOrigin}/api/app/session`, { headers: { Origin: appRequestOrigin } });
      const sessionPayload = await sessionResponse.json();
      appSessionToken = sessionPayload.appSessionToken || sessionPayload.app_session_token || "";
    }
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs || 30000);
  try {
    const response = await fetch(`${appOrigin}${path}`, {
      method: options.method || "GET",
      headers: {
        Origin: appRequestOrigin,
        "Content-Type": "application/json",
        Authorization: `Bearer ${appSessionToken}`,
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
    const text = await response.text();
    let payload = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = { text: text.slice(0, 1000) };
    }
    return { ok: response.ok, status: response.status, payload: sanitizeProbeValue(payload) };
  } finally {
    clearTimeout(timeout);
  }
}

async function waitForBridgeConnected(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await appApi("/api/app/agent/desktop-bridge");
    if (latest?.payload?.connected) {
      return latest;
    }
    await sleep(200);
  }
  return latest;
}

async function waitForActionStatus(actionId, statuses, timeoutMs = 30000) {
  const accepted = new Set(statuses);
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await appApi("/api/app/agent/desktop-actions?limit=30");
    const action = (latest?.payload?.actions || []).find((item) => item.actionId === actionId);
    if (action && accepted.has(action.status)) {
      return { listing: latest, action };
    }
    await sleep(150);
  }
  return { listing: latest, action: null };
}

async function waitForNewAction(previousIds, predicate, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await appApi("/api/app/agent/desktop-actions?limit=50");
    const action = (latest?.payload?.actions || []).find(
      (item) => !previousIds.has(item.actionId) && predicate(item),
    );
    if (action) {
      return { listing: latest, action };
    }
    await sleep(150);
  }
  return { listing: latest, action: null };
}

async function waitForRuntimeRun(predicate, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await appApi("/api/app/agent/runs?limit=80");
    const run = (latest?.payload?.runs || []).find(predicate);
    if (run) {
      return { listing: latest, run };
    }
    await sleep(150);
  }
  return { listing: latest, run: null };
}

async function readActionResult(actionId) {
  return appApi(`/api/app/agent/desktop-actions/${encodeURIComponent(actionId)}/result`);
}

async function restorePermissionMode(mode, attempts = 3) {
  let latest = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const update = await appApi("/api/app/permission", {
      method: "POST",
      body: { execution_mode: mode, acknowledge_roslyn_risk: true },
    }).catch((error) => ({ ok: false, error: String(error) }));
    const readback = update?.ok
      ? await appApi("/api/app/permission").catch((error) => ({ ok: false, error: String(error) }))
      : null;
    const restoredMode = String(readback?.payload?.permission?.executionMode || "");
    latest = { ok: Boolean(update?.ok && readback?.ok && restoredMode === mode), attempt, mode, restoredMode, update, readback };
    if (latest.ok) {
      return latest;
    }
    await sleep(200);
  }
  return latest || { ok: false, mode, restoredMode: "" };
}

async function restoreAdvancedSettings(settings, attempts = 3) {
  let latest = null;
  const expectedDeveloper = Boolean(settings?.developerOptionsEnabled);
  const expectedComputer = Boolean(settings?.computerUseEnabled && expectedDeveloper);
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const update = await appApi("/api/app/advanced-settings", {
      method: "POST",
      body: {
        developerOptionsEnabled: expectedDeveloper,
        computerUseEnabled: expectedComputer,
      },
    }).catch((error) => ({ ok: false, error: String(error) }));
    const readback = update?.ok
      ? await appApi("/api/app/advanced-settings").catch((error) => ({ ok: false, error: String(error) }))
      : null;
    const restored = readback?.payload?.settings || {};
    latest = {
      ok: Boolean(
        update?.ok &&
          readback?.ok &&
          Boolean(restored.developerOptionsEnabled) === expectedDeveloper &&
          Boolean(restored.computerUseEnabled) === expectedComputer
      ),
      attempt,
      expected: { developerOptionsEnabled: expectedDeveloper, computerUseEnabled: expectedComputer },
      restored,
      update,
      readback,
    };
    if (latest.ok) {
      return latest;
    }
    await sleep(200);
  }
  return latest || { ok: false, expected: settings || {}, restored: {} };
}

function snapshotHasResidue(snapshot) {
  return Boolean((snapshot?.processes || []).length || (snapshot?.ports || []).length);
}

function sequenceStepResult(payload, operation) {
  const steps = payload?.result?.steps || [];
  return steps.find((step) => step?.operation === operation)?.result || null;
}

async function bmpEvidence(path) {
  if (!path) {
    return { ok: false, error: "missing artifact path" };
  }
  try {
    const bytes = await readFile(path);
    return {
      ok: bytes.length >= 54 && bytes.subarray(0, 2).toString("ascii") === "BM",
      byteLength: bytes.length,
      signature: bytes.subarray(0, 2).toString("ascii"),
      width: bytes.length >= 26 ? bytes.readInt32LE(18) : 0,
      height: bytes.length >= 26 ? Math.abs(bytes.readInt32LE(22)) : 0,
    };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

async function main() {
  await mkdir(dirname(outPath), { recursive: true });
  await waitForPortReleased(15000);
  const beforeLaunch = await processSnapshot();
  if (snapshotHasResidue(beforeLaunch)) {
    throw new Error(`Refusing to disturb an existing packaged VRCForge instance: ${JSON.stringify(beforeLaunch)}`);
  }
  const cdpPortBusy = await runPowerShell(`
    $row = Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($row) { $row | ConvertTo-Json -Compress } else { '' }
  `);
  if (cdpPortBusy) {
    throw new Error(`CDP port ${port} is already in use: ${cdpPortBusy}`);
  }
  const child = spawn(exe, [], {
    detached: false,
    stdio: "ignore",
    env: {
      ...process.env,
      VRCFORGE_DESKTOP_EXECUTOR: "1",
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --remote-allow-origins=*`,
    },
  });

  const output = {
    schema: "vrcforge.packaged_desktop_bridge_probe.v1",
    marker,
    beforeLaunch,
    assertions: [],
    resourceSnapshots: {},
  };
  let cdp = null;
  let fixtureChild = null;
  let previousPermissionMode = "";
  let permissionRestoreNeeded = false;
  let previousAdvancedSettings = null;
  let advancedSettingsRestoreNeeded = false;
  let gracefulShutdownAttempted = false;
  try {
    const page = await waitForCdpTarget();
    cdp = connectCdp(page.webSocketDebuggerUrl);
    await cdp.opened;
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    await cdp.send("Network.enable");
    await waitForEval(cdp, "document.readyState === 'complete' || document.readyState === 'interactive'");
    output.ready = await waitForEval(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        const submit = document.querySelector("button[type='submit']");
        return { ok: Boolean(textarea && submit && !textarea.disabled), bodyLength: document.body.innerText.length };
      })()`,
      30000,
    );
    output.bootstrap = await appApi("/api/app/bootstrap");
    output.permissionBefore = await appApi("/api/app/permission");
    previousPermissionMode = String(output.permissionBefore?.payload?.permission?.executionMode || "approval");
    output.advancedSettingsBefore = await appApi("/api/app/advanced-settings");
    previousAdvancedSettings = output.advancedSettingsBefore?.payload?.settings || {
      developerOptionsEnabled: false,
      computerUseEnabled: false,
    };
    output.resourceSnapshots.afterReady = await resourceSnapshot();

    output.advancedSettingsDisabled = await appApi("/api/app/advanced-settings", {
      method: "POST",
      body: { developerOptionsEnabled: false, computerUseEnabled: false },
    });
    advancedSettingsRestoreNeeded = true;
    output.reloadAfterDisable = await reloadAppPage(cdp);
    const disabledPlusOpen = await evalValue(
      cdp,
      `(() => {
        const menu = document.querySelector("[data-composer-action-menu]");
        if (!(menu instanceof HTMLButtonElement)) return { ok: false, reason: "missing plus menu" };
        menu.click();
        return { ok: true };
      })()`,
    );
    await sleep(150);
    const disabledPlusEntry = await evalValue(
      cdp,
      `(() => {
        const menu = document.querySelector("[data-composer-action-menu]");
        const action = document.querySelector('[data-composer-action="desktop"]');
        if (menu instanceof HTMLButtonElement) menu.click();
        return { ok: !action, hasDesktopAction: Boolean(action) };
      })()`,
    );
    await evalValue(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
        if (!(textarea instanceof HTMLTextAreaElement) || !setter) return false;
        setter.call(textarea, "/desk");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      })()`,
    );
    await sleep(200);
    const disabledSlashEntry = await evalValue(
      cdp,
      `(() => ({
        ok: !document.querySelector('[data-composer-slash-command="desktop"]'),
        count: document.querySelectorAll('[data-composer-slash-command="desktop"]').length,
      }))()`,
    );
    output.desktopEntryDisabled = {
      ok: Boolean(disabledPlusOpen?.ok && disabledPlusEntry?.ok && disabledSlashEntry?.ok),
      open: disabledPlusOpen,
      plus: disabledPlusEntry,
      slash: disabledSlashEntry,
    };
    if (!output.desktopEntryDisabled?.ok) {
      output.assertions.push("Computer Use entry remained visible while advanced settings were disabled");
    }

    output.advancedSettingsEnabled = await appApi("/api/app/advanced-settings", {
      method: "POST",
      body: { developerOptionsEnabled: true, computerUseEnabled: true },
    });
    output.advancedSettingsEnabledReadback = await appApi("/api/app/advanced-settings");
    const enabledSettings = output.advancedSettingsEnabledReadback?.payload?.settings || {};
    if (!enabledSettings.developerOptionsEnabled || !enabledSettings.computerUseEnabled) {
      output.assertions.push("Computer Use advanced settings did not persist as enabled");
    }
    if (!enabledSettings.developerOptionsEverEnabled || !enabledSettings.computerUseEverEnabled) {
      output.assertions.push("advanced settings did not retain the lightweight ever-enabled flags");
    }
    output.reloadAfterEnable = await reloadAppPage(cdp);
    await evalValue(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
        if (!(textarea instanceof HTMLTextAreaElement) || !setter) return false;
        setter.call(textarea, "/desk");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      })()`,
    );
    await sleep(200);
    output.desktopSlashEnabled = await evalValue(
      cdp,
      `(() => ({
        ok: document.querySelectorAll('[data-composer-slash-command="desktop"]').length === 1 &&
          !document.querySelector('[data-composer-slash-command="desktop-rescue"]'),
        desktopCount: document.querySelectorAll('[data-composer-slash-command="desktop"]').length,
        hasDesktopRescue: Boolean(document.querySelector('[data-composer-slash-command="desktop-rescue"]')),
      }))()`,
    );
    if (!output.desktopSlashEnabled?.ok) {
      output.assertions.push("enabled Computer Use must expose exactly one /desktop command");
    }
    await evalValue(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
        if (!(textarea instanceof HTMLTextAreaElement) || !setter) return false;
        setter.call(textarea, "");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      })()`,
    );
    await sleep(100);
    const actionIdsBeforeComposer = new Set(
      ((await appApi("/api/app/agent/desktop-actions?limit=50"))?.payload?.actions || []).map((item) => item.actionId),
    );
    const enabledPlusOpen = await evalValue(
      cdp,
      `(() => {
        const menu = document.querySelector("[data-composer-action-menu]");
        if (!(menu instanceof HTMLButtonElement)) return { ok: false, reason: "missing plus menu" };
        menu.click();
        return { ok: true };
      })()`,
    );
    await sleep(150);
    const enabledPlusClick = await evalValue(
      cdp,
      `(() => {
        const action = document.querySelector('[data-composer-action="desktop"]');
        if (!(action instanceof HTMLButtonElement)) return { ok: false, reason: "missing Desktop Rescue action" };
        action.click();
        return { ok: true };
      })()`,
    );
    const enabledComposerValue = enabledPlusClick?.ok
      ? await waitForEval(
          cdp,
          `(() => {
            const textarea = document.querySelector("textarea");
            return {
              ok: textarea instanceof HTMLTextAreaElement && textarea.value.startsWith("/desktop"),
              value: textarea instanceof HTMLTextAreaElement ? textarea.value : "",
            };
          })()`,
          5000,
        ).catch((error) => ({ ok: false, error: String(error) }))
      : { ok: false, reason: enabledPlusClick?.reason || "plus action click failed" };
    output.desktopEntryEnabled = {
      ok: Boolean(enabledPlusOpen?.ok && enabledPlusClick?.ok && enabledComposerValue?.ok),
      open: enabledPlusOpen,
      click: enabledPlusClick,
      composer: enabledComposerValue,
    };
    await sleep(500);
    const actionIdsAfterComposer = new Set(
      ((await appApi("/api/app/agent/desktop-actions?limit=50"))?.payload?.actions || []).map((item) => item.actionId),
    );
    output.desktopEntryEnabled.createdActionCount = [...actionIdsAfterComposer].filter((id) => !actionIdsBeforeComposer.has(id)).length;
    if (!output.desktopEntryEnabled?.ok || output.desktopEntryEnabled.createdActionCount !== 0) {
      output.assertions.push("+ > Desktop Rescue must only arm /desktop and must not start Computer Use immediately");
    }
    output.frontendDesktopGateSetup = await evalValue(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
        if (!(textarea instanceof HTMLTextAreaElement) || !setter) return { ok: false };
        setter.call(textarea, ${JSON.stringify(`/desktop ${marker} frontend gate probe`)});
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        return { ok: true, theme: document.documentElement.dataset.theme || "" };
      })()`,
    );
    await sleep(150);
    output.frontendDesktopGateClick = await evalValue(
      cdp,
      `(() => {
        const button = document.querySelector("[data-composer-send]");
        if (!(button instanceof HTMLButtonElement) || button.disabled) return { ok: false, disabled: button?.disabled };
        button.click();
        return { ok: true };
      })()`,
    );
    output.frontendDesktopGate = await waitForRuntimeRun(
      (run) => String(run.messageSummary || "").includes(`${marker} frontend gate probe`),
      10000,
    );
    const frontendGateRun = output.frontendDesktopGate?.run;
    output.frontendDesktopGate.ok = Boolean(
      frontendGateRun?.computerUseRequested === true &&
      frontendGateRun?.computerUseVisualTheme === output.frontendDesktopGateSetup?.theme,
    );
    output.frontendDesktopStop = await waitForEval(
      cdp,
      `(() => {
        const button = document.querySelector("[data-composer-stop]");
        if (!(button instanceof HTMLButtonElement)) return { ok: false };
        button.click();
        return { ok: true };
      })()`,
      5000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.frontendDesktopGateSetup?.ok || !output.frontendDesktopGateClick?.ok || !output.frontendDesktopGate?.ok) {
      output.assertions.push("real composer /desktop submission did not set the turn-scoped Computer Use and theme flags");
    }
    if (!output.frontendDesktopStop?.ok) {
      output.assertions.push("real composer Computer Use turn did not expose a cancellable Stop control");
    }
    await evalValue(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        if (!(textarea instanceof HTMLTextAreaElement)) return false;
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
        setter?.call(textarea, "");
        textarea.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      })()`,
    );

    // Phase 1: the packaged backend must auto-register its embedded Win32 worker.
    output.bridgeConnected = await waitForBridgeConnected(30000);
    const connectedBridges = output.bridgeConnected?.payload?.bridges || [];
    const embeddedBridge = connectedBridges.find((bridge) => bridge.provider === "embedded-ctypes-win32");
    if (!embeddedBridge) {
      output.assertions.push("packaged backend did not auto-register the embedded ctypes Win32 bridge");
    }
    const supportedOperations = output.bridgeConnected?.payload?.supportedOperations || [];
    for (const operation of ["list_windows", "inspect_window", "screenshot", "click", "type_text", "sequence"]) {
      if (!supportedOperations.includes(operation)) {
        output.assertions.push(`embedded bridge did not advertise operation: ${operation}`);
      }
    }
    output.resourceSnapshots.afterBridge = await resourceSnapshot();

    // Phase 2: use the explicit app-turn gate, then prove bridge attribution and a real target-window screenshot.
    const existingActionIds = new Set(
      ((await appApi("/api/app/agent/desktop-actions?limit=50"))?.payload?.actions || []).map((item) => item.actionId),
    );
    const explicitClientTurnId = `${marker}-turn-1`;
    const explicitTurnPromise = appApi("/api/app/agent/message", {
      method: "POST",
      timeoutMs: 45000,
      body: {
        agent_name: "desktop-agent",
        session_id: probeSessionId,
        clientTurnId: explicitClientTurnId,
        message: `${marker} safe explicit Computer Use turn`,
        computerUseRequested: true,
        skill_tool: "vrcforge_agent_desktop_action",
        skill_params: {
          action: "computer_use",
          prompt: `${marker} safe packaged executor proof`,
          waitTimeoutMs: 30000,
          params: {
            operation: "sequence",
            steps: [
              { operation: "list_windows", limit: 50 },
              { operation: "wait", durationMs: 2200 },
            ],
          },
        },
      },
    }).catch((error) => ({ ok: false, error: String(error) }));
    output.explicitTurnAction = await waitForNewAction(
      existingActionIds,
      (item) => item.clientTurnId === explicitClientTurnId,
      15000,
    );
    const actionId = output.explicitTurnAction?.action?.actionId || "";
    if (!actionId) {
      output.assertions.push("explicit Computer Use turn did not create a desktop action");
    }

    output.activityRunning = await waitForEval(
      cdp,
      `(() => {
        const surface = document.querySelector("[data-vrcforge-computer-use]");
        const glow = document.querySelector("[data-vrcforge-computer-use-glow]");
        const banner = document.querySelector("[data-vrcforge-computer-use-banner]");
        const cancel = document.querySelector("[data-vrcforge-computer-use-cancel]");
        const glowStyle = glow ? getComputedStyle(glow) : null;
        const bannerStyle = banner ? getComputedStyle(banner) : null;
        const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
        return {
          ok: Boolean(surface && glow && banner && cancel && surface.getAttribute("data-state") === "running"),
          state: surface?.getAttribute("data-state") || "",
          actionId: surface?.getAttribute("data-action-id") || "",
          palette: surface?.getAttribute("data-visual-palette") || "",
          documentTheme: document.documentElement.dataset.theme || "",
          hasGlow: Boolean(glow),
          glowOpacity: Number(glowStyle?.opacity || 0),
          glowAnimation: glowStyle?.animationName || "",
          glowShadow: glowStyle?.boxShadow || "",
          reducedMotion,
          hasBanner: Boolean(banner),
          bannerWidth: banner?.getBoundingClientRect().width || 0,
          bannerShadow: bannerStyle?.boxShadow || "",
          hasCancel: Boolean(cancel),
          text: surface?.textContent || "",
        };
      })()`,
      15000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.activityRunning?.ok) {
      output.assertions.push("Computer Use running banner, glow, and cancel control were not visible");
    }
    if (output.activityRunning?.actionId !== actionId) {
      output.assertions.push("Computer Use activity surface was not bound to the explicit turn actionId");
    }
    if (
      output.activityRunning?.palette !== `semantic-${output.activityRunning?.documentTheme}` ||
      output.activityRunning?.glowOpacity < 0.05 ||
      !output.activityRunning?.glowShadow ||
      output.activityRunning?.glowShadow === "none" ||
      (!output.activityRunning?.reducedMotion && output.activityRunning?.glowAnimation !== "computer-use-breathe")
    ) {
      output.assertions.push("Computer Use glow did not resolve to the active theme or wide animated visual treatment");
    }
    if (
      !output.activityRunning?.hasBanner ||
      output.activityRunning?.bannerWidth < 540 ||
      !output.activityRunning?.bannerShadow ||
      output.activityRunning?.bannerShadow === "none"
    ) {
      output.assertions.push("Computer Use top banner did not resolve to the expected elevated responsive surface");
    }

    output.explicitTurnResponse = await explicitTurnPromise;
    if (!output.explicitTurnResponse?.ok) {
      output.assertions.push(`explicit Computer Use app turn failed: ${output.explicitTurnResponse?.status || "unknown"}`);
    }
    output.completedAction = await waitForActionStatus(actionId, ["completed", "failed"], 30000);
    if (output.completedAction?.action?.status !== "completed") {
      output.assertions.push(`real executor sequence did not complete: ${output.completedAction?.action?.error || "missing action"}`);
    }
    if (output.completedAction?.action?.resultSummary?.operation !== "sequence") {
      output.assertions.push("real executor result summary did not record the sequence operation");
    }
    if (output.completedAction?.action?.bridgeId !== embeddedBridge?.bridgeId || output.completedAction?.action?.provider !== "embedded-ctypes-win32") {
      output.assertions.push("explicit turn action was not completed by the packaged embedded ctypes bridge");
    }
    if (
      output.completedAction?.action?.sessionId !== probeSessionId ||
      output.completedAction?.action?.clientTurnId !== explicitClientTurnId
    ) {
      output.assertions.push("explicit turn action did not preserve its session/clientTurn ownership");
    }
    output.completedActionResult = await readActionResult(actionId);
    const listWindowsResult = sequenceStepResult(output.completedActionResult?.payload, "list_windows");
    const packagedAppProcess = (output.resourceSnapshots.afterReady?.processes || []).find(
      (item) => item.ProcessName === "VRCForge",
    );
    const vrcforgeWindow = (listWindowsResult?.windows || []).find(
      (item) =>
        Number(item?.processId) === Number(packagedAppProcess?.Id) &&
        (item?.className === "Tauri Window" || item?.title === "VRCForge"),
    );
    output.vrcforgeWindow = vrcforgeWindow || null;
    if (!vrcforgeWindow?.windowHandle) {
      output.assertions.push("native list_windows did not find the packaged VRCForge window");
    }

    output.activityAfterCompletion = await waitForEval(
      cdp,
      `(() => ({ ok: !document.querySelector("[data-vrcforge-computer-use]"), visible: Boolean(document.querySelector("[data-vrcforge-computer-use]")) }))()`,
      10000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.activityAfterCompletion?.ok) {
      output.assertions.push("Computer Use activity surface did not disappear after completion");
    }
    output.resourceSnapshots.afterExplicitTurn = await resourceSnapshot();

    permissionRestoreNeeded = !["auto", "roslyn_full_auto"].includes(previousPermissionMode);
    if (permissionRestoreNeeded) {
      output.permissionForInput = await appApi("/api/app/permission", {
        method: "POST",
        body: { execution_mode: "auto", acknowledge_roslyn_risk: true },
      });
    }
    output.permissionForInputReadback = await appApi("/api/app/permission");
    if (!output.permissionForInputReadback?.ok || !["auto", "roslyn_full_auto"].includes(output.permissionForInputReadback?.payload?.permission?.executionMode)) {
      output.assertions.push("interactive screenshot/input proof did not enter an interactive permission mode");
    }

    output.screenshotRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: `${marker} target-window screenshot proof`,
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-screenshot`,
        params: {
          operation: "sequence",
          steps: [
            { operation: "focus_window", windowHandle: vrcforgeWindow?.windowHandle || 0 },
            { operation: "wait", durationMs: 300 },
            { operation: "screenshot", windowHandle: vrcforgeWindow?.windowHandle || 0 },
          ],
        },
      },
    });
    const screenshotActionId = output.screenshotRequestedAction?.payload?.actionId || "";
    output.screenshotCompletedAction = await waitForActionStatus(screenshotActionId, ["completed", "failed"], 30000);
    output.screenshotActionResult = await readActionResult(screenshotActionId);
    const screenshotResult = sequenceStepResult(output.screenshotActionResult?.payload, "screenshot") || {};
    output.screenshotBmpEvidence = await bmpEvidence(screenshotResult.artifactPath);
    if (output.screenshotCompletedAction?.action?.status !== "completed") {
      output.assertions.push(`target-window screenshot failed: ${output.screenshotCompletedAction?.action?.error || "missing action"}`);
    }
    if (
      output.screenshotCompletedAction?.action?.bridgeId !== embeddedBridge?.bridgeId ||
      output.screenshotCompletedAction?.action?.provider !== "embedded-ctypes-win32"
    ) {
      output.assertions.push("target-window screenshot was not attributed to the embedded ctypes bridge");
    }
    if (
      screenshotResult.operation !== "screenshot" ||
      screenshotResult.windowHandle !== vrcforgeWindow?.windowHandle ||
      screenshotResult.width <= 0 ||
      screenshotResult.height <= 0 ||
      screenshotResult.sampleColorCount <= 1 ||
      screenshotResult.frameWarning ||
      !output.screenshotBmpEvidence?.ok
    ) {
      output.assertions.push("target-window screenshot was blank, malformed, or not tied to the VRCForge HWND");
    }

    // Phase 3: click and type only inside an isolated WPF fixture, then read back its submitted marker.
    await rm(fixtureOutputPath, { force: true });
    fixtureChild = spawn(
      "powershell.exe",
      [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        fixtureScript,
        "-Marker",
        fixtureWindowMarker,
        "-OutputPath",
        fixtureOutputPath,
      ],
      { windowsHide: true, stdio: "ignore" },
    );
    output.fixtureWindowTitle = await waitForFixtureWindow(fixtureChild.pid, fixtureWindowMarker);
    output.inputRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: "Controlled packaged fixture input proof",
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-input`,
        params: {
          operation: "sequence",
          steps: [
            { operation: "wait", durationMs: 300 },
            { operation: "focus_window", titleContains: fixtureWindowMarker },
            { operation: "click", titleContains: fixtureWindowMarker, xRatio: 0.5, yRatio: 0.45 },
            { operation: "type_text", text: fixtureTypedMarker },
            { operation: "click", titleContains: fixtureWindowMarker, xRatio: 0.5, yRatio: 0.74 },
            { operation: "wait", durationMs: 500 },
          ],
        },
      },
    });
    const inputActionId = output.inputRequestedAction?.payload?.actionId || "";
    output.inputCompletedAction = await waitForActionStatus(inputActionId, ["completed", "failed"], 30000);
    if (output.inputCompletedAction?.action?.status !== "completed") {
      output.assertions.push(`packaged fixture input action failed: ${output.inputCompletedAction?.action?.error || "missing action"}`);
    }
    if (
      output.inputCompletedAction?.action?.bridgeId !== embeddedBridge?.bridgeId ||
      output.inputCompletedAction?.action?.provider !== "embedded-ctypes-win32" ||
      output.inputCompletedAction?.action?.sessionId !== probeSessionId
    ) {
      output.assertions.push("fixture input action was not owned and completed by the embedded ctypes bridge");
    }
    let fixturePayload = null;
    for (let attempt = 0; attempt < 20 && !fixturePayload; attempt += 1) {
      try {
        fixturePayload = JSON.parse(await readFile(fixtureOutputPath, "utf8"));
      } catch {
        await sleep(100);
      }
    }
    output.fixtureProof = fixturePayload;
    if (fixturePayload?.status !== "submitted" || fixturePayload?.text !== fixtureTypedMarker) {
      output.assertions.push("isolated fixture did not submit the exact text typed by the packaged executor");
    }
    const desktopActionLedger = resolve(
      process.env.LOCALAPPDATA || "",
      "VRCForge",
      "agentic-app",
      "artifacts",
      "dashboard",
      "agent_gateway",
      "desktop-actions.jsonl",
    );
    const ledgerText = await readFile(desktopActionLedger, "utf8").catch(() => "");
    output.inputPrivacy = { ledgerPathFound: Boolean(ledgerText), typedTextPersisted: ledgerText.includes(fixtureTypedMarker) };
    if (!output.inputPrivacy.ledgerPathFound) {
      output.assertions.push("desktop action JSONL ledger was not found for the packaged privacy check");
    }
    if (output.inputPrivacy.typedTextPersisted) {
      output.assertions.push("typed fixture text leaked into the desktop action JSONL ledger");
    }
    if (permissionRestoreNeeded) {
      output.permissionRestore = await restorePermissionMode(previousPermissionMode);
      if (!output.permissionRestore?.ok) {
        output.assertions.push("probe could not restore the original permission mode after input proof");
      } else {
        permissionRestoreNeeded = false;
      }
    }
    output.resourceSnapshots.afterFixtureInput = await resourceSnapshot();

    // Phase 4: a real action is cancelled from the packaged React surface.
    output.cancelRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: `${marker} packaged cancel proof`,
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-2`,
        params: { operation: "wait", durationMs: 10000 },
      },
    });
    const cancelActionId = output.cancelRequestedAction?.payload?.actionId || "";
    output.cancelSurface = await waitForEval(
      cdp,
      `(() => {
        const surface = document.querySelector("[data-vrcforge-computer-use]");
        const button = document.querySelector("[data-vrcforge-computer-use-cancel]");
        return {
          ok: Boolean(surface && button && surface.getAttribute("data-state") === "running"),
          state: surface?.getAttribute("data-state") || "",
          actionId: surface?.getAttribute("data-action-id") || "",
        };
      })()`,
      15000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.cancelSurface?.ok) {
      output.assertions.push("cancel proof did not reach the running Computer Use surface");
    }
    if (output.cancelSurface?.actionId !== cancelActionId) {
      output.assertions.push("cancel surface was not bound to the action being cancelled");
    }
    output.cancelClick = await cdp.send("Runtime.evaluate", {
      expression: `(() => {
        const button = document.querySelector("[data-vrcforge-computer-use-cancel]");
        if (!(button instanceof HTMLButtonElement)) return { clicked: false };
        button.click();
        return { clicked: true };
      })()`,
      returnByValue: true,
    }).then((response) => response?.result?.value || {});
    if (!output.cancelClick?.clicked) {
      output.assertions.push("packaged Computer Use cancel button could not be clicked");
    }
    output.cancelledAction = await waitForActionStatus(cancelActionId, ["cancelled", "failed"], 15000);
    if (output.cancelledAction?.action?.status !== "cancelled") {
      output.assertions.push(`Computer Use action did not settle as cancelled: ${output.cancelledAction?.action?.status || "missing"}`);
    }
    if (
      output.cancelledAction?.action?.bridgeId !== embeddedBridge?.bridgeId ||
      output.cancelledAction?.action?.sessionId !== probeSessionId
    ) {
      output.assertions.push("cancelled action was not owned by the expected embedded bridge/session");
    }

    output.activityAfterCancel = await waitForEval(
      cdp,
      `(() => ({ ok: !document.querySelector("[data-vrcforge-computer-use]"), visible: Boolean(document.querySelector("[data-vrcforge-computer-use]")) }))()`,
      10000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.activityAfterCancel?.ok) {
      output.assertions.push("Computer Use activity surface did not disappear after cancellation");
    }
    output.resourceSnapshots.afterCancel = await resourceSnapshot();

    output.advancedSettingsRestore = await restoreAdvancedSettings(previousAdvancedSettings);
    if (!output.advancedSettingsRestore?.ok) {
      output.assertions.push("probe could not restore the original advanced setting values");
    } else {
      advancedSettingsRestoreNeeded = false;
    }

    output.resourceSnapshots.beforeClose = await resourceSnapshot();
    const memorySamples = Object.entries(output.resourceSnapshots)
      .filter(([, sample]) => Number.isFinite(Number(sample?.appPrivateMB)))
      .map(([name, sample]) => ({ name, appWorkingSetMB: Number(sample.appWorkingSetMB), appPrivateMB: Number(sample.appPrivateMB) }));
    const baselinePrivateMB = Number(output.resourceSnapshots.afterReady?.appPrivateMB || 0);
    const peakPrivateMB = Math.max(0, ...memorySamples.map((sample) => sample.appPrivateMB));
    const finalPrivateMB = Number(output.resourceSnapshots.beforeClose?.appPrivateMB || 0);
    output.memorySummary = {
      scope: "VRCForge, vrcforge_backend, and vrcforge-agentic-app only; Ollama and unrelated sessions are excluded",
      baselinePrivateMB,
      peakPrivateMB,
      finalPrivateMB,
      growthFromReadyMB: Math.round((finalPrivateMB - baselinePrivateMB) * 10) / 10,
      samples: memorySamples,
    };
    if (peakPrivateMB > 1200 || finalPrivateMB - baselinePrivateMB > 512) {
      output.assertions.push("VRCForge process memory exceeded the packaged Computer Use acceptance envelope");
    }

    if (fixtureChild && !fixtureChild.killed) {
      try {
        fixtureChild.kill();
      } catch {}
    }
    if (cdp) {
      cdp.close();
      cdp = null;
    }
    gracefulShutdownAttempted = true;
    output.closeRequest = await requestMainWindowClose(child.pid);
    output.afterWindowClose = await waitForAppShutdown(20000);
    output.resourceSnapshots.afterWindowClose = await resourceSnapshot();
    if (snapshotHasResidue(output.afterWindowClose)) {
      output.assertions.push("closing the packaged main window left VRCForge/backend or port 8757 alive");
    }
  } catch (error) {
    output.error = String(error && error.stack ? error.stack : error);
    output.assertions.push("probe threw before completion");
  } finally {
    if (permissionRestoreNeeded && previousPermissionMode) {
      output.permissionRestoreFinally = await restorePermissionMode(previousPermissionMode).catch((error) => ({ ok: false, error: String(error) }));
      if (!output.permissionRestoreFinally?.ok) {
        output.assertions.push("probe could not restore the original permission mode during cleanup");
      }
    }
    if (advancedSettingsRestoreNeeded && previousAdvancedSettings) {
      output.advancedSettingsRestoreFinally = await restoreAdvancedSettings(previousAdvancedSettings).catch((error) => ({ ok: false, error: String(error) }));
      if (!output.advancedSettingsRestoreFinally?.ok) {
        output.assertions.push("probe could not restore the original advanced settings during cleanup");
      }
    }
    if (fixtureChild && !fixtureChild.killed) {
      try {
        fixtureChild.kill();
      } catch {}
    }
    if (cdp) {
      cdp.close();
      cdp = null;
    }
    if (!gracefulShutdownAttempted) {
      gracefulShutdownAttempted = true;
      output.closeRequestFinally = await requestMainWindowClose(child.pid).catch((error) => ({ error: String(error) }));
      output.afterWindowCloseFinally = await waitForAppShutdown(20000).catch((error) => ({ error: String(error) }));
      if (snapshotHasResidue(output.afterWindowCloseFinally)) {
        output.assertions.push("cleanup close request left VRCForge/backend or port 8757 alive");
      }
    }
    const residueBeforeForce = await processSnapshot().catch((error) => ({ error: String(error), processes: [], ports: [] }));
    output.forcedCleanupUsed = snapshotHasResidue(residueBeforeForce);
    if (output.forcedCleanupUsed) {
      try {
        process.kill(child.pid);
      } catch {
        // The launched app may already have exited; its Job Object reaps the backend.
      }
      await waitForPortReleased(15000).catch((error) => {
        output.cleanupError = String(error);
      });
    }
    output.afterCleanup = await processSnapshot().catch((error) => ({ error: String(error) }));
    output.resourceSnapshots.afterCleanup = await resourceSnapshot().catch((error) => ({ error: String(error) }));
    if (snapshotHasResidue(output.afterCleanup)) {
      output.assertions.push("forced cleanup still left VRCForge/backend or port 8757 alive");
    }
    await writeFile(outPath, JSON.stringify(output, null, 2), "utf8");
    console.log(outPath);
    if (output.assertions.length) {
      console.error(output.assertions.join("\n"));
      process.exitCode = 1;
    }
  }
}

main();
