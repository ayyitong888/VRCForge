import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createConnection } from "node:net";
import { dirname, resolve } from "node:path";
import { requestPackagedAppQuit } from "./lib/packaged_app_lifecycle.mjs";

const repoRoot = resolve(import.meta.dirname, "..");
const allowUnpushed = process.argv.includes("--allow-unpushed");
const selfTest = process.argv.includes("--self-test");
const port = Number(process.env.VRCFORGE_DESKTOP_PROBE_CDP_PORT || "9343");
const marker = `DB_PROBE_${Date.now()}`;
const evidenceRoot = resolve(repoRoot, "artifacts", "actual-app-desktop-bridge", marker);
const packagedRoot = resolve(evidenceRoot, "package");
const exe = resolve(packagedRoot, "VRCForge.exe");
const probeSessionId = `${marker}_SESSION`;
const outPath = resolve(evidenceRoot, "report.json");
const userDataRoot = resolve(evidenceRoot, "user-data");
const configRoot = resolve(userDataRoot, "config");
const webviewDataRoot = resolve(evidenceRoot, "webview2-user-data");
const hostProfileRoot = resolve(evidenceRoot, "host-profile");
const fixtureSourcePath = resolve(repoRoot, "scripts", "desktop_executor_fixture.cs");
const fixtureExePath = resolve(evidenceRoot, `fixture-${marker}.exe`);
const fixtureTypedMarker = `${marker}_TYPED_VALUE`;
const uiaFixtureTypedMarker = `${marker}_UIA_VALUE`;
const appOrigin = "http://127.0.0.1:8757";
const appRequestOrigin = "tauri://localhost";
let appSessionToken = "";
const protectedSecrets = new Set();

const allowedOptions = new Set(["--allow-unpushed", "--self-test", "--help", "-h"]);
if (process.argv.slice(2).some((item) => !allowedOptions.has(item))) {
  console.error("Unknown packaged Desktop/Computer Use probe option.");
  process.exit(2);
}

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`Usage: node scripts/diagnose_packaged_desktop_bridge.mjs [--allow-unpushed] [--self-test]

Runs the packaged Desktop/Computer Use bridge, UI, action, privacy, cancellation,
and lifecycle matrix. Default mode requires strict release evidence.
--allow-unpushed remains non-release local-preacceptance only and still requires
a clean worktree, manifest commit == HEAD, VERSION and manifest-bound ZIP/main/
backend hashes, plus the exact local-acceptance release-ineligible build policy.

The runtime is isolated to evidence-owned user-data, config, logs, artifacts,
AppData and WebView2 roots. Provider credentials and proxy variables are not
inherited. The packaged process, embedded worker and external fixture processes
have bounded probe-owned lifetimes; only tracked processes are stopped.

This matrix intentionally performs real desktop input against probe-launched
Notepad and the compiled fixture. Do not run it on an interactive desktop unless
that foreground-input acceptance is intended. --self-test performs no package,
process, port, window, mouse or keyboard operation.

Optional environment:
  VRCFORGE_DESKTOP_PROBE_CDP_PORT=<unused port> (default: ${port})`);
  process.exit(0);
}


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

async function processIdentity(processId) {
  if (!Number.isInteger(Number(processId)) || Number(processId) <= 0) return null;
  const raw = await runPowerShell(`
    $process = Get-Process -Id ${Number(processId)} -ErrorAction SilentlyContinue
    if (-not $process) { '' ; exit 0 }
    try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { $path = '' }
    try { $started = $process.StartTime.ToUniversalTime().ToString('o') } catch { $started = '' }
    [pscustomobject]@{ id=$process.Id; name=$process.ProcessName; path=$path; startedAt=$started } |
      ConvertTo-Json -Compress
  `);
  return raw ? JSON.parse(raw) : null;
}

