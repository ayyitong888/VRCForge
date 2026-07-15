import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, realpath, stat, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const cdpPort = Number(process.env.VRCFORGE_MEMORY_PROBE_CDP_PORT || "9348");
const marker = `MEMORY_RESTART_PROBE_${Date.now()}`;
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
const appOrigin = "http://127.0.0.1:8757";
const appRequestOrigin = "http://tauri.localhost";
let appSessionToken = "";

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`Usage: node scripts/diagnose_packaged_memory_restart.mjs

Runs the packaged Memory persistence/isolation/tombstone restart probe.
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
    schema: "vrcforge.packaged_memory_restart_probe.v1",
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
    ]);
    report.beforeLaunch = await processSnapshot();
    if (!snapshotIsClear(report.beforeLaunch)) {
      throw new Error(`Preflight found an existing packaged instance or occupied probe port; nothing was terminated: ${JSON.stringify(report.beforeLaunch)}`);
    }

    app = await launchPackagedApp();
    const createRuntime = await assertIsolatedRuntime(sourceVersion, "create launch");
    report.phases.createLaunch = {
      childPid: app.childPid,
      health: app.health,
      authenticatedHealth: createRuntime,
      renderer: app.renderer,
    };
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

    app.cdp.close();
    report.closures.final = await closePackagedApp(app);
    assertGracefulClosure(report, report.closures.final, "after final restart");
    app = undefined;

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
      schema: "vrcforge.packaged_memory_restart_probe.v1",
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
