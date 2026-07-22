import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, readdir, realpath, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, relative, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const cdpPort = Number(process.env.VRCFORGE_MEMORY_PROBE_CDP_PORT || "9348");
const marker = `MEMORY_RESTART_PROBE_${Date.now()}`;
const redactionSentinels = [
  ["s", "k", "-", "1145141919810"].join(""),
  ["D:", "\\", "private", "\\", `${marker}.txt`].join(""),
  `https://probe.invalid/path?token=${marker}`,
];
const redactionSourceText = `Please remember ${marker} user preference. ${redactionSentinels.join(" ")}`;
const evidenceRoot = resolve(repoRoot, "artifacts", "actual-app-memory-restart", marker);
const packagedRoot = resolve(evidenceRoot, "package");
const exe = resolve(packagedRoot, "VRCForge.exe");
const userDataRoot = resolve(evidenceRoot, "user-data");
const configRoot = resolve(userDataRoot, "config");
const webviewDataRoot = resolve(evidenceRoot, "webview2-user-data");
const projectARoot = resolve(evidenceRoot, "projects", "project-a");
const projectBRoot = resolve(evidenceRoot, "projects", "project-b");
const reportPath = resolve(evidenceRoot, "report.json");
const memoryLogPath = resolve(
  userDataRoot,
  "artifacts",
  "dashboard",
  "agent_gateway",
  "agent-memory.jsonl",
);
const reviewStorePath = resolve(
  userDataRoot,
  "artifacts",
  "dashboard",
  "agent_gateway",
  "memory-review",
  "memory-review.json",
);
const appOrigin = "http://127.0.0.1:8757";
const appRequestOrigin = "http://tauri.localhost";
let appSessionToken = "";

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`Usage: node scripts/diagnose_packaged_memory_restart.mjs

Runs the packaged Memory persistence/isolation/tombstone and review restart probe.
Requires a strict release build whose manifest commit equals pushed origin/main.

Optional environment:
  VRCFORGE_MEMORY_PROBE_CDP_PORT=<unused port> (default: ${cdpPort})`);
  process.exit(0);
}

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function addAssertion(report, message) {
  if (!report.assertions.includes(message)) {
    report.assertions.push(message);
  }
}

function createMemoryReviewProvider() {
  const requests = [];
  const rawBodies = [];
  const candidateBySource = new Map();
  let failuresRemaining = 0;
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const rawBody = Buffer.concat(chunks).toString("utf8");
    rawBodies.push(rawBody);
    let body = {};
    try { body = rawBody ? JSON.parse(rawBody) : {}; } catch { body = {}; }
    if (request.method === "GET" && request.url === "/v1/models") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ object: "list", data: [{ id: "vrcforge-memory-review-probe", object: "model" }] }));
      return;
    }
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { message: "not found" } }));
      return;
    }
    let reviewPayload = {};
    for (const message of [...(Array.isArray(body.messages) ? body.messages : [])].reverse()) {
      if (typeof message?.content !== "string") continue;
      try {
        const parsed = JSON.parse(message.content);
        if (Array.isArray(parsed?.sources)) {
          reviewPayload = parsed;
          break;
        }
      } catch {
        // The dedicated review request contains exactly one JSON user message.
      }
    }
    const sources = Array.isArray(reviewPayload.sources) ? reviewPayload.sources : [];
    requests.push({
      method: request.method,
      url: request.url,
      model: String(body.model || ""),
      sourceCount: sources.length,
      hasTools: Array.isArray(body.tools) && body.tools.length > 0,
      forcedFailure: failuresRemaining > 0,
    });
    if (failuresRemaining > 0) {
      failuresRemaining -= 1;
      response.writeHead(503, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { message: "bounded probe failure" } }));
      return;
    }
    const candidates = sources.map((source, index) => {
      const sourceId = String(source?.sourceId || "");
      const text = candidateBySource.get(sourceId) || `${marker} reviewed candidate ${index + 1}`;
      candidateBySource.set(sourceId, text);
      return {
        kind: ["preference", "fact", "correction", "decision"].includes(source?.kind)
          ? source.kind
          : "preference",
        text,
        sourceIds: [sourceId],
        confidenceFactors: ["explicit_user_intent"],
      };
    });
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({
      id: "chatcmpl-memory-review-probe",
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model: body.model || "vrcforge-memory-review-probe",
      choices: [{
        index: 0,
        message: { role: "assistant", content: JSON.stringify({ candidates }) },
        finish_reason: "stop",
      }],
      usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 },
    }));
  });
  return {
    requests,
    rawBodies,
    candidateBySource,
    failNextRequests(count) {
      failuresRemaining = Math.max(0, Number(count) || 0);
    },
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

function escapePowerShellLiteral(value) {
  return String(value).replaceAll("'", "''");
}

function runPowerShell(script) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(
      "powershell",
      ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
      { windowsHide: true },
    );
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

function sha256File(path) {
  return new Promise((resolveHash, rejectHash) => {
    const digest = createHash("sha256");
    const input = createReadStream(path);
    input.on("error", rejectHash);
    input.on("data", (chunk) => digest.update(chunk));
    input.on("end", () => resolveHash(digest.digest("hex")));
  });
}

async function snapshotUnityProjectTree(projectRoot) {
  const manifest = [];
  for (const topLevel of ["Assets", "Packages", "ProjectSettings"]) {
    const pending = [resolve(projectRoot, topLevel)];
    while (pending.length) {
      const current = pending.pop();
      const entries = (await readdir(current, { withFileTypes: true }))
        .sort((left, right) => left.name.localeCompare(right.name));
      if (entries.length === 0) {
        manifest.push({ path: `${relative(projectRoot, current).replaceAll("\\", "/")}/`, kind: "directory" });
      }
      for (const entry of entries) {
        const path = resolve(current, entry.name);
        const relativePath = relative(projectRoot, path).replaceAll("\\", "/");
        if (entry.isDirectory()) {
          manifest.push({ path: `${relativePath}/`, kind: "directory" });
          pending.push(path);
          continue;
        }
        if (!entry.isFile()) {
          throw new Error("Unity project manifest encountered an unsupported filesystem entry.");
        }
        const metadata = await stat(path);
        manifest.push({
          path: relativePath,
          kind: "file",
          size: metadata.size,
          sha256: await sha256File(path),
        });
      }
    }
  }
  return manifest.sort((left, right) => left.path.localeCompare(right.path));
}

function summarizeTreeManifest(manifest) {
  const serialized = JSON.stringify(manifest);
  return {
    entryCount: manifest.length,
    fileCount: manifest.filter((entry) => entry.kind === "file").length,
    directoryCount: manifest.filter((entry) => entry.kind === "directory").length,
    sha256: createHash("sha256").update(serialized).digest("hex"),
  };
}

async function prepareManifestBoundPackage(sourceVersion) {
  const manifestPath = resolve(repoRoot, "dist", "release", "release-manifest.json");
  let manifest;
  try {
    manifest = JSON.parse((await readFile(manifestPath, "utf8")).replace(/^\uFEFF/, ""));
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(`Strict packaged probe requires ${manifestPath}.`);
    }
    throw new Error(`Release manifest could not be read: ${String(error?.message || error)}`);
  }
  if (String(manifest?.version || "") !== sourceVersion) {
    throw new Error(`Release manifest version ${String(manifest?.version || "<missing>")} did not match VERSION ${sourceVersion}.`);
  }
  const escapedRepoRoot = escapePowerShellLiteral(repoRoot);
  const headCommit = (await runPowerShell(`git -C '${escapedRepoRoot}' rev-parse HEAD`)).trim().toLowerCase();
  const originMainCommit = (await runPowerShell(`git -C '${escapedRepoRoot}' rev-parse origin/main`)).trim().toLowerCase();
  const manifestCommit = String(manifest?.commit || "").trim().toLowerCase();
  if (!/^[0-9a-f]{40}$/.test(headCommit) || !/^[0-9a-f]{40}$/.test(originMainCommit) || headCommit !== originMainCommit) {
    throw new Error(`Strict packaged probe requires HEAD=${headCommit || "<missing>"} to equal origin/main=${originMainCommit || "<missing>"}.`);
  }
  if (manifestCommit !== headCommit) {
    throw new Error(`Release manifest commit ${manifestCommit || "<missing>"} did not match current HEAD ${headCommit}.`);
  }
  const portableName = `VRCForge_Windows_x64_${sourceVersion}.zip`;
  const portable = (Array.isArray(manifest?.artifacts) ? manifest.artifacts : [])
    .find((artifact) => artifact?.name === portableName);
  if (!portable || !/^[0-9a-f]{64}$/i.test(String(portable.sha256 || ""))) {
    throw new Error(`Release manifest did not contain a valid ${portableName} digest.`);
  }
  const portablePath = resolve(dirname(manifestPath), portableName);
  const portableSha256 = await sha256File(portablePath);
  if (portableSha256 !== String(portable.sha256).toLowerCase()) {
    throw new Error(`Portable package digest did not match release-manifest.json for ${portableName}.`);
  }
  const escapedPortable = escapePowerShellLiteral(portablePath);
  const innerExeSha256 = (await runPowerShell(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead('${escapedPortable}')
    try {
      $entry = @($archive.Entries | Where-Object {
        $name = $_.FullName.Replace('\\', '/')
        $name.Equals('VRCForge.exe', [StringComparison]::OrdinalIgnoreCase)
      } | Select-Object -First 1)
      if ($entry.Count -ne 1) { throw 'Portable package did not contain exactly one VRCForge.exe entry.' }
      $sha = [Security.Cryptography.SHA256]::Create()
      $stream = $entry[0].Open()
      try { $digest = $sha.ComputeHash($stream) } finally { $stream.Dispose(); $sha.Dispose() }
      [BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant()
    } finally {
      $archive.Dispose()
    }
  `)).trim().toLowerCase();
  const escapedPackageRoot = escapePowerShellLiteral(packagedRoot);
  await runPowerShell(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destination = '${escapedPackageRoot}'
    if (Test-Path -LiteralPath $destination) { throw 'Isolated package extraction root already exists.' }
    [IO.Compression.ZipFile]::ExtractToDirectory('${escapedPortable}', $destination)
  `);
  const embeddedVersion = (await readFile(resolve(packagedRoot, "VERSION"), "utf8")).replace(/^\uFEFF/, "").trim();
  if (embeddedVersion !== sourceVersion) {
    throw new Error(`Manifest-bound portable VERSION ${embeddedVersion || "<missing>"} did not match ${sourceVersion}.`);
  }
  const exeSha256 = await sha256File(exe);
  if (innerExeSha256 !== exeSha256) {
    throw new Error("Extracted VRCForge.exe did not match the manifest-bound portable package executable.");
  }
  return {
    manifestPath,
    version: String(manifest.version),
    commit: manifestCommit,
    headCommit,
    originMainCommit,
    portableName,
    portableSha256,
    innerExeSha256,
    embeddedVersion,
    extractedPackageRoot: packagedRoot,
    exeSha256,
  };
}

async function processSnapshot() {
  const escapedRoot = escapePowerShellLiteral(packagedRoot);
  const raw = await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${escapedRoot}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $processes = @(foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { continue }
      if ($path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        [pscustomobject]@{ Id = $process.Id; ProcessName = $process.ProcessName; Path = $path }
      }
    })
    $ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object { $_.LocalPort -eq 8757 -or $_.LocalPort -eq ${cdpPort} } |
      Select-Object LocalAddress,LocalPort,State,OwningProcess)
    [pscustomobject]@{ processes = $processes; ports = $ports } | ConvertTo-Json -Depth 5 -Compress
  `);
  return raw ? JSON.parse(raw) : { processes: [], ports: [] };
}

function snapshotIsClear(snapshot) {
  return (snapshot.processes || []).length === 0 && (snapshot.ports || []).length === 0;
}

async function waitForPackagedClear(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let latest = await processSnapshot();
  while (Date.now() < deadline) {
    if (snapshotIsClear(latest)) {
      return { ok: true, snapshot: latest };
    }
    await sleep(500);
    latest = await processSnapshot();
  }
  return { ok: snapshotIsClear(latest), snapshot: latest };
}

async function forceCloseLaunch(launch) {
  if (!launch?.childPid) {
    return processSnapshot();
  }
  const escapedRoot = escapePowerShellLiteral(packagedRoot);
  const escapedExe = escapePowerShellLiteral(exe);
  const rootPid = Number(launch.childPid);
  await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${escapedRoot}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $exe = [IO.Path]::GetFullPath('${escapedExe}')
    $rootPid = [int]${rootPid}
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $ids = [Collections.Generic.HashSet[int]]::new()
    [void]$ids.Add($rootPid)
    do {
      $added = $false
      foreach ($candidate in $all) {
        if ($ids.Contains([int]$candidate.ParentProcessId) -and -not $ids.Contains([int]$candidate.ProcessId)) {
          [void]$ids.Add([int]$candidate.ProcessId)
          $added = $true
        }
      }
    } while ($added)
    $targets = @(foreach ($id in $ids) {
      $process = Get-Process -Id $id -ErrorAction SilentlyContinue
      if (-not $process) { continue }
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { continue }
      $allowed = $false
      if ($id -eq $rootPid) {
        $allowed = $path.Equals($exe, [StringComparison]::OrdinalIgnoreCase)
      } else {
        $allowed = $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
      }
      if ($allowed) { $process }
    })
    $targets |
      Sort-Object @{ Expression = { if ($_.Id -eq $rootPid) { 1 } else { 0 } } } |
      Stop-Process -Force -ErrorAction SilentlyContinue
  `);
  const cleared = await waitForPackagedClear(20000);
  if (!cleared.ok) {
    throw new Error(`Tracked packaged launch did not clear without touching other instances: ${JSON.stringify(cleared.snapshot)}`);
  }
  return cleared.snapshot;
}