async function listenerOwnedByLaunch(identity) {
  if (!identity?.id || !identity?.startedAt) return false;
  const raw = await runPowerShell(`
    $rootProcessId = [int]${Number(identity.id)}
    $rootProcess = Get-Process -Id $rootProcessId -ErrorAction SilentlyContinue
    if (-not $rootProcess) { 'false'; exit 0 }
    try { $rootPath = [IO.Path]::GetFullPath([string]$rootProcess.Path) } catch { 'false'; exit 0 }
    $expectedPath = [IO.Path]::GetFullPath('${escapePowerShellLiteral(exe)}')
    $expectedStart = [DateTime]::Parse('${escapePowerShellLiteral(String(identity.startedAt))}').ToUniversalTime()
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
  return raw.trim().toLowerCase() === "true";
}

async function stopTrackedExternalProcess(identity) {
  const processId = Number(identity?.id || 0);
  const expectedPath = String(identity?.path || "");
  const expectedStartedAt = String(identity?.startedAt || "");
  if (!Number.isInteger(processId) || processId <= 0 || !expectedPath || !expectedStartedAt) {
    return { ok: false, stopped: false, reason: "missing tracked process identity" };
  }
  const raw = await runPowerShell(`
    $process = Get-Process -Id ${processId} -ErrorAction SilentlyContinue
    if (-not $process) {
      [pscustomobject]@{ ok=$true; stopped=$false; alreadyExited=$true } | ConvertTo-Json -Compress
      exit 0
    }
    try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { $path = '' }
    try { $started = $process.StartTime.ToUniversalTime().ToString('o') } catch { $started = '' }
    $pathMatches = $path.Equals([IO.Path]::GetFullPath('${escapePowerShellLiteral(expectedPath)}'), [StringComparison]::OrdinalIgnoreCase)
    $startMatches = $started.Equals('${escapePowerShellLiteral(expectedStartedAt)}', [StringComparison]::Ordinal)
    if (-not $pathMatches -or -not $startMatches) {
      [pscustomobject]@{ ok=$false; stopped=$false; alreadyExited=$false; pathMatches=$pathMatches; startMatches=$startMatches } |
        ConvertTo-Json -Compress
      exit 0
    }
    $process | Stop-Process -Force -ErrorAction Stop
    [pscustomobject]@{ ok=$true; stopped=$true; alreadyExited=$false; pathMatches=$true; startMatches=$true } |
      ConvertTo-Json -Compress
  `);
  const result = raw ? JSON.parse(raw) : { ok: false, stopped: false, reason: "missing cleanup result" };
  if (result.ok) {
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline && processExists(processId)) await sleep(100);
    if (processExists(processId)) return { ...result, ok: false, reason: "tracked process did not exit" };
  }
  return result;
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

function isStrictBuildPolicy(policy) {
  return policy.mode === "strict"
    && policy.releaseEligible === true
    && policy.allowDirty === false
    && policy.allowUnpushed === false
    && policy.allowVersionMismatch === false;
}

function isLocalAcceptanceBuildPolicy(policy) {
  return policy.mode === "local-acceptance"
    && policy.releaseEligible === false
    && policy.allowDirty === false
    && policy.allowUnpushed === true;
}

function inheritedEnvironmentIsSensitive(key) {
  const upper = String(key).toUpperCase();
  return ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"].includes(upper)
    || /(?:API[_-]?KEY|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|CLIENT[_-]?SECRET|PASSWORD|BEARER[_-]?TOKEN)$/.test(upper)
    || /^(?:OPENAI|ANTHROPIC|GOOGLE|GEMINI|DEEPSEEK|OPENROUTER|XAI|OLLAMA|AZURE_OPENAI|AWS|VERTEX|BEDROCK)_/.test(upper)
    || upper === "GOOGLE_APPLICATION_CREDENTIALS"
    || upper === "LLM_API_KEY";
}

function isolatedLaunchEnvironment() {
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
    VRCFORGE_DESKTOP_EXECUTOR: "1",
    APPDATA: hostProfileRoot,
    LOCALAPPDATA: hostProfileRoot,
    WEBVIEW2_USER_DATA_FOLDER: webviewDataRoot,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:
      `--remote-debugging-port=${port} --remote-allow-origins=*`,
  };
}

function composerIsReadyAfterOnboarding(state) {
  return Boolean(state?.composerReady && !state.languageGate && !state.onboarding);
}

function runSelfTest() {
  const strict = normalizeBuildPolicy({
    buildPolicy: {
      mode: "strict",
      releaseEligible: true,
      allowDirty: false,
      allowUnpushed: false,
      allowVersionMismatch: false,
    },
  });
  const local = normalizeBuildPolicy({
    buildPolicy: {
      mode: "local-acceptance",
      releaseEligible: false,
      allowDirty: false,
      allowUnpushed: true,
      allowVersionMismatch: true,
    },
  });
  if (!isStrictBuildPolicy(strict) || isLocalAcceptanceBuildPolicy(strict)) {
    throw new Error("self-test: strict build policy classification failed.");
  }
  if (!isLocalAcceptanceBuildPolicy(local) || isStrictBuildPolicy(local)) {
    throw new Error("self-test: local-acceptance build policy classification failed.");
  }
  if (
    isLocalAcceptanceBuildPolicy({ ...local, releaseEligible: true })
    || isLocalAcceptanceBuildPolicy({ ...local, allowDirty: true })
    || isLocalAcceptanceBuildPolicy({ ...local, allowUnpushed: false })
    || isLocalAcceptanceBuildPolicy({ ...local, mode: "strict" })
  ) {
    throw new Error("self-test: unsafe local-acceptance policy was accepted.");
  }

  const injectedKeys = [
    "VRCFORGE_DISABLE_APP_AUTH",
    "VRCFORGE_CONFIG_PATH",
    "OPENAI_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "OLLAMA_HOST",
    "HTTPS_PROXY",
    "NO_PROXY",
  ];
  const previous = new Map(injectedKeys.map((key) => [key, process.env[key]]));
  for (const key of injectedKeys) process.env[key] = `must-not-escape-${key}`;
  try {
    const environment = isolatedLaunchEnvironment();
    if (
      Object.hasOwn(environment, "VRCFORGE_DISABLE_APP_AUTH")
      || Object.hasOwn(environment, "OPENAI_API_KEY")
      || Object.hasOwn(environment, "ANTHROPIC_AUTH_TOKEN")
      || Object.hasOwn(environment, "GOOGLE_APPLICATION_CREDENTIALS")
      || Object.hasOwn(environment, "OLLAMA_HOST")
      || Object.hasOwn(environment, "HTTPS_PROXY")
      || Object.hasOwn(environment, "NO_PROXY")
      || environment.VRCFORGE_USER_DATA_DIR !== userDataRoot
      || environment.VRCFORGE_CONFIG_DIR !== configRoot
      || environment.VRCFORGE_CONFIG_PATH !== resolve(configRoot, "config.json")
      || environment.VRCFORGE_SETTINGS_PATH !== resolve(configRoot, "settings.json")
      || environment.VRCFORGE_LOG_DIR !== resolve(userDataRoot, "logs")
      || environment.VRCFORGE_ARTIFACTS_DIR !== resolve(userDataRoot, "artifacts")
      || environment.VRCFORGE_DESKTOP_EXECUTOR !== "1"
      || environment.APPDATA !== hostProfileRoot
      || environment.LOCALAPPDATA !== hostProfileRoot
      || environment.WEBVIEW2_USER_DATA_FOLDER !== webviewDataRoot
    ) {
      throw new Error("self-test: packaged Desktop runtime paths or credentials were not isolated.");
    }
    if (
      composerIsReadyAfterOnboarding({ composerReady: true, languageGate: true, onboarding: false })
      || composerIsReadyAfterOnboarding({ composerReady: true, languageGate: false, onboarding: true })
      || !composerIsReadyAfterOnboarding({ composerReady: true, languageGate: false, onboarding: false })
    ) {
      throw new Error("self-test: composer readiness bypassed an active first-run overlay.");
    }
  } finally {
    for (const [key, value] of previous) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
  const selfTestSecret = "desktop-probe-self-test-secret";
  protectedSecrets.add(selfTestSecret);
  try {
    const sanitized = sanitizeProbeValue({ error: `bounded ${selfTestSecret} value` });
    if (containsProtectedSecret(sanitized) || !JSON.stringify(sanitized).includes("<redacted-probe-secret>")) {
      throw new Error("self-test: protected secret redaction failed.");
    }
  } finally {
    protectedSecrets.delete(selfTestSecret);
  }
  const fakeProviderToken = "desktop-self-test-provider-token";
  const fakeRequest = {
    headers: { authorization: `Bearer ${fakeProviderToken}` },
    body: { messages: [{ role: "user", content: `self-test ${marker}` }] },
  };
  if (
    !fakeProviderRequestIsAuthorized(fakeRequest, fakeProviderToken) ||
    fakeProviderRequestIsAuthorized(fakeRequest, `${fakeProviderToken}-wrong`) ||
    !currentUserTurnContains(fakeRequest, marker)
  ) {
    throw new Error("self-test: fake Provider authentication or marker predicates failed.");
  }
  console.log("Desktop/Computer Use probe self-test passed");
}

if (selfTest) {
  runSelfTest();
}

async function prepareManifestBoundPackage(sourceVersion) {
  const manifestPath = resolve(repoRoot, "dist", "release", "release-manifest.json");
  let manifest;
  try {
    manifest = JSON.parse((await readFile(manifestPath, "utf8")).replace(/^\uFEFF/, ""));
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(`Packaged Desktop/Computer Use probe requires ${manifestPath}.`);
    }
    throw new Error(`Release manifest could not be read: ${String(error?.message || error)}`);
  }
  if (String(manifest?.version || "") !== sourceVersion) {
    throw new Error(`Release manifest version ${String(manifest?.version || "<missing>")} did not match VERSION ${sourceVersion}.`);
  }
  const escapedRepoRoot = escapePowerShellLiteral(repoRoot);
  const headCommit = (await runPowerShell(`git -C '${escapedRepoRoot}' rev-parse HEAD`)).trim().toLowerCase();
  const originMainCommit = (await runPowerShell(`git -C '${escapedRepoRoot}' rev-parse origin/main`)).trim().toLowerCase();
  const worktreeClean = (await runPowerShell(`git -C '${escapedRepoRoot}' status --porcelain=v1`)) === "";
  const manifestCommit = String(manifest?.commit || "").trim().toLowerCase();
  const buildPolicy = normalizeBuildPolicy(manifest);
  const strictBuildPolicy = isStrictBuildPolicy(buildPolicy);
  const localAcceptanceBuildPolicy = isLocalAcceptanceBuildPolicy(buildPolicy);
  if (
    !/^[0-9a-f]{40}$/.test(headCommit)
    || !/^[0-9a-f]{40}$/.test(originMainCommit)
    || manifestCommit !== headCommit
  ) {
    throw new Error(`Manifest binding mismatch: manifest=${manifestCommit || "<missing>"}, HEAD=${headCommit || "<missing>"}, origin/main=${originMainCommit || "<missing>"}.`);
  }
  if (!allowUnpushed && (headCommit !== originMainCommit || !worktreeClean || !strictBuildPolicy)) {
    throw new Error("Strict packaged probe requires clean HEAD=origin/main and a strict release-eligible buildPolicy.");
  }
  if (allowUnpushed && (!worktreeClean || !localAcceptanceBuildPolicy)) {
    throw new Error("--allow-unpushed requires a clean worktree plus buildPolicy.mode=local-acceptance, releaseEligible=false, allowDirty=false, and allowUnpushed=true.");
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

  const archivePayload = JSON.parse(await runPowerShell(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead('${escapePowerShellLiteral(portablePath)}')
    try {
      $entries = @($archive.Entries)
      $main = @($entries | Where-Object {
        $_.FullName.Replace('\\', '/').Equals('VRCForge.exe', [StringComparison]::OrdinalIgnoreCase)
      })
      $backend = @($entries | Where-Object {
        $_.FullName.Replace('\\', '/').Equals('backend/vrcforge_backend.exe', [StringComparison]::OrdinalIgnoreCase)
      })
      if ($main.Count -ne 1) { throw 'Portable package did not contain exactly one VRCForge.exe entry.' }
      if ($backend.Count -ne 1) { throw 'Portable package did not contain exactly one vrcforge_backend.exe entry.' }
      function Get-Digest($entry) {
        $sha = [Security.Cryptography.SHA256]::Create()
        $stream = $entry.Open()
        try { [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose(); $sha.Dispose() }
      }
      [pscustomobject]@{
        innerExeSha256 = Get-Digest $main[0]
        innerBackendSha256 = Get-Digest $backend[0]
      } | ConvertTo-Json -Compress
    } finally {
      $archive.Dispose()
    }
  `));
  const innerExeSha256 = String(archivePayload?.innerExeSha256 || "").toLowerCase();
  const innerBackendSha256 = String(archivePayload?.innerBackendSha256 || "").toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(innerExeSha256) || !/^[0-9a-f]{64}$/.test(innerBackendSha256)) {
    throw new Error("Portable package executable digests were invalid.");
  }

  await runPowerShell(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destination = '${escapePowerShellLiteral(packagedRoot)}'
    if (Test-Path -LiteralPath $destination) { throw 'Isolated package extraction root already exists.' }
    [IO.Compression.ZipFile]::ExtractToDirectory('${escapePowerShellLiteral(portablePath)}', $destination)
  `);
  const embeddedVersion = (await readFile(resolve(packagedRoot, "VERSION"), "utf8")).replace(/^\uFEFF/, "").trim();
  if (embeddedVersion !== sourceVersion) {
    throw new Error(`Manifest-bound portable VERSION ${embeddedVersion || "<missing>"} did not match ${sourceVersion}.`);
  }
  const extractedExeSha256 = await sha256File(exe);
  const extractedBackendSha256 = await sha256File(resolve(packagedRoot, "backend", "vrcforge_backend.exe"));
  if (innerExeSha256 !== extractedExeSha256 || innerBackendSha256 !== extractedBackendSha256) {
    throw new Error("Extracted package executables did not match their manifest-bound ZIP entries.");
  }
  return {
    version: String(manifest.version),
    manifestCommit,
    headCommit,
    originMainCommit,
    worktreeClean,
    buildPolicy,
    strictBuildPolicy,
    localAcceptanceBuildPolicy,
    strictReleaseBinding: !allowUnpushed && worktreeClean && headCommit === originMainCommit && strictBuildPolicy,
    portableName,
    portableSha256,
    innerExeSha256,
    extractedExeSha256,
    innerBackendSha256,
    extractedBackendSha256,
    embeddedVersion,
  };
}

async function processSnapshot() {
  const value = await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${escapePowerShellLiteral(packagedRoot)}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $processes = @(foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
      $name = [string]$process.ProcessName
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { $path = '' }
      $knownVrcForge = $name -in @('VRCForge', 'vrcforge_backend', 'vrcforge-agentic-app')
      $insidePackage = $path -and $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
      if ($knownVrcForge -or $insidePackage) {
        [pscustomobject]@{ Id=$process.Id; ProcessName=$name; Path=$path; PackagedRoot=$insidePackage }
      }
    })
    $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object { $_.LocalPort -eq 8757 -or $_.LocalPort -eq ${port} } |
      Select-Object LocalAddress,LocalPort,State,OwningProcess
    [pscustomobject]@{ processes = @($processes); ports = @($ports) } | ConvertTo-Json -Depth 4 -Compress
  `);
  return value ? JSON.parse(value) : { processes: [], ports: [] };
}

