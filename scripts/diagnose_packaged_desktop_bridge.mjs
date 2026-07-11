import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
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
const fixtureSourcePath = resolve(repoRoot, "scripts", "desktop_executor_fixture.cs");
const fixtureExePath = resolve(repoRoot, "artifacts", "actual-app-desktop-bridge", `fixture-${marker}.exe`);
const fixtureTypedMarker = `${marker}_TYPED_VALUE`;
const uiaFixtureTypedMarker = `${marker}_UIA_VALUE`;
const appOrigin = process.env.VRCFORGE_APP_ORIGIN || "http://127.0.0.1:8757";
const appRequestOrigin = "tauri://localhost";
let appSessionToken = "";


function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function processExists(processId) {
  try {
    process.kill(Number(processId), 0);
    return true;
  } catch {
    return false;
  }
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
      Select-Object Id,ProcessName,HandleCount,@{N='ThreadCount';E={$_.Threads.Count}},@{N='WorkingSetMB';E={[math]::Round($_.WorkingSet64/1MB,1)}},@{N='PrivateMB';E={[math]::Round($_.PrivateMemorySize64/1MB,1)}}
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

async function waitForNativeOverlay(visible, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await appApi("/api/app/agent/desktop-bridge");
    const info = latest?.payload?.embeddedExecutor?.nativeOverlayInfo || {};
    if (Boolean(info.visible) === Boolean(visible)) {
      return { response: latest, info };
    }
    await sleep(100);
  }
  return {
    response: latest,
    info: latest?.payload?.embeddedExecutor?.nativeOverlayInfo || {},
  };
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

async function imageEvidence(path) {
  if (!path) {
    return { ok: false, error: "missing artifact path" };
  }
  try {
    const bytes = await readFile(path);
    const bmp = bytes.length >= 54 && bytes.subarray(0, 2).toString("ascii") === "BM";
    const png = bytes.length >= 33 && bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    const width = bmp ? bytes.readInt32LE(18) : png ? bytes.readUInt32BE(16) : 0;
    const height = bmp ? Math.abs(bytes.readInt32LE(22)) : png ? bytes.readUInt32BE(20) : 0;
    return {
      ok: (bmp || png) && width > 0 && height > 0,
      byteLength: bytes.length,
      format: bmp ? "bmp" : png ? "png" : "unknown",
      signature: bytes.subarray(0, png ? 8 : 2).toString("hex"),
      width,
      height,
      sha256: createHash("sha256").update(bytes).digest("hex"),
    };
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

async function main() {
  await mkdir(dirname(outPath), { recursive: true });
  const fixtureCompileOutput = await runPowerShell(`
    $csc = 'C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe'
    $presentationFramework = (Get-ChildItem 'C:\\Windows\\Microsoft.NET\\assembly\\GAC_MSIL\\PresentationFramework' -Recurse -Filter PresentationFramework.dll | Select-Object -First 1).FullName
    $presentationCore = (Get-ChildItem 'C:\\Windows\\Microsoft.NET\\assembly\\GAC_64\\PresentationCore' -Recurse -Filter PresentationCore.dll | Select-Object -First 1).FullName
    $windowsBase = (Get-ChildItem 'C:\\Windows\\Microsoft.NET\\assembly\\GAC_MSIL\\WindowsBase' -Recurse -Filter WindowsBase.dll | Select-Object -First 1).FullName
    $systemXaml = (Get-ChildItem 'C:\\Windows\\Microsoft.NET\\assembly\\GAC_MSIL\\System.Xaml' -Recurse -Filter System.Xaml.dll | Select-Object -First 1).FullName
    & $csc /nologo /target:winexe /reference:$presentationFramework /reference:$presentationCore /reference:$windowsBase /reference:$systemXaml /out:'${fixtureExePath.replaceAll("'", "''")}' '${fixtureSourcePath.replaceAll("'", "''")}'
    if ($LASTEXITCODE -ne 0) { throw "fixture compiler exited $LASTEXITCODE" }
    Get-Item '${fixtureExePath.replaceAll("'", "''")}' | Select-Object FullName,Length | ConvertTo-Json -Compress
  `);
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
    fixtureCompile: fixtureCompileOutput ? JSON.parse(fixtureCompileOutput) : null,
  };
  let cdp = null;
  let launchedAppPid = 0;
  let launchedFixturePid = 0;
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
    output.restoredTransientPlaceholders = await evalValue(
      cdp,
      `(() => {
        const turns = Array.from(document.querySelectorAll("[data-conversation-streaming-turn]"))
          .map((item) => item.getAttribute("data-conversation-streaming-turn") || "");
        return { ok: turns.length === 0, turns };
      })()`,
    );
    if (!output.restoredTransientPlaceholders?.ok) {
      output.assertions.push("persisted chat restore left an orphan streaming placeholder visible");
    }
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
    for (const operation of [
      "list_apps",
      "launch_app",
      "list_windows",
      "get_window",
      "window_state",
      "inspect_window",
      "cursor_position",
      "screenshot",
      "focus_window",
      "move_pointer",
      "click",
      "drag",
      "scroll",
      "type_text",
      "key_press",
      "focus_element",
      "invoke_element",
      "set_value",
      "secondary_action",
      "sequence",
    ]) {
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
    output.ungrantedTurnResponse = await appApi("/api/app/agent/message", {
      method: "POST",
      timeoutMs: 10000,
      body: {
        agent_name: "desktop-agent",
        session_id: probeSessionId,
        clientTurnId: `${marker}-turn-ungranted`,
        message: `${marker} ungranted Computer Use gate proof`,
        computerUseRequested: true,
      },
    });
    if (output.ungrantedTurnResponse?.status !== 403) {
      output.assertions.push("Computer Use turn without a server-issued grant was not rejected");
    }
    output.explicitTurnGrant = await appApi("/api/app/agent/computer-use/grants", {
      method: "POST",
      body: {
        sessionId: probeSessionId,
        clientTurnId: explicitClientTurnId,
      },
    });
    const explicitTurnGrantId = output.explicitTurnGrant?.payload?.grantId || "";
    if (!output.explicitTurnGrant?.ok || !explicitTurnGrantId) {
      output.assertions.push("packaged backend did not issue a Computer Use turn grant");
    }
    const explicitTurnPromise = appApi("/api/app/agent/message", {
      method: "POST",
      timeoutMs: 45000,
      body: {
        agent_name: "desktop-agent",
        session_id: probeSessionId,
        clientTurnId: explicitClientTurnId,
        message: `${marker} safe explicit Computer Use turn`,
        computerUseRequested: true,
        computerUseGrantId: explicitTurnGrantId,
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

    output.nativeActivityRunning = await waitForNativeOverlay(true, 15000);
    const nativeOverlayInfo = output.nativeActivityRunning?.info || {};
    if (
      nativeOverlayInfo.renderer !== "win32-layered-ambient-v2" ||
      !nativeOverlayInfo.captureExcluded ||
      nativeOverlayInfo.windowCount !== 5 ||
      nativeOverlayInfo.glowWindowCount !== 4 ||
      nativeOverlayInfo.fontFamily !== "Segoe UI" ||
      !Array.isArray(nativeOverlayInfo.stopHitTargetSize) ||
      nativeOverlayInfo.stopHitTargetSize.some((value) => Number(value) <= 0)
    ) {
      output.assertions.push("native Computer Use overlay did not expose the expected capture-safe visual contract");
    }
    output.nativeOverlayAccent = {
      accent: nativeOverlayInfo.accent || "",
      accentSource: nativeOverlayInfo.accentSource || "",
    };
    output.activityRunning = await waitForEval(
      cdp,
      `(() => {
        const surfaces = document.querySelectorAll("[data-vrcforge-computer-use]");
        return {
          ok: surfaces.length === 0,
          surfaceCount: surfaces.length,
          documentTheme: document.documentElement.dataset.theme || "",
        };
      })()`,
      15000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.activityRunning?.ok) {
      output.assertions.push("embedded Computer Use action rendered a duplicate React activity surface");
    }
    output.activityRunningAction = await waitForActionStatus(
      actionId,
      ["requested", "claimed", "cancel_requested", "completed", "failed", "cancelled"],
      5000,
    );
    if (
      output.activityRunningAction?.action?.sessionId !== probeSessionId ||
      output.activityRunningAction?.action?.clientTurnId !== explicitClientTurnId
    ) {
      output.assertions.push("native Computer Use activity was not bound to an action owned by the explicit turn");
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
    output.nativeActivityAfterComplete = await waitForNativeOverlay(false, 10000);
    if (output.nativeActivityAfterComplete?.info?.visible) {
      output.assertions.push("native Computer Use overlay remained visible after completion");
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
          operation: "get_window_state",
          window: {
            id: vrcforgeWindow?.windowHandle || 0,
            app: vrcforgeWindow?.app || vrcforgeWindow?.processPath || "",
            processId: vrcforgeWindow?.processId || 0,
          },
          include_screenshot: true,
          include_text: false,
        },
      },
    });
    const screenshotActionId = output.screenshotRequestedAction?.payload?.actionId || "";
    output.screenshotCompletedAction = await waitForActionStatus(screenshotActionId, ["completed", "failed"], 30000);
    output.screenshotActionResult = await readActionResult(screenshotActionId);
    const windowStateResult = output.screenshotActionResult?.payload?.result || {};
    const screenshotResult = windowStateResult.screenshot || {};
    output.screenshotImageEvidence = await imageEvidence(screenshotResult.artifactPath);
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
      windowStateResult.operation !== "window_state" ||
      screenshotResult.operation !== "screenshot" ||
      screenshotResult.windowHandle !== vrcforgeWindow?.windowHandle ||
      screenshotResult.captureBackend !== "windows_graphics_capture" ||
      screenshotResult.occlusionSafe !== true ||
      screenshotResult.format !== "png" ||
      screenshotResult.width <= 0 ||
      screenshotResult.height <= 0 ||
      screenshotResult.sampleColorCount <= 1 ||
      screenshotResult.frameWarning ||
      !output.screenshotImageEvidence?.ok
    ) {
      output.assertions.push("target-window state did not return a nonblank occlusion-safe WGC PNG tied to the VRCForge HWND");
    }

    // Phase 3: launch a safe app and exercise the canonical Window2-shaped input contract.
    output.listAppsRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: "Packaged Windows application discovery proof",
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-list-apps`,
        params: { operation: "list_apps", limit: 200 },
      },
    });
    const listAppsActionId = output.listAppsRequestedAction?.payload?.actionId || "";
    output.listAppsCompletedAction = await waitForActionStatus(listAppsActionId, ["completed", "failed"], 30000);
    output.listAppsActionResult = await readActionResult(listAppsActionId);
    const listAppsResult = output.listAppsActionResult?.payload?.result || {};
    const listedApps = Array.isArray(listAppsResult.apps) ? listAppsResult.apps : [];
    output.listAppsProof = {
      count: listedApps.length,
      hasRunningVRCForge: listedApps.some((app) =>
        Boolean(app?.isRunning) && (app?.windows || []).some((window) => window?.windowHandle === vrcforgeWindow?.windowHandle),
      ),
      hasLaunchCandidate: listedApps.some((app) => !app?.isRunning && app?.id),
    };
    if (
      output.listAppsCompletedAction?.action?.status !== "completed" ||
      listAppsResult.operation !== "list_apps" ||
      !output.listAppsProof.hasRunningVRCForge ||
      !output.listAppsProof.hasLaunchCandidate
    ) {
      output.assertions.push("packaged list_apps did not return registered apps plus the running VRCForge window");
    }

    output.launchAppRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: "Launch a preinstalled Notepad fixture",
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-launch-app`,
        params: { operation: "launch_app", app: "C:\\Windows\\System32\\notepad.exe", timeout_ms: 8000 },
      },
    });
    const launchAppActionId = output.launchAppRequestedAction?.payload?.actionId || "";
    output.launchAppCompletedAction = await waitForActionStatus(launchAppActionId, ["completed", "failed"], 30000);
    output.launchAppActionResult = await readActionResult(launchAppActionId);
    const launchAppResult = output.launchAppActionResult?.payload?.result || {};
    const notepadWindow = launchAppResult.window || (launchAppResult.windows || [])[0] || null;
    output.notepadWindow = notepadWindow;
    launchedAppPid = Number(notepadWindow?.processId || 0);
    if (
      output.launchAppCompletedAction?.action?.status !== "completed" ||
      launchAppResult.operation !== "launch_app" ||
      !launchAppResult.windowDetected ||
      !notepadWindow?.windowHandle ||
      !launchedAppPid
    ) {
      output.assertions.push("packaged launch_app did not launch and resolve a Notepad window");
    }

    output.inputRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: "Controlled packaged Notepad input proof",
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-input`,
        params: {
          operation: "sequence",
          steps: [
            {
              operation: "click",
              window: { id: notepadWindow?.windowHandle || 0, app: notepadWindow?.app || "", processId: launchedAppPid },
              x: 120,
              y: 140,
              click_count: 1,
              mouse_button: "left",
            },
            { operation: "type", text: fixtureTypedMarker },
            { operation: "press_key", key: "Control_L+a" },
            { operation: "type_text", text: fixtureTypedMarker },
            { operation: "drag", from_x: 30, from_y: 140, to_x: 260, to_y: 140, duration_ms: 200 },
            { operation: "scroll", x: 200, y: 140, scroll_x: 120, scroll_y: 240 },
            { operation: "wait", duration_ms: 200 },
          ],
        },
      },
    });
    const inputActionId = output.inputRequestedAction?.payload?.actionId || "";
    output.inputCompletedAction = await waitForActionStatus(inputActionId, ["completed", "failed"], 30000);
    if (output.inputCompletedAction?.action?.status !== "completed") {
      output.assertions.push(`packaged Notepad input action failed: ${output.inputCompletedAction?.action?.error || "missing action"}`);
    }
    if (
      output.inputCompletedAction?.action?.bridgeId !== embeddedBridge?.bridgeId ||
      output.inputCompletedAction?.action?.provider !== "embedded-ctypes-win32" ||
      output.inputCompletedAction?.action?.sessionId !== probeSessionId
    ) {
      output.assertions.push("Notepad input action was not owned and completed by the embedded ctypes bridge");
    }
    output.inputActionResult = await readActionResult(inputActionId);
    const inputSequence = output.inputActionResult?.payload?.result || {};
    const executedInputOperations = (inputSequence.steps || []).map((step) => step.operation);
    output.inputOperationProof = { executedInputOperations };
    for (const operation of ["click", "type_text", "key_press", "drag", "scroll"]) {
      if (!executedInputOperations.includes(operation)) {
        output.assertions.push(`packaged Notepad sequence did not execute operation: ${operation}`);
      }
    }

    output.uiaFixtureLaunchRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: "Launch the native UI Automation acceptance fixture",
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-uia-fixture-launch`,
        params: { operation: "launch_app", app: fixtureExePath, timeout_ms: 8000 },
      },
    });
    const uiaFixtureLaunchActionId = output.uiaFixtureLaunchRequestedAction?.payload?.actionId || "";
    output.uiaFixtureLaunchCompletedAction = await waitForActionStatus(uiaFixtureLaunchActionId, ["completed", "failed"], 30000);
    output.uiaFixtureLaunchActionResult = await readActionResult(uiaFixtureLaunchActionId);
    const uiaFixtureLaunchResult = output.uiaFixtureLaunchActionResult?.payload?.result || {};
    const uiaFixtureWindow = uiaFixtureLaunchResult.window || (uiaFixtureLaunchResult.windows || [])[0] || null;
    launchedFixturePid = Number(uiaFixtureWindow?.processId || 0);
    if (
      output.uiaFixtureLaunchCompletedAction?.action?.status !== "completed" ||
      !uiaFixtureLaunchResult.windowDetected ||
      !uiaFixtureWindow?.windowHandle ||
      !launchedFixturePid
    ) {
      output.assertions.push("packaged launch_app did not launch the native UI Automation fixture");
    }

    output.occlusionFocusRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: "Bring the native fixture over Notepad before passive capture",
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-occlusion-focus`,
        params: {
          operation: "activate_window",
          window: {
            id: uiaFixtureWindow?.windowHandle || 0,
            app: uiaFixtureWindow?.app || "",
            processId: launchedFixturePid,
          },
        },
      },
    });
    const occlusionFocusActionId = output.occlusionFocusRequestedAction?.payload?.actionId || "";
    output.occlusionFocusCompletedAction = await waitForActionStatus(occlusionFocusActionId, ["completed", "failed"], 30000);
    output.occlusionFocusActionResult = await readActionResult(occlusionFocusActionId);
    const focusedCoverWindow = output.occlusionFocusActionResult?.payload?.result?.window || {};
    const coverRect = focusedCoverWindow.rect || uiaFixtureWindow?.rect || {};
    const coveredRect = notepadWindow?.rect || {};
    const coveredWidth = Math.max(
      0,
      Math.min(Number(coverRect.right), Number(coveredRect.right)) -
        Math.max(Number(coverRect.left), Number(coveredRect.left)),
    );
    const coveredHeight = Math.max(
      0,
      Math.min(Number(coverRect.bottom), Number(coveredRect.bottom)) -
        Math.max(Number(coverRect.top), Number(coveredRect.top)),
    );
    const targetArea = Math.max(1, Number(coveredRect.width) * Number(coveredRect.height));
    const coveredAreaRatio = (coveredWidth * coveredHeight) / targetArea;
    output.occludedWindowStateRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: "Capture the fully covered Notepad window without activating it",
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-occluded-state`,
        params: {
          operation: "get_window_state",
          window: { id: notepadWindow?.windowHandle || 0, app: notepadWindow?.app || "", processId: launchedAppPid },
          include_screenshot: true,
          include_text: false,
        },
      },
    });
    const occludedWindowStateActionId = output.occludedWindowStateRequestedAction?.payload?.actionId || "";
    output.occludedWindowStateCompletedAction = await waitForActionStatus(occludedWindowStateActionId, ["completed", "failed"], 30000);
    output.occludedWindowStateActionResult = await readActionResult(occludedWindowStateActionId);
    const occludedWindowState = output.occludedWindowStateActionResult?.payload?.result || {};
    const occludedScreenshot = occludedWindowState.screenshot || {};
    output.occludedScreenshotEvidence = await imageEvidence(occludedScreenshot.artifactPath);
    output.occlusionProof = {
      coveredAreaRatio,
      coverForeground: focusedCoverWindow.foreground === true,
      targetForeground: occludedWindowState.window?.foreground === true,
      targetWindowHandle: notepadWindow?.windowHandle || 0,
      captureBackend: occludedScreenshot.captureBackend || "",
      occlusionSafe: occludedScreenshot.occlusionSafe === true,
      image: output.occludedScreenshotEvidence,
    };
    if (
      output.occlusionFocusCompletedAction?.action?.status !== "completed" ||
      output.occludedWindowStateCompletedAction?.action?.status !== "completed" ||
      output.occlusionProof.coveredAreaRatio < 0.9 ||
      !output.occlusionProof.coverForeground ||
      output.occlusionProof.targetForeground ||
      output.occlusionProof.captureBackend !== "windows_graphics_capture" ||
      !output.occlusionProof.occlusionSafe ||
      !output.occludedScreenshotEvidence?.ok
    ) {
      output.assertions.push("packaged get_window_state did not prove WGC capture of a substantially occluded, non-foreground window");
    }

    output.windowsKeyRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: "Protected Windows-key rejection proof",
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-protected-key`,
        params: {
          operation: "press_key",
          window: { id: notepadWindow?.windowHandle || 0, app: notepadWindow?.app || "", processId: launchedAppPid },
          key: "Win+r",
        },
      },
    });
    const windowsKeyActionId = output.windowsKeyRequestedAction?.payload?.actionId || "";
    output.windowsKeyCompletedAction = await waitForActionStatus(windowsKeyActionId, ["completed", "failed"], 30000);
    if (
      output.windowsKeyCompletedAction?.action?.status !== "failed" ||
      !/does not allow windows/i.test(String(output.windowsKeyCompletedAction?.action?.error || ""))
    ) {
      output.assertions.push("packaged Computer Use did not fail closed on a Windows-key shortcut");
    }

    // Prove UI Automation state, fresh element indexes, element click, set_value, and Raise on a real native app.
    output.uiaInspectRequestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: {
        action: "computer_use",
        prompt: "Packaged native UI Automation inspection proof",
        sessionId: probeSessionId,
        clientTurnId: `${marker}-turn-uia-inspect`,
        params: {
          operation: "get_window_state",
          window: {
            id: uiaFixtureWindow?.windowHandle || 0,
            app: uiaFixtureWindow?.app || "",
            processId: launchedFixturePid,
          },
          include_screenshot: false,
          include_text: true,
          limit: 200,
        },
      },
    });
    const uiaInspectActionId = output.uiaInspectRequestedAction?.payload?.actionId || "";
    output.uiaInspectCompletedAction = await waitForActionStatus(uiaInspectActionId, ["completed", "failed"], 30000);
    output.uiaInspectActionResult = await readActionResult(uiaInspectActionId);
    const uiaWindowState = output.uiaInspectActionResult?.payload?.result || {};
    const uiaInspectResult = uiaWindowState.accessibility || {};
    const uiaControls = Array.isArray(uiaInspectResult.controls) ? uiaInspectResult.controls : [];
    const uiaInputs = uiaControls.filter((item) => String(item.controlType || "").endsWith(".Edit") && item.enabled && !item.offscreen);
    const uiaInput = uiaInputs.sort((left, right) => Number(right?.rect?.top || 0) - Number(left?.rect?.top || 0))[0] || null;
    const uiaButton = uiaControls.find((item) => String(item.controlType || "").endsWith(".Button") && item.enabled && !item.offscreen) || null;
    output.uiaInspectionProof = {
      accessibilityTree: uiaInspectResult.accessibilityTree === true,
      count: uiaControls.length,
      input: uiaInput || null,
      button: uiaButton || null,
      treeIsString: typeof uiaInspectResult.tree === "string" && uiaInspectResult.tree.length > 0,
      hasCanonicalFields:
        Object.hasOwn(uiaInspectResult, "focused_element") &&
        Object.hasOwn(uiaInspectResult, "selected_elements") &&
        Object.hasOwn(uiaInspectResult, "selected_text") &&
        Object.hasOwn(uiaInspectResult, "document_text"),
    };
    if (
      output.uiaInspectCompletedAction?.action?.status !== "completed" ||
      !output.uiaInspectionProof.accessibilityTree ||
      !output.uiaInspectionProof.treeIsString ||
      !output.uiaInspectionProof.hasCanonicalFields ||
      !uiaInput ||
      !uiaButton
    ) {
      output.assertions.push("packaged UI Automation window state did not expose the native Edit/Button controls and canonical text fields");
    }
    if (uiaInput && uiaButton) {
      output.uiaInputRequestedAction = await appApi("/api/app/agent/desktop-actions", {
        method: "POST",
        body: {
          action: "computer_use",
          prompt: "Controlled packaged native UI Automation input proof",
          sessionId: probeSessionId,
          clientTurnId: `${marker}-turn-uia-input`,
          params: {
            operation: "sequence",
            steps: [
              {
                operation: "set_value",
                window: {
                  id: uiaFixtureWindow?.windowHandle || 0,
                  app: uiaFixtureWindow?.app || "",
                  processId: launchedFixturePid,
                },
                element_index: uiaInput.index,
                value: uiaFixtureTypedMarker,
              },
              {
                operation: "click",
                element_index: uiaButton.index,
                click_count: 1,
                mouse_button: "left",
              },
              {
                operation: "perform_secondary_action",
                element_index: uiaInput.index,
                action: "Raise",
              },
              { operation: "wait", duration_ms: 200 },
            ],
          },
        },
      });
      const uiaInputActionId = output.uiaInputRequestedAction?.payload?.actionId || "";
      output.uiaInputCompletedAction = await waitForActionStatus(uiaInputActionId, ["completed", "failed"], 30000);
      output.uiaInputActionResult = await readActionResult(uiaInputActionId);
      if (output.uiaInputCompletedAction?.action?.status !== "completed") {
        output.assertions.push(`packaged UI Automation input action failed: ${output.uiaInputCompletedAction?.action?.error || "missing action"}`);
      }
      output.uiaReadbackRequestedAction = await appApi("/api/app/agent/desktop-actions", {
        method: "POST",
        body: {
          action: "computer_use",
          prompt: "Read back the native UI Automation fixture value",
          sessionId: probeSessionId,
          clientTurnId: `${marker}-turn-uia-readback`,
          params: {
            operation: "get_window_state",
            window: { id: uiaFixtureWindow?.windowHandle || 0, app: uiaFixtureWindow?.app || "", processId: launchedFixturePid },
            include_screenshot: false,
            include_text: true,
            limit: 200,
          },
        },
      });
      const uiaReadbackActionId = output.uiaReadbackRequestedAction?.payload?.actionId || "";
      output.uiaReadbackCompletedAction = await waitForActionStatus(uiaReadbackActionId, ["completed", "failed"], 30000);
      output.uiaReadbackActionResult = await readActionResult(uiaReadbackActionId);
      const readbackControls = output.uiaReadbackActionResult?.payload?.result?.accessibility?.controls || [];
      output.uiaValueReadback = readbackControls.find((item) => Number(item?.index) === Number(uiaInput.index)) || null;
      output.uiaAppliedLabel = readbackControls.find((item) => String(item?.name || "").includes(uiaFixtureTypedMarker)) || null;
      if (
        output.uiaReadbackCompletedAction?.action?.status !== "completed" ||
        output.uiaValueReadback?.value !== uiaFixtureTypedMarker ||
        !output.uiaAppliedLabel
      ) {
        output.assertions.push("packaged UI Automation set_value/element click did not update the native fixture");
      }
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
    output.inputPrivacy = {
      ledgerPathFound: Boolean(ledgerText),
      typedTextPersisted: ledgerText.includes(fixtureTypedMarker) || ledgerText.includes(uiaFixtureTypedMarker),
    };
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

    // Phase 4: cancel a real embedded action through the same backend path used by native Stop.
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
    output.nativeCancelSurface = await waitForNativeOverlay(true, 15000);
    if (!output.nativeCancelSurface?.info?.visible) {
      output.assertions.push("cancel proof did not reach the native Computer Use overlay");
    }
    output.cancelSurface = await waitForEval(
      cdp,
      `(() => {
        const surfaces = document.querySelectorAll("[data-vrcforge-computer-use]");
        return {
          ok: surfaces.length === 0,
          surfaceCount: surfaces.length,
        };
      })()`,
      15000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.cancelSurface?.ok) {
      output.assertions.push("cancel proof rendered a duplicate React Computer Use surface");
    }
    output.cancelRequest = await appApi(`/api/app/agent/desktop-actions/${cancelActionId}/cancel`, {
      method: "POST",
      body: { reason: "Packaged native Stop path proof" },
    });
    if (!output.cancelRequest?.ok || output.cancelRequest?.payload?.status !== "cancel_requested") {
      output.assertions.push("packaged Computer Use cancel request was not accepted");
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
      output.assertions.push("React Computer Use fallback appeared after native cancellation");
    }
    output.nativeActivityAfterCancel = await waitForNativeOverlay(false, 10000);
    if (output.nativeActivityAfterCancel?.info?.visible) {
      output.assertions.push("native Computer Use overlay did not disappear after cancellation");
    }
    output.resourceSnapshots.afterCancel = await resourceSnapshot();

    output.advancedSettingsRestore = await restoreAdvancedSettings(previousAdvancedSettings);
    if (!output.advancedSettingsRestore?.ok) {
      output.assertions.push("probe could not restore the original advanced setting values");
    } else {
      advancedSettingsRestoreNeeded = false;
    }

    output.streamingPlaceholdersAfterLaterWork = await waitForEval(
      cdp,
      `(() => {
        const turns = Array.from(document.querySelectorAll("[data-conversation-streaming-turn]"))
          .map((item) => item.getAttribute("data-conversation-streaming-turn") || "");
        return { ok: turns.length === 0, turns };
      })()`,
      30000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.streamingPlaceholdersAfterLaterWork?.ok) {
      output.assertions.push("a prior unanswered turn kept spinning after later work reached terminal state");
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

    if (launchedAppPid) {
      try {
        process.kill(launchedAppPid);
      } catch {}
      launchedAppPid = 0;
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
    output.launchedFixtureSurvivedAppClose = launchedFixturePid > 0 && processExists(launchedFixturePid);
    if (!output.launchedFixtureSurvivedAppClose) {
      output.assertions.push("closing VRCForge also terminated an external application launched by Computer Use");
    }
    if (launchedFixturePid) {
      try {
        process.kill(launchedFixturePid);
      } catch {}
      launchedFixturePid = 0;
    }
  } catch (error) {
    output.error = String(error && error.stack ? error.stack : error);
    output.assertions.push("probe threw before completion");
  } finally {
    if (launchedAppPid) {
      try {
        process.kill(launchedAppPid);
      } catch {}
      launchedAppPid = 0;
    }
    if (launchedFixturePid) {
      try {
        process.kill(launchedFixturePid);
      } catch {}
      launchedFixturePid = 0;
    }
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