async function closePackagedApp(launch) {
  if (!launch?.childPid) {
    throw new Error("Tracked packaged launch was unavailable for close.");
  }
  const escapedExe = escapePowerShellLiteral(exe);
  const rootPid = Number(launch.childPid);
  const requestedRaw = await runPowerShell(`
    $exe = [IO.Path]::GetFullPath('${escapedExe}')
    $targets = @(Get-Process -Id ${rootPid} -ErrorAction SilentlyContinue | Where-Object {
      try { [IO.Path]::GetFullPath([string]$_.Path).Equals($exe, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
    })
    $results = @(foreach ($target in $targets) {
      [pscustomobject]@{
        pid = $target.Id
        mainWindowHandle = [int64]$target.MainWindowHandle
        closeRequested = [bool]$target.CloseMainWindow()
      }
    })
    [pscustomobject]@{ targets = $results } | ConvertTo-Json -Depth 4 -Compress
  `);
  const requested = requestedRaw ? JSON.parse(requestedRaw) : { targets: [] };
  const requestedTargets = Array.isArray(requested?.targets)
    ? requested.targets
    : requested?.targets
      ? [requested.targets]
      : [];
  const closeAccepted = requestedTargets.length === 1
    && Number(requestedTargets[0]?.pid) === rootPid
    && Number(requestedTargets[0]?.mainWindowHandle) !== 0
    && requestedTargets[0]?.closeRequested === true;
  const graceful = await waitForPackagedClear(30000);
  if (graceful.ok) {
    return {
      requested,
      trackedPid: rootPid,
      closeAccepted,
      graceful: closeAccepted,
      forced: false,
      finalSnapshot: graceful.snapshot,
    };
  }
  const beforeForce = graceful.snapshot;
  await forceCloseLaunch(launch);
  return {
    requested,
    trackedPid: rootPid,
    closeAccepted,
    graceful: false,
    forced: true,
    beforeForce,
    finalSnapshot: await processSnapshot(),
  };
}

function assertGracefulClosure(report, closure, label) {
  const targets = Array.isArray(closure.requested?.targets)
    ? closure.requested.targets
    : closure.requested?.targets
      ? [closure.requested.targets]
      : [];
  if (!closure.graceful) {
    addAssertion(report, `packaged app did not complete an accepted graceful close ${label}`);
  }
  if (targets.length !== 1 || Number(targets[0]?.pid) !== Number(closure.trackedPid)) {
    addAssertion(report, `packaged app did not target exactly its tracked main process ${label}`);
  } else {
    if (Number(targets[0]?.mainWindowHandle) === 0) {
      addAssertion(report, `packaged app tracked main process had no main window handle ${label}`);
    }
    if (targets[0]?.closeRequested !== true) {
      addAssertion(report, `packaged app tracked main window rejected graceful close ${label}`);
    }
  }
  if (!snapshotIsClear(closure.finalSnapshot || {})) {
    addAssertion(report, `packaged processes or probe ports remained ${label}`);
  }
}

async function waitForJson(url, timeoutMs = 45000) {
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
      result.exceptionDetails.exception?.description ||
      result.exceptionDetails.text ||
      "Runtime.evaluate failed",
    );
  }
  return result.result?.value;
}

async function waitForEval(cdp, expression, timeoutMs = 45000) {
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

function isolatedLaunchEnvironment() {
  const env = { ...process.env };
  delete env.VRCFORGE_APP_SESSION_TOKEN;
  env.VRCFORGE_USER_DATA_DIR = userDataRoot;
  env.VRCFORGE_CONFIG_DIR = configRoot;
  env.VRCFORGE_CONFIG_PATH = resolve(configRoot, "config.json");
  env.VRCFORGE_SETTINGS_PATH = resolve(configRoot, "settings.json");
  env.VRCFORGE_LOG_DIR = resolve(userDataRoot, "logs");
  env.VRCFORGE_ARTIFACTS_DIR = resolve(userDataRoot, "artifacts");
  env.WEBVIEW2_USER_DATA_FOLDER = webviewDataRoot;
  env.WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS =
    `--remote-debugging-port=${cdpPort} --remote-allow-origins=*`;
  return env;
}

async function launchPackagedApp() {
  appSessionToken = "";
  const child = spawn(exe, [], {
    detached: false,
    stdio: "ignore",
    env: isolatedLaunchEnvironment(),
  });
  const launch = { childPid: child.pid, launchedAt: new Date().toISOString(), cdp: null };
  const spawnFailure = new Promise((_, rejectSpawn) => {
    child.once("error", rejectSpawn);
  });
  try {
    const targets = await Promise.race([
      waitForJson(`http://127.0.0.1:${cdpPort}/json/list`, 45000),
      spawnFailure,
    ]);
    const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
    if (!page) {
      throw new Error("Packaged WebView2 page target was not found.");
    }
    const cdp = connectCdp(page.webSocketDebuggerUrl);
    launch.cdp = cdp;
    await cdp.opened;
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    const renderer = await waitForEval(
      cdp,
      `(() => ({
        ok: Boolean(document.body && document.body.innerText.length > 0 &&
          window.__TAURI_INTERNALS__ && typeof window.__TAURI_INTERNALS__.invoke === "function"),
        bodyLength: document.body?.innerText?.length || 0,
        tauriInvoke: typeof window.__TAURI_INTERNALS__?.invoke,
      }))()`,
      45000,
    );
    const health = await waitForJson(`${appOrigin}/api/health`, 45000);
    return { ...launch, cdp, health, renderer };
  } catch (error) {
    try { launch.cdp?.close(); } catch { /* Renderer may not have connected. */ }
    await forceCloseLaunch(launch).catch(() => {});
    throw error;
  }
}

async function readAppToken() {
  const tokenPath = resolve(configRoot, "app-session-token");
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const value = (await readFile(tokenPath, "utf8")).trim();
      if (value) {
        return value;
      }
    } catch {
      // The managed backend has not written the isolated token yet.
    }
    await sleep(150);
  }
  throw new Error("Packaged app session token was not created in the isolated user-data root.");
}