async function resourceSnapshot() {
  const value = await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${escapePowerShellLiteral(packagedRoot)}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $processes = @(foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { continue }
      if ($path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        [pscustomobject]@{
          Id=$process.Id
          ProcessName=$process.ProcessName
          HandleCount=$process.HandleCount
          ThreadCount=$process.Threads.Count
          WorkingSetMB=[math]::Round($process.WorkingSet64/1MB,1)
          PrivateMB=[math]::Round($process.PrivateMemorySize64/1MB,1)
        }
      }
    })
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

async function requestMainWindowClose(processId, expectedStartedAt) {
  const value = await runPowerShell(`
    $expected = [IO.Path]::GetFullPath('${escapePowerShellLiteral(exe)}')
    $expectedStart = '${escapePowerShellLiteral(expectedStartedAt)}'
    $process = Get-Process -Id ${Number(processId)} -ErrorAction SilentlyContinue
    if ($process) {
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { $path = '' }
      try { $startedAtUtc = $process.StartTime.ToUniversalTime().ToString('o') } catch { $startedAtUtc = '' }
      if ($path.Equals($expected, [StringComparison]::OrdinalIgnoreCase) -and
          $startedAtUtc.Equals($expectedStart, [StringComparison]::Ordinal)) {
        $windowHandle = [long]$process.MainWindowHandle
        @([pscustomobject]@{
          id = $process.Id
          path = $path
          startedAtUtc = $startedAtUtc
          windowHandle = $windowHandle
          closeRequested = $process.CloseMainWindow()
        }) | ConvertTo-Json -Compress
      } else {
        throw 'Tracked PID no longer belongs to the extracted VRCForge executable.'
      }
    } else { '[]' }
  `);
  return value ? JSON.parse(value) : [];
}

async function waitForCloseToTray(processId, startedAtUtc, windowHandle, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    const value = await runPowerShell(`
      Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class VRCForgeProbeWindow {
  [DllImport("user32.dll")]
  public static extern bool IsWindowVisible(IntPtr hWnd);
}
'@
      $expected = [IO.Path]::GetFullPath('${escapePowerShellLiteral(exe)}')
      $expectedStart = [DateTime]::Parse('${escapePowerShellLiteral(startedAtUtc)}').ToUniversalTime()
      $process = Get-Process -Id ${Number(processId)} -ErrorAction SilentlyContinue
      $identityMatched = $false
      if ($process) {
        try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { $path = '' }
        $identityMatched = $path.Equals($expected, [StringComparison]::OrdinalIgnoreCase) -and
          $process.StartTime.ToUniversalTime() -eq $expectedStart
      }
      $windowVisible = [VRCForgeProbeWindow]::IsWindowVisible([IntPtr]${Number(windowHandle)})
      $backendAlive = [bool](Get-NetTCPConnection -LocalPort 8757 -State Listen -ErrorAction SilentlyContinue)
      [pscustomobject]@{
        processAlive = [bool]$process
        identityMatched = $identityMatched
        windowVisible = $windowVisible
        backendAlive = $backendAlive
      } | ConvertTo-Json -Compress
    `);
    latest = value
      ? JSON.parse(value)
      : { processAlive: false, identityMatched: false, windowVisible: true, backendAlive: false };
    latest.ok = Boolean(
      latest.processAlive && latest.identityMatched && !latest.windowVisible && latest.backendAlive,
    );
    if (latest.ok) return latest;
    await sleep(200);
  }
  return latest || {
    ok: false,
    processAlive: false,
    identityMatched: false,
    windowVisible: true,
    backendAlive: false,
  };
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

