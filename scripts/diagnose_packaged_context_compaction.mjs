/*
 * Actual packaged WebView acceptance for VRCForge context compaction.
 *
 * This probe deliberately has no production-only switches.  It drives the
 * same Tauri IPC, persisted chat records, and composer DOM used by the app.
 * The probe owns an isolated loopback chat-completions fixture, configures it
 * through the real product settings command, and never reads a user key.
 */
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const allowUnpushed = process.argv.includes("--allow-unpushed");
const cdpPort = Number(process.env.VRCFORGE_CONTEXT_PROBE_CDP_PORT || "9354");
const marker = `CONTEXT_COMPACTION_PROBE_${Date.now()}`;
const evidenceRoot = resolve(repoRoot, "artifacts", "actual-app-context-compaction", marker);
const packageRoot = resolve(evidenceRoot, "package");
const exe = resolve(packageRoot, "VRCForge.exe");
const userDataRoot = resolve(evidenceRoot, "user-data");
const configRoot = resolve(userDataRoot, "config");
const webviewRoot = resolve(evidenceRoot, "webview2-user-data");
const reportPath = resolve(evidenceRoot, "report.json");
const appOrigin = "http://127.0.0.1:8757";
const appRequestOrigin = "http://tauri.localhost";
let token = "";

const allowedOptions = new Set(["--allow-unpushed", "--help", "-h"]);
if (process.argv.slice(2).some((item) => !allowedOptions.has(item))) {
  console.error("Unknown packaged context-compaction probe option.");
  process.exit(2);
}

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`Usage: node scripts/diagnose_packaged_context_compaction.mjs [--allow-unpushed]

Runs the strict packaged context-compaction acceptance probe.
The packaged app must be built from HEAD and origin/main. The probe starts its
own loopback chat-completions-compatible fixture and configures it through the packaged
Tauri provider-settings command; no user key or provider environment is used.

Default mode is strict-release evidence: a clean worktree, HEAD=origin/main,
manifest commit/version, an exact ZIP/payload digest, and strict release-eligible
buildPolicy with every Allow* flag false are mandatory.
--allow-unpushed is only local-preacceptance: it still verifies VERSION, manifest,
ZIP, and extracted payload hashes, but accepts only an explicitly non-release
local build policy and writes strictReleaseBinding=false to its report. It can
never be used as strict release evidence.
Optional: VRCFORGE_CONTEXT_PROBE_CDP_PORT=<unused port> (default ${cdpPort})`);
  process.exit(0);
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));
const assertion = (report, message) => {
  if (!report.assertions.includes(message)) report.assertions.push(message);
};
const psLiteral = (value) => String(value).replaceAll("'", "''");

function powershell(script) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], { windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.on("error", rejectRun);
    child.on("close", (code) => code === 0 ? resolveRun(stdout.trim()) : rejectRun(new Error(stderr.trim() || stdout.trim() || `PowerShell exited ${code}`)));
  });
}

function sha256File(path) {
  return new Promise((resolveHash, rejectHash) => {
    const hash = createHash("sha256");
    const stream = createReadStream(path);
    stream.on("error", rejectHash);
    stream.on("data", (chunk) => hash.update(chunk));
    stream.on("end", () => resolveHash(hash.digest("hex")));
  });
}

