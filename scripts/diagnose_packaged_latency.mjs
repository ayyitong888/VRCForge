import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { requestPackagedAppQuit } from "./lib/packaged_app_lifecycle.mjs";

const repoRoot = resolve(import.meta.dirname, "..");
const args = process.argv.slice(2);
const selfTest = args.includes("--self-test");
const startupOnly = args.includes("--startup-only");
const allowUnpushed = args.includes("--allow-unpushed");
const profileRootIndex = args.indexOf("--profile-root");
const sampleIndex = args.indexOf("--sample");
const explicitProfileRoot = profileRootIndex >= 0 ? String(args[profileRootIndex + 1] || "").trim() : "";
const startupSample = sampleIndex >= 0 ? String(args[sampleIndex + 1] || "").trim().toLowerCase() : "";
if (startupOnly && !explicitProfileRoot) {
  throw new Error("--startup-only requires --profile-root <isolated-path> so cold and warm runs can be bound explicitly.");
}
if (startupOnly && !["cold", "warm"].includes(startupSample)) {
  throw new Error("--startup-only requires --sample cold or --sample warm.");
}
const port = Number(process.env.VRCFORGE_CDP_PORT || "9340");
const marker = `LATENCY_PROBE_${Date.now()}`;
const runRoot = resolve(repoRoot, "artifacts", "latency", marker);
const packagedRoot = startupOnly ? resolve(runRoot, "package") : resolve(repoRoot, "dist", "VRCForge_Windows_x64");
const packagedRootPowerShell = packagedRoot.replaceAll("'", "''");
const exe = resolve(packagedRoot, "VRCForge.exe");
const outPath = resolve(repoRoot, "artifacts", "latency", `packaged-latency-${marker}.json`);
const maxWaitMs = Number(process.env.VRCFORGE_PROBE_WAIT_MS || "180000");
const closeOnComplete = process.env.VRCFORGE_PROBE_CLOSE_ON_COMPLETE === "1";
const profileRoot = explicitProfileRoot ? resolve(explicitProfileRoot) : "";
const startupProfilesRoot = resolve(repoRoot, "artifacts", "latency", "profiles");
const profileRelative = profileRoot ? relative(startupProfilesRoot, profileRoot) : "";
if (startupOnly && (!profileRelative || profileRelative.startsWith("..") || isAbsolute(profileRelative))) {
  throw new Error("--profile-root must be a dedicated child of artifacts/latency/profiles.");
}
const userDataRoot = profileRoot ? resolve(profileRoot, "user-data") : "";
const configRoot = userDataRoot ? resolve(userDataRoot, "config") : "";
const hostProfileRoot = profileRoot ? resolve(profileRoot, "host-profile") : "";
const webviewDataRoot = profileRoot ? resolve(profileRoot, "webview2-user-data") : "";
const startupPairMarkerPath = profileRoot ? resolve(profileRoot, "startup-pair.json") : "";

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
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