async function forceCloseTrackedLaunch(identity) {
  if (!identity?.id || !identity?.startedAt) return processSnapshot();
  await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${escapePowerShellLiteral(packagedRoot)}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $exe = [IO.Path]::GetFullPath('${escapePowerShellLiteral(exe)}')
    $rootProcessId = [int]${Number(identity.id)}
    $expectedStart = '${escapePowerShellLiteral(identity.startedAt)}'
    $rootProcess = Get-Process -Id $rootProcessId -ErrorAction SilentlyContinue
    if (-not $rootProcess) { exit 0 }
    try { $rootPath = [IO.Path]::GetFullPath([string]$rootProcess.Path) } catch { exit 0 }
    try { $rootStart = $rootProcess.StartTime.ToUniversalTime().ToString('o') } catch { exit 0 }
    if (-not $rootPath.Equals($exe, [StringComparison]::OrdinalIgnoreCase) -or
        -not $rootStart.Equals($expectedStart, [StringComparison]::Ordinal)) { exit 0 }
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
    $targets = @(foreach ($candidateId in $ids) {
      $process = Get-Process -Id $candidateId -ErrorAction SilentlyContinue
      if (-not $process) { continue }
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { continue }
      $allowed = if ($candidateId -eq $rootProcessId) {
        $path.Equals($exe, [StringComparison]::OrdinalIgnoreCase)
      } else {
        $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
      }
      if ($allowed) { $process }
    })
    $targets |
      Sort-Object @{ Expression = { if ($_.Id -eq $rootProcessId) { 1 } else { 0 } } } |
      Stop-Process -Force -ErrorAction SilentlyContinue
  `);
  const finalSnapshot = await waitForAppShutdown(30000);
  if (snapshotHasResidue(finalSnapshot)) {
    throw new Error(`Tracked packaged launch did not clear without touching other instances: ${JSON.stringify(finalSnapshot)}`);
  }
  return finalSnapshot;
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

async function prepareComposerAfterFirstRun(cdp, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  const actions = [];
  let lastState = null;
  while (Date.now() < deadline) {
    lastState = await evalValue(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        const submit = document.querySelector("button[type='submit']");
        return {
          composerReady: Boolean(textarea && submit),
          composerDisabled: Boolean(textarea?.disabled),
          languageGate: Boolean(document.querySelector("[data-vrcforge-onboarding-language-gate='true']")),
          onboarding: Boolean(document.querySelector("[data-vrcforge-onboarding='true']")),
          bodyLength: document.body.innerText.length,
        };
      })()`,
      5000,
    ).catch((error) => ({ error: String(error) }));
    if (composerIsReadyAfterOnboarding(lastState)) {
      return {
        ok: true,
        actions,
        bodyLength: lastState.bodyLength,
        composerDisabled: lastState.composerDisabled,
      };
    }
    if (lastState?.languageGate && !actions.includes("language-continue")) {
      const clicked = await evalValue(
        cdp,
        `(() => {
          const selected = document.querySelector(
            "button[data-vrcforge-onboarding-language-option][aria-pressed='true']"
          );
          const button = document.querySelector("button[data-vrcforge-onboarding-language-continue]");
          if (!selected || !button) return false;
          selected.click();
          button.click();
          return true;
        })()`,
        5000,
      );
      if (clicked) actions.push("language-continue");
    } else if (lastState?.onboarding && !actions.includes("onboarding-skip")) {
      const clicked = await evalValue(
        cdp,
        `(() => {
          const button = document.querySelector("button[data-vrcforge-onboarding-skip]");
          if (!button) return false;
          button.click();
          return true;
        })()`,
        5000,
      );
      if (clicked) actions.push("onboarding-skip");
    }
    await sleep(200);
  }
  throw new Error(`Timed out preparing the isolated first-run composer; last=${JSON.stringify(lastState)}`);
}

function currentUserTurnContains(request, text) {
  const messages = Array.isArray(request?.body?.messages) ? request.body.messages : [];
  const currentUser = [...messages].reverse().find((message) => String(message?.role || "") === "user");
  return JSON.stringify(currentUser?.content ?? currentUser ?? "").includes(text);
}

function fakeProviderRequestIsAuthorized(request, token) {
  return String(request?.headers?.authorization || "") === `Bearer ${token}`;
}

function createFakeProvider(token) {
  const requests = [];
  const pendingResponses = new Set();
  let completionCount = 0;
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const rawBody = Buffer.concat(chunks).toString("utf8");
    let body = {};
    try { body = rawBody ? JSON.parse(rawBody) : {}; } catch { body = {}; }
    const authorized = fakeProviderRequestIsAuthorized(request, token);
    const entry = {
      index: requests.length,
      method: request.method,
      url: request.url,
      authorized,
      stream: body.stream === true,
      model: String(body.model || ""),
      body,
      providerFinished: false,
      responseClosed: false,
      closedByClient: false,
    };
    requests.push(entry);
    if (!authorized) {
      response.writeHead(401, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { message: "unauthorized probe provider request" } }));
      return;
    }
    if (request.method === "GET" && request.url === "/v1/models") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ object: "list", data: [{ id: "vrcforge-desktop-probe", object: "model" }] }));
      return;
    }
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { message: "not found" } }));
      return;
    }
    completionCount += 1;
    const replyText = `PACKAGED_DESKTOP_PROVIDER_REPLY_${completionCount}_${marker}`;
    const content = JSON.stringify({ action: "reply", summary: replyText, reply: replyText });
    const finish = () => {
      if (response.destroyed || response.writableEnded) return;
      entry.providerFinished = true;
      if (body.stream === true) {
        response.write(`data: ${JSON.stringify({
          id: `chatcmpl-desktop-probe-${completionCount}`,
          object: "chat.completion.chunk",
          created: Math.floor(Date.now() / 1000),
          model: body.model || "vrcforge-desktop-probe",
          choices: [{ index: 0, delta: { content }, finish_reason: null }],
        })}\n\n`);
        response.write(`data: ${JSON.stringify({
          id: `chatcmpl-desktop-probe-${completionCount}`,
          object: "chat.completion.chunk",
          created: Math.floor(Date.now() / 1000),
          model: body.model || "vrcforge-desktop-probe",
          choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
          usage: { prompt_tokens: 12, completion_tokens: 4, total_tokens: 16 },
        })}\n\n`);
        response.end("data: [DONE]\n\n");
      } else {
        response.end(JSON.stringify({
          id: `chatcmpl-desktop-probe-${completionCount}`,
          object: "chat.completion",
          created: Math.floor(Date.now() / 1000),
          model: body.model || "vrcforge-desktop-probe",
          choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
          usage: { prompt_tokens: 12, completion_tokens: 4, total_tokens: 16 },
        }));
      }
    };
    response.writeHead(200, {
      "Content-Type": body.stream === true ? "text/event-stream" : "application/json",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    response.flushHeaders?.();
    if (body.stream === true) {
      response.write(`data: ${JSON.stringify({
        id: `chatcmpl-desktop-probe-${completionCount}`,
        object: "chat.completion.chunk",
        created: Math.floor(Date.now() / 1000),
        model: body.model || "vrcforge-desktop-probe",
        choices: [{ index: 0, delta: { role: "assistant", content: "" }, finish_reason: null }],
      })}\n\n`);
    }
    const pending = {
      response,
      timer: setTimeout(finish, 60000),
    };
    pending.timer.unref?.();
    pendingResponses.add(pending);
    response.once("close", () => {
      clearTimeout(pending.timer);
      pendingResponses.delete(pending);
      entry.responseClosed = true;
      entry.closedByClient = !entry.providerFinished;
    });
  });
  return {
    requests,
    get chatRequests() {
      return requests.filter((request) => request.method === "POST" && request.url === "/v1/chat/completions");
    },
    async listen() {
      await new Promise((resolveListen, rejectListen) => {
        server.once("error", rejectListen);
        server.listen(0, "127.0.0.1", resolveListen);
      });
      return server.address().port;
    },
    close() {
      for (const pending of pendingResponses) {
        clearTimeout(pending.timer);
        pending.response.destroy();
      }
      pendingResponses.clear();
      return new Promise((resolveClose, rejectClose) => {
        if (!server.listening) {
          resolveClose();
          return;
        }
        server.close((error) => error ? rejectClose(error) : resolveClose());
        server.closeAllConnections?.();
      });
    },
  };
}