async function strictPackage() {
  const sourceVersion = (await readFile(resolve(repoRoot, "VERSION"), "utf8")).trim();
  const manifestPath = resolve(repoRoot, "dist", "release", "release-manifest.json");
  const manifest = JSON.parse((await readFile(manifestPath, "utf8")).replace(/^\uFEFF/, ""));
  const head = (await powershell(`git -C '${psLiteral(repoRoot)}' rev-parse HEAD`)).toLowerCase();
  const origin = (await powershell(`git -C '${psLiteral(repoRoot)}' rev-parse origin/main`)).toLowerCase();
  const worktreeClean = (await powershell(`git -C '${psLiteral(repoRoot)}' status --porcelain=v1`)) === "";
  const manifestCommit = String(manifest.commit || "").trim().toLowerCase();
  const buildPolicy = normalizeBuildPolicy(manifest);
  const strictPolicy = isStrictBuildPolicy(buildPolicy);
  const localPolicy = isLocalPreacceptancePolicy(buildPolicy);
  const commitsValid = [head, origin, manifestCommit].every((value) => /^[0-9a-f]{40}$/.test(value));
  const baseBinding = commitsValid && manifestCommit === head && String(manifest.version || "") === sourceVersion;
  if (!baseBinding) throw new Error("Packaged probe requires manifest version and commit to bind the current local HEAD.");
  if (!allowUnpushed && (!worktreeClean || head !== origin || !strictPolicy)) {
    throw new Error("Strict packaged probe requires clean HEAD=origin/main and strict release-eligible buildPolicy with every Allow* flag false.");
  }
  if (allowUnpushed && !localPolicy) {
    throw new Error("--allow-unpushed requires an explicit non-release local-preacceptance buildPolicy; strict/release-eligible manifests are refused in local mode.");
  }
  const name = `VRCForge_Windows_x64_${sourceVersion}.zip`;
  const artifact = (manifest.artifacts || []).find((item) => item?.name === name);
  const archive = resolve(dirname(manifestPath), name);
  if (!artifact || !/^[0-9a-f]{64}$/i.test(String(artifact.sha256 || "")) || await sha256File(archive) !== String(artifact.sha256).toLowerCase()) {
    throw new Error("Portable archive is absent or does not match its strict manifest digest.");
  }
  await mkdir(evidenceRoot, { recursive: true });
  const payload = JSON.parse(await powershell(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead('${psLiteral(archive)}')
    try {
      $entries = @($archive.Entries)
      $main = @($entries | Where-Object { $_.FullName.Replace('\\', '/').Equals('VRCForge.exe', [StringComparison]::OrdinalIgnoreCase) })
      $backend = @($entries | Where-Object { $_.FullName.Replace('\\', '/').Equals('backend/vrcforge_backend.exe', [StringComparison]::OrdinalIgnoreCase) })
      if ($main.Count -ne 1 -or $backend.Count -ne 1) { throw 'Portable payload must contain exactly one main executable and backend executable.' }
      function Get-Digest($entry) { $sha=[Security.Cryptography.SHA256]::Create(); $stream=$entry.Open(); try { [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() } finally { $stream.Dispose(); $sha.Dispose() } }
      [pscustomobject]@{ mainSha256=(Get-Digest $main[0]); backendSha256=(Get-Digest $backend[0]) } | ConvertTo-Json -Compress
    } finally { $archive.Dispose() }
  `));
  await powershell(`Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory('${psLiteral(archive)}', '${psLiteral(packageRoot)}')`);
  const embeddedVersion = (await readFile(resolve(packageRoot, "VERSION"), "utf8")).trim();
  const mainSha256 = await sha256File(exe);
  const backendSha256 = await sha256File(resolve(packageRoot, "backend", "vrcforge_backend.exe"));
  if (embeddedVersion !== sourceVersion || mainSha256 !== String(payload.mainSha256 || "").toLowerCase() || backendSha256 !== String(payload.backendSha256 || "").toLowerCase()) {
    throw new Error("Extracted package payload did not match its ZIP entries and VERSION.");
  }
  return {
    version: sourceVersion, manifestCommit, headCommit: head, originMainCommit: origin, worktreeClean,
    archive: name, archiveSha256: artifact.sha256, embeddedVersion, mainSha256, backendSha256,
    buildPolicy, strictBuildPolicy: strictPolicy, strictReleaseBinding: !allowUnpushed && worktreeClean && head === origin && strictPolicy,
  };
}

function normalizeBuildPolicy(manifest) {
  const raw = manifest?.buildPolicy && typeof manifest.buildPolicy === "object" ? manifest.buildPolicy : {};
  return { mode: String(raw.mode || ""), releaseEligible: raw.releaseEligible === true, allowDirty: raw.allowDirty === true, allowUnpushed: raw.allowUnpushed === true, allowVersionMismatch: raw.allowVersionMismatch === true };
}

function isStrictBuildPolicy(policy) {
  return policy.mode === "strict" && policy.releaseEligible === true && policy.allowDirty === false && policy.allowUnpushed === false && policy.allowVersionMismatch === false;
}

function isLocalPreacceptancePolicy(policy) {
  return policy.releaseEligible === false && !isStrictBuildPolicy(policy) && (policy.allowDirty || policy.allowUnpushed || policy.allowVersionMismatch || /local|preacceptance/i.test(policy.mode));
}

async function snapshot() {
  const raw = await powershell(`
    $root = [IO.Path]::GetFullPath('${psLiteral(packageRoot)}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $processes = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
      try { [IO.Path]::GetFullPath([string]$_.Path).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
    } | ForEach-Object { [pscustomobject]@{ pid=$_.Id; name=$_.ProcessName } })
    $ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq 8757 -or $_.LocalPort -eq ${cdpPort} } | Select-Object LocalPort,OwningProcess)
    [pscustomobject]@{ processes=$processes; ports=$ports } | ConvertTo-Json -Compress -Depth 4
  `);
  return raw ? JSON.parse(raw) : { processes: [], ports: [] };
}
const clear = (value) => !(value?.processes?.length || value?.ports?.length);
async function waitClear(timeout = 30000) {
  const end = Date.now() + timeout;
  let latest = await snapshot();
  while (Date.now() < end && !clear(latest)) { await sleep(250); latest = await snapshot(); }
  return latest;
}

function cdpConnection(url) {
  const socket = new WebSocket(url);
  let id = 0;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(String(event.data));
    const request = pending.get(payload.id);
    if (!request) return;
    pending.delete(payload.id);
    payload.error ? request.reject(new Error(payload.error.message || JSON.stringify(payload.error))) : request.resolve(payload.result);
  });
  const opened = new Promise((resolveOpen, rejectOpen) => {
    socket.addEventListener("open", resolveOpen, { once: true });
    socket.addEventListener("error", rejectOpen, { once: true });
  });
  return { opened, close: () => socket.close(), send(method, params = {}) {
    const requestId = ++id;
    socket.send(JSON.stringify({ id: requestId, method, params }));
    return new Promise((resolveSend, rejectSend) => pending.set(requestId, { resolve: resolveSend, reject: rejectSend }));
  }};
}
async function waitJson(url, timeout = 45000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try { const response = await fetch(url); if (response.ok) return await response.json(); last = `${response.status}`; } catch (error) { last = String(error); }
    await sleep(150);
  }
  throw new Error(`Timed out waiting for ${url}: ${last || "unknown"}`);
}
async function evaluate(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "renderer evaluation failed");
  return result.result?.value;
}
async function waitEval(cdp, expression, timeout = 45000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try { last = await evaluate(cdp, expression); if (last?.ok || last === true) return last; } catch (error) { last = String(error); }
    await sleep(150);
  }
  throw new Error(`Timed out waiting for renderer state: ${JSON.stringify(last)}`);
}

async function waitForValue(load, predicate, label, timeout = 120000) {
  const end = Date.now() + timeout;
  let latest;
  while (Date.now() < end) {
    latest = await load();
    if (predicate(latest)) return latest;
    await sleep(150);
  }
  throw new Error(`Timed out waiting for ${label}: ${JSON.stringify(latest)}`);
}

function isolatedEnvironment() {
  const env = { ...process.env };
  delete env.VRCFORGE_APP_SESSION_TOKEN;
  Object.assign(env, {
    VRCFORGE_USER_DATA_DIR: userDataRoot,
    VRCFORGE_CONFIG_DIR: configRoot,
    VRCFORGE_CONFIG_PATH: resolve(configRoot, "config.json"),
    VRCFORGE_SETTINGS_PATH: resolve(configRoot, "settings.json"),
    VRCFORGE_LOG_DIR: resolve(userDataRoot, "logs"),
    VRCFORGE_ARTIFACTS_DIR: resolve(userDataRoot, "artifacts"),
    WEBVIEW2_USER_DATA_FOLDER: webviewRoot,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${cdpPort} --remote-allow-origins=*`,
  });
  return env;
}
async function launch() {
  const child = spawn(exe, [], { stdio: "ignore", env: isolatedEnvironment() });
  const targets = await waitJson(`http://127.0.0.1:${cdpPort}/json/list`);
  const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
  if (!page) throw new Error("Packaged WebView page target was not found.");
  const cdp = cdpConnection(page.webSocketDebuggerUrl);
  await cdp.opened;
  await cdp.send("Runtime.enable");
  await waitEval(cdp, `(() => ({ ok: Boolean(document.body?.innerText && window.__TAURI_INTERNALS__?.invoke), tauri: typeof window.__TAURI_INTERNALS__?.invoke }))()`);
  await waitJson(`${appOrigin}/api/health`);
  return { child, cdp };
}
async function appToken() {
  if (token) return token;
  const path = resolve(configRoot, "app-session-token");
  const end = Date.now() + 30000;
  while (Date.now() < end) {
    try { token = (await readFile(path, "utf8")).trim(); if (token) return token; } catch { /* startup */ }
    await sleep(150);
  }
  throw new Error("The isolated packaged app did not create an app-session token.");
}
async function api(path, options = {}) {
  const response = await fetch(`${appOrigin}${path}`, {
    method: options.method || "GET",
    headers: { Origin: appRequestOrigin, Authorization: `Bearer ${await appToken()}`, "Content-Type": "application/json" },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  const value = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(`${response.status} ${path}: ${JSON.stringify(value)}`);
  return value;
}
async function invoke(cdp, command, request) {
  return evaluate(cdp, `(async () => {
    try { return { ok: true, value: await window.__TAURI_INTERNALS__.invoke(${JSON.stringify(command)}, ${JSON.stringify({ request })}) }; }
    catch (error) { return { ok: false, error: String(error?.message || error) }; }
  })()`);
}

async function waitForTauriSession(cdp, timeout = 45000) {
  const end = Date.now() + timeout;
  let latest = null;
  while (Date.now() < end) {
    latest = await invoke(cdp, "fetch_app_health", { timeoutMs: 5000 });
    if (latest?.ok && latest.value?.version) return latest.value;
    await sleep(250);
  }
  throw new Error(`Timed out waiting for the packaged Tauri/runtime session binding: ${JSON.stringify(latest)}`);
}

function probeChat(kind, usageRatio = 0.86, options = {}) {
  const now = new Date().toISOString();
  const words = Array.from({ length: 18 }, (_, index) => `evidence-${kind}-${index}: completion details and durable TODO state`).join("\n");
  const historyCharacters = Math.max(0, Math.floor(Number(options.historyCharacters) || 0));
  const userFiller = historyCharacters ? `\n${"u".repeat(Math.floor(historyCharacters / 2))}` : "";
  const agentFiller = historyCharacters ? `\n${"a".repeat(historyCharacters - Math.floor(historyCharacters / 2))}` : "";
  return {
    id: `${marker}-${kind}`, sessionId: `${marker}-${kind}-session`, title: `${marker} ${kind}`, projectPath: "", createdAt: now, updatedAt: now, revision: 1,
    contextUsageCache: { provider: "deepseek", model: "deepseek-v4-pro", exact: true, lastInputTokens: Math.floor(1_000_000 * usageRatio), peakInputTokens: Math.floor(1_000_000 * usageRatio), contextLimit: 1_000_000 },
    items: [
      { id: `${kind}-u1`, type: "user", text: `${marker} preserve goal and plan\n${words}${userFiller}`, createdAt: now },
      {
        id: `${kind}-a1`, type: "agent", createdAt: now,
        response: {
          ok: true, sessionId: `${marker}-${kind}-session`, turnId: `${marker}-${kind}-seed`,
          plan: { summary: `${marker} completed work and validation`, reply: `${marker} completed work and validation\n${words}${agentFiller}` },
          contextUsage: { provider: "deepseek", model: "deepseek-v4-pro", exact: true, inputTokens: Math.floor(1_000_000 * usageRatio), lastInputTokens: Math.floor(1_000_000 * usageRatio), peakInputTokens: Math.floor(1_000_000 * usageRatio) },
        },
      },
      {
        id: `${kind}-subagent`, type: "subagent",
        task: { id: `${marker}-${kind}-task`, role: "review", displayName: `${marker} ownership`, task: "Retain durable ownership.", status: "completed", mergeDecision: "adopted", handoffStatus: "adopted", summary: `${marker} ownership card`, result: { summaryText: `${marker} ownership evidence` }, createdAt: now, updatedAt: now },
      },
    ],
  };
}
async function saveProbeChats(cdp, chats) {
  const ipc = await invoke(cdp, "save_chats", { body: { chats }, timeoutMs: 60000 });
  if (!ipc?.ok || ipc?.value?.ok !== true) throw new Error(`Tauri save_chats failed: ${JSON.stringify(ipc)}`);
  const rest = await api("/api/app/chats");
  if (!Array.isArray(rest.chats) || !rest.chats.some((chat) => chat.id === chats[0].id)) throw new Error("REST did not read the chat saved through real Tauri IPC.");
  return { ipc, restCount: rest.count };
}
async function reload(cdp) {
  await cdp.send("Page.reload", { ignoreCache: true }).catch((error) => {
    if (!/Promise was collected/i.test(String(error))) throw error;
  });
  await waitEval(cdp, `(() => ({ ok: Boolean(document.body?.innerText && window.__TAURI_INTERNALS__?.invoke) }))()`);
  await sleep(1000);
}
async function activateProbeChat(cdp, title) {
  const result = await waitEval(cdp, `(() => {
    const wanted = ${JSON.stringify(title)};
    const leaf = Array.from(document.querySelectorAll("*")).find((node) => node.children.length === 0 && String(node.textContent || "").includes(wanted));
    const target = leaf?.closest("button, [role='button'], a, li, div");
    if (!target) return { ok:false, reason:"saved chat was not rendered in the sidebar" };
    target.click();
    return { ok:true, tag: target.tagName };
  })()`, 30000);
  if (!result?.ok) throw new Error(`Could not activate persisted probe chat: ${result?.reason || "unknown"}`);
  await sleep(100);
  return result;
}
async function uiSend(cdp, text) {
  const prepared = await evaluate(cdp, `(() => {
    const textarea = document.querySelector("textarea");
    if (!textarea) return { ok:false, reason:"composer textarea missing" };
    const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    set?.call(textarea, ${JSON.stringify(text)});
    textarea.dispatchEvent(new InputEvent("input", { bubbles:true, inputType:"insertText", data:${JSON.stringify(text)} }));
    return { ok:true };
  })()`);
  if (!prepared?.ok) return prepared;
  await sleep(100);
  return evaluate(cdp, `(() => {
    const buttons = Array.from(document.querySelectorAll("button"));
    const send = document.querySelector("button[type='submit']") || buttons.find((button) => !button.disabled && /send|发送|送信|傳送/i.test(String(button.getAttribute("aria-label") || button.textContent || "")));
    if (!send) return { ok:false, reason:"send button missing" };
    send.click();
    return { ok:true };
  })()`);
}
async function uiState(cdp) {
  return evaluate(cdp, `(() => ({
    status: document.querySelector("[data-context-compaction-status]")?.getAttribute("data-context-compaction-status") || "",
    cancel: Boolean(document.querySelector("[data-context-compaction-cancel]")),
    markers: Array.from(document.querySelectorAll("[data-conversation-item-type='compact'], [data-context-compaction-marker]")).length,
    text: document.body?.innerText?.slice(0, 20000) || ""
  }))()`);
}
async function requestClose(child) {
  await powershell(`$p=Get-Process -Id ${Number(child.pid)} -ErrorAction SilentlyContinue; if($p){ [void]$p.CloseMainWindow() }`);
  const final = await waitClear();
  return { final, graceful: clear(final) };
}

function createLoopbackProvider() {
  const requests = [];
  const waiters = new Set();
  const turnCounts = new Map();
  let cancellationDelayAvailable = true;
  const notify = (value) => {
    for (const waiter of [...waiters]) {
      if (waiter.matches(value)) {
        waiters.delete(waiter);
        clearTimeout(waiter.timer);
        waiter.resolve(value);
      }
    }
  };
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    let body = {};
    try { body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); } catch { /* malformed request is recorded */ }
    const prompt = JSON.stringify(body.messages || []);
    const record = { method: request.method, url: request.url, model: body.model || "", stream: body.stream === true, prompt };
    requests.push(record);
    if (request.method === "GET" && request.url === "/v1/models") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ object: "list", data: [{ id: "deepseek-v4-pro", object: "model", context_window: 1_000_000 }] }));
      return;
    }
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { message: "fixture endpoint not found" } }));
      return;
    }
    const isCompaction = prompt.includes("continuity-preserving context summary") && prompt.includes("REDACTED_ENTRIES=");
    const isCancelCase = prompt.includes("evidence-cancel-0") && cancellationDelayAvailable;
    if (isCompaction) {
      record.kind = isCancelCase ? "cancel-compaction" : "compaction";
      record.phase = /phase=([a-z_]+)/i.exec(prompt)?.[1] || "";
      notify(record);
      if (isCancelCase) {
        cancellationDelayAvailable = false;
        await sleep(5000);
      }
      const content = JSON.stringify({
        currentGoal: `retain ${marker} goal`, completed: ["fixture evidence retained"], decisions: ["use local fixture"],
        constraints: ["no user provider credentials"], todo: ["continue safely"], references: [marker], recentContext: ["bounded summary"],
      });
      return respondOpenAi(response, body, content, 12000);
    }
    const turnKey = [`${marker} prefire observation`, `${marker} pre-turn continuation`, `${marker} mid-turn continuation`, `${marker}-cancel`]
      .find((value) => prompt.includes(value)) || "other";
    const previousCalls = turnCounts.get(turnKey) || 0;
    turnCounts.set(turnKey, previousCalls + 1);
    const priorTool = previousCalls > 0;
    record.kind = priorTool ? "reply" : "tool";
    const content = priorTool
      ? JSON.stringify({ action: "reply", summary: `fixture completed ${marker}`, reply: `fixture completed ${marker}` })
      : JSON.stringify({ action: "skill", skill_tool: "vrcforge_skill_manifest", skill_params: {}, summary: `fixture tool ${marker}` });
    notify(record);
    if (turnKey === `${marker} prefire observation` && !priorTool) await sleep(2500);
    const inputTokens = estimateProviderInputTokens(body.messages || []);
    record.inputTokens = inputTokens;
    return respondOpenAi(response, body, content, inputTokens);
  });
  return {
    requests,
    async listen() { await new Promise((resolveListen, rejectListen) => { server.once("error", rejectListen); server.listen(0, "127.0.0.1", resolveListen); }); return server.address().port; },
    waitFor(kind, markerText = "", timeout = 45000, phase = "") {
      const matches = (item) => {
        if (markerText && !item.prompt.includes(markerText)) return false;
        return item.kind === kind && (!phase || item.phase === phase);
      };
      const existing = requests.find(matches);
      if (existing) return Promise.resolve(existing);
      return new Promise((resolveWaiter, rejectWaiter) => {
        const waiter = { matches, resolve: resolveWaiter, timer: setTimeout(() => {
          waiters.delete(waiter);
          rejectWaiter(new Error(`Timed out waiting for fixture ${kind}${phase ? `/${phase}` : ""}`));
        }, timeout) };
        waiters.add(waiter);
      });
    },
    close() { return new Promise((resolveClose) => server.close(resolveClose)); },
  };
}

