import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const packagedRoot = resolve(repoRoot, "dist", "VRCForge_Windows_x64");
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

async function waitForPortReleased(port, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const listeners = await runPowerShell(`
      $rows = Get-NetTCPConnection -LocalPort ${port} -State Listen -ErrorAction SilentlyContinue
      if ($rows) { $rows.Count } else { 0 }
    `);
    if (Number(listeners || 0) === 0) {
      return;
    }
    await sleep(250);
  }
  throw new Error(`Port ${port} remained in use.`);
}

async function closePackagedProcesses() {
  const escapedRoot = packagedRoot.replaceAll("'", "''");
  await runPowerShell(`
    $root = '${escapedRoot}'
    Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Path -and $_.Path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) } |
      Stop-Process -Force -ErrorAction SilentlyContinue
  `);
  await Promise.all([waitForPortReleased(8757), waitForPortReleased(cdpPort)]);
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

async function launchPackagedApp(requireComposerEnabled = true) {
  const child = spawn(exe, [], {
    detached: false,
    stdio: "ignore",
    env: {
      ...process.env,
      VRCFORGE_USER_DATA_DIR: userDataRoot,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${cdpPort} --remote-allow-origins=*`,
    },
  });
  const targets = await waitForJson(`http://127.0.0.1:${cdpPort}/json/list`, 45000);
  const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
  if (!page) {
    throw new Error("Packaged WebView2 page target was not found.");
  }
  const cdp = connectCdp(page.webSocketDebuggerUrl);
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
  return { child, cdp };
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
  await closePackagedProcesses();
  const provider = createFakeProvider();
  const providerPort = await provider.listen();
  const report = {
    schema: "vrcforge.packaged_goal_delivery_probe.v1",
    marker,
    exe,
    userDataRoot,
    providerPort,
    assertions: [],
  };
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

    app.cdp.close();
    await closePackagedProcesses();
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
    app.cdp.close();
    await closePackagedProcesses();
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
    if (app?.cdp) app.cdp.close();
    await closePackagedProcesses().catch(() => {});
    await provider.close().catch(() => {});
    await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  }
  console.log(reportPath);
  if (report.assertions.length > 0) {
    console.error(`Packaged goal delivery probe failed: ${report.assertions.join("; ")}`);
    process.exitCode = 1;
  }
}

main().catch(async (error) => {
  await mkdir(dirname(reportPath), { recursive: true });
  await writeFile(
    reportPath,
    `${JSON.stringify({ schema: "vrcforge.packaged_goal_delivery_probe.v1", marker, ok: false, error: String(error?.stack || error) }, null, 2)}\n`,
    "utf8",
  ).catch(() => {});
  console.error(error);
  process.exit(1);
});