async function appApi(path, options = {}) {
  if (!appSessionToken) {
    appSessionToken = await readAppToken();
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs || 30000);
  try {
    const response = await fetch(`${appOrigin}${path}`, {
      method: options.method || "GET",
      headers: {
        Origin: appRequestOrigin,
        Authorization: `Bearer ${appSessionToken}`,
        "Content-Type": "application/json",
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
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
  } finally {
    clearTimeout(timeout);
  }
}

async function expectAppApiFailure(path, options, expectedStatus) {
  try {
    await appApi(path, options);
  } catch (error) {
    const message = String(error?.message || error);
    if (message.startsWith(`${expectedStatus} `)) return message;
    throw error;
  }
  throw new Error(`${path} unexpectedly succeeded; expected HTTP ${expectedStatus}.`);
}

function stableProbePromotionId(candidateId, acceptedText, generation) {
  const canonical = JSON.stringify({
    acceptedText: String(acceptedText).trim().toLowerCase(),
    candidateId,
    contract: "vrcforge.memory_review_promotion.v1",
    generation,
  });
  return `memprom_${createHash("sha256").update(canonical, "utf8").digest("hex").slice(0, 32)}`;
}

async function seedPromotionCrashState(candidateId, acceptedText, phase) {
  const store = JSON.parse(await readFile(reviewStorePath, "utf8"));
  const candidate = (Array.isArray(store.candidates) ? store.candidates : [])
    .find((item) => item?.candidateId === candidateId);
  if (!candidate) throw new Error(`promotion crash seed could not find ${candidateId}`);
  const generation = Number(candidate.promotionGeneration || 0);
  const promotionId = stableProbePromotionId(candidateId, acceptedText, generation);
  if (phase === "after_memory_write" && candidate.promotionId !== promotionId) {
    throw new Error("after-memory promotion crash seed did not match the durable promotion identity");
  }
  candidate.state = "promoting";
  candidate.promotionId = promotionId;
  candidate.acceptedText = acceptedText;
  candidate.memoryId = "";
  delete candidate.acceptedAt;
  candidate.updatedAt = new Date().toISOString();
  store.revision = Number(store.revision || 0) + 1;
  await writeFile(reviewStorePath, `${JSON.stringify(store)}\n`, "utf8");
  return { phase, candidateId, promotionId, generation, revision: store.revision };
}

async function tauriInvoke(cdp, command, args) {
  const envelope = await evalValue(
    cdp,
    `(async () => {
      try {
        const value = await window.__TAURI_INTERNALS__.invoke(
          ${JSON.stringify(command)},
          ${JSON.stringify(args)},
        );
        return { ok: true, value };
      } catch (error) {
        return { ok: false, error: String(error?.stack || error) };
      }
    })()`,
  );
  if (!envelope?.ok) {
    throw new Error(`Tauri ${command} failed: ${envelope?.error || "unknown error"}`);
  }
  return envelope.value;
}

function summarizeReviewSnapshot(payload) {
  const summarizeUsage = (usage) => ({
    inputTokens: Number(usage?.inputTokens || 0),
    outputTokens: Number(usage?.outputTokens || 0),
    totalTokens: Number(usage?.totalTokens || 0),
    costUsd: Number(usage?.costUsd || 0),
    attempts: Number(usage?.attempts || 0),
    costUpperBoundUsd: Number(usage?.costUpperBoundUsd || 0),
    costAccounting: String(usage?.costAccounting || ""),
  });
  const summarizeCounts = (counts) => Object.fromEntries(
    Object.entries(counts && typeof counts === "object" ? counts : {})
      .map(([key, value]) => [String(key), Number(value || 0)])
      .sort(([left], [right]) => left.localeCompare(right)),
  );
  return {
    ok: payload?.ok === true,
    schema: String(payload?.schema || ""),
    mode: String(payload?.mode || ""),
    policyVersion: String(payload?.policyVersion || ""),
    scope: String(payload?.scope || ""),
    projectBound: Boolean(payload?.projectRoot),
    requestedProjectBound: Boolean(payload?.requestedProjectRoot),
    configuredProjectMatches: payload?.configuredProjectMatches !== false,
    revision: Number(payload?.revision || 0),
    config: {
      cadenceMinutes: Number(payload?.cadenceMinutes || 0),
      inputCharCap: Number(payload?.inputCharCap || 0),
      tokenCap: Number(payload?.tokenCap || 0),
      costCapUsd: Number(payload?.costCapUsd || 0),
      inputCostPerMillionUsd: Number(payload?.inputCostPerMillionUsd || 0),
      outputCostPerMillionUsd: Number(payload?.outputCostPerMillionUsd || 0),
      retentionDays: Number(payload?.retentionDays || 0),
      provider: String(payload?.provider || ""),
      model: String(payload?.model || ""),
    },
    unreadCount: Number(payload?.unreadCount || 0),
    runState: String(payload?.runStatus?.state || ""),
    runPhase: String(payload?.runStatus?.phase || ""),
    runAttempt: Number(payload?.runStatus?.attempt || 0),
    lastRunStatus: String(payload?.lastRun?.status || ""),
    lastFailureClass: String(payload?.lastRun?.failureClass || payload?.lastRun?.deferredReason || ""),
    lastRunNonConsuming: payload?.lastRun?.nonConsuming === true,
    lastRun: {
      status: String(payload?.lastRun?.status || ""),
      phase: String(payload?.lastRun?.phase || ""),
      failureClass: String(payload?.lastRun?.failureClass || ""),
      deferredReason: String(payload?.lastRun?.deferredReason || ""),
      nonConsuming: payload?.lastRun?.nonConsuming === true,
      attempt: Number(payload?.lastRun?.attempt || 0),
      eligibleCount: Number(payload?.lastRun?.eligibleCount || 0),
      candidateCount: Number(payload?.lastRun?.candidateCount || 0),
      provider: String(payload?.lastRun?.provider || ""),
      model: String(payload?.lastRun?.model || ""),
      budget: {
        inputCharCap: Number(payload?.lastRun?.budget?.inputCharCap || 0),
        tokenCap: Number(payload?.lastRun?.budget?.tokenCap || 0),
        costCapUsd: Number(payload?.lastRun?.budget?.costCapUsd || 0),
      },
      usage: summarizeUsage(payload?.lastRun?.usage),
    },
    providerDisclosure: {
      paidRun: payload?.providerDisclosure?.paidRun === true,
      provider: String(payload?.providerDisclosure?.provider || ""),
      providerLabel: String(payload?.providerDisclosure?.providerLabel || ""),
      model: String(payload?.providerDisclosure?.model || ""),
      activeConfigMatches: payload?.providerDisclosure?.activeConfigMatches !== false,
      cadenceMinutes: Number(payload?.providerDisclosure?.cadenceMinutes || 0),
      inputCharCap: Number(payload?.providerDisclosure?.inputCharCap || 0),
      tokenCap: Number(payload?.providerDisclosure?.tokenCap || 0),
      costCapUsd: Number(payload?.providerDisclosure?.costCapUsd || 0),
      inputCostPerMillionUsd: Number(payload?.providerDisclosure?.inputCostPerMillionUsd || 0),
      outputCostPerMillionUsd: Number(payload?.providerDisclosure?.outputCostPerMillionUsd || 0),
      privacyScope: String(payload?.providerDisclosure?.privacyScope || ""),
    },
    usage: summarizeUsage(payload?.usage),
    shadowSummary: payload?.shadowSummary ? {
      scope: String(payload.shadowSummary.scope || ""),
      projectBound: Boolean(payload.shadowSummary.projectRoot),
      eligibleCount: Number(payload.shadowSummary.eligibleCount || 0),
      sourceTypeCounts: summarizeCounts(payload.shadowSummary.sourceTypeCounts),
      reasonCounts: summarizeCounts(payload.shadowSummary.reasonCounts),
      revision: Number(payload.shadowSummary.revision || 0),
    } : null,
    candidates: (Array.isArray(payload?.candidates) ? payload.candidates : []).map((candidate) => ({
      candidateId: String(candidate?.candidateId || ""),
      scope: String(candidate?.scope || ""),
      kind: String(candidate?.kind || ""),
      proposedText: String(candidate?.proposedText || ""),
      state: String(candidate?.state || ""),
      evidenceCount: Number(candidate?.evidenceCount || 0),
      conflictCount: Number(candidate?.conflictCount || 0),
      conflictExplanation: String(candidate?.conflictExplanation || "none"),
      confidenceScore: Number(candidate?.confidenceScore || 0),
      sourceTypeCounts: summarizeCounts(candidate?.sourceTypeCounts),
      unread: candidate?.unread === true,
      eraseOnly: candidate?.eraseOnly === true,
      provider: String(candidate?.provider || ""),
      model: String(candidate?.model || ""),
      usage: summarizeUsage(candidate?.usage),
    })),
  };
}

async function fetchReviewPair(cdp, scope, projectRoot = "") {
  const query = new URLSearchParams({ scope });
  if (projectRoot) query.set("projectRoot", projectRoot);
  const [rest, tauri] = await Promise.all([
    appApi(`/api/app/agent/memory/review?${query.toString()}`),
    tauriInvoke(cdp, "fetch_agent_memory_review", {
      request: { scope, ...(projectRoot ? { projectRoot } : {}), timeoutMs: 30000 },
    }),
  ]);
  return { rest: summarizeReviewSnapshot(rest), tauri: summarizeReviewSnapshot(tauri) };
}

function assertReviewPair(report, pair, label) {
  for (const [transport, payload] of Object.entries(pair)) {
    if (!payload.ok || payload.schema !== "vrcforge.memory_review_snapshot.v1") {
      addAssertion(report, `${label} ${transport} did not return the Memory Review snapshot contract`);
    }
  }
  if (JSON.stringify(pair.rest) !== JSON.stringify(pair.tauri)) {
    addAssertion(report, `${label} REST and Tauri Memory Review projections differed`);
  }
}

function onlyCandidate(snapshot, expectedState, label) {
  if (snapshot?.candidates?.length !== 1) {
    throw new Error(`${label}: expected exactly one candidate, got ${snapshot?.candidates?.length ?? "unknown"}.`);
  }
  const candidate = snapshot.candidates[0];
  if (candidate.state !== expectedState) {
    throw new Error(`${label}: candidate state ${candidate.state || "<missing>"} did not equal ${expectedState}.`);
  }
  return candidate;
}

async function seedReviewChats({
  projectBText = `Please remember ${marker} project B preference.`,
  userText = redactionSourceText,
} = {}) {
  const query = new URLSearchParams();
  query.append("projectPath", projectARoot);
  query.append("projectPath", projectBRoot);
  const current = await appApi(`/api/app/chats?${query.toString()}`);
  const agentResponse = (label) => ({
    id: `${label}-agent`,
    type: "agent",
    response: {
      ok: true,
      plan: { summary: "Acknowledged", planner: "packaged-probe", shellNeeded: false, reply: "Acknowledged" },
    },
  });
  const chats = [
    {
      id: `${marker}-user-chat`,
      projectPath: "",
      items: [
        { id: `${marker}-user-source`, type: "user", text: userText },
        agentResponse(`${marker}-user`),
      ],
    },
    {
      id: `${marker}-project-a-chat`,
      projectPath: projectARoot,
      items: [
        { id: `${marker}-project-a-source`, type: "user", text: `Please remember ${marker} project A preference.` },
        agentResponse(`${marker}-project-a`),
      ],
    },
    {
      id: `${marker}-project-b-chat`,
      projectPath: projectBRoot,
      items: [
        { id: `${marker}-project-b-source`, type: "user", text: projectBText },
        agentResponse(`${marker}-project-b`),
      ],
    },
  ];
  return appApi("/api/app/chats", {
    method: "POST",
    body: { chats, sourceRevisions: Array.isArray(current?.sources) ? current.sources : [] },
    timeoutMs: 60000,
  });
}

async function findTextInTree(root, needle) {
  const pending = [root];
  let scannedFiles = 0;
  let scannedBytes = 0;
  const matches = [];
  const unreadable = [];
  const needleBuffer = Buffer.from(needle, "utf8");
  while (pending.length) {
    const current = pending.pop();
    let entries;
    try {
      entries = await readdir(current, { withFileTypes: true });
    } catch (error) {
      unreadable.push({ path: current, reason: String(error?.code || "read_failed") });
      continue;
    }
    for (const entry of entries) {
      const path = resolve(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(path);
        continue;
      }
      if (!entry.isFile()) {
        unreadable.push({ path, reason: "unsupported_entry" });
        continue;
      }
      scannedFiles += 1;
      try {
        let tail = Buffer.alloc(0);
        let found = false;
        for await (const chunk of createReadStream(path, { highWaterMark: 1024 * 1024 })) {
          scannedBytes += chunk.length;
          const combined = tail.length ? Buffer.concat([tail, chunk]) : chunk;
          if (combined.includes(needleBuffer)) found = true;
          const overlap = Math.max(0, needleBuffer.length - 1);
          tail = overlap
            ? Buffer.from(combined.subarray(Math.max(0, combined.length - overlap)))
            : Buffer.alloc(0);
        }
        if (found) matches.push(path);
      } catch (error) {
        unreadable.push({ path, reason: String(error?.code || "read_failed") });
      }
    }
  }
  return { scannedFiles, scannedBytes, matches, unreadable };
}

async function findTextInRoots(roots, needle) {
  const scans = await Promise.all(roots.map((root) => findTextInTree(root, needle)));
  return {
    scannedFiles: scans.reduce((total, scan) => total + scan.scannedFiles, 0),
    scannedBytes: scans.reduce((total, scan) => total + scan.scannedBytes, 0),
    matches: scans.flatMap((scan) => scan.matches),
    unreadable: scans.flatMap((scan) => scan.unreadable),
  };
}

function summarizeMemory(memory) {
  return {
    memoryId: String(memory?.memoryId || ""),
    scope: String(memory?.scope || ""),
    kind: String(memory?.kind || ""),
    text: String(memory?.text || ""),
    projectRoot: String(memory?.projectRoot || ""),
    status: String(memory?.status || ""),
  };
}

function summarizeMemoryPayload(payload) {
  const memories = Array.isArray(payload?.memories)
    ? payload.memories.map(summarizeMemory)
    : [];
  return {
    ok: payload?.ok === true,
    schema: String(payload?.schema || ""),
    count: Number(payload?.count ?? memories.length),
    memories,
  };
}

function memoryIds(payload) {
  return (payload?.memories || []).map((memory) => String(memory.memoryId || "")).sort();
}

function assertMemoryIds(report, payload, expectedIds, label) {
  if (payload?.ok !== true) {
    addAssertion(report, `${label} did not return ok=true`);
  }
  if (payload?.schema !== "vrcforge.agent_memory_list.v1") {
    addAssertion(
      report,
      `${label} returned schema ${JSON.stringify(payload?.schema || "")}; expected "vrcforge.agent_memory_list.v1"`,
    );
  }
  const actual = memoryIds(payload);
  const expected = [...expectedIds].sort();
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    addAssertion(
      report,
      `${label} returned memory ids ${JSON.stringify(actual)}; expected ${JSON.stringify(expected)}`,
    );
  }
  if (payload?.count !== actual.length) {
    addAssertion(report, `${label} count did not match its memory array`);
  }
}

async function fetchMemoryPair(cdp, projectRoot = "", scope = "") {
  const query = new URLSearchParams({ limit: "50" });
  if (projectRoot) query.set("projectRoot", projectRoot);
  if (scope) query.set("scope", scope);
  const [rest, tauri] = await Promise.all([
    appApi(`/api/app/agent/memory?${query.toString()}`),
    tauriInvoke(cdp, "fetch_agent_memory", {
      request: {
        limit: 50,
        ...(projectRoot ? { projectRoot } : {}),
        ...(scope ? { scope } : {}),
        timeoutMs: 30000,
      },
    }),
  ]);
  return {
    rest: summarizeMemoryPayload(rest),
    tauri: summarizeMemoryPayload(tauri),
  };
}

function assertMemoryPair(report, pair, expectedIds, label) {
  assertMemoryIds(report, pair.rest, expectedIds, `${label} REST`);
  assertMemoryIds(report, pair.tauri, expectedIds, `${label} Tauri`);
  if (JSON.stringify(memoryIds(pair.rest)) !== JSON.stringify(memoryIds(pair.tauri))) {
    addAssertion(report, `${label} REST and Tauri projections differed`);
  }
}

function normalizedPath(value) {
  return String(value || "").replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase();
}

async function assertIsolatedRuntime(sourceVersion, label) {
  const health = await appApi("/api/health");
  const expected = {
    userDataDir: userDataRoot,
    configDir: configRoot,
    artifactsDir: resolve(userDataRoot, "artifacts"),
  };
  if (String(health?.version || "") !== sourceVersion) {
    throw new Error(`${label}: packaged backend version ${String(health?.version || "<missing>")} did not match VERSION ${sourceVersion}.`);
  }
  if (health?.portableMode !== true) {
    throw new Error(`${label}: authenticated health did not report portableMode=true.`);
  }
  const canonicalEvidenceRoot = normalizedPath(await realpath(evidenceRoot));
  const canonicalPaths = {};
  for (const [key, expectedPath] of Object.entries(expected)) {
    const actualPath = health?.paths?.[key];
    const canonicalExpected = normalizedPath(await realpath(expectedPath));
    const canonicalActual = actualPath ? normalizedPath(await realpath(actualPath)) : "";
    const insideEvidence = canonicalActual.startsWith(`${canonicalEvidenceRoot}/`);
    if (
      !actualPath ||
      normalizedPath(actualPath) !== normalizedPath(expectedPath) ||
      canonicalActual !== canonicalExpected ||
      !insideEvidence
    ) {
      throw new Error(`${label}: authenticated health ${key} ${JSON.stringify(actualPath || "")} did not match isolated path ${JSON.stringify(expectedPath)}.`);
    }
    canonicalPaths[key] = canonicalActual;
  }
  return {
    version: String(health.version),
    portableMode: health.portableMode === true,
    paths: Object.fromEntries(Object.keys(expected).map((key) => [key, String(health.paths[key])])),
    canonicalPaths,
  };
}

function assertMemoryDetailPair(report, pair, expected, label) {
  for (const [transport, payload] of Object.entries(pair)) {
    const memory = (payload.memories || []).find((item) => item.memoryId === expected.memoryId);
    if (!memory) {
      addAssertion(report, `${label} ${transport} did not contain ${expected.memoryId}`);
      continue;
    }
    for (const key of ["scope", "kind", "text", "status"]) {
      if (memory[key] !== expected[key]) {
        addAssertion(
          report,
          `${label} ${transport} ${key} was ${JSON.stringify(memory[key])}; expected ${JSON.stringify(expected[key])}`,
        );
      }
    }
    if (normalizedPath(memory.projectRoot) !== normalizedPath(expected.projectRoot)) {
      addAssertion(report, `${label} ${transport} projectRoot changed across persistence`);
    }
  }
}

async function captureMemoryMatrix(cdp) {
  return {
    userOnly: await fetchMemoryPair(cdp),
    projectA: await fetchMemoryPair(cdp, projectARoot),
    projectB: await fetchMemoryPair(cdp, projectBRoot),
    projectAOnly: await fetchMemoryPair(cdp, projectARoot, "project"),
    projectBOnly: await fetchMemoryPair(cdp, projectBRoot, "project"),
  };
}

function assertPopulatedMatrix(report, matrix, expected, phase) {
  assertMemoryPair(report, matrix.userOnly, [expected.user.memoryId], `${phase} user-only`);
  assertMemoryPair(
    report,
    matrix.projectA,
    [expected.user.memoryId, expected.projectA.memoryId],
    `${phase} project A`,
  );
  assertMemoryPair(
    report,
    matrix.projectB,
    [expected.user.memoryId, expected.projectB.memoryId],
    `${phase} project B`,
  );
  assertMemoryPair(
    report,
    matrix.projectAOnly,
    [expected.projectA.memoryId],
    `${phase} project A scoped`,
  );
  assertMemoryPair(
    report,
    matrix.projectBOnly,
    [expected.projectB.memoryId],
    `${phase} project B scoped`,
  );
  assertMemoryDetailPair(report, matrix.userOnly, expected.user, `${phase} user memory`);
  assertMemoryDetailPair(report, matrix.projectAOnly, expected.projectA, `${phase} project A memory`);
  assertMemoryDetailPair(report, matrix.projectBOnly, expected.projectB, `${phase} project B memory`);
}

function assertEmptyMatrix(report, matrix, phase) {
  for (const [name, pair] of Object.entries(matrix)) {
    assertMemoryPair(report, pair, [], `${phase} ${name}`);
  }
}

async function readMemoryEventSummary(memoryIdsByName) {
  const raw = await readFile(memoryLogPath, "utf8");
  const rows = raw
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  const byMemory = {};
  for (const [name, memoryId] of Object.entries(memoryIdsByName)) {
    byMemory[name] = rows
      .filter((row) => row.memoryId === memoryId)
      .map((row) => ({ event: row.event, status: row.status, reason: row.reason || "" }));
  }
  return { rowCount: rows.length, byMemory };
}

function assertTombstones(report, eventSummary) {
  for (const [name, events] of Object.entries(eventSummary.byMemory || {})) {
    const eventNames = events.map((event) => event.event);
    const creationCount = eventNames.filter((event) => event === "memory_created").length;
    const deletionCount = eventNames.filter((event) => event === "memory_deleted").length;
    if (creationCount !== 1) {
      addAssertion(
        report,
        `${name} memory had ${creationCount} creation events in the durable JSONL log instead of 1`,
      );
    }
    if (deletionCount !== 1) {
      addAssertion(
        report,
        `${name} memory had ${deletionCount} tombstones in the durable JSONL log instead of 1`,
      );
    }
  }
}

async function main() {
  await mkdir(evidenceRoot, { recursive: true });
  const report = {
    schema: "vrcforge.packaged_memory_restart_probe.v2",
    marker,
    exe,
    cdpPort,
    evidenceRoot,
    userDataRoot,
    webviewDataRoot,
    projects: { projectA: projectARoot, projectB: projectBRoot },
    transports: ["packaged-webview-tauri-ipc", "authenticated-loopback-rest"],
    assertions: [],
    phases: {},
    closures: {},
  };
  let app;
  let provider;
  try {
    if (!Number.isInteger(cdpPort) || cdpPort < 1024 || cdpPort > 65535 || cdpPort === 8757) {
      throw new Error(`Invalid VRCFORGE_MEMORY_PROBE_CDP_PORT: ${process.env.VRCFORGE_MEMORY_PROBE_CDP_PORT || cdpPort}`);
    }
    const sourceVersion = (await readFile(resolve(repoRoot, "VERSION"), "utf8")).trim();
    const releaseBinding = await prepareManifestBoundPackage(sourceVersion);
    const packageStat = await stat(exe);
    report.package = {
      sourceVersion,
      size: packageStat.size,
      modifiedAt: packageStat.mtime.toISOString(),
      exeSha256: releaseBinding.exeSha256,
      releaseBinding,
    };
    await Promise.all([
      mkdir(configRoot, { recursive: true }),
      mkdir(webviewDataRoot, { recursive: true }),
      mkdir(projectARoot, { recursive: true }),
      mkdir(projectBRoot, { recursive: true }),
      mkdir(resolve(projectARoot, "Assets"), { recursive: true }),
      mkdir(resolve(projectARoot, "Packages"), { recursive: true }),
      mkdir(resolve(projectARoot, "ProjectSettings"), { recursive: true }),
      mkdir(resolve(projectBRoot, "Assets"), { recursive: true }),
      mkdir(resolve(projectBRoot, "Packages"), { recursive: true }),
      mkdir(resolve(projectBRoot, "ProjectSettings"), { recursive: true }),
    ]);
    const unitySentinels = {
      projectAAsset: resolve(projectARoot, "Assets", `${marker}.txt`),
      projectAVersion: resolve(projectARoot, "ProjectSettings", "ProjectVersion.txt"),
      projectBAsset: resolve(projectBRoot, "Assets", `${marker}.txt`),
      projectBVersion: resolve(projectBRoot, "ProjectSettings", "ProjectVersion.txt"),
    };
    await Promise.all([
      writeFile(unitySentinels.projectAAsset, `${marker} project A Unity sentinel\n`, "utf8"),
      writeFile(unitySentinels.projectAVersion, "m_EditorVersion: 2022.3.22f1\n", "utf8"),
      writeFile(unitySentinels.projectBAsset, `${marker} project B Unity sentinel\n`, "utf8"),
      writeFile(unitySentinels.projectBVersion, "m_EditorVersion: 2022.3.22f1\n", "utf8"),
    ]);
    report.beforeLaunch = await processSnapshot();
    if (!snapshotIsClear(report.beforeLaunch)) {
      throw new Error(`Preflight found an existing packaged instance or occupied probe port; nothing was terminated: ${JSON.stringify(report.beforeLaunch)}`);
    }

    provider = createMemoryReviewProvider();
    const providerPort = await provider.listen();
    report.provider = { port: providerPort, model: "vrcforge-memory-review-probe" };

    app = await launchPackagedApp();
    const createRuntime = await assertIsolatedRuntime(sourceVersion, "create launch");
    report.phases.createLaunch = {
      childPid: app.childPid,
      health: app.health,
      authenticatedHealth: createRuntime,
      renderer: app.renderer,
    };
    const configuredProvider = await appApi("/api/config", {
      method: "POST",
      body: {
        provider: "custom",
        api_key: "packaged-memory-review-probe-key",
        base_url: `http://127.0.0.1:${providerPort}/v1`,
        model: "vrcforge-memory-review-probe",
        thinking_level: "",
      },
    });
    report.phases.reviewProviderConfig = {
      provider: String(configuredProvider?.apiConfig?.provider || ""),
      model: String(configuredProvider?.apiConfig?.model || ""),
      baseUrlConfigured: Boolean(configuredProvider?.apiConfig?.base_url || configuredProvider?.apiConfig?.baseUrl),
    };
    const seededChats = await seedReviewChats();
    const unityTreeManifest = {
      projectA: await snapshotUnityProjectTree(projectARoot),
      projectB: await snapshotUnityProjectTree(projectBRoot),
    };
    report.phases.reviewSources = {
      appCount: Number(seededChats?.appCount || 0),
      projectCount: Array.isArray(seededChats?.projectPaths) ? seededChats.projectPaths.length : 0,
      unityTreeManifest: {
        projectA: summarizeTreeManifest(unityTreeManifest.projectA),
        projectB: summarizeTreeManifest(unityTreeManifest.projectB),
      },
    };
    const initialReview = await fetchReviewPair(app.cdp, "project", projectARoot);
    assertReviewPair(report, initialReview, "initial project A review");
    const reviewConfig = {
      mode: "suggest_only",
      cadenceMinutes: 1440,
      inputCharCap: 12000,
      tokenCap: 2048,
      costCapUsd: 0,
      inputCostPerMillionUsd: 0,
      outputCostPerMillionUsd: 0,
      retentionDays: 30,
      provider: "custom",
      model: "vrcforge-memory-review-probe",
    };
    const configuredProjectA = await appApi("/api/app/agent/memory/review/config", {
      method: "POST",
      body: {
        ...reviewConfig,
        scope: "project",
        projectRoot: projectARoot,
        expectedRevision: initialReview.rest.revision,
      },
    });
    const concurrentRevisionResults = await Promise.allSettled([
      appApi("/api/app/agent/memory/review/config", {
        method: "POST",
        body: { ...reviewConfig, retentionDays: 31, scope: "project", projectRoot: projectARoot, expectedRevision: configuredProjectA.revision },
      }),
      appApi("/api/app/agent/memory/review/config", {
        method: "POST",
        body: { ...reviewConfig, retentionDays: 32, scope: "project", projectRoot: projectARoot, expectedRevision: configuredProjectA.revision },
      }),
    ]);
    const concurrentRevisionAccepted = concurrentRevisionResults.filter((result) => result.status === "fulfilled").length;
    const concurrentRevisionRejected = concurrentRevisionResults.filter((result) => result.status === "rejected").length;
    if (concurrentRevisionAccepted !== 1 || concurrentRevisionRejected !== 1) {
      addAssertion(report, "concurrent Memory Review config mutations did not accept exactly one revision");
    }
    const afterRevisionRace = await fetchReviewPair(app.cdp, "project", projectARoot);
    assertReviewPair(report, afterRevisionRace, "project A concurrent revision result");
    report.phases.concurrentRevision = {
      accepted: concurrentRevisionAccepted,
      rejected: concurrentRevisionRejected,
      revision: afterRevisionRace.rest.revision,
    };
    const projectARun = await tauriInvoke(app.cdp, "run_agent_memory_review", {
      request: {
        body: {
          scope: "project",
          projectRoot: projectARoot,
          expectedRevision: afterRevisionRace.rest.revision,
        },
        timeoutMs: 300000,
      },
    });
    const projectACandidate = onlyCandidate(projectARun, "proposed", "project A review run");
    const crashPromotionText = `${marker} crash-reconciled review memory`;
    const editedReviewText = `${marker} edited accepted review memory`;

    app.cdp.close();
    report.closures.beforePromotionCrash = await closePackagedApp(app);
    assertGracefulClosure(report, report.closures.beforePromotionCrash, "before promotion crash seed");
    app = undefined;
    report.phases.promotionCrashBeforeMemory = await seedPromotionCrashState(
      projectACandidate.candidateId,
      crashPromotionText,
      "before_memory_write",
    );

    app = await launchPackagedApp();
    await assertIsolatedRuntime(sourceVersion, "promotion crash before Memory write restart");
    const recoveredBeforeMemory = await fetchReviewPair(app.cdp, "project", projectARoot);
    assertReviewPair(report, recoveredBeforeMemory, "promotion crash before-memory recovery");
    onlyCandidate(recoveredBeforeMemory.rest, "accepted", "promotion crash before-memory recovery");
    const crashRecoveredMemory = await fetchMemoryPair(app.cdp, projectARoot, "project");
    if (crashRecoveredMemory.rest.memories.length !== 1 || crashRecoveredMemory.rest.memories[0].text !== crashPromotionText) {
      addAssertion(report, "before-memory promotion crash did not reconcile exactly one accepted Memory");
    }
    const undoneCrashPromotion = await tauriInvoke(app.cdp, "mutate_agent_memory_review_candidate", {
      request: {
        id: projectACandidate.candidateId,
        action: "undo",
        body: {
          expectedRevision: recoveredBeforeMemory.rest.revision,
          projectRoot: projectARoot,
        },
        timeoutMs: 60000,
      },
    });
    onlyCandidate(undoneCrashPromotion, "proposed", "promotion crash recovery undo");

    const acceptedProjectA = await appApi(
      `/api/app/agent/memory/review/candidates/${encodeURIComponent(projectACandidate.candidateId)}/accept`,
      {
        method: "POST",
        body: {
          expectedRevision: undoneCrashPromotion.revision,
          projectRoot: projectARoot,
          editedText: editedReviewText,
        },
      },
    );
    onlyCandidate(acceptedProjectA, "accepted", "project A edited acceptance");
    const acceptedMemory = await fetchMemoryPair(app.cdp, projectARoot, "project");
    if (acceptedMemory.rest.memories.length !== 1 || acceptedMemory.rest.memories[0].text !== editedReviewText) {
      addAssertion(report, "edited Review acceptance did not create exactly one project A Memory record");
    }
    report.phases.reviewAccepted = {
      candidateId: projectACandidate.candidateId,
      revision: acceptedProjectA.revision,
      memory: acceptedMemory.rest.memories[0] || null,
    };

    app.cdp.close();
    report.closures.beforeAfterMemoryCrash = await closePackagedApp(app);
    assertGracefulClosure(report, report.closures.beforeAfterMemoryCrash, "before after-memory crash seed");
    app = undefined;
    report.phases.promotionCrashAfterMemory = await seedPromotionCrashState(
      projectACandidate.candidateId,
      editedReviewText,
      "after_memory_write",
    );

    app = await launchPackagedApp();
    const reviewRestartRuntime = await assertIsolatedRuntime(sourceVersion, "promotion crash after Memory write restart");
    const persistedProjectA = await fetchReviewPair(app.cdp, "project", projectARoot);
    assertReviewPair(report, persistedProjectA, "project A review after restart");
    const persistedCandidate = onlyCandidate(persistedProjectA.rest, "accepted", "project A review after restart");
    if (persistedCandidate.candidateId !== projectACandidate.candidateId) {
      addAssertion(report, "project A Review candidate identity changed across restart");
    }
    const persistedMemory = await fetchMemoryPair(app.cdp, projectARoot, "project");
    if (persistedMemory.rest.memories.length !== 1 || persistedMemory.rest.memories[0].text !== editedReviewText) {
      addAssertion(report, "accepted project A Review memory did not survive restart exactly once");
    }
    const undoneProjectA = await tauriInvoke(app.cdp, "mutate_agent_memory_review_candidate", {
      request: {
        id: projectACandidate.candidateId,
        action: "undo",
        body: {
          expectedRevision: persistedProjectA.rest.revision,
          projectRoot: projectARoot,
        },
        timeoutMs: 60000,
      },
    });
    onlyCandidate(undoneProjectA, "proposed", "project A undo");
    const erasedProjectA = await appApi(
      `/api/app/agent/memory/review/candidates/${encodeURIComponent(projectACandidate.candidateId)}/erase`,
      {
        method: "POST",
        body: { expectedRevision: undoneProjectA.revision, projectRoot: projectARoot },
        timeoutMs: 60000,
      },
    );
    if (erasedProjectA.candidates.length !== 0) addAssertion(report, "project A Review erase left a candidate card");

    const configuredProjectB = await tauriInvoke(app.cdp, "update_agent_memory_review", {
      request: {
        body: {
          ...reviewConfig,
          scope: "project",
          projectRoot: projectBRoot,
          expectedRevision: erasedProjectA.revision,
        },
        timeoutMs: 60000,
      },
    });
    const projectBRun = await appApi("/api/app/agent/memory/review/run", {
      method: "POST",
      body: {
        scope: "project",
        projectRoot: projectBRoot,
        expectedRevision: configuredProjectB.revision,
      },
      timeoutMs: 120000,
    });
    const projectBCandidate = onlyCandidate(projectBRun, "proposed", "project B review run");
    await seedReviewChats({
      projectBText: `Please remember ${marker} changed project B preference.`,
    });
    const staleAcceptSnapshot = await appApi(
      `/api/app/agent/memory/review/candidates/${encodeURIComponent(projectBCandidate.candidateId)}/accept`,
      {
        method: "POST",
        body: { expectedRevision: projectBRun.revision, projectRoot: projectBRoot },
      },
    );
    if (staleAcceptSnapshot.candidates?.[0]?.state !== "expired") {
      addAssertion(report, "stale candidate acceptance did not return the authoritative expired state");
    }
    const invalidatedProjectB = await fetchReviewPair(app.cdp, "project", projectBRoot);
    assertReviewPair(report, invalidatedProjectB, "project B source invalidation");
    if (invalidatedProjectB.rest.candidates.length !== 1 || invalidatedProjectB.rest.candidates[0].state !== "expired") {
      addAssertion(report, "project B source edit did not invalidate the stale candidate");
    }
    const projectBRerun = await appApi("/api/app/agent/memory/review/run", {
      method: "POST",
      body: {
        scope: "project",
        projectRoot: projectBRoot,
        expectedRevision: invalidatedProjectB.rest.revision,
      },
      timeoutMs: 120000,
    });
    const replacementProjectB = projectBRerun.candidates.find((candidate) => candidate.state === "proposed");
    if (!replacementProjectB || projectBRerun.candidates.length !== 2) {
      throw new Error("project B rerun did not preserve one invalidated card plus one fresh candidate");
    }
    const rejectedProjectB = await tauriInvoke(app.cdp, "mutate_agent_memory_review_candidate", {
      request: {
        id: replacementProjectB.candidateId,
        action: "reject",
        body: { expectedRevision: projectBRerun.revision, projectRoot: projectBRoot },
        timeoutMs: 60000,
      },
    });
    if (!rejectedProjectB.candidates.some((candidate) => candidate.candidateId === replacementProjectB.candidateId && candidate.state === "rejected")) {
      addAssertion(report, "project B replacement candidate was not rejected");
    }
    const erasedReplacementProjectB = await appApi(
      `/api/app/agent/memory/review/candidates/${encodeURIComponent(replacementProjectB.candidateId)}/erase`,
      {
        method: "POST",
        body: { expectedRevision: rejectedProjectB.revision, projectRoot: projectBRoot },
        timeoutMs: 60000,
      },
    );
    const erasedProjectB = await appApi(
      `/api/app/agent/memory/review/candidates/${encodeURIComponent(projectBCandidate.candidateId)}/erase`,
      {
        method: "POST",
        body: { expectedRevision: erasedReplacementProjectB.revision, projectRoot: projectBRoot },
        timeoutMs: 60000,
      },
    );
    report.phases.sourceInvalidation = {
      staleCandidateId: projectBCandidate.candidateId,
      replacementCandidateId: replacementProjectB.candidateId,
      finalCandidateCount: erasedProjectB.candidates.length,
    };

    const configuredUser = await appApi("/api/app/agent/memory/review/config", {
      method: "POST",
      body: { ...reviewConfig, scope: "user", expectedRevision: erasedProjectB.revision },
    });
    const userRun = await tauriInvoke(app.cdp, "run_agent_memory_review", {
      request: {
        body: { scope: "user", expectedRevision: configuredUser.revision },
        timeoutMs: 120000,
      },
    });
    const userCandidate = onlyCandidate(userRun, "proposed", "user review run");
    const deferredUser = await appApi(
      `/api/app/agent/memory/review/candidates/${encodeURIComponent(userCandidate.candidateId)}/defer`,
      { method: "POST", body: { expectedRevision: userRun.revision } },
    );
    onlyCandidate(deferredUser, "deferred", "user candidate deferral");
    const erasedUser = await tauriInvoke(app.cdp, "mutate_agent_memory_review_candidate", {
      request: {
        id: userCandidate.candidateId,
        action: "erase",
        body: { expectedRevision: deferredUser.revision },
        timeoutMs: 60000,
      },
    });
    provider.failNextRequests(3);
    await expectAppApiFailure(
      "/api/app/agent/memory/review/run",
      {
        method: "POST",
        body: { scope: "user", expectedRevision: erasedUser.revision },
        timeoutMs: 300000,
      },
      503,
    );
    const providerFailureReview = await fetchReviewPair(app.cdp, "user");
    assertReviewPair(report, providerFailureReview, "provider failure review");
    if (providerFailureReview.rest.lastRunStatus !== "failed" || !providerFailureReview.rest.lastFailureClass) {
      addAssertion(report, "provider failure did not remain visible in durable Memory Review status");
    }
    report.phases.providerFailure = {
      runState: providerFailureReview.rest.runState,
      lastRunStatus: providerFailureReview.rest.lastRunStatus,
      failureClass: providerFailureReview.rest.lastFailureClass,
      candidateCount: providerFailureReview.rest.candidates.length,
    };
    const scopePairs = {
      user: await fetchReviewPair(app.cdp, "user"),
      projectA: await fetchReviewPair(app.cdp, "project", projectARoot),
      projectB: await fetchReviewPair(app.cdp, "project", projectBRoot),
    };
    for (const [scopeName, pair] of Object.entries(scopePairs)) {
      assertReviewPair(report, pair, `${scopeName} final Review scope`);
      if (pair.rest.candidates.length !== 0 || pair.rest.unreadCount !== 0) {
        addAssertion(report, `${scopeName} Review scope was not empty after permanent erase`);
      }
    }
    app.cdp.close();
    report.closures.afterReviewErase = await closePackagedApp(app);
    assertGracefulClosure(report, report.closures.afterReviewErase, "after review erase");
    app = undefined;
    const erasedTextScans = {
      priorGeneration: await findTextInRoots([userDataRoot, webviewDataRoot], crashPromotionText),
      latestGeneration: await findTextInRoots([userDataRoot, webviewDataRoot], editedReviewText),
    };
    for (const scan of Object.values(erasedTextScans)) {
      if (scan.matches.length > 0) {
        addAssertion(report, "permanently erased Review prose remained in app-owned storage");
      }
      if (scan.unreadable.length > 0) {
        addAssertion(report, "physical erase proof could not read every app-owned storage file");
      }
    }
    const rawProviderBodies = provider.rawBodies.join("\n");
    const renderedReviewSnapshots = JSON.stringify(scopePairs);
    const redactionScans = await Promise.all(
      redactionSentinels.map((sentinel) => findTextInRoots([userDataRoot, webviewDataRoot], sentinel)),
    );
    for (let index = 0; index < redactionSentinels.length; index += 1) {
      if (rawProviderBodies.includes(redactionSentinels[index])) {
        addAssertion(report, "Memory Review provider request exposed a redaction sentinel");
      }
      if (renderedReviewSnapshots.includes(redactionSentinels[index])) {
        addAssertion(report, "Memory Review WebView projection exposed a redaction sentinel");
      }
      if (redactionScans[index].matches.length > 0) {
        addAssertion(report, "Memory Review persisted a redaction sentinel in app-owned storage");
      }
      if (redactionScans[index].unreadable.length > 0) {
        addAssertion(report, "redaction proof could not read every app-owned storage file");
      }
    }
    if (rawProviderBodies.includes(projectARoot) || rawProviderBodies.includes(projectBRoot)) {
      addAssertion(report, "Memory Review provider request exposed an exact local project path");
    }
    if (provider.requests.length !== 7 || provider.requests.some((request) => request.hasTools)) {
      addAssertion(report, "Memory Review provider calls were not exactly seven tool-free bounded requests");
    }
    const unityTreeAfterReview = {
      projectA: await snapshotUnityProjectTree(projectARoot),
      projectB: await snapshotUnityProjectTree(projectBRoot),
    };
    const unityTreeUnchanged = JSON.stringify(unityTreeAfterReview) === JSON.stringify(unityTreeManifest);
    if (!unityTreeUnchanged) {
      addAssertion(report, "Memory Review changed an isolated Unity project file tree");
    }
    report.phases.reviewRestart = {
      authenticatedHealth: reviewRestartRuntime,
      candidateCountAfterErase: erasedUser.candidates.length,
      providerRequests: provider.requests,
      erasedTextScans: Object.fromEntries(
        Object.entries(erasedTextScans).map(([name, scan]) => [name, {
          scannedFiles: scan.scannedFiles,
          scannedBytes: scan.scannedBytes,
          matchCount: scan.matches.length,
          unreadableCount: scan.unreadable.length,
        }]),
      ),
      redactionScans: redactionScans.map((scan) => ({
        scannedFiles: scan.scannedFiles,
        scannedBytes: scan.scannedBytes,
        matchCount: scan.matches.length,
        unreadableCount: scan.unreadable.length,
      })),
      scopePairs,
      unityTreeUnchanged,
      unityTreeManifest: {
        projectA: summarizeTreeManifest(unityTreeAfterReview.projectA),
        projectB: summarizeTreeManifest(unityTreeAfterReview.projectB),
      },
    };

    app = await launchPackagedApp();
    report.phases.afterReviewEraseRestart = await assertIsolatedRuntime(
      sourceVersion,
      "after review erase restart",
    );

    const userText = `${marker} user memory`;
    const projectAText = `${marker} project A memory`;
    const projectBText = `${marker} project B isolation sentinel`;
    const userCreated = await tauriInvoke(app.cdp, "create_agent_memory", {
      request: {
        body: { text: userText, scope: "user", kind: "preference", source: "packaged-probe" },
        timeoutMs: 60000,
      },
    });
    const projectACreated = await appApi("/api/app/agent/memory", {
      method: "POST",
      body: {
        text: projectAText,
        scope: "project",
        kind: "project_fact",
        source: "packaged-probe",
        projectRoot: projectARoot,
      },
    });
    const projectBCreated = await tauriInvoke(app.cdp, "create_agent_memory", {
      request: {
        body: {
          text: projectBText,
          scope: "project",
          kind: "project_fact",
          source: "packaged-probe",
          projectRoot: projectBRoot,
        },
        timeoutMs: 60000,
      },
    });
    const ids = {
      user: String(userCreated?.memory?.memoryId || ""),
      projectA: String(projectACreated?.memory?.memoryId || ""),
      projectB: String(projectBCreated?.memory?.memoryId || ""),
    };
    report.memoryIds = ids;
    report.phases.created = {
      userViaTauri: summarizeMemory(userCreated?.memory),
      projectAViaRest: summarizeMemory(projectACreated?.memory),
      projectBViaTauri: summarizeMemory(projectBCreated?.memory),
    };
    for (const [name, memoryId] of Object.entries(ids)) {
      if (!memoryId) {
        addAssertion(report, `${name} memory creation did not return a memoryId`);
      }
    }
    if (Object.values(ids).some((memoryId) => !memoryId)) {
      throw new Error("Cannot continue restart proof without all three memory ids.");
    }
    const expected = {
      user: {
        memoryId: ids.user,
        scope: "user",
        kind: "preference",
        text: userText,
        projectRoot: "",
        status: "active",
      },
      projectA: {
        memoryId: ids.projectA,
        scope: "project",
        kind: "project_fact",
        text: projectAText,
        projectRoot: projectARoot,
        status: "active",
      },
      projectB: {
        memoryId: ids.projectB,
        scope: "project",
        kind: "project_fact",
        text: projectBText,
        projectRoot: projectBRoot,
        status: "active",
      },
    };
    const beforeRestart = await captureMemoryMatrix(app.cdp);
    report.phases.beforeRestart = beforeRestart;
    assertPopulatedMatrix(report, beforeRestart, expected, "before restart");
    if (report.assertions.length > 0) {
      throw new Error("Refusing to continue to destructive memory operations after the isolated pre-restart matrix failed.");
    }

    app.cdp.close();
    report.closures.afterCreate = await closePackagedApp(app);
    assertGracefulClosure(report, report.closures.afterCreate, "after memory creation");
    app = undefined;

    app = await launchPackagedApp();
    const persistenceRuntime = await assertIsolatedRuntime(sourceVersion, "persistence restart");
    report.phases.persistenceLaunch = {
      childPid: app.childPid,
      health: app.health,
      authenticatedHealth: persistenceRuntime,
      renderer: app.renderer,
    };
    const afterRestart = await captureMemoryMatrix(app.cdp);
    report.phases.afterRestart = afterRestart;
    assertPopulatedMatrix(report, afterRestart, expected, "after restart");
    if (report.assertions.length > 0) {
      throw new Error("Refusing to clear memory after the isolated persistence matrix failed.");
    }

    const clearedUser = await tauriInvoke(app.cdp, "clear_agent_memory", {
      request: {
        body: { scope: "user", reason: `${marker} user clear` },
        timeoutMs: 60000,
      },
    });
    const afterUserClear = await captureMemoryMatrix(app.cdp);
    report.phases.afterUserClear = afterUserClear;
    assertMemoryPair(report, afterUserClear.userOnly, [], "after user clear user-only");
    assertMemoryPair(
      report,
      afterUserClear.projectA,
      [ids.projectA],
      "after user clear project A",
    );
    assertMemoryPair(
      report,
      afterUserClear.projectB,
      [ids.projectB],
      "after user clear project B",
    );
    assertMemoryPair(
      report,
      afterUserClear.projectAOnly,
      [ids.projectA],
      "after user clear project A scoped",
    );
    assertMemoryPair(
      report,
      afterUserClear.projectBOnly,
      [ids.projectB],
      "after user clear project B scoped",
    );
    if (report.assertions.length > 0) {
      throw new Error("Refusing further memory deletion after the isolated user-clear matrix failed.");
    }
    const clearedProjectA = await appApi("/api/app/agent/memory/clear", {
      method: "POST",
      body: {
        scope: "project",
        projectRoot: projectARoot,
        reason: `${marker} project A clear`,
      },
    });
    const afterProjectAClear = await captureMemoryMatrix(app.cdp);
    report.phases.afterProjectAClear = afterProjectAClear;
    assertMemoryPair(report, afterProjectAClear.userOnly, [], "after project A clear user-only");
    assertMemoryPair(report, afterProjectAClear.projectA, [], "after project A clear project A");
    assertMemoryPair(
      report,
      afterProjectAClear.projectB,
      [ids.projectB],
      "after project A clear project B",
    );
    assertMemoryPair(
      report,
      afterProjectAClear.projectAOnly,
      [],
      "after project A clear project A scoped",
    );
    assertMemoryPair(
      report,
      afterProjectAClear.projectBOnly,
      [ids.projectB],
      "after project A clear project B scoped",
    );
    if (report.assertions.length > 0) {
      throw new Error("Refusing the final memory tombstone after the isolated project-clear matrix failed.");
    }
    const deletedProjectB = await tauriInvoke(app.cdp, "delete_agent_memory", {
      request: {
        id: ids.projectB,
        body: { reason: `${marker} project B tombstone` },
        timeoutMs: 60000,
      },
    });
    report.phases.mutations = {
      userClearViaTauri: clearedUser,
      projectAClearViaRest: clearedProjectA,
      projectBDeleteViaTauri: {
        ok: deletedProjectB?.ok === true,
        memory: summarizeMemory(deletedProjectB?.memory),
      },
    };
    if (clearedUser?.cleared !== 1) {
      addAssertion(report, `user clear removed ${clearedUser?.cleared ?? "unknown"} memories instead of 1`);
    }
    if (clearedProjectA?.cleared !== 1) {
      addAssertion(
        report,
        `project A clear removed ${clearedProjectA?.cleared ?? "unknown"} memories instead of 1`,
      );
    }
    if (deletedProjectB?.memory?.status !== "deleted") {
      addAssertion(report, "project B delete did not return a deleted tombstone");
    }
    const afterMutation = await captureMemoryMatrix(app.cdp);
    report.phases.afterMutation = afterMutation;
    assertEmptyMatrix(report, afterMutation, "after clear/tombstone");

    app.cdp.close();
    report.closures.afterMutation = await closePackagedApp(app);
    assertGracefulClosure(report, report.closures.afterMutation, "after clear/tombstone");
    app = undefined;

    app = await launchPackagedApp();
    const tombstoneRuntime = await assertIsolatedRuntime(sourceVersion, "tombstone restart");
    report.phases.tombstoneRestartLaunch = {
      childPid: app.childPid,
      health: app.health,
      authenticatedHealth: tombstoneRuntime,
      renderer: app.renderer,
    };
    const afterTombstoneRestart = await captureMemoryMatrix(app.cdp);
    report.phases.afterTombstoneRestart = afterTombstoneRestart;
    assertEmptyMatrix(report, afterTombstoneRestart, "after tombstone restart");
    const finalReviewScopes = {
      user: await fetchReviewPair(app.cdp, "user"),
      projectA: await fetchReviewPair(app.cdp, "project", projectARoot),
      projectB: await fetchReviewPair(app.cdp, "project", projectBRoot),
    };
    for (const [scopeName, pair] of Object.entries(finalReviewScopes)) {
      assertReviewPair(report, pair, `${scopeName} Review scope after later restarts`);
      if (pair.rest.candidates.length !== 0 || pair.rest.unreadCount !== 0) {
        addAssertion(report, `${scopeName} Review candidate revived after later restart`);
      }
    }
    app.cdp.close();
    report.closures.final = await closePackagedApp(app);
    assertGracefulClosure(report, report.closures.final, "after final restart");
    app = undefined;

    const finalErasedTextScans = {
      priorGeneration: await findTextInRoots([userDataRoot, webviewDataRoot], crashPromotionText),
      latestGeneration: await findTextInRoots([userDataRoot, webviewDataRoot], editedReviewText),
    };
    for (const scan of Object.values(finalErasedTextScans)) {
      if (scan.matches.length > 0) {
        addAssertion(report, "permanently erased Review prose reappeared after later restart");
      }
      if (scan.unreadable.length > 0) {
        addAssertion(report, "restart erase proof could not read every app-owned storage file");
      }
    }
    const finalRedactionScans = await Promise.all(
      redactionSentinels.map((sentinel) => findTextInRoots([userDataRoot, webviewDataRoot], sentinel)),
    );
    for (const scan of finalRedactionScans) {
      if (scan.matches.length > 0) {
        addAssertion(report, "redaction sentinel reappeared in app-owned storage after restart");
      }
      if (scan.unreadable.length > 0) {
        addAssertion(report, "final redaction proof could not read every app-owned storage file");
      }
    }
    const finalUnityTreeManifest = {
      projectA: await snapshotUnityProjectTree(projectARoot),
      projectB: await snapshotUnityProjectTree(projectBRoot),
    };
    const finalUnityTreeUnchanged = JSON.stringify(finalUnityTreeManifest) === JSON.stringify(unityTreeManifest);
    if (!finalUnityTreeUnchanged) {
      addAssertion(report, "later Memory operations changed an isolated Unity project file tree");
    }
    report.phases.reviewAfterLaterRestarts = {
      scopes: finalReviewScopes,
      erasedTextScans: Object.fromEntries(
        Object.entries(finalErasedTextScans).map(([name, scan]) => [name, {
          scannedFiles: scan.scannedFiles,
          scannedBytes: scan.scannedBytes,
          matchCount: scan.matches.length,
          unreadableCount: scan.unreadable.length,
        }]),
      ),
      redactionScans: finalRedactionScans.map((scan) => ({
        scannedFiles: scan.scannedFiles,
        scannedBytes: scan.scannedBytes,
        matchCount: scan.matches.length,
        unreadableCount: scan.unreadable.length,
      })),
      unityTreeUnchanged: finalUnityTreeUnchanged,
      unityTreeManifest: {
        projectA: summarizeTreeManifest(finalUnityTreeManifest.projectA),
        projectB: summarizeTreeManifest(finalUnityTreeManifest.projectB),
      },
    };

    report.memoryLog = await readMemoryEventSummary(ids);
    assertTombstones(report, report.memoryLog);
  } catch (error) {
    report.error = String(error?.stack || error);
    addAssertion(report, `probe aborted: ${String(error?.message || error)}`);
  } finally {
    if (app?.cdp) {
      app.cdp.close();
    }
    try {
      if (app) {
        const closure = await closePackagedApp(app);
        report.closures.finally = closure;
        assertGracefulClosure(report, closure, "during finally cleanup");
      }
      if (provider) {
        await provider.close();
        report.providerRequests = provider.requests;
        provider = undefined;
      }
      report.finalCleanup = await processSnapshot();
      if (!snapshotIsClear(report.finalCleanup)) {
        addAssertion(report, "packaged processes or probe ports remained after final cleanup");
      }
    } catch (cleanupError) {
      report.cleanupError = String(cleanupError?.stack || cleanupError);
      addAssertion(report, `final cleanup failed: ${String(cleanupError?.message || cleanupError)}`);
      if (app) {
        await forceCloseLaunch(app).catch((forceError) => {
          addAssertion(report, `final scoped cleanup failed: ${String(forceError?.message || forceError)}`);
        });
      }
      if (provider) {
        await provider.close().catch(() => {});
        provider = undefined;
      }
    }
    report.ok = report.assertions.length === 0;
    await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  }
  console.log(reportPath);
  if (!report.ok) {
    console.error(`Packaged memory restart probe failed: ${report.assertions.join("; ")}`);
    process.exitCode = 1;
  }
}

main().catch(async (error) => {
  await mkdir(dirname(reportPath), { recursive: true });
  await writeFile(
    reportPath,
    `${JSON.stringify({
      schema: "vrcforge.packaged_memory_restart_probe.v2",
      marker,
      ok: false,
      assertions: [`unhandled probe failure: ${String(error?.message || error)}`],
      error: String(error?.stack || error),
    }, null, 2)}\n`,
    "utf8",
  ).catch(() => {});
  console.error(error);
  process.exit(1);
});
