import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const exe = resolve(repoRoot, "dist", "VRCForge_Windows_x64", "VRCForge.exe");
const port = Number(process.env.VRCFORGE_CDP_PORT || "9343");
const marker = `DB_PROBE_${Date.now()}`;
const outPath = resolve(repoRoot, "artifacts", "actual-app-desktop-bridge", `desktop-bridge-${marker}.json`);
const appOrigin = process.env.VRCFORGE_APP_ORIGIN || "http://127.0.0.1:8757";
const appRequestOrigin = "tauri://localhost";
let appSessionToken = "";

const bridgeHintPattern = /桌面控制桥已连接|桌面控制橋已連接|Desktop control bridge connected|デスクトップ制御ブリッジ接続中/;

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

async function closeExistingVrcforgeProcesses() {
  await runPowerShell(`
    Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.ProcessName -eq 'VRCForge' -or $_.ProcessName -eq 'vrcforge_backend' -or $_.ProcessName -eq 'vrcforge-agentic-app' } |
      Stop-Process -Force -ErrorAction SilentlyContinue
  `);
  await waitForPortReleased(15000);
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
    $processes = Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.ProcessName -eq 'VRCForge' -or $_.ProcessName -eq 'vrcforge_backend' -or $_.ProcessName -eq 'vrcforge-agentic-app' } |
      Select-Object Id,ProcessName,Path
    $ports = Get-NetTCPConnection -LocalPort 8757 -ErrorAction SilentlyContinue |
      Select-Object LocalAddress,LocalPort,State,OwningProcess
    [pscustomobject]@{ processes = @($processes); ports = @($ports) } | ConvertTo-Json -Depth 4 -Compress
  `);
  return value ? JSON.parse(value) : { processes: [], ports: [] };
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

async function main() {
  await mkdir(dirname(outPath), { recursive: true });
  await closeExistingVrcforgeProcesses();
  const beforeLaunch = await processSnapshot();
  const child = spawn(exe, [], {
    detached: false,
    stdio: "ignore",
    env: {
      ...process.env,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --remote-allow-origins=*`,
    },
  });

  const output = {
    schema: "vrcforge.packaged_desktop_bridge_probe.v1",
    marker,
    beforeLaunch,
    assertions: [],
  };
  let cdp = null;
  try {
    const page = await waitForCdpTarget();
    cdp = connectCdp(page.webSocketDebuggerUrl);
    await cdp.opened;
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
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

    // Phase 1: no bridge connected -> explicit unavailable, no fake success.
    output.bridgeBaseline = await appApi("/api/app/agent/desktop-bridge");
    output.noBridgeAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: { action: "computer_use", prompt: `${marker} no bridge yet`, clientTurnId: `${marker}-turn-0` },
    });
    if (output.bridgeBaseline?.payload?.connected !== false) {
      output.assertions.push("bridge baseline should report connected=false before registration");
    }
    if (output.noBridgeAction?.payload?.status !== "unavailable") {
      output.assertions.push("desktop action without a bridge should be explicitly unavailable");
    }

    // Phase 2: register mock bridge -> requested lifecycle.
    output.register = await appApi("/api/app/agent/desktop-bridge/register", {
      method: "POST",
      body: { name: `${marker}-mock-bridge`, provider: "probe-mock", capabilities: ["computer_use", "desktop_rescue"] },
    });
    const bridgeId = output.register?.payload?.bridge?.bridgeId || "";
    if (!bridgeId) {
      output.assertions.push("bridge registration did not return a bridgeId");
    }
    output.heartbeat = await appApi("/api/app/agent/desktop-bridge/heartbeat", {
      method: "POST",
      body: { bridgeId },
    });
    output.bridgeConnected = await appApi("/api/app/agent/desktop-bridge");
    if (output.bridgeConnected?.payload?.connected !== true) {
      output.assertions.push("bridge status should report connected=true after register+heartbeat");
    }

    output.requestedAction = await appApi("/api/app/agent/desktop-actions", {
      method: "POST",
      body: { action: "computer_use", prompt: `${marker} open mock window`, clientTurnId: `${marker}-turn-1` },
    });
    const actionId = output.requestedAction?.payload?.actionId || "";
    if (output.requestedAction?.payload?.status !== "requested" || !actionId) {
      output.assertions.push("desktop action with live bridge should be requested with an actionId");
    }

    // Right rail should show the bridge hint and the requested action (snapshot poll <= 15s).
    output.railRequested = await waitForEval(
      cdp,
      `(() => {
        const asides = Array.from(document.querySelectorAll("aside")).map((node) => node.innerText || "");
        const rightRailText = asides[asides.length - 1] || "";
        const bridgeHint = ${bridgeHintPattern.toString()}.test(rightRailText);
        const hasAction = rightRailText.includes("computer_use");
        const hasRequested = rightRailText.includes("requested");
        return { ok: bridgeHint && hasAction && hasRequested, bridgeHint, hasAction, hasRequested, rightRailText: rightRailText.slice(0, 2000) };
      })()`,
      45000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.railRequested?.ok) {
      output.assertions.push("right rail did not show bridge hint + requested desktop action");
    }

    // Phase 3: mock bridge claims and completes; UI reflects completion.
    output.claim = await appApi("/api/app/agent/desktop-actions/claim", {
      method: "POST",
      body: { bridgeId },
    });
    if (output.claim?.payload?.action?.actionId !== actionId) {
      output.assertions.push("mock bridge claim did not return the requested action");
    }
    output.complete = await appApi("/api/app/agent/desktop-actions/complete", {
      method: "POST",
      body: {
        bridgeId,
        actionId,
        status: "completed",
        result: { summary: `${marker} mock window opened`, windowTitle: "Mock Window" },
      },
    });
    if (output.complete?.payload?.ok !== true) {
      output.assertions.push("mock bridge completion did not return ok=true");
    }

    output.listing = await appApi("/api/app/agent/desktop-actions?limit=20");
    const mergedRow = (output.listing?.payload?.actions || []).find((row) => row.actionId === actionId);
    if (!mergedRow || mergedRow.status !== "completed") {
      output.assertions.push("desktop action listing did not merge the lifecycle into a completed row");
    }

    output.railCompleted = await waitForEval(
      cdp,
      `(() => {
        const asides = Array.from(document.querySelectorAll("aside")).map((node) => node.innerText || "");
        const rightRailText = asides[asides.length - 1] || "";
        const hasCompleted = rightRailText.includes("completed");
        const hasAction = rightRailText.includes("computer_use");
        return { ok: hasCompleted && hasAction, hasCompleted, hasAction, rightRailText: rightRailText.slice(0, 2000) };
      })()`,
      45000,
    ).catch((error) => ({ ok: false, error: String(error) }));
    if (!output.railCompleted?.ok) {
      output.assertions.push("right rail did not show the completed desktop action");
    }
  } catch (error) {
    output.error = String(error && error.stack ? error.stack : error);
    output.assertions.push("probe threw before completion");
  } finally {
    if (cdp) {
      cdp.close();
    }
    try {
      child.kill();
    } catch {}
    await closeExistingVrcforgeProcesses().catch((error) => {
      output.cleanupError = String(error);
    });
    output.afterCleanup = await processSnapshot().catch((error) => ({ error: String(error) }));
    await writeFile(outPath, JSON.stringify(output, null, 2), "utf8");
    console.log(outPath);
    if (output.assertions.length) {
      console.error(output.assertions.join("\n"));
      process.exitCode = 1;
    }
  }
}

main();
