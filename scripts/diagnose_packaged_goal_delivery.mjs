import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { requestPackagedAppQuit } from "./lib/packaged_app_lifecycle.mjs";

const repoRoot = resolve(import.meta.dirname, "..");
const packagedRoot = resolve(repoRoot, "dist", "VRCForge_Windows_x64");
const packagedRootPowerShell = packagedRoot.replaceAll("'", "''");
const exe = resolve(packagedRoot, "VRCForge.exe");
const cdpPort = Number(process.env.VRCFORGE_GOAL_PROBE_CDP_PORT || "9347");
const marker = `GOAL_RESTART_PROBE_${Date.now()}`;
const evidenceRoot = resolve(repoRoot, "artifacts", "actual-app-goal-delivery", marker);
const userDataRoot = resolve(evidenceRoot, "user-data");
const reportPath = resolve(evidenceRoot, "report.json");
const appOrigin = "http://127.0.0.1:8757";
const appRequestOrigin = "http://tauri.localhost";
let appSessionToken = "";

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function runPowerShell(script) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
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

async function processSnapshot() {
  const value = await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${packagedRootPowerShell}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $processes = @(foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { continue }
      if ($path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        [pscustomobject]@{ Id=$process.Id; ProcessName=$process.ProcessName; Path=$path }
      }
    })
    $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object { $_.LocalPort -eq 8757 -or $_.LocalPort -eq ${cdpPort} } |
      Select-Object LocalAddress,LocalPort,State,OwningProcess
    [pscustomobject]@{ processes=@($processes); ports=@($ports) } | ConvertTo-Json -Depth 4 -Compress
  `);
  return value ? JSON.parse(value) : { processes: [], ports: [] };
}

function snapshotHasResidue(snapshot) {
  return Boolean((snapshot?.processes || []).length || (snapshot?.ports || []).length);
}

async function assertProbePreflightClear() {
  const snapshot = await processSnapshot();
  if (snapshotHasResidue(snapshot)) {
    throw new Error(`Preflight found an existing packaged-root process or occupied 8757/probe CDP port; nothing was terminated: ${JSON.stringify(snapshot)}`);
  }
  return snapshot;
}

async function waitForAppShutdown(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await processSnapshot();
    if (!snapshotHasResidue(latest)) return latest;
    await sleep(200);
  }
  return latest || processSnapshot();
}

async function captureLaunchIdentity(processId) {
  const value = await runPowerShell(`
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
      $process = Get-Process -Id ${Number(processId)} -ErrorAction SilentlyContinue
      if ($process) {
        try { $candidatePath = [IO.Path]::GetFullPath([string]$process.Path) } catch { $candidatePath = '' }
        if ($candidatePath) { break }
      }
      Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $process) { throw 'Tracked packaged process exited before its identity could be captured.' }
    try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { throw 'Tracked packaged process path was unavailable.' }
    $expected = [IO.Path]::GetFullPath('${packagedRootPowerShell}\\VRCForge.exe')
    if (-not $path.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
      throw 'Tracked packaged PID did not resolve to the expected executable.'
    }
    [pscustomobject]@{
      id = [int]$process.Id
      path = $path
      startedAtUtc = $process.StartTime.ToUniversalTime().ToString('o')
    } | ConvertTo-Json -Compress
  `);
  return JSON.parse(value);
}

async function listenerOwnedByLaunch(identity) {
  if (!identity?.id || !identity?.startedAtUtc) return false;
  const value = await runPowerShell(`
    $rootProcessId = [int]${Number(identity.id)}
    $rootProcess = Get-Process -Id $rootProcessId -ErrorAction SilentlyContinue
    if (-not $rootProcess) { 'false'; exit 0 }
    try { $rootPath = [IO.Path]::GetFullPath([string]$rootProcess.Path) } catch { 'false'; exit 0 }
    $expectedPath = [IO.Path]::GetFullPath('${packagedRootPowerShell}\\VRCForge.exe')
    $expectedStart = [DateTime]::Parse('${String(identity.startedAtUtc).replaceAll("'", "''")}').ToUniversalTime()
    if (-not $rootPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)) { 'false'; exit 0 }
    if ($rootProcess.StartTime.ToUniversalTime() -ne $expectedStart) { 'false'; exit 0 }
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $ids = [Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($rootProcessId)
    do {
      $added = $false
      foreach ($candidate in $all) {
        if ($ids.Contains([int]$candidate.ParentProcessId) -and -not $ids.Contains([int]$candidate.ProcessId)) {
          [void]$ids.Add([int]$candidate.ProcessId)
          $added = $true
        }
      }
    } while ($added)
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort ${cdpPort} -ErrorAction SilentlyContinue)
    if ($listeners.Count -lt 1) { 'false'; exit 0 }
    foreach ($listener in $listeners) {
      if (-not $ids.Contains([int]$listener.OwningProcess)) { 'false'; exit 0 }
    }
    'true'
  `);
  return value.trim().toLowerCase() === "true";
}

async function forceCloseTrackedLaunch(identity) {
  if (!identity?.id || !identity?.startedAtUtc) return processSnapshot();
  await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${packagedRootPowerShell}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $expectedPath = [IO.Path]::GetFullPath('${packagedRootPowerShell}\\VRCForge.exe')
    $expectedStart = [DateTime]::Parse('${String(identity.startedAtUtc).replaceAll("'", "''")}').ToUniversalTime()
    $rootProcessId = [int]${Number(identity.id)}
    $rootProcess = Get-Process -Id $rootProcessId -ErrorAction SilentlyContinue
    if (-not $rootProcess) { exit 0 }
    try { $rootPath = [IO.Path]::GetFullPath([string]$rootProcess.Path) } catch { exit 0 }
    if (-not $rootPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)) { exit 0 }
    if ($rootProcess.StartTime.ToUniversalTime() -ne $expectedStart) { exit 0 }
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $ids = [Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($rootProcessId)
    do {
      $added = $false
      foreach ($candidate in $all) {
        if ($ids.Contains([int]$candidate.ParentProcessId) -and -not $ids.Contains([int]$candidate.ProcessId)) {
          [void]$ids.Add([int]$candidate.ProcessId)
          $added = $true
        }
      }
    } while ($added)
    @(foreach ($candidateId in $ids) {
      $process = Get-Process -Id $candidateId -ErrorAction SilentlyContinue
      if (-not $process) { continue }
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { continue }
      if ($candidateId -eq $rootProcessId -or $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        $process
      }
    }) |
      Sort-Object @{ Expression = { if ($_.Id -eq $rootProcessId) { 1 } else { 0 } } } |
      Stop-Process -Force -ErrorAction SilentlyContinue
  `);
  return waitForAppShutdown(30000);
}