function respondOpenAi(response, body, content, inputTokens) {
  const payload = { id: "chatcmpl-context-probe", object: "chat.completion", created: Math.floor(Date.now() / 1000), model: body.model || "deepseek-v4-pro", choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }], usage: { prompt_tokens: inputTokens, completion_tokens: 10, total_tokens: inputTokens + 10 } };
  if (body.stream === true) {
    response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    const common = { id: payload.id, object: "chat.completion.chunk", created: payload.created, model: payload.model };
    response.write(`data: ${JSON.stringify({ ...common, choices: [{ index: 0, delta: { role: "assistant", content }, finish_reason: null }] })}\n\n`);
    response.write(`data: ${JSON.stringify({ ...common, choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\n`);
    response.write(`data: ${JSON.stringify({ ...common, choices: [], usage: payload.usage })}\n\n`);
    response.end("data: [DONE]\n\n");
  } else { response.writeHead(200, { "Content-Type": "application/json" }); response.end(JSON.stringify(payload)); }
}

function estimateProviderInputTokens(messages) {
  const text = (Array.isArray(messages) ? messages : []).map((message) => {
    if (typeof message?.content === "string") return message.content;
    if (!Array.isArray(message?.content)) return "";
    return message.content.map((part) => typeof part?.text === "string" ? part.text : "").join("\n");
  }).join("\n");
  let quarterTokens = 0;
  for (const character of text) {
    const codepoint = character.codePointAt(0) || 0;
    const isCjk = (codepoint >= 0x3400 && codepoint <= 0x4DBF)
      || (codepoint >= 0x4E00 && codepoint <= 0x9FFF)
      || (codepoint >= 0xF900 && codepoint <= 0xFAFF)
      || (codepoint >= 0x3040 && codepoint <= 0x30FF)
      || (codepoint >= 0xAC00 && codepoint <= 0xD7AF);
    quarterTokens += isCjk ? 4 : Buffer.byteLength(character, "utf8");
  }
  return Math.max(1, Math.ceil(quarterTokens / 4));
}