function escapePowerShellLiteral(value) {
  return String(value).replaceAll("'", "''");
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

function normalizeBuildPolicy(manifest) {
  const raw = manifest?.buildPolicy && typeof manifest.buildPolicy === "object"
    ? manifest.buildPolicy
    : {};
  return {
    mode: String(raw.mode || ""),
    releaseEligible: raw.releaseEligible === true,
    allowDirty: raw.allowDirty === true,
    allowUnpushed: raw.allowUnpushed === true,
    allowVersionMismatch: raw.allowVersionMismatch === true,
  };
}

function strictBuildPolicy(policy) {
  return policy.mode === "strict"
    && policy.releaseEligible === true
    && policy.allowDirty === false
    && policy.allowUnpushed === false
    && policy.allowVersionMismatch === false;
}

function localAcceptanceBuildPolicy(policy) {
  return policy.mode === "local-acceptance"
    && policy.releaseEligible === false
    && policy.allowDirty === false
    && policy.allowUnpushed === true;
}

function expectedStartupPairMarker(releaseBinding) {
  return {
    schema: "vrcforge.packaged_startup_pair.v1",
    manifestCommit: releaseBinding.manifestCommit,
    portableSha256: releaseBinding.portableSha256,
    profileRoot,
    coldCompleted: true,
    profilePreparedForWarm: true,
  };
}

function startupPairMarkerMatches(markerDocument, releaseBinding) {
  const expected = expectedStartupPairMarker(releaseBinding);
  return markerDocument?.schema === expected.schema
    && markerDocument?.manifestCommit === expected.manifestCommit
    && markerDocument?.portableSha256 === expected.portableSha256
    && markerDocument?.profileRoot === expected.profileRoot
    && markerDocument?.coldCompleted === true
    && markerDocument?.profilePreparedForWarm === true;
}

async function requireWarmStartupPairMarker(releaseBinding) {
  let markerDocument;
  try {
    markerDocument = JSON.parse((await readFile(startupPairMarkerPath, "utf8")).replace(/^\uFEFF/, ""));
  } catch (error) {
    throw new Error(`Warm startup evidence requires the successful cold marker: ${String(error?.message || error)}`);
  }
  if (!startupPairMarkerMatches(markerDocument, releaseBinding)) {
    throw new Error("Warm startup profile marker did not match the exact manifest commit, portable ZIP, and profile identity.");
  }
  return markerDocument;
}

async function writeColdStartupPairMarker(releaseBinding) {
  const markerDocument = expectedStartupPairMarker(releaseBinding);
  const temporaryPath = `${startupPairMarkerPath}.${marker}.tmp`;
  try {
    await writeFile(temporaryPath, `${JSON.stringify(markerDocument, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
    await rename(temporaryPath, startupPairMarkerPath);
  } finally {
    await rm(temporaryPath, { force: true }).catch(() => undefined);
  }
  return markerDocument;
}

async function prepareStartupPackage() {
  const sourceVersion = (await readFile(resolve(repoRoot, "VERSION"), "utf8")).replace(/^\uFEFF/, "").trim();
  const manifestPath = resolve(repoRoot, "dist", "release", "release-manifest.json");
  const manifest = JSON.parse((await readFile(manifestPath, "utf8")).replace(/^\uFEFF/, ""));
  const buildPolicy = normalizeBuildPolicy(manifest);
  const escapedRepoRoot = escapePowerShellLiteral(repoRoot);
  const headCommit = (await runPowerShell(`git -C '${escapedRepoRoot}' rev-parse HEAD`)).trim().toLowerCase();
  const originMainCommit = (await runPowerShell(`git -C '${escapedRepoRoot}' rev-parse origin/main`)).trim().toLowerCase();
  const worktreeClean = (await runPowerShell(`git -C '${escapedRepoRoot}' status --porcelain=v1`)) === "";
  const manifestCommit = String(manifest?.commit || "").trim().toLowerCase();
  if (String(manifest?.version || "") !== sourceVersion || manifestCommit !== headCommit || !worktreeClean) {
    throw new Error("Startup probe requires a clean worktree and a release manifest bound to the exact source version and HEAD.");
  }
  if (allowUnpushed) {
    if (!localAcceptanceBuildPolicy(buildPolicy)) {
      throw new Error("--allow-unpushed requires a local-acceptance manifest with releaseEligible=false, allowDirty=false, and allowUnpushed=true.");
    }
  } else if (headCommit !== originMainCommit || !strictBuildPolicy(buildPolicy)) {
    throw new Error("Strict startup evidence requires HEAD=origin/main and a strict release-eligible manifest.");
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
    throw new Error("Startup probe portable ZIP did not match release-manifest.json.");
  }
  await mkdir(runRoot, { recursive: true });
  await runPowerShell(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destination = '${escapePowerShellLiteral(packagedRoot)}'
    if (Test-Path -LiteralPath $destination) { throw 'Isolated startup package root already exists.' }
    [IO.Compression.ZipFile]::ExtractToDirectory('${escapePowerShellLiteral(portablePath)}', $destination)
  `);
  const embeddedVersion = (await readFile(resolve(packagedRoot, "VERSION"), "utf8")).replace(/^\uFEFF/, "").trim();
  if (embeddedVersion !== sourceVersion) {
    throw new Error("Extracted startup package VERSION did not match the source version.");
  }
  return {
    version: sourceVersion,
    manifestCommit,
    headCommit,
    originMainCommit,
    buildPolicy,
    strictReleaseBinding: !allowUnpushed && headCommit === originMainCommit && strictBuildPolicy(buildPolicy),
    portableName,
    portableSha256,
  };
}

function inheritedEnvironmentIsSensitive(key) {
  const upper = String(key).toUpperCase();
  return ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"].includes(upper)
    || /(?:API[_-]?KEY|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|CLIENT[_-]?SECRET|PASSWORD|BEARER[_-]?TOKEN)$/.test(upper)
    || /^(?:OPENAI|ANTHROPIC|GOOGLE|GEMINI|DEEPSEEK|OPENROUTER|XAI|OLLAMA|AZURE_OPENAI|AWS|VERTEX|BEDROCK)_/.test(upper)
    || upper === "GOOGLE_APPLICATION_CREDENTIALS"
    || upper === "LLM_API_KEY";
}

function startupLaunchEnvironment() {
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => (
      !key.toUpperCase().startsWith("VRCFORGE_")
      && !inheritedEnvironmentIsSensitive(key)
    )),
  );
  return {
    ...inherited,
    VRCFORGE_USER_DATA_DIR: userDataRoot,
    VRCFORGE_CONFIG_DIR: configRoot,
    VRCFORGE_CONFIG_PATH: resolve(configRoot, "config.json"),
    VRCFORGE_SETTINGS_PATH: resolve(configRoot, "settings.json"),
    VRCFORGE_LOG_DIR: resolve(userDataRoot, "logs"),
    VRCFORGE_ARTIFACTS_DIR: resolve(userDataRoot, "artifacts"),
    APPDATA: hostProfileRoot,
    LOCALAPPDATA: hostProfileRoot,
    WEBVIEW2_USER_DATA_FOLDER: webviewDataRoot,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:
      `--remote-debugging-port=${port} --remote-allow-origins=*`,
  };
}