async function waitForJson(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return await response.json();
      }
      lastError = new Error(`${response.status} ${response.statusText}`);
    } catch (error) {
      lastError = error;
    }
    await sleep(150);
  }
  throw lastError || new Error(`Timed out waiting for ${url}`);
}

function connectCdp(webSocketDebuggerUrl) {
  const ws = new WebSocket(webSocketDebuggerUrl);
  let nextId = 1;
  const pending = new Map();
  ws.addEventListener("message", (event) => {
    const payload = JSON.parse(String(event.data));
    if (!payload.id || !pending.has(payload.id)) {
      return;
    }
    const request = pending.get(payload.id);
    pending.delete(payload.id);
    if (payload.error) {
      request.reject(new Error(payload.error.message || JSON.stringify(payload.error)));
    } else {
      request.resolve(payload.result);
    }
  });
  const opened = new Promise((resolveOpen, rejectOpen) => {
    ws.addEventListener("open", resolveOpen, { once: true });
    ws.addEventListener("error", rejectOpen, { once: true });
  });
  return {
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

async function evalValue(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(
      result.exceptionDetails.exception?.description || result.exceptionDetails.text || "Runtime.evaluate failed",
    );
  }
  return result.result?.value;
}

async function waitForEval(cdp, expression, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await evalValue(cdp, expression);
      if (last === true || last?.ok) {
        return last;
      }
    } catch (error) {
      last = String(error);
    }
    await sleep(150);
  }
  throw new Error(`Timed out waiting for renderer state; last=${JSON.stringify(last)}`);
}

let activeLaunch = null;