async function configureLoopbackProvider(cdp, port) {
  const baseUrl = `http://127.0.0.1:${port}/v1`;
  const configured = await invoke(cdp, "update_api_config", {
    provider: "deepseek", api_key: "isolated-context-probe-key",
    base_url: baseUrl, model: "deepseek-v4-pro", timeoutMs: 30000,
  });
  if (
    !configured?.ok
    || configured.value?.apiConfig?.provider !== "deepseek"
    || configured.value?.apiConfig?.model !== "deepseek-v4-pro"
    || configured.value?.apiConfig?.base_url !== baseUrl
  ) {
    throw new Error(`Packaged Tauri provider configuration failed: ${JSON.stringify(configured)}`);
  }
  return { provider: configured.value.apiConfig.provider, model: configured.value.apiConfig.model, baseUrlConfigured: true };
}

async function staticContract(report) {
  const files = [
    "src/hooks/use-context-compaction-controller.ts", "src/hooks/use-chat-run-controller.ts", "src/lib/chat-compaction-state.ts",
    "src/lib/context-compaction.ts", "context_compaction.py", "agent_gateway.py", "dashboard_server.py", "src-tauri/src/commands.rs",
  ];
  const source = Object.fromEntries(await Promise.all(files.map(async (path) => [path, await readFile(resolve(repoRoot, path), "utf8")] )));
  const required = [
    ["controller state machine", source[files[0]], "compactChat"], ["pre-turn controller", source[files[0]], "prepareTurnContext"],
    ["restart normalization", source[files[2]], "normalizeRestoredCompaction"], ["75/85/90/95 policy", source[files[3]], "CONTEXT_COMPACTION_PREFIRE_RATIO"],
    ["structured compaction backend", source[files[4]], "COMPACTION_SCHEMA"], ["post-tool runtime boundary", source[files[5]], "_maybe_compact_runtime_history"],
    ["Tauri compact command", source[files[7]], "compact_agent_history"], ["backend compact route", source[files[6]], "/api/app/agent/compact"],
  ];
  report.staticContracts = required.map(([name, text, needle]) => ({ name, present: text.includes(needle) }));
  for (const item of report.staticContracts.filter((item) => !item.present)) assertion(report, `missing required static contract: ${item.name}`);
  const joined = Object.values(source).join("\n");
  const externalResearchBrand = new RegExp(["\\b(?:gr", "ok[ _-]?build|x", "ai)\\b"].join(""), "i");
  report.brandBoundary = { forbiddenExternalResearchBrandFound: externalResearchBrand.test(joined) };
  if (report.brandBoundary.forbiddenExternalResearchBrandFound) assertion(report, "public compaction source contains an external research product brand");
}