async function waitForFakeProviderRequest(provider, text, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const matches = provider.chatRequests.filter((entry) => currentUserTurnContains(entry, text));
    if (matches.length > 1) throw new Error(`Fake provider observed duplicate current-user turns containing ${text}.`);
    if (matches.length === 1) return matches[0];
    await sleep(100);
  }
  throw new Error(`Fake provider request containing ${text} was not observed.`);
}

async function waitForFakeProviderCancellation(entry, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (entry?.responseClosed) {
      return {
        responseClosed: true,
        closedByClient: entry.closedByClient === true,
        providerFinished: entry.providerFinished === true,
      };
    }
    await sleep(50);
  }
  return {
    responseClosed: entry?.responseClosed === true,
    closedByClient: entry?.closedByClient === true,
    providerFinished: entry?.providerFinished === true,
    timedOut: true,
  };
}

function loopbackPortAcceptsConnections(port, timeoutMs = 1000) {
  return new Promise((resolveProbe) => {
    const socket = createConnection({ host: "127.0.0.1", port });
    let settled = false;
    const settle = (accepted) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      socket.destroy();
      resolveProbe(accepted);
    };
    const timer = setTimeout(() => settle(false), timeoutMs);
    timer.unref?.();
    socket.once("connect", () => settle(true));
    socket.once("error", () => settle(false));
  });
}