async function launchPackagedApp(requireComposerEnabled = true) {
  await assertProbePreflightClear();
  appSessionToken = "";
  const child = spawn(exe, [], {
    detached: false,
    stdio: "ignore",
    env: {
      ...process.env,
      VRCFORGE_USER_DATA_DIR: userDataRoot,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${cdpPort} --remote-allow-origins=*`,
    },
  });
  activeLaunch = { child, identity: null, cdp: null };
  activeLaunch.identity = await captureLaunchIdentity(child.pid);
  const targets = await waitForJson(`http://127.0.0.1:${cdpPort}/json/list`, 45000);
  if (!(await listenerOwnedByLaunch(activeLaunch.identity))) {
    throw new Error("Packaged probe CDP listener was not owned by the captured launch generation.");
  }
  const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
  if (!page) {
    throw new Error("Packaged WebView2 page target was not found.");
  }
  const cdp = connectCdp(page.webSocketDebuggerUrl);
  activeLaunch.cdp = cdp;
  await cdp.opened;
  await cdp.send("Runtime.enable");
  await cdp.send("Page.enable");
  await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      return {
        ok: Boolean(textarea && (${requireComposerEnabled ? "!textarea.disabled" : "true"})),
        bodyLength: document.body.innerText.length,
        disabled: textarea?.disabled ?? null,
      };
    })()`,
    45000,
  );
  await waitForJson(`${appOrigin}/api/health`, 45000);
  return activeLaunch;
}

async function shutdownPackagedApp(app, report, label) {
  if (!app) return null;
  const lifecycle = {
    label,
    quitRequest: { accepted: false, error: "CDP was unavailable before cleanup." },
    afterQuit: null,
    forcedCleanupUsed: false,
  };
  if (app.cdp) {
    const listenerOwned = await listenerOwnedByLaunch(app.identity).catch(() => false);
    lifecycle.quitRequest = listenerOwned
      ? await requestPackagedAppQuit(app.cdp).catch((error) => ({ accepted: false, error: String(error) }))
      : { accepted: false, listenerOwnershipChanged: true, error: "Tracked packaged CDP listener changed owner; no Quit was attempted." };
    app.cdp.close();
    app.cdp = null;
  }
  lifecycle.afterQuit = await waitForAppShutdown(20000)
    .catch((error) => ({ error: String(error), processes: [], ports: [] }));
  lifecycle.ok = Boolean(lifecycle.quitRequest.accepted && !snapshotHasResidue(lifecycle.afterQuit));
  if (!lifecycle.ok) {
    report.assertions.push(`${label}: explicit packaged-app Quit was not accepted or left an owned process/port alive`);
    if (snapshotHasResidue(lifecycle.afterQuit)) {
      lifecycle.forcedCleanupUsed = true;
      if (app.identity) {
        lifecycle.afterForcedCleanup = await forceCloseTrackedLaunch(app.identity)
          .catch((error) => ({ error: String(error) }));
      } else {
        lifecycle.identityCaptureFailed = true;
        lifecycle.unverifiedProcessPreserved = true;
        lifecycle.afterForcedCleanup = await waitForAppShutdown(20000)
          .catch((error) => ({ error: String(error) }));
      }
      report.assertions.push(`${label}: failure cleanup required forced termination of the exact probe-owned launch`);
    }
  }
  report.lifecycle.push(lifecycle);
  if (activeLaunch === app) activeLaunch = null;
  return lifecycle;
}

async function readAppToken() {
  const tokenPath = resolve(userDataRoot, "config", "app-session-token");
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const value = (await readFile(tokenPath, "utf8")).trim();
      if (value) {
        return value;
      }
    } catch {
      // Backend startup has not written the token yet.
    }
    await sleep(150);
  }
  throw new Error("Packaged app session token was not created.");
}

async function appApi(path, options = {}) {
  if (!appSessionToken) {
    appSessionToken = await readAppToken();
  }
  const response = await fetch(`${appOrigin}${path}`, {
    method: options.method || "GET",
    headers: {
      Origin: appRequestOrigin,
      Authorization: `Bearer ${appSessionToken}`,
      "Content-Type": "application/json",
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { text: text.slice(0, 2000) };
  }
  if (!response.ok) {
    throw new Error(`${response.status} ${path}: ${JSON.stringify(payload)}`);
  }
  return payload;
}

async function typeAndSubmit(cdp, text) {
  const typed = await evalValue(
    cdp,
    `(async () => {
      const textarea = document.querySelector("textarea");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      textarea.focus();
      setter.call(textarea, ${JSON.stringify(text)});
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolveFrame) => requestAnimationFrame(resolveFrame));
      return { value: textarea.value, disabled: textarea.disabled };
    })()`,
  );
  const submitted = await evalValue(
    cdp,
    `(async () => {
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      const form = textarea?.closest("form");
      if (submit) submit.click(); else form?.requestSubmit();
      await new Promise((resolveFrame) => requestAnimationFrame(resolveFrame));
      return { ok: Boolean(submit || form), disabled: submit?.disabled ?? null };
    })()`,
  );
  return { typed, submitted };
}

function createFakeProvider() {
  const requests = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) {
      chunks.push(chunk);
    }
    const rawBody = Buffer.concat(chunks).toString("utf8");
    let body = {};
    try { body = rawBody ? JSON.parse(rawBody) : {}; } catch { body = {}; }
    requests.push({ method: request.method, url: request.url, stream: body.stream === true, model: body.model || "" });
    if (request.method === "GET" && request.url === "/v1/models") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ object: "list", data: [{ id: "vrcforge-goal-probe", object: "model" }] }));
      return;
    }
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { message: "not found" } }));
      return;
    }
    const content = JSON.stringify({
      action: "reply",
      summary: `PACKAGED_GOAL_RESULT ${marker}`,
      reply: `PACKAGED_GOAL_RESULT ${marker}`,
    });
    if (body.stream === true) {
      response.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      });
      response.write(`data: ${JSON.stringify({
        id: "chatcmpl-goal-probe",
        object: "chat.completion.chunk",
        created: Math.floor(Date.now() / 1000),
        model: body.model || "vrcforge-goal-probe",
        choices: [{ index: 0, delta: { role: "assistant", content }, finish_reason: null }],
      })}\n\n`);
      response.write(`data: ${JSON.stringify({
        id: "chatcmpl-goal-probe",
        object: "chat.completion.chunk",
        created: Math.floor(Date.now() / 1000),
        model: body.model || "vrcforge-goal-probe",
        choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
        usage: { prompt_tokens: 12, completion_tokens: 4, total_tokens: 16 },
      })}\n\n`);
      response.end("data: [DONE]\n\n");
      return;
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({
      id: "chatcmpl-goal-probe",
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model: body.model || "vrcforge-goal-probe",
      choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
      usage: { prompt_tokens: 12, completion_tokens: 4, total_tokens: 16 },
    }));
  });
  return {
    requests,
    async listen() {
      await new Promise((resolveListen, rejectListen) => {
        server.once("error", rejectListen);
        server.listen(0, "127.0.0.1", resolveListen);
      });
      return server.address().port;
    },
    close() {
      return new Promise((resolveClose) => server.close(resolveClose));
    },
  };
}

function findGoal(payload) {
  return (payload.goals || []).find((goal) => goal.title === marker);
}

function findChat(payload, chatId) {
  return (payload.chats || []).find((chat) => chat.id === chatId);
}

async function waitForGoalCompletion(goalId, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    const payload = await appApi("/api/app/agent/goals?limit=100");
    latest = (payload.goals || []).find((goal) => goal.goalId === goalId);
    if (latest?.wakeCount === 1 && !latest.wakeAt) {
      return latest;
    }
    await sleep(500);
  }
  throw new Error(`Goal did not complete after restart: ${JSON.stringify(latest)}`);
}

async function main() {
  await mkdir(evidenceRoot, { recursive: true });
  const beforeLaunch = await assertProbePreflightClear();
  const provider = createFakeProvider();
  const providerPort = await provider.listen();
  const report = {
    schema: "vrcforge.packaged_goal_delivery_probe.v1",
    marker,
    exe,
    userDataRoot,
    providerPort,
    beforeLaunch,
    assertions: [],
    lifecycle: [],
  };
  finalReport = report;
  let app;
  try {
    app = await launchPackagedApp(false);
    const configured = await appApi("/api/config", {
      method: "POST",
      body: {
        provider: "custom",
        api_key: "local-probe-key",
        base_url: `http://127.0.0.1:${providerPort}/v1`,
        model: "vrcforge-goal-probe",
      },
    });
    report.config = {
      provider: configured.apiConfig?.provider,
      model: configured.apiConfig?.model,
      baseUrlConfigured: Boolean(configured.apiConfig?.base_url || configured.apiConfig?.baseUrl),
    };
    await app.cdp.send("Page.reload", { ignoreCache: true });
    await waitForEval(app.cdp, `Boolean(document.querySelector("textarea") && !document.querySelector("textarea").disabled)`, 45000);
    report.goalCommand = await typeAndSubmit(app.cdp, `/goal ${marker} +30m`);

    const goalDeadline = Date.now() + 30000;
    let goal;
    while (Date.now() < goalDeadline && !goal) {
      goal = findGoal(await appApi("/api/app/agent/goals?limit=100"));
      if (!goal) await sleep(250);
    }
    if (!goal) {
      throw new Error("The packaged composer did not create the scheduled goal.");
    }
    report.createdGoal = goal;
    const chatsBeforeRestart = await appApi("/api/app/chats");
    report.ownerBeforeRestart = findChat(chatsBeforeRestart, goal.chatId) || null;
    if (!report.ownerBeforeRestart) {
      report.assertions.push("scheduled goal owner chat was not persisted before restart");
    }
    report.armedGoal = (await appApi(`/api/app/agent/goals/${encodeURIComponent(goal.goalId)}`, {
      method: "POST",
      body: { status: "active", wakeAt: new Date(Date.now() - 60_000).toISOString() },
    })).goal;

    await shutdownPackagedApp(app, report, "restart-for-goal-recovery");
    app = null;
    app = await launchPackagedApp();
    const completedGoal = await waitForGoalCompletion(goal.goalId);
    await sleep(1500);
    const completedChats = await appApi("/api/app/chats");
    const completedChat = findChat(completedChats, goal.chatId);
    const completedItemsJson = JSON.stringify(completedChat?.items || []);
    const completedUserItem = completedChat?.items?.find((item) => item.type === "user");
    const completedAgentItem = completedChat?.items?.find((item) => item.type === "agent");
    const recoverableAfterSave = await appApi(`/api/app/agent/goals/deliveries/recoverable?chatId=${encodeURIComponent(goal.chatId)}`);
    report.afterRecovery = {
      goal: completedGoal,
      chat: completedChat ? {
        id: completedChat.id,
        title: completedChat.title,
        sessionId: completedChat.sessionId,
        itemCount: completedChat.items?.length || 0,
        itemIds: (completedChat.items || []).map((item) => item.id),
        userText: completedUserItem?.text || "",
        agentGoalDeliveryId: completedAgentItem?.response?.goalDeliveryId || "",
        agentReply: completedAgentItem?.response?.plan?.reply || "",
        agentOk: completedAgentItem?.response?.ok === true,
      } : null,
      providerRequestCount: provider.requests.length,
      recoverableCount: recoverableAfterSave.count,
    };
    if (!completedItemsJson.includes(marker)) {
      report.assertions.push("resumed goal user turn was not saved in its owner chat");
    }
    if (!completedItemsJson.includes(`PACKAGED_GOAL_RESULT ${marker}`)) {
      report.assertions.push("resumed goal agent result was not saved in its owner chat");
    }
    if (recoverableAfterSave.count !== 0) {
      report.assertions.push("completed delivery was not acknowledged after chat persistence");
    }
    if (provider.requests.length < 1) {
      report.assertions.push("packaged WebView did not dispatch the due goal to the provider");
    }

    const requestCountBeforeSecondRestart = provider.requests.length;
    const itemCountBeforeSecondRestart = completedChat?.items?.length || 0;
    await shutdownPackagedApp(app, report, "restart-for-idempotency-proof");
    app = null;
    app = await launchPackagedApp();
    await sleep(8000);
    const finalGoal = findGoal(await appApi("/api/app/agent/goals?limit=100"));
    const finalChat = findChat(await appApi("/api/app/chats"), goal.chatId);
    report.afterIdempotencyRestart = {
      goal: finalGoal || null,
      providerRequestCount: provider.requests.length,
      chatItemCount: finalChat?.items?.length || 0,
    };
    if (provider.requests.length !== requestCountBeforeSecondRestart) {
      report.assertions.push("materialized goal was dispatched again after another restart");
    }
    if ((finalChat?.items?.length || 0) !== itemCountBeforeSecondRestart) {
      report.assertions.push("materialized goal duplicated chat items after another restart");
    }
    if (finalGoal?.wakeCount !== 1 || finalGoal?.wakeAt) {
      report.assertions.push("one-shot goal schedule was not durably consumed exactly once");
    }
    report.providerRequests = provider.requests;
  } finally {
    const cleanupTarget = app || activeLaunch;
    if (cleanupTarget) {
      await shutdownPackagedApp(cleanupTarget, report, "final-probe-shutdown");
    }
    await provider.close().catch(() => {});
    await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  }
  console.log(reportPath);
  if (report.assertions.length > 0) {
    console.error(`Packaged goal delivery probe failed: ${report.assertions.join("; ")}`);
    process.exitCode = 1;
  }
}

let finalReport = null;

main().catch(async (error) => {
  await mkdir(dirname(reportPath), { recursive: true });
  await writeFile(
    reportPath,
    `${JSON.stringify({
      schema: "vrcforge.packaged_goal_delivery_probe.v1",
      marker,
      ok: false,
      error: String(error?.stack || error),
      assertions: finalReport?.assertions || ["probe threw before lifecycle evidence could be initialized"],
      lifecycle: finalReport?.lifecycle || [],
    }, null, 2)}\n`,
    "utf8",
  ).catch(() => {});
  console.error(error);
  process.exit(1);
});