async function main() {
  const report = {
    schema: "vrcforge.packaged_context_compaction_probe.v1", marker, startedAt: new Date().toISOString(), assertions: [], phases: {},
    mode: allowUnpushed ? "local-preacceptance" : "strict-release", strictReleaseBinding: false,
    releaseEvidence: allowUnpushed ? "non-release local-preacceptance only" : "strict release binding pending completion",
  };
  let launchInfo;
  let provider;
  try {
    await staticContract(report);
    report.package = await strictPackage();
    report.strictReleaseBinding = report.package.strictReleaseBinding === true;
    report.releaseBinding = {
      strict: report.strictReleaseBinding, manifestCommit: report.package.manifestCommit, headCommit: report.package.headCommit,
      originMainCommit: report.package.originMainCommit, worktreeClean: report.package.worktreeClean,
      embeddedVersion: report.package.embeddedVersion, portableSha256: report.package.archiveSha256,
      extractedMainSha256: report.package.mainSha256, extractedBackendSha256: report.package.backendSha256,
      buildPolicy: report.package.buildPolicy, strictBuildPolicy: report.package.strictBuildPolicy,
    };
    if (allowUnpushed && report.strictReleaseBinding) assertion(report, "allow-unpushed mode was incorrectly marked as strict release evidence");
    if (!allowUnpushed && !report.strictReleaseBinding) assertion(report, "strict mode did not retain strict release binding");
    provider = createLoopbackProvider();
    const providerPort = await provider.listen();
    report.provider = { transport: "isolated loopback chat-completions fixture", port: providerPort };
    launchInfo = await launch();
    const { cdp, child } = launchInfo;
    report.renderer = await evaluate(cdp, `(() => ({ tauri: typeof window.__TAURI_INTERNALS__?.invoke, title: document.title }))()`);
    const authenticatedBootstrap = await api("/api/app/chats");
    const tauriHealth = await waitForTauriSession(cdp);
    report.runtimeBinding = {
      authenticatedRest: Array.isArray(authenticatedBootstrap?.chats),
      tauriHealthVersion: String(tauriHealth?.version || ""),
    };
    report.providerConfig = await configureLoopbackProvider(cdp, providerPort);
    await evaluate(cdp, `(() => {
      localStorage.setItem("vrcforge_onboarded", "true");
      localStorage.setItem("vrcforge_onboarding_language_gate_completed", "true");
      return true;
    })()`);
    await reload(cdp);

    const prefireChat = probeChat("prefire", 0.75);
    report.phases.prefireSeed = await saveProbeChats(cdp, [prefireChat]);
    await reload(cdp);
    report.phases.prefireActivation = await activateProbeChat(cdp, prefireChat.title);
    report.phases.prefireSend = await uiSend(cdp, `${marker} prefire observation`);
    if (!report.phases.prefireSend?.ok) assertion(report, `prefire composer path could not start: ${report.phases.prefireSend?.reason || "unknown"}`);
    await provider.waitFor("tool", `${marker} prefire observation`, 30000).catch((error) => assertion(report, `75% prefire turn did not reach the fixture: ${String(error)}`));
    report.phases.prefire = await waitEval(cdp, `(() => ({ ok: document.querySelector("[data-context-compaction-status='prefire']") !== null }))()`, 10000)
      .catch((error) => ({ ok: false, error: String(error) }));
    if (!report.phases.prefire?.ok) assertion(report, "75% prefire was not visibly rendered during a real composer turn");
    report.phases.prefireCompleted = await waitForValue(
      async () => (await api("/api/app/chats")).chats?.find((chat) => chat.id === prefireChat.id),
      (chat) => (chat?.items || []).some((item) => item.type === "agent" && item.id !== "prefire-a1"),
      "prefire composer turn completion",
    ).catch((error) => {
      assertion(report, `prefire composer turn did not finish cleanly: ${String(error)}`);
      return null;
    });

    const manualChat = probeChat("manual", 0.70);
    report.phases.manualSeed = await saveProbeChats(cdp, [manualChat]);
    await reload(cdp);
    await activateProbeChat(cdp, manualChat.title);
    report.phases.manualComposer = await uiSend(cdp, "/compact");
    if (!report.phases.manualComposer?.ok) assertion(report, `manual /compact composer path could not start: ${report.phases.manualComposer?.reason || "unknown"}`);
    await provider.waitFor("compaction", "evidence-manual-0", 120000, "standalone").catch((error) => assertion(report, `manual /compact did not reach the controller/provider: ${String(error)}`));
    const manualStored = await waitForValue(
      async () => (await api("/api/app/chats")).chats?.find((chat) => chat.id === manualChat.id),
      (chat) => chat?.compaction?.status === "applied" && (chat?.items || []).some((item) => item.type === "compact"),
      "manual compaction persistence",
    );
    const manualMarker = (manualStored?.items || []).find((item) => item.type === "compact");
    report.phases.manualController = { marker: manualMarker || null, state: manualStored?.compaction || null };
    if (!manualMarker || !(manualMarker.beforeTokens > manualMarker.afterTokens) || manualStored?.compaction?.status !== "applied") assertion(report, "manual /compact did not persist an applied before/after marker through the shared controller");

    const history = manualChat.items
      .filter((item) => item.type === "user" || item.type === "agent")
      .map((item) => ({
        role: item.type === "user" ? "user" : "agent",
        text: item.type === "user" ? item.text : item.response?.plan?.reply || item.response?.plan?.summary || "",
      }));
    report.phases.manualIpc = await invoke(cdp, "compact_agent_history", { body: { history, trigger: "manual", phase: "standalone", language: "en", sourceDigest: marker }, timeoutMs: 120000 });
    if (!report.phases.manualIpc?.ok || report.phases.manualIpc?.value?.ok !== true) assertion(report, "manual compaction did not complete through real WebView Tauri IPC");
    else if (!report.phases.manualIpc.value.summary || !report.phases.manualIpc.value.summaryDigest) assertion(report, "manual IPC compaction did not return a structured successor summary and digest");

    const preTurnChat = probeChat("pre-turn", 0.86);
    report.phases.preTurnSeed = await saveProbeChats(cdp, [preTurnChat]);
    await reload(cdp);
    await activateProbeChat(cdp, preTurnChat.title);
    report.phases.preTurnSend = await uiSend(cdp, `${marker} pre-turn continuation`);
    if (!report.phases.preTurnSend?.ok) assertion(report, `pre-turn composer path could not start: ${report.phases.preTurnSend?.reason || "unknown"}`);
    await provider.waitFor("compaction", "evidence-pre-turn-0", 120000, "pre_turn").catch((error) => assertion(report, `pre-turn automatic compaction did not reach the isolated provider: ${String(error)}`));
    await provider.waitFor("tool", `${marker} pre-turn continuation`, 120000).catch((error) => assertion(report, `pre-turn turn did not reach the isolated provider: ${String(error)}`));
    await provider.waitFor("reply", `${marker} pre-turn continuation`, 120000).catch((error) => assertion(report, `pre-turn tool continuation did not finish: ${String(error)}`));
    const preTurnStored = await waitForValue(
      async () => (await api("/api/app/chats")).chats?.find((chat) => chat.id === preTurnChat.id),
      (chat) => (chat?.items || []).some((item) => item.type === "compact" && item.beforeTokens > item.afterTokens),
      "pre-turn compaction persistence",
    );
    const preTurnCompact = (preTurnStored?.items || []).find((item) => item.type === "compact");
    report.phases.preTurn = { compact: preTurnCompact || null, itemCount: preTurnStored?.items?.length || 0 };
    if (!preTurnCompact || !(preTurnCompact.beforeTokens > preTurnCompact.afterTokens)) assertion(report, "85% pre-turn automatic compaction did not persist a reduced before/after marker");

    const midTurnChat = probeChat("mid-turn", 0.70, { historyCharacters: 3_520_000 });
    report.phases.midTurnSeed = await saveProbeChats(cdp, [midTurnChat]);
    await reload(cdp);
    await activateProbeChat(cdp, midTurnChat.title);
    report.phases.midTurnSend = await uiSend(cdp, `${marker} mid-turn continuation`);
    await provider.waitFor("tool", `${marker} mid-turn continuation`, 120000).catch((error) => assertion(report, `mid-turn first planner/tool request was not observed: ${String(error)}`));
    await provider.waitFor("reply", `${marker} mid-turn continuation`, 120000).catch((error) => assertion(report, `mid-turn continuation reply was not observed: ${String(error)}`));
    const runtimeRuns = await waitForValue(
      () => api("/api/app/agent/runs?limit=50"),
      (runs) => (runs?.runs || []).some((run) => run?.contextCompaction?.phase === "mid_turn" && run?.contextCompaction?.applied === true),
      "mid-turn runtime ledger persistence",
    );
    const midTurn = (runtimeRuns.runs || []).find((run) => run?.contextCompaction?.phase === "mid_turn" && run?.contextCompaction?.applied === true);
    report.phases.midTurn = { runCount: runtimeRuns.count, compaction: midTurn?.contextCompaction || null };
    const midTurnProviderRequests = provider.requests.filter((request) => request.kind === "compaction" && request.phase === "mid_turn" && request.prompt.includes("evidence-mid-turn-0"));
    report.phases.midTurn.providerCompactionRequests = midTurnProviderRequests.length;
    if (
      !midTurn
      || !(midTurn.contextCompaction.beforeTokens >= 850_000 && midTurn.contextCompaction.beforeTokens < 950_000)
      || !(midTurn.contextCompaction.afterTokens < 850_000)
      || midTurn.contextCompaction.failureClass !== "input_oversize"
      || midTurnProviderRequests.length !== 0
    ) assertion(report, "post-tool automatic compaction did not use the bounded oversized-input fallback and reduce below the continuation threshold");

    const cancelChat = probeChat("cancel", 0.86);
    const expectedCancelIds = cancelChat.items.map((item) => item.id);
    report.phases.cancelSeed = await saveProbeChats(cdp, [cancelChat]);
    const persistedCancelSeed = (await api("/api/app/chats")).chats?.find((chat) => chat.id === cancelChat.id);
    const cancelOriginalItems = persistedCancelSeed?.items || [];
    const cancelOriginalDigest = createHash("sha256").update(JSON.stringify(cancelOriginalItems)).digest("hex");
    const cancelOriginalIds = cancelOriginalItems.map((item) => item.id);
    if (JSON.stringify(cancelOriginalIds) !== JSON.stringify(expectedCancelIds)) {
      throw new Error("Persisted cancellation baseline did not retain every original item identity.");
    }
    await reload(cdp);
    await activateProbeChat(cdp, cancelChat.title);
    report.phases.cancelSend = await uiSend(cdp, `${marker}-cancel pre-turn cancellation`);
    await provider.waitFor("cancel-compaction", "evidence-cancel-0", 120000, "pre_turn").catch((error) => assertion(report, `cancellation case did not reach delayed pre-turn compaction: ${String(error)}`));
    report.phases.cancelVisible = await waitEval(cdp, `(() => ({ ok: Boolean(document.querySelector("[data-context-compaction-cancel]")) }))()`, 10000)
      .catch((error) => ({ ok: false, error: String(error) }));
    report.phases.cancelRequest = await evaluate(cdp, `(() => {
      const button = document.querySelector("[data-context-compaction-cancel]");
      if (!button) return { ok:false, reason:"visible compaction cancel control missing" };
      button.click();
      return { ok:true, tag:button.tagName };
    })()`);
    if (!report.phases.cancelVisible?.ok || !report.phases.cancelRequest?.ok) assertion(report, "packaged cancellation did not use the visible WebView compaction cancel control");
    const cancelledChat = await waitForValue(
      async () => (await api("/api/app/chats")).chats?.find((chat) => chat.id === cancelChat.id),
      (chat) => chat?.compaction?.status === "cancelled",
      "cancelled compaction persistence",
      30000,
    );
    const cancelledItems = cancelledChat?.items || [];
    const cancelledItemsDigest = createHash("sha256").update(JSON.stringify(cancelledItems)).digest("hex");
    const retainedOriginalItems = cancelOriginalIds.map((id) => cancelledItems.find((item) => item.id === id));
    const retainedOriginalDigest = createHash("sha256").update(JSON.stringify(retainedOriginalItems)).digest("hex");
    const cancellationNotices = cancelledItems.filter((item) => !cancelOriginalIds.includes(item.id));
    const cancelPlannerRequests = provider.requests.filter((request) => ["tool", "reply"].includes(request.kind) && request.prompt.includes(`${marker}-cancel pre-turn cancellation`));
    report.phases.cancel = {
      compaction: cancelledChat?.compaction || null,
      originalIds: cancelOriginalIds,
      storedIds: cancelledItems.map((item) => item.id),
      originalDigest: cancelOriginalDigest,
      retainedOriginalDigest,
      storedDigest: cancelledItemsDigest,
      cancellationNoticeTypes: cancellationNotices.map((item) => item.type),
      plannerRequests: cancelPlannerRequests.length,
    };
    if (
      cancelledChat?.compaction?.status !== "cancelled"
      || retainedOriginalItems.some((item) => !item)
      || retainedOriginalDigest !== cancelOriginalDigest
      || cancellationNotices.some((item) => item.type !== "error")
      || cancelledItems.some((item) => item.type === "compact")
      || cancelPlannerRequests.length !== 0
    ) assertion(report, "cancelling delayed pre-turn compaction changed an original history item, produced a non-status replacement, or incorrectly started the planner");

    // Tauri cannot terminate an already-dispatched blocking command, so wait
    // beyond the fixture delay and prove its late response cannot pass the
    // controller's generation/revision guard or mutate the original items.
    await sleep(5500);
    const lateCancelledChat = (await api("/api/app/chats")).chats?.find((chat) => chat.id === cancelChat.id);
    const lateCancelledDigest = createHash("sha256").update(JSON.stringify(lateCancelledChat?.items || [])).digest("hex");
    report.phases.cancel.lateResultDigest = lateCancelledDigest;
    report.phases.cancel.lateResultStatus = lateCancelledChat?.compaction?.status || "";
    if (lateCancelledDigest !== cancelledItemsDigest || lateCancelledChat?.compaction?.status !== "cancelled") {
      assertion(report, "a late provider response mutated the persisted cancellation snapshot");
    }

    report.phases.cancelRecoverySend = await uiSend(cdp, "/compact");
    await provider.waitFor("compaction", "evidence-cancel-0", 120000, "standalone").catch((error) => assertion(report, `manual recovery after cancellation did not reach the provider: ${String(error)}`));
    const recoveredCancelChat = await waitForValue(
      async () => (await api("/api/app/chats")).chats?.find((chat) => chat.id === cancelChat.id),
      (chat) => chat?.compaction?.status === "applied" && (chat?.items || []).some((item) => item.type === "compact"),
      "post-cancellation controller recovery",
    );
    report.phases.cancelRecovery = { status: recoveredCancelChat?.compaction?.status || "", itemCount: recoveredCancelChat?.items?.length || 0 };
    if (recoveredCancelChat?.compaction?.status !== "applied") assertion(report, "compaction controller remained wedged after a visible cancellation");

    // Restart recovery: write an interrupted state through the app's normal
    // transcript IPC, restart, then require it to normalize without deleting
    // the original durable items.
    const interrupted = probeChat("restart", 0.86);
    interrupted.compaction = { status: "compacting", generation: `${marker}-interrupted`, sourceDigest: marker, startedAt: new Date().toISOString(), beforeTokens: 8600, contextLimit: 10000 };
    report.phases.restartSeed = await saveProbeChats(cdp, [interrupted]);
    report.phases.firstClose = await requestClose(child);
    if (!report.phases.firstClose.graceful) assertion(report, "first packaged restart close did not release tracked processes/ports");
    launchInfo.cdp.close();
    token = "";
    launchInfo = await launch();
    const chat = await waitForValue(
      async () => (await api("/api/app/chats")).chats?.find((item) => item.id === interrupted.id),
      (item) => item?.compaction?.status === "failed",
      "interrupted compaction restart normalization persistence",
      15000,
    );
    report.phases.restart = { found: Boolean(chat), status: chat?.compaction?.status || "", itemCount: chat?.items?.length || 0, originalItemIds: (chat?.items || []).map((item) => item.id) };
    if (!chat || chat.compaction?.status !== "failed" || !chat.items?.some((item) => item.id === "restart-u1") || !chat.items?.some((item) => item.id === "restart-a1")) {
      assertion(report, "restart recovery did not preserve original history while normalizing interrupted compaction");
    }
  } catch (error) {
    report.error = String(error?.stack || error);
    assertion(report, "probe threw before all required acceptance phases completed");
  } finally {
    if (launchInfo) {
      try { launchInfo.cdp.close(); } catch { /* closing renderer */ }
      report.cleanup = await requestClose(launchInfo.child).catch((error) => ({ graceful: false, error: String(error), final: null }));
    } else report.cleanup = { graceful: false, reason: "packaged launch never completed" };
    if (provider) await provider.close().catch((error) => { assertion(report, `loopback provider did not close: ${String(error)}`); });
    const residue = await waitClear(20000).catch((error) => ({ error: String(error) }));
    report.cleanup.finalSnapshot = residue;
    if (!clear(residue)) assertion(report, "packaged app, backend listener, or CDP listener remained after probe cleanup");
    report.finishedAt = new Date().toISOString();
    report.ok = report.assertions.length === 0;
    await mkdir(evidenceRoot, { recursive: true });
    await writeFile(reportPath, JSON.stringify(report, null, 2), "utf8");
    console.log(reportPath);
    if (report.assertions.length) process.exitCode = 1;
  }
}

main();