async function proveLoopbackPortReleased(port, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await loopbackPortAcceptsConnections(port))) return true;
    await sleep(100);
  }
  return false;
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
  if (typeof value === "string") {
    let sanitized = value;
    for (const secret of protectedSecrets) {
      if (secret) sanitized = sanitized.split(secret).join("<redacted-probe-secret>");
    }
    return sanitized;
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

function containsProtectedSecret(value) {
  if (!protectedSecrets.size) return false;
  const serialized = typeof value === "string" ? value : JSON.stringify(value);
  return [...protectedSecrets].some((secret) => secret && serialized.includes(secret));
}

async function scanTreeForProtectedSecrets(root, limits = {}) {
  const maxFiles = Number(limits.maxFiles || 5000);
  const maxBytes = Number(limits.maxBytes || 256 * 1024 * 1024);
  const secretBuffers = [...protectedSecrets].filter(Boolean).map((secret) => Buffer.from(secret, "utf8"));
  const state = { root, filesScanned: 0, bytesScanned: 0, matches: [], readErrors: [], truncated: false };
  if (!secretBuffers.length) return state;
  const visit = async (directory) => {
    if (state.truncated) return;
    let entries;
    try {
      entries = await readdir(directory, { withFileTypes: true });
    } catch (error) {
      if (error?.code !== "ENOENT") state.readErrors.push({ path: directory, error: String(error?.message || error) });
      return;
    }
    for (const entry of entries) {
      if (state.truncated) break;
      const path = resolve(directory, entry.name);
      if (entry.isDirectory()) {
        await visit(path);
        continue;
      }
      if (!entry.isFile()) continue;
      if (state.filesScanned >= maxFiles || state.bytesScanned >= maxBytes) {
        state.truncated = true;
        break;
      }
      try {
        const info = await stat(path);
        if (state.bytesScanned + info.size > maxBytes) {
          state.truncated = true;
          break;
        }
        const bytes = await readFile(path);
        state.filesScanned += 1;
        state.bytesScanned += bytes.length;
        if (secretBuffers.some((secret) => bytes.indexOf(secret) >= 0)) state.matches.push(path);
      } catch (error) {
        state.readErrors.push({ path, error: String(error?.message || error) });
      }
    }
  };
  await visit(root);
  return state;
}

async function appApi(path, options = {}) {
  if (!appSessionToken) {
    const tokenPath = resolve(configRoot, "app-session-token");
    try {
      appSessionToken = (await readFile(tokenPath, "utf8")).trim();
    } catch {
      const sessionResponse = await fetch(`${appOrigin}/api/app/session`, { headers: { Origin: appRequestOrigin } });
      const sessionPayload = await sessionResponse.json();
      appSessionToken = sessionPayload.appSessionToken || sessionPayload.app_session_token || "";
    }
    if (appSessionToken) protectedSecrets.add(appSessionToken);
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

async function advancedSettingsRequestBody(developerOptionsEnabled, computerUseEnabled) {
  const body = {
    developerOptionsEnabled: Boolean(developerOptionsEnabled),
    computerUseEnabled: Boolean(computerUseEnabled && developerOptionsEnabled),
  };
  if (!body.developerOptionsEnabled) return body;

  const current = await appApi("/api/app/advanced-settings");
  if (current?.payload?.settings?.developerOptionsEnabled) return body;

  const challenge = await appApi("/api/app/advanced-settings/developer-challenge", {
    method: "POST",
  });
  const challengeId = String(challenge?.payload?.challengeId || "");
  const waitMs = Number(challenge?.payload?.waitMs || 0);
  if (!challengeId || !Number.isFinite(waitMs) || waitMs < 5_000) {
    throw new Error("Developer Options challenge did not provide the required safety wait.");
  }
  await sleep(waitMs + 100);
  return { ...body, developerChallengeId: challengeId };
}

async function restoreAdvancedSettings(settings, attempts = 3) {
  let latest = null;
  const expectedDeveloper = Boolean(settings?.developerOptionsEnabled);
  const expectedComputer = Boolean(settings?.computerUseEnabled && expectedDeveloper);
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const body = await advancedSettingsRequestBody(expectedDeveloper, expectedComputer);
    const update = await appApi("/api/app/advanced-settings", {
      method: "POST",
      body,
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
  const output = {
    schema: "vrcforge.packaged_desktop_bridge_probe.v2",
    marker,
    mode: allowUnpushed ? "local-preacceptance" : "strict-release",
    strictReleaseBinding: false,
    releaseEligible: false,
    releaseEvidence: allowUnpushed
      ? "non-release local-preacceptance only"
      : "strict release binding pending completion",
    cdpPort: port,
    evidenceRoot,
    ownership: {
      process: "one tracked extracted VRCForge.exe PID and its extracted-package descendants",
      worker: "the embedded Desktop worker is owned by the packaged backend lifetime and authenticated by the isolated app session",
      provider: "one probe-owned ephemeral 127.0.0.1 listener authenticated by a per-run bearer token and closed in finally",
      externalFixtures: "only launch_app-returned Notepad and the compiled marker fixture PIDs are targeted and then stopped",
      powershell: "each hidden PowerShell child and its pipe handles are owned until its close event",
      ports: `loopback backend 8757 and WebView2 CDP ${port}; both must be unused before launch and released after close`,
    },
    isolation: {
      userDataRoot,
      configRoot,
      logRoot: resolve(userDataRoot, "logs"),
      artifactRoot: resolve(userDataRoot, "artifacts"),
      hostProfileRoot,
      webviewDataRoot,
      inheritedVrcForgeControls: false,
      inheritedProviderCredentials: false,
      inheritedProxy: false,
    },
    inputBoundary: "real input is limited to probe-launched Notepad and the compiled marker fixture; self-test never enters this matrix",
    assertions: [],
    resourceSnapshots: {},
  };
  let child = null;
  let packagedProcessIdentity = null;
  let cdp = null;
  let launchedAppPid = 0;
  let launchedFixturePid = 0;
  let notepadProcessIdentity = null;
  let uiaFixtureProcessIdentity = null;
  let previousPermissionMode = "";
  let permissionRestoreNeeded = false;
  let previousAdvancedSettings = null;
  let advancedSettingsRestoreNeeded = false;
  let gracefulShutdownAttempted = false;
  let fakeProvider = null;
  let fakeProviderPort = 0;
  let fakeProviderToken = "";
  try {
    if (!Number.isInteger(port) || port < 1024 || port > 65535 || port === 8757) {
      throw new Error(`Invalid VRCFORGE_DESKTOP_PROBE_CDP_PORT: ${process.env.VRCFORGE_DESKTOP_PROBE_CDP_PORT || port}`);
    }
    output.beforePackage = await processSnapshot();
    if (snapshotHasResidue(output.beforePackage)) {
      throw new Error(`Preflight found an existing VRCForge process or occupied backend/CDP port; nothing was terminated: ${JSON.stringify(output.beforePackage)}`);
    }
    const sourceVersion = (await readFile(resolve(repoRoot, "VERSION"), "utf8")).replace(/^\uFEFF/, "").trim();
    const binding = await prepareManifestBoundPackage(sourceVersion);
    output.strictReleaseBinding = binding.strictReleaseBinding === true;
    output.releaseEligible = binding.buildPolicy.releaseEligible === true;
    output.releaseBinding = {
      manifestCommit: binding.manifestCommit,
      headCommit: binding.headCommit,
      originMainCommit: binding.originMainCommit,
      worktreeClean: binding.worktreeClean,
      buildPolicy: binding.buildPolicy,
      strictBuildPolicy: binding.strictBuildPolicy,
      localAcceptanceBuildPolicy: binding.localAcceptanceBuildPolicy,
      portableName: binding.portableName,
      portableSha256: binding.portableSha256,
      innerExeSha256: binding.innerExeSha256,
      extractedExeSha256: binding.extractedExeSha256,
      innerBackendSha256: binding.innerBackendSha256,
      extractedBackendSha256: binding.extractedBackendSha256,
      embeddedVersion: binding.embeddedVersion,
    };
    if (allowUnpushed && (output.strictReleaseBinding || output.releaseEligible)) {
      output.assertions.push("allow-unpushed mode was incorrectly marked strict or release-eligible");
    }
    if (!allowUnpushed && (!output.strictReleaseBinding || !output.releaseEligible)) {
      output.assertions.push("strict mode did not retain strict release binding and release eligibility");
    }
    await Promise.all([
      mkdir(configRoot, { recursive: true }),
      mkdir(resolve(userDataRoot, "logs"), { recursive: true }),
      mkdir(resolve(userDataRoot, "artifacts"), { recursive: true }),
      mkdir(webviewDataRoot, { recursive: true }),
      mkdir(hostProfileRoot, { recursive: true }),
    ]);
    const fixtureCompileOutput = await runPowerShell(`
      $csc = 'C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe'
      $presentationFramework = (Get-ChildItem 'C:\\Windows\\Microsoft.NET\\assembly\\GAC_MSIL\\PresentationFramework' -Recurse -Filter PresentationFramework.dll | Select-Object -First 1).FullName
      $presentationCore = (Get-ChildItem 'C:\\Windows\\Microsoft.NET\\assembly\\GAC_64\\PresentationCore' -Recurse -Filter PresentationCore.dll | Select-Object -First 1).FullName
      $windowsBase = (Get-ChildItem 'C:\\Windows\\Microsoft.NET\\assembly\\GAC_MSIL\\WindowsBase' -Recurse -Filter WindowsBase.dll | Select-Object -First 1).FullName
      $systemXaml = (Get-ChildItem 'C:\\Windows\\Microsoft.NET\\assembly\\GAC_MSIL\\System.Xaml' -Recurse -Filter System.Xaml.dll | Select-Object -First 1).FullName
      & $csc /nologo /target:winexe /reference:$presentationFramework /reference:$presentationCore /reference:$windowsBase /reference:$systemXaml /out:'${escapePowerShellLiteral(fixtureExePath)}' '${escapePowerShellLiteral(fixtureSourcePath)}'
      if ($LASTEXITCODE -ne 0) { throw "fixture compiler exited $LASTEXITCODE" }
      Get-Item '${escapePowerShellLiteral(fixtureExePath)}' | Select-Object FullName,Length | ConvertTo-Json -Compress
    `);
    output.fixtureCompile = fixtureCompileOutput ? JSON.parse(fixtureCompileOutput) : null;
    output.beforeLaunch = await processSnapshot();
    if (snapshotHasResidue(output.beforeLaunch)) {
      throw new Error(`Launch preflight found an existing VRCForge process or occupied backend/CDP port; nothing was terminated: ${JSON.stringify(output.beforeLaunch)}`);
    }

    child = spawn(exe, [], {
      detached: false,
      stdio: "ignore",
      env: isolatedLaunchEnvironment(),
    });
    packagedProcessIdentity = await processIdentity(child.pid);
    if (
      !packagedProcessIdentity?.startedAt ||
      String(packagedProcessIdentity.path || "").toLowerCase() !== exe.toLowerCase()
    ) {
      throw new Error("Packaged app launch identity could not be bound before lifecycle checks.");
    }
    const childTerminated = new Promise((resolveTerminated) => {
      child.once("error", (error) => resolveTerminated({ kind: "error", error }));
      child.once("exit", (code, signal) => resolveTerminated({ kind: "exit", code, signal }));
    });
    const cdpOutcome = await Promise.race([
      waitForCdpTarget().then((page) => ({ kind: "page", page })),
      childTerminated,
    ]);
    if (cdpOutcome.kind !== "page") {
      throw new Error(cdpOutcome.kind === "error"
        ? `Packaged app failed to start: ${String(cdpOutcome.error?.message || cdpOutcome.error)}`
        : `Packaged app exited before WebView2 became ready: code=${cdpOutcome.code}, signal=${cdpOutcome.signal || "none"}`);
    }
    const page = cdpOutcome.page;
    if (!(await listenerOwnedByLaunch(packagedProcessIdentity))) {
      throw new Error("Packaged probe CDP listener was not owned by the captured launch generation.");
    }
    cdp = connectCdp(page.webSocketDebuggerUrl);
    await cdp.opened;
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    await cdp.send("Network.enable");
    await waitForEval(cdp, "document.readyState === 'complete' || document.readyState === 'interactive'");
    output.ready = await prepareComposerAfterFirstRun(cdp);
    if (output.ready.composerDisabled !== true) {
      output.assertions.push("the isolated first-run composer unexpectedly inherited an enabled Provider");
    }
    fakeProviderToken = `desktop-probe-${randomBytes(24).toString("hex")}`;
    protectedSecrets.add(fakeProviderToken);
    fakeProvider = createFakeProvider(fakeProviderToken);
    fakeProviderPort = await fakeProvider.listen();
    output.provider = {
      loopback: true,
      port: fakeProviderPort,
      model: "vrcforge-desktop-probe",
      authenticated: true,
      bounded: true,
    };
    const configuredProvider = await appApi("/api/config", {
      method: "POST",
      body: {
        provider: "custom",
        api_key: fakeProviderToken,
        base_url: `http://127.0.0.1:${fakeProviderPort}/v1`,
        model: "vrcforge-desktop-probe",
      },
    });
    const configuredApi = configuredProvider?.payload?.apiConfig || {};
    output.providerConfig = {
      ok: configuredProvider?.ok === true,
      status: configuredProvider?.status || 0,
      provider: configuredApi.provider || "",
      model: configuredApi.model || "",
      isolatedBaseUrlConfigured: String(configuredApi.base_url || configuredApi.baseUrl || "") ===
        `http://127.0.0.1:${fakeProviderPort}/v1`,
    };
    if (
      !output.providerConfig.ok ||
      output.providerConfig.provider !== "custom" ||
      output.providerConfig.model !== "vrcforge-desktop-probe" ||
      !output.providerConfig.isolatedBaseUrlConfigured
    ) {
      throw new Error(`Isolated fake Provider configuration failed: ${JSON.stringify(output.providerConfig)}`);
    }
    output.reloadAfterProviderConfig = await reloadAppPage(cdp);
    output.providerComposerReady = await waitForEval(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        const send = document.querySelector("[data-composer-send]");
        return {
          ok: textarea instanceof HTMLTextAreaElement && !textarea.disabled &&
            send instanceof HTMLButtonElement,
          textareaDisabled: textarea?.disabled ?? null,
          sendDisabled: send?.disabled ?? null,
        };
      })()`,
      30000,
    );
    if (output.providerComposerReady.sendDisabled !== true) {
      output.assertions.push("the empty Provider-backed composer unexpectedly enabled submission without input");
    }
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

    const enabledAdvancedSettingsBody = await advancedSettingsRequestBody(true, true);
    output.advancedSettingsEnabled = await appApi("/api/app/advanced-settings", {
      method: "POST",
      body: enabledAdvancedSettingsBody,
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
    const observedProviderRequest = await waitForFakeProviderRequest(fakeProvider, `${marker} frontend gate probe`);
    output.providerRequest = {
      index: observedProviderRequest.index,
      method: observedProviderRequest.method,
      url: observedProviderRequest.url,
      authorized: observedProviderRequest.authorized === true,
      model: observedProviderRequest.model,
      currentUserMarkerObserved: currentUserTurnContains(observedProviderRequest, `${marker} frontend gate probe`),
    };
    if (!output.providerRequest.authorized || !output.providerRequest.currentUserMarkerObserved) {
      output.assertions.push("the isolated fake Provider did not receive the authenticated /desktop marker request");
    }
    output.providerPendingBeforeStop = {
      pending: !observedProviderRequest.providerFinished && !observedProviderRequest.responseClosed,
      providerFinished: observedProviderRequest.providerFinished === true,
      responseClosed: observedProviderRequest.responseClosed === true,
    };
    if (!output.providerPendingBeforeStop.pending) {
      output.assertions.push("the fake Provider request completed before the real Stop path was exercised");
    }
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
    output.providerCancellationAfterStop = await waitForFakeProviderCancellation(observedProviderRequest);
    if (!output.frontendDesktopGateSetup?.ok || !output.frontendDesktopGateClick?.ok || !output.frontendDesktopGate?.ok) {
      output.assertions.push("real composer /desktop submission did not set the turn-scoped Computer Use and theme flags");
    }
    if (!output.frontendDesktopStop?.ok) {
      output.assertions.push("real composer Computer Use turn did not expose a cancellable Stop control");
    }
    if (
      !output.providerCancellationAfterStop.responseClosed ||
      !output.providerCancellationAfterStop.closedByClient ||
      output.providerCancellationAfterStop.providerFinished
    ) {
      output.assertions.push("real composer Stop did not cancel the still-pending fake Provider request");
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

    const notepadLaunchStartedAt = Date.now();
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
    notepadProcessIdentity = await processIdentity(launchedAppPid);
    output.notepadProcessIdentity = notepadProcessIdentity;
    const notepadStartedAt = Date.parse(String(notepadProcessIdentity?.startedAt || ""));
    const notepadTarget = {
      id: notepadWindow?.windowHandle || 0,
      app: notepadWindow?.app || "",
      processId: launchedAppPid,
    };
    if (
      output.launchAppCompletedAction?.action?.status !== "completed" ||
      launchAppResult.operation !== "launch_app" ||
      !launchAppResult.windowDetected ||
      !notepadWindow?.windowHandle ||
      !launchedAppPid ||
      !String(notepadProcessIdentity?.path || "").toLowerCase().endsWith("\\notepad.exe") ||
      !Number.isFinite(notepadStartedAt) ||
      notepadStartedAt < notepadLaunchStartedAt - 5000
    ) {
      output.assertions.push("packaged launch_app did not launch and resolve a Notepad window");
      throw new Error("Notepad ownership was not proven; no desktop input was attempted.");
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
          window: notepadTarget,
          steps: [
            {
              operation: "click",
              window: notepadTarget,
              x: 120,
              y: 140,
              click_count: 1,
              mouse_button: "left",
            },
            { operation: "type", window: notepadTarget, text: fixtureTypedMarker },
            { operation: "press_key", window: notepadTarget, key: "Control_L+a" },
            { operation: "type_text", window: notepadTarget, text: fixtureTypedMarker },
            { operation: "drag", window: notepadTarget, from_x: 30, from_y: 140, to_x: 260, to_y: 140, duration_ms: 200 },
            { operation: "scroll", window: notepadTarget, x: 200, y: 140, scroll_x: 120, scroll_y: 240 },
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

    const fixtureLaunchStartedAt = Date.now();
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
    uiaFixtureProcessIdentity = await processIdentity(launchedFixturePid);
    output.uiaFixtureProcessIdentity = uiaFixtureProcessIdentity;
    const fixtureStartedAt = Date.parse(String(uiaFixtureProcessIdentity?.startedAt || ""));
    const fixtureTarget = {
      id: uiaFixtureWindow?.windowHandle || 0,
      app: uiaFixtureWindow?.app || "",
      processId: launchedFixturePid,
    };
    if (
      output.uiaFixtureLaunchCompletedAction?.action?.status !== "completed" ||
      !uiaFixtureLaunchResult.windowDetected ||
      !uiaFixtureWindow?.windowHandle ||
      !launchedFixturePid ||
      String(uiaFixtureProcessIdentity?.path || "").toLowerCase() !== fixtureExePath.toLowerCase() ||
      !Number.isFinite(fixtureStartedAt) ||
      fixtureStartedAt < fixtureLaunchStartedAt - 5000
    ) {
      output.assertions.push("packaged launch_app did not launch the native UI Automation fixture");
      throw new Error("Native fixture ownership was not proven; no fixture input was attempted.");
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
          window: fixtureTarget,
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
          window: notepadTarget,
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
          window: notepadTarget,
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
          window: fixtureTarget,
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
            window: fixtureTarget,
            steps: [
              {
                operation: "set_value",
                window: fixtureTarget,
                element_index: uiaInput.index,
                value: uiaFixtureTypedMarker,
              },
              {
                operation: "click",
                window: fixtureTarget,
                element_index: uiaButton.index,
                click_count: 1,
                mouse_button: "left",
              },
              {
                operation: "perform_secondary_action",
                window: fixtureTarget,
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
            window: fixtureTarget,
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
      userDataRoot,
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
      output.notepadCleanup = await stopTrackedExternalProcess(notepadProcessIdentity);
      if (!output.notepadCleanup?.ok) {
        output.assertions.push("tracked Notepad process identity changed before cleanup; no unverified process was stopped");
      } else {
        launchedAppPid = 0;
      }
    }
    output.closeRequest = await requestMainWindowClose(child.pid, packagedProcessIdentity.startedAt);
    const closeRequest = Array.isArray(output.closeRequest)
      ? output.closeRequest[0]
      : output.closeRequest;
    output.closeToTray = closeRequest
      ? await waitForCloseToTray(child.pid, closeRequest.startedAtUtc, closeRequest.windowHandle)
      : { ok: false, error: "The tracked packaged window was unavailable for the X-button check." };
    if (!closeRequest?.closeRequested || !output.closeToTray?.ok) {
      output.assertions.push("the packaged X button did not hide the main window while preserving the exact app process and backend listener");
    }
    gracefulShutdownAttempted = true;
    const explicitQuitListenerOwned = await listenerOwnedByLaunch(packagedProcessIdentity).catch(() => false);
    output.explicitQuit = explicitQuitListenerOwned
      ? await requestPackagedAppQuit(cdp)
      : { accepted: false, listenerOwnershipChanged: true, error: "Tracked packaged CDP listener changed owner; no Quit was attempted." };
    if (!output.explicitQuit.accepted) {
      output.assertions.push("the packaged app did not accept the explicit Tauri Quit request after the X-button check");
    }
    if (cdp) {
      cdp.close();
      cdp = null;
    }
    output.afterWindowClose = await waitForAppShutdown(20000);
    output.resourceSnapshots.afterWindowClose = await resourceSnapshot();
    if (snapshotHasResidue(output.afterWindowClose)) {
      output.assertions.push("explicit Quit left VRCForge/backend or backend/CDP port alive after the X-button check");
    }
    output.launchedFixtureSurvivedAppClose = launchedFixturePid > 0 && processExists(launchedFixturePid);
    if (!output.launchedFixtureSurvivedAppClose) {
      output.assertions.push("explicitly quitting VRCForge also terminated an external application launched by Computer Use");
    }
    if (launchedFixturePid) {
      output.fixtureCleanup = await stopTrackedExternalProcess(uiaFixtureProcessIdentity);
      if (!output.fixtureCleanup?.ok) {
        output.assertions.push("tracked native fixture identity changed before cleanup; no unverified process was stopped");
      } else {
        launchedFixturePid = 0;
      }
    }
  } catch (error) {
    output.error = String(error && error.stack ? error.stack : error);
    output.assertions.push("probe threw before completion");
  } finally {
    if (launchedAppPid) {
      output.notepadCleanupFinally = await stopTrackedExternalProcess(notepadProcessIdentity)
        .catch((error) => ({ ok: false, error: String(error) }));
      if (!output.notepadCleanupFinally?.ok) {
        output.assertions.push("final cleanup could not verify and stop the tracked Notepad process");
      }
      launchedAppPid = 0;
    }
    if (launchedFixturePid) {
      output.fixtureCleanupFinally = await stopTrackedExternalProcess(uiaFixtureProcessIdentity)
        .catch((error) => ({ ok: false, error: String(error) }));
      if (!output.fixtureCleanupFinally?.ok) {
        output.assertions.push("final cleanup could not verify and stop the tracked native fixture process");
      }
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
    if (!gracefulShutdownAttempted && child?.pid) {
      gracefulShutdownAttempted = true;
      const finalQuitListenerOwned = cdp
        ? await listenerOwnedByLaunch(packagedProcessIdentity).catch(() => false)
        : false;
      output.explicitQuitFinally = finalQuitListenerOwned
        ? await requestPackagedAppQuit(cdp).catch((error) => ({ accepted: false, error: String(error) }))
        : { accepted: false, listenerOwnershipChanged: true, error: "Tracked packaged CDP listener changed owner; no Quit was attempted." };
      if (!output.explicitQuitFinally?.accepted) {
        output.assertions.push("failure cleanup could not obtain an accepted explicit Tauri Quit request");
      }
      if (cdp) {
        cdp.close();
        cdp = null;
      }
      output.afterWindowCloseFinally = await waitForAppShutdown(20000).catch((error) => ({ error: String(error) }));
      if (snapshotHasResidue(output.afterWindowCloseFinally)) {
        output.assertions.push("failure cleanup explicit Quit left VRCForge/backend or backend/CDP port alive");
      }
    }
    if (cdp) {
      cdp.close();
      cdp = null;
    }
    const residueBeforeForce = await processSnapshot().catch((error) => ({ error: String(error), processes: [], ports: [] }));
    output.forcedCleanupUsed = Boolean(child?.pid && snapshotHasResidue(residueBeforeForce));
    if (output.forcedCleanupUsed) {
      output.assertions.push("failure cleanup required identity-bound forced termination");
      await forceCloseTrackedLaunch(packagedProcessIdentity).catch((error) => {
        output.cleanupError = String(error);
      });
    }
    if (fakeProvider) {
      output.providerCleanup = await fakeProvider.close()
        .then(() => ({ ok: true }))
        .catch((error) => ({ ok: false, error: String(error) }));
      fakeProvider = null;
      output.providerPortReleased = fakeProviderPort > 0
        ? await proveLoopbackPortReleased(fakeProviderPort)
        : false;
      if (!output.providerCleanup.ok || !output.providerPortReleased) {
        output.assertions.push("the isolated fake Provider did not release its owned loopback listener cleanly");
      }
    }
    output.afterCleanup = await processSnapshot().catch((error) => ({ error: String(error) }));
    output.resourceSnapshots.afterCleanup = await resourceSnapshot().catch((error) => ({ error: String(error) }));
    if (snapshotHasResidue(output.afterCleanup)) {
      output.assertions.push("final tracked cleanup still left VRCForge/backend or backend/CDP port alive");
    }
    const outputContainedSecret = containsProtectedSecret(output);
    const [logSecretScan, artifactSecretScan] = await Promise.all([
      scanTreeForProtectedSecrets(resolve(userDataRoot, "logs")),
      scanTreeForProtectedSecrets(resolve(userDataRoot, "artifacts")),
    ]);
    output.protectedSecretScan = {
      outputContainedSecret,
      logs: logSecretScan,
      artifacts: artifactSecretScan,
    };
    if (outputContainedSecret) {
      output.assertions.push("the in-memory probe report contained the exact app-session token before final redaction");
    }
    if (logSecretScan.matches.length || artifactSecretScan.matches.length) {
      output.assertions.push("the exact app-session token leaked into isolated logs or audit artifacts");
    }
    if (
      logSecretScan.truncated || artifactSecretScan.truncated ||
      logSecretScan.readErrors.length || artifactSecretScan.readErrors.length
    ) {
      output.assertions.push("the bounded exact protected-secret log/audit scan was incomplete");
    }
    output.ok = output.assertions.length === 0;
    output.releaseEvidence = output.ok
      ? allowUnpushed
        ? "non-release local-preacceptance only"
        : "strict release-bound Desktop/Computer Use evidence"
      : allowUnpushed
        ? "failed non-release local-preacceptance evidence"
        : "failed strict-release evidence";
    await writeFile(outPath, `${JSON.stringify(sanitizeProbeValue(output), null, 2)}\n`, "utf8");
    console.log(outPath);
    if (output.assertions.length) {
      console.error(output.assertions.join("\n"));
      process.exitCode = 1;
    }
  }
}

if (!selfTest) {
  main();
}