async function processSnapshot() {
  return runPowerShell(`
    $root = [IO.Path]::GetFullPath('${packagedRootPowerShell}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $processes = @(foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { continue }
      if ($path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        [pscustomobject]@{ Id=$process.Id; ProcessName=$process.ProcessName; Path=$path }
      }
    })
    $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object { $_.LocalPort -eq 8757 -or $_.LocalPort -eq ${port} } |
      Select-Object LocalAddress,LocalPort,State,OwningProcess
    [pscustomobject]@{ processes = @($processes); ports = @($ports) } | ConvertTo-Json -Depth 4 -Compress
  `).then((value) => (value ? JSON.parse(value) : { processes: [], ports: [] }));
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
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort ${port} -ErrorAction SilentlyContinue)
    if ($listeners.Count -lt 1) { 'false'; exit 0 }
    foreach ($listener in $listeners) {
      if (-not $ids.Contains([int]$listener.OwningProcess)) { 'false'; exit 0 }
    }
    'true'
  `);
  return value.trim().toLowerCase() === "true";
}

async function waitForOwnedCdpListener(identity, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  do {
    try {
      if (await listenerOwnedByLaunch(identity)) return true;
    } catch {
      // A newly-created WebView process may not be visible to CIM yet.
    }
    await sleep(50);
  } while (Date.now() < deadline);
  return false;
}

async function nativeWindowSnapshot(identity) {
  if (!identity?.id || !identity?.startedAtUtc) return { identityMatched: false, visible: false, handle: 0 };
  const value = await runPowerShell(`
    Add-Type @'
      using System;
      using System.Runtime.InteropServices;
      public static class VRCForgeStartupWindow {
        [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
      }
'@
    $process = Get-Process -Id ${Number(identity.id)} -ErrorAction SilentlyContinue
    if (-not $process) {
      [pscustomobject]@{ identityMatched=$false; visible=$false; handle=0 } | ConvertTo-Json -Compress
      exit 0
    }
    try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { $path = '' }
    $expectedPath = [IO.Path]::GetFullPath('${packagedRootPowerShell}\\VRCForge.exe')
    $expectedStart = [DateTime]::Parse('${String(identity.startedAtUtc).replaceAll("'", "''")}').ToUniversalTime()
    $matched = $path.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase) -and $process.StartTime.ToUniversalTime() -eq $expectedStart
    $handle = if ($matched) { [Int64]$process.MainWindowHandle } else { 0 }
    [pscustomobject]@{
      identityMatched = $matched
      visible = $matched -and $handle -ne 0 -and [VRCForgeStartupWindow]::IsWindowVisible([IntPtr]$handle)
      handle = $handle
    } | ConvertTo-Json -Compress
  `);
  return JSON.parse(value);
}

async function waitForFirstNativeWindowVisible(identity, launchedAt, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let latest = { identityMatched: false, visible: false, handle: 0 };
  while (Date.now() < deadline) {
    try {
      latest = await nativeWindowSnapshot(identity);
    } catch (error) {
      latest = { identityMatched: false, visible: false, handle: 0, error: String(error?.message || error) };
    }
    if (latest.identityMatched === true && latest.visible === true) {
      return { ...latest, visibleAtMs: Date.now() - launchedAt, timedOut: false };
    }
    await sleep(50);
  }
  return { ...latest, visibleAtMs: null, timedOut: true };
}

function nativeVisibilityEvidenceOk(snapshot) {
  return snapshot?.identityMatched === true
    && snapshot?.visible === true
    && Number.isFinite(snapshot?.visibleAtMs)
    && snapshot?.timedOut === false;
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
      const { resolve, reject } = pending.get(payload.id);
      pending.delete(payload.id);
      if (payload.error) {
        reject(new Error(payload.error.message || JSON.stringify(payload.error)));
      } else {
        resolve(payload.result);
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
    await sleep(100);
  }
  throw new Error(`Timed out waiting for expression: ${expression}; last=${JSON.stringify(lastValue)}`);
}

async function readFirstRunUiState(cdp) {
  return evalValue(
    cdp,
    `(() => ({
      languageGate: Boolean(document.querySelector('[data-vrcforge-onboarding-language-gate="true"]')),
      onboarding: Boolean(document.querySelector('[data-vrcforge-onboarding="true"]')),
      centerSurface: Boolean(document.querySelector('[data-chat-composer-dock], [data-empty-chat-content]')),
    }))()`,
  );
}

async function prepareColdProfileForWarm(cdp, initialState) {
  const actions = [];
  if (initialState?.languageGate) {
    await evalValue(
      cdp,
      `(() => {
        const selected = document.querySelector('button[data-vrcforge-onboarding-language-option][aria-pressed="true"]');
        const proceed = document.querySelector('button[data-vrcforge-onboarding-language-continue]');
        if (!selected || !proceed) return { ok: false };
        proceed.click();
        return { ok: true };
      })()`,
    );
    await waitForEval(cdp, `(() => ({ ok: !document.querySelector('[data-vrcforge-onboarding-language-gate="true"]') }))()`);
    actions.push("language-continue");
  }
  const afterLanguage = await readFirstRunUiState(cdp);
  if (afterLanguage?.onboarding) {
    await evalValue(
      cdp,
      `(() => {
        const skip = document.querySelector('button[data-vrcforge-onboarding-skip]');
        if (!skip) return { ok: false };
        skip.click();
        return { ok: true };
      })()`,
    );
    await waitForEval(cdp, `(() => ({ ok: !document.querySelector('[data-vrcforge-onboarding="true"]') }))()`);
    actions.push("onboarding-skip");
  }
  const finalState = await readFirstRunUiState(cdp);
  return {
    actions,
    finalState,
    ok: finalState?.languageGate === false && finalState?.onboarding === false,
  };
}

function summarizeNetwork(events) {
  const requests = new Map();
  for (const event of events) {
    const params = event.params || {};
    const id = params.requestId;
    if (!id) {
      continue;
    }
    const entry = requests.get(id) || { id };
    if (event.method === "Network.requestWillBeSent") {
      entry.url = params.request?.url;
      entry.method = params.request?.method;
      entry.startTs = params.timestamp;
      entry.wallTime = params.wallTime;
    } else if (event.method === "Network.responseReceived") {
      entry.status = params.response?.status;
      entry.responseTs = params.timestamp;
    } else if (event.method === "Network.loadingFinished") {
      entry.endTs = params.timestamp;
      entry.encodedDataLength = params.encodedDataLength;
    } else if (event.method === "Network.loadingFailed") {
      entry.failedTs = params.timestamp;
      entry.errorText = params.errorText;
    }
    requests.set(id, entry);
  }
  return [...requests.values()]
    .filter((entry) => entry.url)
    .map((entry) => ({
      url: entry.url,
      method: entry.method,
      status: entry.status,
      durationMs: entry.endTs && entry.startTs ? Math.round((entry.endTs - entry.startTs) * 1000) : null,
      responseMs: entry.responseTs && entry.startTs ? Math.round((entry.responseTs - entry.startTs) * 1000) : null,
      errorText: entry.errorText,
    }));
}

const STARTUP_SHELL_BUDGET_MS = 100;
const BACKEND_INVOKE_BUDGET_MS = 100;
const CACHED_BOOTSTRAP_BUDGET_MS = 100;

function evaluateStartupBudget(snapshot, sample = "warm") {
  const metrics = snapshot?.vrcforge || {};
  const checks = {
    startupShellRecorded: Number.isFinite(metrics.startupShellPaintedMs),
    startupShellPainted: Number.isFinite(metrics.startupShellPaintedMs)
      && metrics.startupShellPaintedMs <= STARTUP_SHELL_BUDGET_MS,
    backendInvoke: Number.isFinite(metrics.startBackendInvokeMs)
      && metrics.startBackendInvokeMs <= BACKEND_INVOKE_BUDGET_MS,
    cachedBootstrap: Number.isFinite(metrics.bootstrapRefreshMs)
      && metrics.bootstrapRefreshMs <= CACHED_BOOTSTRAP_BUDGET_MS,
    startupBeforeCenter: Number.isFinite(metrics.startupShellPaintedMs)
      && Number.isFinite(metrics.shellPaintedMs)
      && metrics.startupShellPaintedMs <= metrics.shellPaintedMs,
    sidebarMountsRecorded: Number.isFinite(metrics.leftSidebarMountedMs)
      && Number.isFinite(metrics.rightSidebarMountedMs),
    centerBeforeSidebars: Number.isFinite(metrics.centerUsableMs)
      && Number.isFinite(metrics.sidebarsRequestedMs)
      && Number.isFinite(metrics.sidebarsMountedMs)
      && metrics.centerUsableMs <= metrics.sidebarsRequestedMs
      && metrics.sidebarsRequestedMs <= metrics.sidebarsMountedMs,
    sidebarsHydrated: Number.isFinite(metrics.sidebarsHydratedMs)
      && Number.isFinite(metrics.sidebarsMountedMs)
      && metrics.sidebarsMountedMs <= metrics.sidebarsHydratedMs,
    firstContentfulPaintRecorded: Array.isArray(snapshot?.paint)
      && snapshot.paint.some((entry) => entry?.name === "first-contentful-paint" && Number.isFinite(entry.startTime)),
  };
  const requiredCheckNames = sample === "cold"
    ? [
        "startupShellRecorded",
        "backendInvoke",
        "startupBeforeCenter",
        "sidebarMountsRecorded",
        "centerBeforeSidebars",
        "sidebarsHydrated",
        "firstContentfulPaintRecorded",
      ]
    : Object.keys(checks);
  return {
    ok: requiredCheckNames.every((name) => checks[name] === true),
    sample,
    requiredCheckNames,
    budgetsMs: {
      startupShellPainted: STARTUP_SHELL_BUDGET_MS,
      backendInvoke: BACKEND_INVOKE_BUDGET_MS,
      cachedBootstrap: CACHED_BOOTSTRAP_BUDGET_MS,
    },
    checks,
    coldBackendReadyEventMs: Number.isFinite(metrics.backendReadyEventMs) ? metrics.backendReadyEventMs : null,
  };
}

function runSelfTest() {
  const passing = {
    vrcforge: {
      startupShellPaintedMs: 50,
      shellPaintedMs: 70,
      centerUsableMs: 75,
      sidebarsRequestedMs: 80,
      leftSidebarMountedMs: 85,
      rightSidebarMountedMs: 86,
      sidebarsMountedMs: 90,
      sidebarsHydratedMs: 95,
      startBackendInvokeMs: 40,
      bootstrapRefreshMs: 45,
      backendReadyEventMs: 800,
    },
    paint: [{ name: "first-contentful-paint", startTime: 18 }],
  };
  if (!evaluateStartupBudget(passing).ok) {
    throw new Error("self-test: valid startup timing evidence was rejected.");
  }
  if (evaluateStartupBudget({ ...passing, vrcforge: { ...passing.vrcforge, bootstrapRefreshMs: 101 } }).ok) {
    throw new Error("self-test: an over-budget bootstrap was accepted.");
  }
  if (evaluateStartupBudget({ ...passing, vrcforge: { ...passing.vrcforge, sidebarsHydratedMs: 89 } }).ok) {
    throw new Error("self-test: sidebar hydration ordering drift was accepted.");
  }
  if (evaluateStartupBudget({ ...passing, paint: [] }).ok) {
    throw new Error("self-test: missing first-contentful-paint evidence was accepted.");
  }
  const coldCalibration = evaluateStartupBudget({
    ...passing,
    vrcforge: {
      ...passing.vrcforge,
      startupShellPaintedMs: 108,
      shellPaintedMs: 130,
      centerUsableMs: 135,
      sidebarsRequestedMs: 140,
      leftSidebarMountedMs: 145,
      rightSidebarMountedMs: 146,
      sidebarsMountedMs: 150,
      sidebarsHydratedMs: 1850,
      bootstrapRefreshMs: 1850,
    },
  }, "cold");
  if (!coldCalibration.ok || coldCalibration.checks.startupShellPainted !== false || coldCalibration.checks.cachedBootstrap !== false) {
    throw new Error("self-test: cold calibration incorrectly required warm-only latency budgets.");
  }
  const fakeBinding = { manifestCommit: "a".repeat(40), portableSha256: "b".repeat(64) };
  const pairMarker = expectedStartupPairMarker(fakeBinding);
  if (!startupPairMarkerMatches(pairMarker, fakeBinding)) {
    throw new Error("self-test: exact cold-to-warm profile binding was rejected.");
  }
  if (startupPairMarkerMatches({ ...pairMarker, portableSha256: "c".repeat(64) }, fakeBinding)) {
    throw new Error("self-test: a warm profile from a different portable ZIP was accepted.");
  }
  if (!nativeVisibilityEvidenceOk({ identityMatched: true, visible: true, visibleAtMs: 42, timedOut: false })) {
    throw new Error("self-test: valid first-native-window visibility evidence was rejected.");
  }
  if (nativeVisibilityEvidenceOk({ identityMatched: true, visible: true, visibleAtMs: null, timedOut: true })) {
    throw new Error("self-test: late native-window visibility without a timestamp was accepted.");
  }
  for (const key of ["OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN", "HTTPS_PROXY", "VRCFORGE_CONFIG_PATH"]) {
    const excluded = key.toUpperCase().startsWith("VRCFORGE_") || inheritedEnvironmentIsSensitive(key);
    if (!excluded) {
      throw new Error(`self-test: sensitive environment classifier missed ${key}.`);
    }
  }
  console.log("Packaged startup latency probe self-test passed");
}

let trackedChild = null;
let trackedLaunchIdentity = null;
let activeCdp = null;
let gracefulQuitAttempted = false;

async function main() {
  await mkdir(dirname(outPath), { recursive: true });
  const releaseBinding = startupOnly ? await prepareStartupPackage() : null;
  const profileExistedBefore = startupOnly
    ? (await runPowerShell(`if (Test-Path -LiteralPath '${escapePowerShellLiteral(profileRoot)}') { 'true' } else { 'false' }`)) === "true"
    : null;
  let startupPairMarker = null;
  if (startupOnly) {
    if (startupSample === "cold" && profileExistedBefore) {
      throw new Error("Cold startup evidence requires a profile root that does not exist yet.");
    }
    if (startupSample === "warm" && !profileExistedBefore) {
      throw new Error("Warm startup evidence requires the exact profile root created by the cold run.");
    }
    if (startupSample === "warm") {
      startupPairMarker = await requireWarmStartupPairMarker(releaseBinding);
    }
    await Promise.all([
      mkdir(configRoot, { recursive: true }),
      mkdir(resolve(userDataRoot, "logs"), { recursive: true }),
      mkdir(resolve(userDataRoot, "artifacts"), { recursive: true }),
      mkdir(hostProfileRoot, { recursive: true }),
      mkdir(webviewDataRoot, { recursive: true }),
    ]);
  }
  const beforeLaunch = await assertProbePreflightClear();
  const launchedAt = Date.now();
  const child = spawn(exe, [], {
    detached: startupOnly ? false : !closeOnComplete,
    stdio: "ignore",
    env: startupOnly
      ? startupLaunchEnvironment()
      : {
          ...process.env,
          WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --remote-allow-origins=*`,
        },
  });
  trackedChild = child;
  trackedLaunchIdentity = await captureLaunchIdentity(child.pid);
  const firstNativeWindowVisible = startupOnly
    ? waitForFirstNativeWindowVisible(trackedLaunchIdentity, launchedAt)
    : null;
  if (!startupOnly && !closeOnComplete) {
    child.unref();
  }
  const page = await waitForCdpTarget();
  if (!(await waitForOwnedCdpListener(trackedLaunchIdentity))) {
    throw new Error("Packaged probe CDP listener was not owned by the captured launch generation.");
  }
  const cdp = connectCdp(page.webSocketDebuggerUrl);
  activeCdp = cdp;
  await cdp.opened;
  await cdp.send("Runtime.enable");
  await cdp.send("Page.enable");
  await cdp.send("Network.enable");
  await cdp.send("Performance.enable");

  const attachedAt = Date.now();
  await waitForEval(cdp, "document.readyState === 'complete' || document.readyState === 'interactive'");
  await evalValue(
    cdp,
    `(() => {
      const probe = window.__vrcLatencyProbe = {
        installedAt: performance.now(),
        fetches: [],
        longTasks: [],
        marks: [],
      };
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const input = args[0];
        const url = typeof input === "string" ? input : (input && input.url) || String(input);
        const method = (args[1] && args[1].method) || (input && input.method) || "GET";
        const start = performance.now();
        const row = { url, method, start };
        probe.fetches.push(row);
        try {
          const response = await originalFetch(...args);
          row.status = response.status;
          row.end = performance.now();
          row.duration = row.end - start;
          if (url.includes("send_agent_message")) {
            row.responsePreview = await response
              .clone()
              .text()
              .then((text) => text.slice(0, 2000))
              .catch((error) => "response preview failed: " + String(error && error.message || error));
          }
          return response;
        } catch (error) {
          row.error = String(error && error.message || error);
          row.end = performance.now();
          row.duration = row.end - start;
          throw error;
        }
      };
      if ("PerformanceObserver" in window) {
        try {
          const observer = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
              probe.longTasks.push({ name: entry.name, start: entry.startTime, duration: entry.duration });
            }
          });
          observer.observe({ entryTypes: ["longtask"] });
          probe.longTaskObserver = true;
        } catch (error) {
          probe.longTaskObserverError = String(error && error.message || error);
        }
      }
      return true;
    })()`,
  );

  const readyProbe = await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      return { ok: Boolean(textarea && submit), readyState: document.readyState, bodyLength: document.body.innerText.length };
    })()`,
    30000,
  );

  await waitForEval(
    cdp,
    `(() => {
      const metrics = window.__vrcforgeStartupMetrics || {};
      return { ok: [
        metrics.startupShellPaintedMs,
        metrics.shellPaintedMs,
        metrics.centerUsableMs,
        metrics.sidebarsRequestedMs,
        metrics.sidebarsMountedMs,
        metrics.sidebarsHydratedMs,
        metrics.startBackendInvokeMs,
        metrics.bootstrapRefreshMs,
      ].every(Number.isFinite) };
    })()`,
    30000,
  );

  const startupMetrics = await evalValue(
    cdp,
    `(() => ({
      readyState: document.readyState,
      bodyLength: document.body.innerText.length,
      textTail: document.body.innerText.slice(-500),
      perfNow: performance.now(),
      timeOrigin: performance.timeOrigin,
      vrcforge: window.__vrcforgeStartupMetrics || {},
      shellReady: document.documentElement.dataset.vrcforgeShell || "",
      centerReady: document.documentElement.dataset.vrcforgeCenter || "",
      sidebarsReady: document.documentElement.dataset.vrcforgeSidebars || "",
      navigation: performance.getEntriesByType("navigation").map((entry) => ({
        startTime: entry.startTime,
        domInteractive: entry.domInteractive,
        domContentLoadedEventEnd: entry.domContentLoadedEventEnd,
        loadEventEnd: entry.loadEventEnd,
        duration: entry.duration,
      })),
      paint: performance.getEntriesByType("paint").map((entry) => ({
        name: entry.name,
        startTime: entry.startTime,
        duration: entry.duration,
      })),
    }))()`,
  );

  if (startupOnly) {
    const startupBudget = evaluateStartupBudget(startupMetrics, startupSample);
    const nativeWindow = await firstNativeWindowVisible;
    const firstRunUiState = await readFirstRunUiState(cdp);
    const profilePreparation = startupSample === "cold"
      ? await prepareColdProfileForWarm(cdp, firstRunUiState)
      : {
          actions: [],
          finalState: firstRunUiState,
          ok: firstRunUiState?.languageGate === false && firstRunUiState?.onboarding === false,
        };
    const centerEvidenceKind = firstRunUiState?.languageGate || firstRunUiState?.onboarding
      ? "first-run-center-surface-under-onboarding"
      : "interactive-center-surface";
    const providerRequests = summarizeNetwork(cdp.events).filter((entry) =>
      /(?:chat\/completions|\/v1\/responses|generativelanguage|anthropic\.com|openrouter\.ai)/i.test(entry.url || ""),
    );
    gracefulQuitAttempted = true;
    const listenerOwned = await listenerOwnedByLaunch(trackedLaunchIdentity).catch(() => false);
    const quitRequest = listenerOwned
      ? await requestPackagedAppQuit(cdp)
      : { accepted: false, listenerOwnershipChanged: true, error: "Tracked packaged CDP listener changed owner; no Quit was attempted." };
    cdp.close();
    activeCdp = null;
    const afterQuit = await waitForAppShutdown(20000);
    const lifecycle = {
      mode: "explicit-quit",
      quitRequest,
      afterQuit,
      forcedCleanupUsed: false,
      ok: Boolean(quitRequest.accepted && !snapshotHasResidue(afterQuit)),
    };
    if (!lifecycle.ok && snapshotHasResidue(afterQuit)) {
      lifecycle.forcedCleanupUsed = true;
      lifecycle.afterForcedCleanup = await forceCloseTrackedLaunch(trackedLaunchIdentity);
    }
    const output = {
      schema: "vrcforge.packaged_startup_probe.v1",
      marker,
      mode: "startup-only",
      sample: startupSample,
      profileRoot,
      profileExistedBefore,
      startupPairMarker: startupSample === "cold"
        ? expectedStartupPairMarker(releaseBinding)
        : startupPairMarker,
      releaseBinding,
      launchedAt,
      attachedAt,
      attachMs: attachedAt - launchedAt,
      readyProbe,
      startupMetrics,
      startupBudget,
      nativeWindow,
      firstRunUiState,
      centerEvidenceKind,
      profilePreparation,
      providerRequests,
      providerRequestCount: providerRequests.length,
      lifecycle,
      ok: startupBudget.ok
        && nativeVisibilityEvidenceOk(nativeWindow)
        && profilePreparation.ok === true
        && providerRequests.length === 0
        && lifecycle.ok
        && lifecycle.forcedCleanupUsed === false,
    };
    await writeFile(outPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
    if (output.ok && startupSample === "cold") {
      await writeColdStartupPairMarker(releaseBinding);
    }
    console.log(outPath);
    if (!output.ok) {
      console.error(`Packaged startup-only evidence failed: ${JSON.stringify({ startupBudget, nativeWindow, providerRequests, lifecycle })}`);
      process.exitCode = 1;
    }
    return;
  }

  const inputText = `Do not use tools. Reply in one short sentence and include this exact token: ${marker}`;
  const composerReady = await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      return {
        ok: Boolean(textarea && submit && !textarea.disabled),
        textareaDisabled: Boolean(textarea && textarea.disabled),
        submitDisabled: Boolean(submit && submit.disabled),
        bodyLength: document.body.innerText.length,
        tail: document.body.innerText.slice(-500),
      };
    })()`,
    30000,
  );
  const inputResult = await evalValue(
    cdp,
    `(async () => {
      const textarea = document.querySelector("textarea");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      const start = performance.now();
      textarea.focus();
      setter.call(textarea, ${JSON.stringify(inputText)});
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolve) => requestAnimationFrame(resolve));
      return {
        duration: performance.now() - start,
        valueLength: textarea.value.length,
        disabled: textarea.disabled,
        activeTag: document.activeElement && document.activeElement.tagName,
      };
    })()`,
  );
  const submitReady = await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      return {
        ok: Boolean(textarea && submit && !textarea.disabled && !submit.disabled && textarea.value.includes(${JSON.stringify(marker)})),
        textareaDisabled: Boolean(textarea && textarea.disabled),
        submitDisabled: Boolean(submit && submit.disabled),
        valueLength: textarea ? textarea.value.length : null,
      };
    })()`,
    5000,
  );

  const clickResult = await evalValue(
    cdp,
    `(async () => {
      const submit = document.querySelector("button[type='submit']");
      const start = performance.now();
      submit.click();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      return {
        duration: performance.now() - start,
        disabledAfterFrame: submit.disabled,
        bodyLength: document.body.innerText.length,
      };
    })()`,
  );

  const samples = [];
  const sampleStartedAt = Date.now();
  const sampleDeadline = sampleStartedAt + maxWaitMs;
  let completed = false;
  let stableCompleteSamples = 0;
  while (Date.now() < sampleDeadline) {
    await sleep(250);
    samples.push(
      await evalValue(
        cdp,
        `(() => {
          const start = performance.now();
          const textarea = document.querySelector("textarea");
          const submit = document.querySelector("button[type='submit']");
          const body = document.body.innerText;
          return {
            at: performance.now(),
            evalCost: performance.now() - start,
            bodyLength: body.length,
            markerCount: (body.match(new RegExp(${JSON.stringify(marker)}, "g")) || []).length,
            runningText: /执行中|等待模型响应|思考中|running|thinking/i.test(body),
            textareaDisabled: Boolean(textarea && textarea.disabled),
            submitDisabled: Boolean(submit && submit.disabled),
            textareaValueLength: textarea ? textarea.value.length : null,
            runningText: /\u6267\u884c\u4e2d|\u7b49\u5f85\u6a21\u578b\u54cd\u5e94|\u601d\u8003\u4e2d|\u8fd0\u884c\u4e2d|running|thinking/i.test(body),
          };
        })()`,
      ),
    );
    const latest = samples.at(-1);
    if (latest.markerCount > 1 && !latest.runningText) {
      stableCompleteSamples += 1;
    } else {
      stableCompleteSamples = 0;
    }
    if (stableCompleteSamples >= 4) {
      completed = true;
      break;
    }
  }

  const finalProbe = await evalValue(
    cdp,
    `(() => ({
      probe: window.__vrcLatencyProbe,
      bodyLength: document.body.innerText.length,
      markerCount: (document.body.innerText.match(new RegExp(${JSON.stringify(marker)}, "g")) || []).length,
      markerSnippets: (() => {
        const body = document.body.innerText;
        const snippets = [];
        let index = -1;
        while ((index = body.indexOf(${JSON.stringify(marker)}, index + 1)) >= 0) {
          snippets.push(body.slice(Math.max(0, index - 180), Math.min(body.length, index + 240)));
        }
        return snippets;
      })(),
      tail: document.body.innerText.slice(-1200),
    }))()`,
  );
  const perfMetrics = await cdp.send("Performance.getMetrics");
  const network = summarizeNetwork(cdp.events).filter((entry) =>
    /ipc\.localhost|tauri\.localhost|127\.0\.0\.1|localhost/.test(entry.url || ""),
  );
  const startupBudget = evaluateStartupBudget(startupMetrics);
  const output = {
    schema: "vrcforge.packaged_latency_probe.v1",
    marker,
    exe,
    port,
    beforeLaunch,
    launchedAt,
    attachedAt,
    attachMs: attachedAt - launchedAt,
    readyProbe,
    startupMetrics,
    startupBudget,
    composerReady,
    inputResult,
    submitReady,
    clickResult,
    completed,
    timedOut: !completed,
    waitMs: Date.now() - sampleStartedAt,
    maxWaitMs,
    samples,
    finalProbe,
    network,
    performanceMetrics: perfMetrics.metrics,
    childPid: child.pid,
    closeOnComplete,
  };
  if (completed && closeOnComplete) {
    gracefulQuitAttempted = true;
    const listenerOwned = await listenerOwnedByLaunch(trackedLaunchIdentity).catch(() => false);
    const quitRequest = listenerOwned
      ? await requestPackagedAppQuit(cdp)
      : { accepted: false, listenerOwnershipChanged: true, error: "Tracked packaged CDP listener changed owner; no Quit was attempted." };
    cdp.close();
    activeCdp = null;
    const afterQuit = await waitForAppShutdown(20000);
    output.lifecycle = {
      mode: "explicit-quit",
      quitRequest,
      afterQuit,
      forcedCleanupUsed: false,
      ok: Boolean(quitRequest.accepted && !snapshotHasResidue(afterQuit)),
    };
    if (!output.lifecycle.ok && snapshotHasResidue(afterQuit)) {
      output.lifecycle.forcedCleanupUsed = true;
      output.lifecycle.afterForcedCleanup = await forceCloseTrackedLaunch(trackedLaunchIdentity);
    }
  } else {
    cdp.close();
    activeCdp = null;
    output.lifecycle = {
      mode: completed ? "preserved-for-manual-inspection" : "preserved-after-incomplete-probe-for-manual-inspection",
      quitRequest: null,
      afterQuit: null,
      forcedCleanupUsed: false,
      ok: true,
    };
    trackedChild = null;
    trackedLaunchIdentity = null;
  }
  await writeFile(outPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  console.log(outPath);
  if (!startupBudget.ok) {
    console.error(`Packaged startup budget failed: ${JSON.stringify(startupBudget)}`);
    process.exitCode = 1;
  }
  if (completed && closeOnComplete && !output.lifecycle.ok) {
    console.error(`Packaged app explicit Quit failed; forced cleanup cannot count as success: ${JSON.stringify(output.lifecycle)}`);
    process.exitCode = 1;
  }
}

if (selfTest) {
  runSelfTest();
} else {
  main().catch(async (error) => {
  const cleanup = {
    quitRequest: { accepted: false, error: "CDP was unavailable before cleanup." },
    afterQuit: null,
    forcedCleanupUsed: false,
    afterForcedCleanup: null,
  };
  if (!gracefulQuitAttempted && activeCdp) {
    gracefulQuitAttempted = true;
    const listenerOwned = await listenerOwnedByLaunch(trackedLaunchIdentity).catch(() => false);
    cleanup.quitRequest = listenerOwned
      ? await requestPackagedAppQuit(activeCdp).catch((quitError) => ({ accepted: false, error: String(quitError) }))
      : { accepted: false, listenerOwnershipChanged: true, error: "Tracked packaged CDP listener changed owner; no Quit was attempted." };
  }
  if (activeCdp) {
    activeCdp.close();
    activeCdp = null;
  }
  cleanup.afterQuit = trackedChild?.pid
    ? await waitForAppShutdown(20000).catch((shutdownError) => ({ error: String(shutdownError), processes: [], ports: [] }))
    : await processSnapshot().catch((snapshotError) => ({ error: String(snapshotError), processes: [], ports: [] }));
  if (trackedChild?.pid && snapshotHasResidue(cleanup.afterQuit)) {
    cleanup.forcedCleanupUsed = true;
    if (trackedLaunchIdentity) {
      cleanup.afterForcedCleanup = await forceCloseTrackedLaunch(trackedLaunchIdentity).catch((cleanupError) => ({ error: String(cleanupError) }));
    } else {
      cleanup.identityCaptureFailed = true;
      cleanup.unverifiedProcessPreserved = true;
      cleanup.afterForcedCleanup = await waitForAppShutdown(20000).catch((cleanupError) => ({ error: String(cleanupError) }));
    }
  }
  await mkdir(dirname(outPath), { recursive: true });
  await writeFile(outPath, `${JSON.stringify({
    ok: false,
    error: String(error && error.stack || error),
    marker,
    lifecycle: cleanup,
  }, null, 2)}\n`, "utf8").catch(() => {});
  console.error(error);
    process.exit(1);
  });
}
