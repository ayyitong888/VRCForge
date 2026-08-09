import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { createServer } from "node:http";
import { appendFile, mkdir, readFile, realpath, stat, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { requestPackagedAppQuit } from "./lib/packaged_app_lifecycle.mjs";

const repoRoot = resolve(import.meta.dirname, "..");
const allowUnpushed = process.argv.includes("--allow-unpushed");
const selfTest = process.argv.includes("--self-test");
const cdpPort = Number(process.env.VRCFORGE_SUBAGENT_PROBE_CDP_PORT || "9349");
const marker = `SUBAGENT_HANDOFF_PROBE_${Date.now()}`;
const shortMarker = `SAPH${Date.now().toString(36)}`;
const evidenceRoot = resolve(repoRoot, "artifacts", "actual-app-subagent-handoff", marker);
const packagedRoot = resolve(evidenceRoot, "package");
const exe = resolve(packagedRoot, "VRCForge.exe");
const userDataRoot = resolve(evidenceRoot, "user-data");
const configRoot = resolve(userDataRoot, "config");
const webviewDataRoot = resolve(evidenceRoot, "webview2-user-data");
const hostProfileRoot = resolve(evidenceRoot, "host-profile");
const reportPath = resolve(evidenceRoot, "report.json");
const appOrigin = "http://127.0.0.1:8757";
const appRequestOrigin = "http://tauri.localhost";
const adoptHistoryMarker = `ADOPT_HISTORY_${marker}`;
const dismissHistoryMarker = `DISMISS_HISTORY_${marker}`;
const orphanHistoryMarker = `ORPHAN_HISTORY_${marker}`;
const adoptDisplayName = `Adopt-${shortMarker}`;
const dismissDisplayName = `Dismiss-${shortMarker}`;
const orphanDisplayName = `Recovered-${shortMarker}`;
const subAgentArtifactRoot = resolve(userDataRoot, "artifacts", "dashboard", "sub-agents");
const subAgentEventLogPath = resolve(subAgentArtifactRoot, "sub-agent-events.jsonl");
let appSessionToken = "";

const allowedOptions = new Set(["--allow-unpushed", "--self-test", "--help", "-h"]);
if (process.argv.slice(2).some((item) => !allowedOptions.has(item))) {
  console.error("Unknown packaged sub-agent handoff probe option.");
  process.exit(2);
}

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`Usage: node scripts/diagnose_packaged_subagent_handoff.mjs [--allow-unpushed] [--self-test]

Runs a packaged VRCForge restart/idempotency acceptance probe for durable
Sub-agent result handoffs.

Default mode is strict-release evidence: release-manifest.json, VERSION, HEAD,
origin/main, ZIP and extracted executable must bind a strict release-eligible
build policy. --allow-unpushed is only non-release local acceptance: it still
requires VERSION, manifest commit == HEAD, ZIP and extracted executable hashes,
but accepts only a clean worktree plus an explicit local-acceptance,
release-ineligible policy with allowDirty=false, and writes
strictReleaseBinding=false to its report.

--self-test validates the pure build-policy boundary without reading a package,
reserving a port, or starting VRCForge.

Coverage:
  - isolated backend and WebView2 user data
  - real packaged Tauri create/merge IPC
  - startup recovery of a synthetic running projection plus durable result sidecar
  - real WebView card persistence and handoff acknowledgement
  - restart-safe original-chat ownership and stable card IDs
  - adopted/recovered history injected once; dismissed and cross-chat history excluded
  - final process and ports 8757/${cdpPort} cleanup

Optional environment:
  VRCFORGE_SUBAGENT_PROBE_CDP_PORT=<unused port> (default: ${cdpPort})`);
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

function countOccurrences(value, needle) {
  if (!needle) {
    return 0;
  }
  return String(value || "").split(needle).length - 1;
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

function isChatStoreSnapshotChanged(error) {
  return error?.status === 409
    && error?.path === "/api/app/chats"
    && error?.payload?.detail?.code === "chat_store_snapshot_changed";
}

function sameLaunchIdentity(expected, observed) {
  return Number(expected?.processId) > 0
    && Number(observed?.processId) === Number(expected.processId)
    && String(expected?.path || "").toLowerCase() === String(observed?.path || "").toLowerCase()
    && Boolean(expected?.startedAtUtc)
    && String(observed?.startedAtUtc || "") === String(expected.startedAtUtc)
    && Boolean(expected?.creationDateUtc)
    && String(observed?.creationDateUtc || "") === String(expected.creationDateUtc);
}

function cdpOwnershipAllowsAction(ownership) {
  return ownership?.ok === true && ownership?.status === "owned";
}

function closeAuthorizationAction(identityCheck, cdpOwnership) {
  if (identityCheck?.ok !== true) return "preserve";
  return cdpOwnershipAllowsAction(cdpOwnership) ? "quit" : "force-own-root";
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
  if (isLocalAcceptanceBuildPolicy({ ...local, releaseEligible: true })) {
    throw new Error("self-test: release-eligible policy was accepted as local-only evidence.");
  }
  if (isLocalAcceptanceBuildPolicy({ ...local, allowUnpushed: false })) {
    throw new Error("self-test: local policy without explicit allowUnpushed was accepted.");
  }
  if (isLocalAcceptanceBuildPolicy({ ...local, allowDirty: true })) {
    throw new Error("self-test: dirty local policy was accepted.");
  }
  const launchIdentity = {
    processId: 731,
    path: "C:\\probe\\VRCForge.exe",
    startedAtUtc: "2026-08-09T01:02:03.0000000Z",
    creationDateUtc: "2026-08-09T01:02:03.0000000Z",
  };
  if (
    !sameLaunchIdentity(launchIdentity, { ...launchIdentity, path: "c:\\PROBE\\vrcforge.EXE" })
    || sameLaunchIdentity(launchIdentity, { ...launchIdentity, startedAtUtc: "2026-08-09T01:02:04.0000000Z" })
    || sameLaunchIdentity(launchIdentity, { ...launchIdentity, creationDateUtc: "2026-08-09T01:02:04.0000000Z" })
  ) {
    throw new Error("self-test: packaged PID generation identity was not fail-closed.");
  }
  if (
    !cdpOwnershipAllowsAction({ ok: true, status: "owned" })
    || cdpOwnershipAllowsAction({ ok: true, status: "foreign" })
    || cdpOwnershipAllowsAction({ ok: false, status: "absent" })
  ) {
    throw new Error("self-test: foreign CDP listener ownership was accepted for an app action.");
  }
  const closeCalls = { quit: 0, force: 0 };
  const closeAction = closeAuthorizationAction(
    { ok: true },
    { ok: false, status: "foreign" },
  );
  if (closeAction === "quit") closeCalls.quit += 1;
  if (closeAction === "force-own-root") closeCalls.force += 1;
  if (closeAction !== "force-own-root" || closeCalls.quit !== 0 || closeCalls.force !== 1) {
    throw new Error("self-test: exact root with a foreign CDP listener did not fail through one owned force cleanup.");
  }
  if (!isChatStoreSnapshotChanged({
    status: 409,
    path: "/api/app/chats",
    payload: { detail: { code: "chat_store_snapshot_changed" } },
  })) {
    throw new Error("self-test: chat snapshot conflict classification failed.");
  }
  if (isChatStoreSnapshotChanged({
    status: 409,
    path: "/api/app/chats",
    payload: { detail: { code: "chat_store_recovery_required" } },
  })) {
    throw new Error("self-test: a non-snapshot chat conflict was accepted for retry.");
  }
  const previousDisableAuth = process.env.VRCFORGE_DISABLE_APP_AUTH;
  process.env.VRCFORGE_DISABLE_APP_AUTH = "1";
  try {
    const env = isolatedLaunchEnvironment();
    if (Object.hasOwn(env, "VRCFORGE_DISABLE_APP_AUTH")) {
      throw new Error("self-test: inherited VRCForge control escaped environment isolation.");
    }
    if (
      env.VRCFORGE_CONFIG_PATH !== resolve(configRoot, "config.json")
      || env.VRCFORGE_DESKTOP_EXECUTOR !== "0"
      || env.APPDATA !== hostProfileRoot
      || env.LOCALAPPDATA !== hostProfileRoot
    ) {
      throw new Error("self-test: packaged runtime paths were not fully isolated.");
    }
  } finally {
    if (previousDisableAuth === undefined) {
      delete process.env.VRCFORGE_DISABLE_APP_AUTH;
    } else {
      process.env.VRCFORGE_DISABLE_APP_AUTH = previousDisableAuth;
    }
  }
  console.log("sub-agent handoff probe self-test passed");
}

if (selfTest) {
  runSelfTest();
  process.exit(0);
}

async function prepareManifestBoundPackage(sourceVersion) {
  const manifestPath = resolve(repoRoot, "dist", "release", "release-manifest.json");
  let manifest;
  try {
    manifest = JSON.parse((await readFile(manifestPath, "utf8")).replace(/^\uFEFF/, ""));
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error(`Packaged sub-agent handoff probe requires ${manifestPath}.`);
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
    !/^[0-9a-f]{40}$/.test(headCommit) ||
    !/^[0-9a-f]{40}$/.test(originMainCommit) ||
    manifestCommit !== headCommit
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
  const escapedPortable = escapePowerShellLiteral(portablePath);
  const innerExeSha256 = (await runPowerShell(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead('${escapedPortable}')
    try {
      $entry = @($archive.Entries | Where-Object {
        $name = $_.FullName.Replace('\\', '/')
        $name.Equals('VRCForge.exe', [StringComparison]::OrdinalIgnoreCase)
      })
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
    worktreeClean,
    buildPolicy,
    strictBuildPolicy,
    localAcceptanceBuildPolicy,
    strictReleaseBinding: !allowUnpushed && worktreeClean && headCommit === originMainCommit && strictBuildPolicy,
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

async function readProcessIdentity(processId) {
  const raw = await runPowerShell(`
    $processId = [int]${Number(processId)}
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if (-not $process -or -not $cim) { '' ; exit 0 }
    try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { $path = '' }
    try { $startedAtUtc = $process.StartTime.ToUniversalTime().ToString('o') } catch { $startedAtUtc = '' }
    try { $creationDateUtc = $cim.CreationDate.ToUniversalTime().ToString('o') } catch { $creationDateUtc = '' }
    [pscustomobject]@{
      processId = $processId
      path = $path
      startedAtUtc = $startedAtUtc
      creationDateUtc = $creationDateUtc
    } | ConvertTo-Json -Compress
  `);
  return raw ? JSON.parse(raw) : null;
}

async function captureLaunchIdentity(processId, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  let identity = null;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      identity = await readProcessIdentity(processId);
    } catch (error) {
      lastError = error;
      identity = null;
    }
    if (
      identity?.startedAtUtc
      && identity?.creationDateUtc
      && String(identity.path || "").toLowerCase() === exe.toLowerCase()
    ) {
      return identity;
    }
    await sleep(50);
  }
  throw new Error(
    `Packaged launch PID generation could not be captured within ${timeoutMs}ms; no lifecycle action is authorized.${lastError ? ` Last read error: ${String(lastError?.message || lastError)}` : ""}`,
  );
}

async function validateLaunchIdentity(launch) {
  const observed = await readProcessIdentity(launch?.identity?.processId || launch?.childPid);
  return {
    ok: sameLaunchIdentity(launch?.identity, observed),
    expected: launch?.identity || null,
    observed,
    error: sameLaunchIdentity(launch?.identity, observed)
      ? ""
      : "Tracked packaged PID generation changed; no Quit or forced termination is authorized.",
  };
}

async function inspectCdpListenerOwnership(launch) {
  const identityCheck = await validateLaunchIdentity(launch);
  if (!identityCheck.ok) {
    return { ok: false, status: "root-generation-mismatch", identityCheck, listenerCount: 0 };
  }
  const expected = launch.identity;
  const raw = await runPowerShell(`
    $rootPid = [int]${Number(expected.processId)}
    $expectedPath = [IO.Path]::GetFullPath('${escapePowerShellLiteral(expected.path)}')
    $expectedStartedAtUtc = '${escapePowerShellLiteral(expected.startedAtUtc)}'
    $expectedCreationDateUtc = '${escapePowerShellLiteral(expected.creationDateUtc)}'
    $rootProcess = Get-Process -Id $rootPid -ErrorAction SilentlyContinue
    $rootCim = Get-CimInstance Win32_Process -Filter "ProcessId = $rootPid" -ErrorAction SilentlyContinue
    try { $rootPath = [IO.Path]::GetFullPath([string]$rootProcess.Path) } catch { $rootPath = '' }
    try { $rootStartedAtUtc = $rootProcess.StartTime.ToUniversalTime().ToString('o') } catch { $rootStartedAtUtc = '' }
    try { $rootCreationDateUtc = $rootCim.CreationDate.ToUniversalTime().ToString('o') } catch { $rootCreationDateUtc = '' }
    $rootMatches = $rootProcess -and $rootCim -and
      $rootPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase) -and
      $rootStartedAtUtc.Equals($expectedStartedAtUtc, [StringComparison]::Ordinal) -and
      $rootCreationDateUtc.Equals($expectedCreationDateUtc, [StringComparison]::Ordinal)
    if (-not $rootMatches) {
      [pscustomobject]@{ ok=$false; status='root-generation-mismatch'; listenerCount=0; owningProcess=0 } | ConvertTo-Json -Compress
      exit 0
    }
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
    $listeners = @(Get-NetTCPConnection -LocalPort ${cdpPort} -State Listen -ErrorAction SilentlyContinue)
    $owned = $listeners.Count -eq 1 -and $ids.Contains([int]$listeners[0].OwningProcess)
    $status = if ($listeners.Count -eq 0) { 'absent' } elseif ($owned) { 'owned' } else { 'foreign' }
    [pscustomobject]@{
      ok = $owned
      status = $status
      listenerCount = $listeners.Count
      owningProcess = if ($listeners.Count -eq 1) { [int]$listeners[0].OwningProcess } else { 0 }
    } | ConvertTo-Json -Compress
  `);
  return { ...(raw ? JSON.parse(raw) : { ok: false, status: "absent", listenerCount: 0 }), identityCheck };
}

async function waitForOwnedCdpListener(launch, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await inspectCdpListenerOwnership(launch);
    if (cdpOwnershipAllowsAction(latest)) return latest;
    if (latest.status !== "absent") {
      throw new Error(`Packaged CDP listener was not owned by the captured root generation; no page connection is authorized: ${JSON.stringify(latest)}`);
    }
    await sleep(100);
  }
  throw new Error(`Timed out waiting for a CDP listener owned by the captured root generation: ${JSON.stringify(latest)}`);
}

function snapshotIsClear(snapshot) {
  return (snapshot.processes || []).length === 0 && (snapshot.ports || []).length === 0;
}

async function waitForPackagedClear(timeoutMs = 30000) {
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
  const beforeForce = await processSnapshot();
  if (snapshotIsClear(beforeForce)) {
    return beforeForce;
  }
  const identityCheck = await validateLaunchIdentity(launch);
  if (!identityCheck.ok) {
    throw new Error(identityCheck.error);
  }
  const escapedRoot = escapePowerShellLiteral(packagedRoot);
  const escapedExe = escapePowerShellLiteral(exe);
  const escapedStartedAtUtc = escapePowerShellLiteral(launch.identity.startedAtUtc);
  const escapedCreationDateUtc = escapePowerShellLiteral(launch.identity.creationDateUtc);
  const rootPid = Number(launch.childPid);
  await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${escapedRoot}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $exe = [IO.Path]::GetFullPath('${escapedExe}')
    $rootPid = [int]${rootPid}
    $expectedStartedAtUtc = '${escapedStartedAtUtc}'
    $expectedCreationDateUtc = '${escapedCreationDateUtc}'
    function Assert-TrackedRootIdentity {
      $rootProcess = Get-Process -Id $rootPid -ErrorAction SilentlyContinue
      $rootCim = Get-CimInstance Win32_Process -Filter "ProcessId = $rootPid" -ErrorAction SilentlyContinue
      if (-not $rootProcess -or -not $rootCim) {
        throw 'Tracked packaged PID generation is absent; no process was stopped.'
      }
      try { $rootPath = [IO.Path]::GetFullPath([string]$rootProcess.Path) } catch { $rootPath = '' }
      try { $rootStartedAtUtc = $rootProcess.StartTime.ToUniversalTime().ToString('o') } catch { $rootStartedAtUtc = '' }
      try { $rootCreationDateUtc = $rootCim.CreationDate.ToUniversalTime().ToString('o') } catch { $rootCreationDateUtc = '' }
      if (
        -not $rootPath.Equals($exe, [StringComparison]::OrdinalIgnoreCase) -or
        -not $rootStartedAtUtc.Equals($expectedStartedAtUtc, [StringComparison]::Ordinal) -or
        -not $rootCreationDateUtc.Equals($expectedCreationDateUtc, [StringComparison]::Ordinal)
      ) {
        throw 'Tracked packaged PID generation changed; no process was stopped.'
      }
    }
    Assert-TrackedRootIdentity
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
    Assert-TrackedRootIdentity
    $targets |
      Sort-Object @{ Expression = { if ($_.Id -eq $rootPid) { 1 } else { 0 } } } |
      Stop-Process -Force -ErrorAction SilentlyContinue
  `);
  const cleared = await waitForPackagedClear();
  if (!cleared.ok) {
    throw new Error(`Tracked packaged launch did not clear without touching other instances: ${JSON.stringify(cleared.snapshot)}`);
  }
  return cleared.snapshot;
}

async function closePackagedApp(launch) {
  if (!launch?.childPid || !launch?.cdp) {
    throw new Error("Tracked packaged launch was unavailable for explicit Quit.");
  }
  const rootPid = Number(launch.childPid);
  const identityCheck = await validateLaunchIdentity(launch);
  const cdpOwnership = identityCheck.ok
    ? await inspectCdpListenerOwnership(launch)
    : { ok: false, status: "root-generation-mismatch" };
  const closeAction = closeAuthorizationAction(identityCheck, cdpOwnership);
  if (closeAction === "preserve") {
    try { launch.cdp.close(); } catch { /* Closing CDP is not a process lifecycle action. */ }
    return {
      quit: {
        accepted: false,
        error: identityCheck.error || "Packaged CDP listener ownership changed; no Quit is authorized.",
      },
      trackedPid: rootPid,
      identityCheck,
      cdpOwnership,
      graceful: false,
      forced: false,
      finalSnapshot: await processSnapshot(),
    };
  }
  if (closeAction === "force-own-root") {
    try { launch.cdp.close(); } catch { /* Closing CDP is not a process lifecycle action. */ }
    const beforeForce = await processSnapshot();
    const forceCleanup = await forceCloseLaunch(launch)
      .then((snapshot) => ({ ok: true, snapshot }))
      .catch(async (error) => ({ ok: false, error: String(error?.stack || error), snapshot: await processSnapshot() }));
    return {
      quit: { accepted: false, error: "Packaged CDP listener was foreign or absent; Quit was not authorized." },
      trackedPid: rootPid,
      identityCheck,
      cdpOwnership,
      graceful: false,
      forced: true,
      evidenceFailure: true,
      beforeForce,
      forceCleanup,
      finalSnapshot: forceCleanup.snapshot,
    };
  }
  const quit = await requestPackagedAppQuit(launch.cdp);
  const cleared = await waitForPackagedClear();
  if (quit.accepted && cleared.ok) {
    return {
      quit,
      trackedPid: rootPid,
      identityCheck,
      cdpOwnership,
      graceful: true,
      forced: false,
      finalSnapshot: cleared.snapshot,
    };
  }
  const beforeForce = cleared.snapshot;
  await forceCloseLaunch(launch);
  return {
    quit,
    trackedPid: rootPid,
    identityCheck,
    cdpOwnership,
    graceful: false,
    forced: true,
    beforeForce,
    finalSnapshot: await processSnapshot(),
  };
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
  throw new Error(`Timed out waiting for ${url}: ${String(lastError?.message || lastError || "unknown error")}`);
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
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith("VRCFORGE_")),
  );
  return {
    ...inherited,
    VRCFORGE_USER_DATA_DIR: userDataRoot,
    VRCFORGE_CONFIG_DIR: configRoot,
    VRCFORGE_CONFIG_PATH: resolve(configRoot, "config.json"),
    VRCFORGE_SETTINGS_PATH: resolve(configRoot, "settings.json"),
    VRCFORGE_LOG_DIR: resolve(userDataRoot, "logs"),
    VRCFORGE_ARTIFACTS_DIR: resolve(userDataRoot, "artifacts"),
    VRCFORGE_DESKTOP_EXECUTOR: "0",
    APPDATA: hostProfileRoot,
    LOCALAPPDATA: hostProfileRoot,
    WEBVIEW2_USER_DATA_FOLDER: webviewDataRoot,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:
      `--remote-debugging-port=${cdpPort} --remote-allow-origins=*`,
  };
}

async function proveLoopbackPortReleased(port) {
  if (!Number.isInteger(port) || port <= 0 || port > 65535) {
    return false;
  }
  const server = createServer();
  try {
    await new Promise((resolveListen, rejectListen) => {
      server.once("error", rejectListen);
      server.listen(port, "127.0.0.1", resolveListen);
    });
    return true;
  } catch {
    return false;
  } finally {
    if (server.listening) {
      await new Promise((resolveClose) => server.close(() => resolveClose()));
    }
  }
}

async function assertNoHostUnityProcesses() {
  const raw = await runPowerShell(`
    $count = @(Get-Process -Name Unity -ErrorAction SilentlyContinue).Count
    [pscustomobject]@{ count = $count } | ConvertTo-Json -Compress
  `);
  const count = Number((raw ? JSON.parse(raw) : {}).count || 0);
  if (count !== 0) {
    throw new Error(`environment-not-isolated: ${count} host Unity process(es) were running; nothing was terminated.`);
  }
  return true;
}

async function launchPackagedApp(requireComposerEnabled = true) {
  await assertNoHostUnityProcesses();
  appSessionToken = "";
  const child = spawn(exe, [], {
    detached: false,
    stdio: "ignore",
    env: isolatedLaunchEnvironment(),
  });
  const launch = { childPid: child.pid, launchedAt: new Date().toISOString(), identity: null, cdp: null };
  const spawnFailure = new Promise((_, rejectSpawn) => child.once("error", rejectSpawn));
  try {
    launch.identity = await Promise.race([
      captureLaunchIdentity(child.pid),
      spawnFailure,
    ]);
    launch.cdpListenerOwnership = await Promise.race([
      waitForOwnedCdpListener(launch),
      spawnFailure,
    ]);
    const targets = await Promise.race([
      waitForJson(`http://127.0.0.1:${cdpPort}/json/list`),
      spawnFailure,
    ]);
    const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
    if (!page) {
      throw new Error("Packaged WebView2 page target was not found.");
    }
    const connectOwnership = await inspectCdpListenerOwnership(launch);
    if (!cdpOwnershipAllowsAction(connectOwnership)) {
      throw new Error("Packaged CDP listener ownership changed before page connection; no connection is authorized.");
    }
    launch.cdpListenerOwnership = connectOwnership;
    const cdp = connectCdp(page.webSocketDebuggerUrl);
    launch.cdp = cdp;
    await cdp.opened;
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    const renderer = await waitForEval(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        return {
          ok: Boolean(document.body?.innerText?.length && window.__TAURI_INTERNALS__?.invoke &&
            (${requireComposerEnabled ? "textarea && !textarea.disabled" : "true"})),
          bodyLength: document.body?.innerText?.length || 0,
          composerDisabled: textarea?.disabled ?? null,
          tauriInvoke: typeof window.__TAURI_INTERNALS__?.invoke,
        };
      })()`,
    );
    const health = await waitForJson(`${appOrigin}/api/health`);
    return { ...launch, cdp, health, renderer };
  } catch (error) {
    try { launch.cdp?.close(); } catch { /* Renderer may not have connected. */ }
    const cleanup = await forceCloseLaunch(launch).catch((cleanupError) => ({
      error: String(cleanupError?.stack || cleanupError),
    }));
    if (cleanup?.error) {
      throw new Error(
        `Packaged launch failed and identity-bound cleanup was blocked: ${cleanup.error}; cause=${String(error?.message || error)}`,
        { cause: error },
      );
    }
    throw error;
  }
}

async function waitForComposer(cdp) {
  return waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      return { ok: Boolean(textarea && !textarea.disabled), disabled: textarea?.disabled ?? null };
    })()`,
  );
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
      const error = new Error(`${response.status} ${path}: ${JSON.stringify(payload)}`);
      error.status = response.status;
      error.path = path;
      error.payload = payload;
      throw error;
    }
    return payload;
  } finally {
    clearTimeout(timeout);
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

async function typeAndSubmit(cdp, text) {
  const typed = await evalValue(
    cdp,
    `(async () => {
      const textarea = document.querySelector("textarea");
      if (!textarea) return { ok: false, error: "textarea missing" };
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      textarea.focus();
      setter.call(textarea, ${JSON.stringify(text)});
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolveFrame) => requestAnimationFrame(resolveFrame));
      return { ok: textarea.value === ${JSON.stringify(text)}, value: textarea.value, disabled: textarea.disabled };
    })()`,
  );
  const submitted = await evalValue(
    cdp,
    `(async () => {
      const textarea = document.querySelector("textarea");
      const form = textarea?.closest("form");
      const submit = form?.querySelector("button[type='submit']");
      if (submit) submit.click(); else form?.requestSubmit();
      await new Promise((resolveFrame) => requestAnimationFrame(resolveFrame));
      return { ok: Boolean(submit || form), disabled: submit?.disabled ?? null };
    })()`,
  );
  if (!typed?.ok || !submitted?.ok) {
    throw new Error(`Packaged composer submit failed: ${JSON.stringify({ typed, submitted })}`);
  }
  return { typed, submitted };
}

function createFakeProvider() {
  const requests = [];
  let completionCount = 0;
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) {
      chunks.push(chunk);
    }
    const rawBody = Buffer.concat(chunks).toString("utf8");
    let body = {};
    try { body = rawBody ? JSON.parse(rawBody) : {}; } catch { body = {}; }
    const entry = {
      index: requests.length,
      method: request.method,
      url: request.url,
      stream: body.stream === true,
      model: body.model || "",
      body,
      replyText: "",
    };
    requests.push(entry);
    if (request.method === "GET" && request.url === "/v1/models") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ object: "list", data: [{ id: "vrcforge-subagent-probe", object: "model" }] }));
      return;
    }
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: { message: "not found" } }));
      return;
    }
    completionCount += 1;
    entry.replyText = `PACKAGED_SUBAGENT_PROVIDER_REPLY_${completionCount}_${marker}`;
    const content = JSON.stringify({
      action: "reply",
      summary: entry.replyText,
      reply: entry.replyText,
    });
    if (body.stream === true) {
      response.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      });
      response.write(`data: ${JSON.stringify({
        id: `chatcmpl-subagent-probe-${completionCount}`,
        object: "chat.completion.chunk",
        created: Math.floor(Date.now() / 1000),
        model: body.model || "vrcforge-subagent-probe",
        choices: [{ index: 0, delta: { role: "assistant", content }, finish_reason: null }],
      })}\n\n`);
      response.write(`data: ${JSON.stringify({
        id: `chatcmpl-subagent-probe-${completionCount}`,
        object: "chat.completion.chunk",
        created: Math.floor(Date.now() / 1000),
        model: body.model || "vrcforge-subagent-probe",
        choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
        usage: { prompt_tokens: 20, completion_tokens: 5, total_tokens: 25 },
      })}\n\n`);
      response.end("data: [DONE]\n\n");
      return;
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({
      id: `chatcmpl-subagent-probe-${completionCount}`,
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model: body.model || "vrcforge-subagent-probe",
      choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
      usage: { prompt_tokens: 20, completion_tokens: 5, total_tokens: 25 },
    }));
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
      return new Promise((resolveClose) => {
        server.closeAllConnections?.();
        server.close(resolveClose);
      });
    },
  };
}

function currentUserTurnContains(request, text) {
  const messages = Array.isArray(request?.body?.messages) ? request.body.messages : [];
  const currentUser = [...messages].reverse().find((message) => String(message?.role || "") === "user");
  return JSON.stringify(currentUser?.content ?? currentUser ?? "").includes(text);
}

function currentUserTurnMarkerCount(request, text) {
  const messages = Array.isArray(request?.body?.messages) ? request.body.messages : [];
  const currentUser = [...messages].reverse().find((message) => String(message?.role || "") === "user");
  return countOccurrences(JSON.stringify(currentUser?.content ?? currentUser ?? ""), text);
}

async function waitForProviderRequest(provider, text, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const matches = provider.chatRequests.filter((entry) => currentUserTurnContains(entry, text));
    if (matches.length > 1) {
      throw new Error(`Provider observed duplicate current-user turns containing ${text}.`);
    }
    if (matches.length === 1) {
      return matches[0];
    }
    await sleep(200);
  }
  throw new Error(`Provider request containing ${text} was not observed.`);
}

function providerHistoryEvidence(request) {
  const messages = Array.isArray(request?.body?.messages) ? request.body.messages : [];
  const serializedMessages = JSON.stringify(messages);
  return {
    requestIndex: request?.index ?? -1,
    model: request?.model || "",
    stream: request?.stream === true,
    messageCount: messages.length,
    messageRoles: messages.map((message) => String(message?.role || "")),
    adoptMarkerCount: countOccurrences(serializedMessages, adoptHistoryMarker),
    orphanMarkerCount: countOccurrences(serializedMessages, orphanHistoryMarker),
    dismissMarkerCount: countOccurrences(serializedMessages, dismissHistoryMarker),
  };
}

function findChat(payload, chatId) {
  return (payload.chats || []).find((chat) => chat.id === chatId);
}

function findChatContaining(payload, text) {
  return (payload.chats || []).find((chat) => JSON.stringify(chat.items || []).includes(text));
}

function taskCards(chat, taskId) {
  return (chat?.items || []).filter((item) => item?.type === "subagent" && item?.task?.id === taskId);
}

function taskProjectionCount(payload, taskId) {
  return (payload.tasks || []).filter((task) => task.id === taskId).length;
}

function taskCardLocations(payload, taskId) {
  return (payload.chats || []).flatMap((chat) =>
    taskCards(chat, taskId).map((item) => ({ chatId: chat.id, itemId: item.id })),
  );
}

async function injectSyntheticOrphanTask({ taskId, parentChatId, parentSessionId }) {
  const createdAt = new Date().toISOString();
  const startedAt = new Date(Date.now() + 1).toISOString();
  const taskText = "Recover a synthetic result sidecar that became durable before its terminal lifecycle event.";
  const baseTask = {
    id: taskId,
    role: "selected_context_review",
    displayName: orphanDisplayName,
    task: taskText,
    parentChatId,
    parentSessionId,
    projectPath: "",
    toolProfile: "read-only",
    status: "queued",
    createdAt,
    startedAt: "",
    stoppedAt: "",
    updatedAt: createdAt,
    cancelRequested: false,
    summary: "",
    error: "",
    eventCount: 1,
    revision: 1,
    retryOf: "",
    handoffStatus: "",
    handoffAt: "",
    mergedAt: "",
    mergedChatId: "",
    mergeDecision: "",
    resultAvailable: false,
    resultUnavailable: false,
    params: { selectedText: orphanHistoryMarker, source: "packaged-synthetic-orphan" },
  };
  const runningTask = {
    ...baseTask,
    status: "running",
    startedAt,
    updatedAt: startedAt,
    eventCount: 2,
    revision: 2,
  };
  const rows = [
    {
      schema: "vrcforge.sub_agent_lifecycle.v2",
      timestamp: createdAt,
      taskId,
      event: "created",
      revision: 1,
      data: { role: "selected_context_review", task: taskText, retryOf: "" },
      task: baseTask,
    },
    {
      schema: "vrcforge.sub_agent_lifecycle.v2",
      timestamp: startedAt,
      taskId,
      event: "started",
      revision: 2,
      data: { role: "selected_context_review", displayName: orphanDisplayName },
      task: runningTask,
    },
  ];
  const summary = `Selected context opened in a sub-agent thread: ${orphanHistoryMarker.length} character(s).`;
  const result = {
    ok: true,
    schema: "vrcforge.sub_agent.selected_context_review.v1",
    role: "selected_context_review",
    readOnly: true,
    summaryText: summary,
    selectedTextPreview: orphanHistoryMarker,
    selectedTextCharacters: orphanHistoryMarker.length,
    proposedNextAction: "Use this recovered scoped result without duplicating it after restart.",
  };
  const resultPath = resolve(subAgentArtifactRoot, "results", `${taskId}.json`);
  await mkdir(resolve(subAgentArtifactRoot, "results"), { recursive: true });
  let separator = "";
  try {
    const existing = await readFile(subAgentEventLogPath, "utf8");
    separator = existing && !existing.endsWith("\n") ? "\n" : "";
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  await appendFile(
    subAgentEventLogPath,
    `${separator}${rows.map((row) => JSON.stringify(row)).join("\n")}\n`,
    "utf8",
  );
  await writeFile(
    resultPath,
    `${JSON.stringify({ schema: "vrcforge.sub_agent_result.v1", taskId, summary, result })}\n`,
    "utf8",
  );
  return {
    taskId,
    eventLogPath: subAgentEventLogPath,
    resultPath,
    seededEvents: rows.map((row) => row.event),
    seededRevision: runningTask.revision,
    resultMarker: orphanHistoryMarker,
  };
}

async function persistSiblingChat(parentChatId) {
  const siblingId = `chat-sibling-${shortMarker}`;
  const siblingSessionId = `session-sibling-${shortMarker}`;
  const siblingTitle = `Sibling-${shortMarker}`;
  const siblingSeed = `SIBLING_SEED_${marker}`;
  const timestamp = new Date().toISOString();
  const sibling = {
    id: siblingId,
    sessionId: siblingSessionId,
    title: siblingTitle,
    projectPath: "",
    createdAt: timestamp,
    updatedAt: timestamp,
    items: [{ id: `user-${shortMarker}`, type: "user", text: siblingSeed, createdAt: timestamp }],
  };
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const payload = await appApi("/api/app/chats");
    const parent = findChat(payload, parentChatId);
    if (!parent) {
      throw new Error("Parent chat was unavailable while creating the cross-chat isolation fixture.");
    }
    const chats = (payload.chats || []).filter((chat) => chat.id !== siblingId);
    try {
      await appApi("/api/app/chats", {
        method: "POST",
        body: { chats: [...chats, sibling], sourceRevisions: payload.sources || [] },
      });
      break;
    } catch (error) {
      if (attempt === 0 && isChatStoreSnapshotChanged(error)) {
        continue;
      }
      throw error;
    }
  }
  const readback = await appApi("/api/app/chats");
  const stored = findChat(readback, siblingId);
  if (!stored || JSON.stringify(stored.items || []).includes(adoptHistoryMarker) ||
      JSON.stringify(stored.items || []).includes(orphanHistoryMarker) ||
      JSON.stringify(stored.items || []).includes(dismissHistoryMarker)) {
    throw new Error("Cross-chat isolation fixture was not stored cleanly.");
  }
  return { id: siblingId, sessionId: siblingSessionId, title: siblingTitle, seed: siblingSeed };
}

async function waitForSavedChatContaining(text, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    latest = await appApi("/api/app/chats");
    const chat = findChatContaining(latest, text);
    if (chat) {
      return { payload: latest, chat };
    }
    await sleep(250);
  }
  throw new Error(`Saved chat containing ${text} was not observed: ${JSON.stringify(latest)}`);
}

async function waitForTask(taskId, predicate, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    latest = await appApi(`/api/app/sub-agents/${encodeURIComponent(taskId)}`);
    if (latest?.task && predicate(latest.task)) {
      return latest.task;
    }
    await sleep(250);
  }
  throw new Error(`Sub-agent task ${taskId} did not reach the expected state: ${JSON.stringify(latest)}`);
}

async function waitForMaterializedCard(chatId, taskId, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    const [taskPayload, chatsPayload] = await Promise.all([
      appApi(`/api/app/sub-agents/${encodeURIComponent(taskId)}`),
      appApi("/api/app/chats"),
    ]);
    const task = taskPayload.task;
    const chat = findChat(chatsPayload, chatId);
    const cards = taskCards(chat, taskId);
    latest = { task, cardCount: cards.length, card: cards[0] || null };
    if (
      task?.status === "completed" &&
      task?.handoffStatus === "materialized" &&
      cards.length === 1 &&
      cards[0]?.id === `subagent-${taskId}` &&
      cards[0]?.task?.revision === task.revision
    ) {
      return latest;
    }
    await sleep(250);
  }
  throw new Error(`Sub-agent card ${taskId} was not materialized by WebView: ${JSON.stringify(latest)}`);
}

async function waitForMergedCard(chatId, taskId, decision, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    const [taskPayload, chatsPayload] = await Promise.all([
      appApi(`/api/app/sub-agents/${encodeURIComponent(taskId)}`),
      appApi("/api/app/chats"),
    ]);
    const task = taskPayload.task;
    const chat = findChat(chatsPayload, chatId);
    const cards = taskCards(chat, taskId);
    latest = { task, cardCount: cards.length, card: cards[0] || null };
    if (
      task?.mergeDecision === decision &&
      task?.handoffStatus === decision &&
      cards.length === 1 &&
      cards[0]?.task?.mergeDecision === decision &&
      cards[0]?.task?.revision === task.revision
    ) {
      return latest;
    }
    await sleep(250);
  }
  throw new Error(`Sub-agent card ${taskId} did not persist ${decision}: ${JSON.stringify(latest)}`);
}

async function selectChatAndInspect(cdp, title, expectedTexts) {
  await waitForEval(
    cdp,
    `(() => {
      const title = ${JSON.stringify(title)};
      const button = Array.from(document.querySelectorAll("button")).find((candidate) =>
        Array.from(candidate.querySelectorAll("span")).some((span) => span.textContent?.trim() === title));
      if (!button) return { ok: false, reason: "chat button missing" };
      button.click();
      return { ok: true };
    })()`,
  );
  await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const workspace = textarea?.closest("section");
      const text = workspace?.innerText || "";
      const expected = ${JSON.stringify(expectedTexts)};
      return { ok: Boolean(workspace && expected.every((item) => text.includes(item))), textLength: text.length };
    })()`,
  );
  return evalValue(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const workspace = textarea?.closest("section");
      const text = workspace?.innerText || "";
      const count = (needle) => needle ? text.split(needle).length - 1 : 0;
      return {
        title: ${JSON.stringify(title)},
        workspaceTextLength: text.length,
        adoptDisplayCount: count(${JSON.stringify(adoptDisplayName)}),
        dismissDisplayCount: count(${JSON.stringify(dismissDisplayName)}),
        orphanDisplayCount: count(${JSON.stringify(orphanDisplayName)}),
        adoptResultMarkerCount: count(${JSON.stringify(adoptHistoryMarker)}),
        dismissResultMarkerCount: count(${JSON.stringify(dismissHistoryMarker)}),
        orphanResultMarkerCount: count(${JSON.stringify(orphanHistoryMarker)}),
      };
    })()`,
  );
}

function compactTask(task) {
  return task ? {
    id: task.id,
    role: task.role,
    displayName: task.displayName,
    parentChatId: task.parentChatId,
    parentSessionId: task.parentSessionId,
    projectPath: task.projectPath,
    status: task.status,
    revision: task.revision,
    handoffStatus: task.handoffStatus,
    mergeDecision: task.mergeDecision,
    mergedChatId: task.mergedChatId,
    resultAvailable: task.resultAvailable,
    resultReadOnly: task.result?.readOnly,
    selectedTextPreview: task.result?.selectedTextPreview,
  } : null;
}

function compactChat(chat, taskIds) {
  return chat ? {
    id: chat.id,
    sessionId: chat.sessionId,
    title: chat.title,
    projectPath: chat.projectPath,
    itemCount: (chat.items || []).length,
    itemIds: (chat.items || []).map((item) => item.id),
    cards: taskIds.flatMap((taskId) => taskCards(chat, taskId).map((item) => ({
      itemId: item.id,
      taskId,
      revision: item.task?.revision,
      handoffStatus: item.task?.handoffStatus,
      mergeDecision: item.task?.mergeDecision,
    }))),
  } : null;
}

function validateHistoryEvidence(report, label, evidence, expected = {}) {
  const expectedAdopt = expected.adopt ?? 1;
  const expectedOrphan = expected.orphan ?? 1;
  const expectedDismiss = expected.dismiss ?? 0;
  if (evidence.adoptMarkerCount !== expectedAdopt) {
    addAssertion(report, `${label}: adopted sub-agent history marker count was ${evidence.adoptMarkerCount}, expected ${expectedAdopt}`);
  }
  if (evidence.orphanMarkerCount !== expectedOrphan) {
    addAssertion(report, `${label}: recovered adopted history marker count was ${evidence.orphanMarkerCount}, expected ${expectedOrphan}`);
  }
  if (evidence.dismissMarkerCount !== expectedDismiss) {
    addAssertion(report, `${label}: dismissed sub-agent history marker count was ${evidence.dismissMarkerCount}, expected ${expectedDismiss}`);
  }
}

function validateTaskEvents(report, label, task, decision, terminalEvent = "completed") {
  const names = (task?.events || []).map((event) => event.event);
  const expectedOnce = ["created", "started", terminalEvent, "handoff_materialized", "merged"];
  const counts = Object.fromEntries(expectedOnce.map((name) => [name, names.filter((item) => item === name).length]));
  for (const [name, count] of Object.entries(counts)) {
    if (count !== 1) {
      addAssertion(report, `${label}: lifecycle event ${name} occurred ${count} time(s), expected 1`);
    }
  }
  if (task?.mergeDecision !== decision || task?.handoffStatus !== decision) {
    addAssertion(report, `${label}: final merge/handoff decision was not durably ${decision}`);
  }
  if (terminalEvent === "recovered" && names.includes("completed")) {
    addAssertion(report, `${label}: synthetic orphan unexpectedly contained a completed lifecycle event`);
  }
  return { counts, eventNames: names };
}

async function closeForRestart(report, app, label) {
  const result = await closePackagedApp(app);
  try { app?.cdp?.close(); } catch { /* WebView may already be gone. */ }
  report.closeouts.push({ label, ...result });
  if (result?.quit?.accepted !== true || result?.graceful !== true || result?.forced === true) {
    addAssertion(report, `${label}: packaged app did not complete an accepted explicit Quit`);
  }
}

async function main() {
  await mkdir(evidenceRoot, { recursive: true });
  await mkdir(hostProfileRoot, { recursive: true });
  const report = {
    schema: "vrcforge.packaged_subagent_handoff_probe.v1",
    marker,
    mode: allowUnpushed ? "local-preacceptance" : "strict-release",
    strictReleaseBinding: false,
    releaseEligible: false,
    releaseEvidence: allowUnpushed
      ? "non-release local-preacceptance only"
      : "strict release binding pending completion",
    exe,
    userDataRoot,
    webviewDataRoot,
    reportPath,
    coverage: {
      packagedBackend: true,
      packagedWebView: true,
      createTransport: "packaged Tauri create_sub_agent IPC",
      handoffTransport: "WebView stable-card save followed by packaged Tauri handoff acknowledgement",
      mergeTransport: "revision-checked packaged Tauri merge_sub_agent IPC",
      historyTransport: "actual packaged composer to isolated OpenAI-compatible provider",
      pixelMergeButtonsClicked: false,
      boundary: "Adopt/Dismiss use the same packaged Tauri product commands as the UI, but avoid localized pixel/button selection. Card materialization, renderer restore, composer submission, and history construction all run in the real packaged WebView.",
    },
    closeouts: [],
    assertions: [],
  };
  let provider;
  let providerPort = 0;
  let app;
  try {
    if (!Number.isInteger(cdpPort) || cdpPort < 1024 || cdpPort > 65535 || cdpPort === 8757) {
      throw new Error(`Invalid VRCFORGE_SUBAGENT_PROBE_CDP_PORT: ${process.env.VRCFORGE_SUBAGENT_PROBE_CDP_PORT || cdpPort}`);
    }
    const sourceVersion = (await readFile(resolve(repoRoot, "VERSION"), "utf8")).trim();
    const releaseBinding = await prepareManifestBoundPackage(sourceVersion);
    report.strictReleaseBinding = releaseBinding.strictReleaseBinding === true;
    report.releaseEligible = releaseBinding.buildPolicy.releaseEligible === true;
    report.releaseBinding = {
      strict: report.strictReleaseBinding,
      manifestCommit: releaseBinding.commit,
      headCommit: releaseBinding.headCommit,
      originMainCommit: releaseBinding.originMainCommit,
      worktreeClean: releaseBinding.worktreeClean,
      buildPolicy: releaseBinding.buildPolicy,
      strictBuildPolicy: releaseBinding.strictBuildPolicy,
      localAcceptanceBuildPolicy: releaseBinding.localAcceptanceBuildPolicy,
      portableSha256: releaseBinding.portableSha256,
      extractedExeSha256: releaseBinding.exeSha256,
    };
    if (allowUnpushed && (report.strictReleaseBinding || report.releaseEligible)) {
      addAssertion(report, "allow-unpushed mode was incorrectly marked as strict or release-eligible evidence");
    }
    if (!allowUnpushed && (!report.strictReleaseBinding || !report.releaseEligible)) {
      addAssertion(report, "strict mode did not retain strict release binding and release eligibility");
    }
    const packageStat = await stat(exe);
    report.package = {
      sourceVersion,
      size: packageStat.size,
      modifiedAt: packageStat.mtime.toISOString(),
      exeSha256: releaseBinding.exeSha256,
      releaseBinding,
    };
    report.initialSnapshot = await processSnapshot();
    if (!snapshotIsClear(report.initialSnapshot)) {
      throw new Error(`Preflight found an existing packaged instance or occupied probe port; nothing was terminated: ${JSON.stringify(report.initialSnapshot)}`);
    }
    report.hostUnityIsolation = await assertNoHostUnityProcesses();

    provider = createFakeProvider();
    providerPort = await provider.listen();
    report.provider = { port: providerPort };

    app = await launchPackagedApp(false);
    report.package.firstHealth = app.health;
    report.package.firstRenderer = app.renderer;
    report.package.firstAuthenticatedHealth = await assertIsolatedRuntime(sourceVersion, "initial launch");
    const configured = await appApi("/api/config", {
      method: "POST",
      body: {
        provider: "custom",
        api_key: "isolated-subagent-probe-key",
        base_url: `http://127.0.0.1:${providerPort}/v1`,
        model: "vrcforge-subagent-probe",
      },
    });
    report.config = {
      provider: configured.apiConfig?.provider,
      model: configured.apiConfig?.model,
      isolatedBaseUrlConfigured: Boolean(configured.apiConfig?.base_url || configured.apiConfig?.baseUrl),
    };
    await app.cdp.send("Page.reload", { ignoreCache: true });
    await waitForComposer(app.cdp);

    const seedPrompt = shortMarker;
    report.seedSubmit = await typeAndSubmit(app.cdp, seedPrompt);
    const seedProviderRequest = await waitForProviderRequest(provider, seedPrompt);
    const savedSeed = await waitForSavedChatContaining(seedProviderRequest.replyText);
    const parentChatId = savedSeed.chat.id;
    const parentSessionId = savedSeed.chat.sessionId || "";
    report.owner = {
      chatId: parentChatId,
      sessionId: parentSessionId,
      title: savedSeed.chat.title,
      projectPath: savedSeed.chat.projectPath || "",
      seedProviderRequestIndex: seedProviderRequest.index,
    };
    if (savedSeed.chat.projectPath) {
      addAssertion(report, "probe owner chat was not temporary/projectless");
    }

    const adoptCreated = await tauriInvoke(app.cdp, "create_sub_agent", {
      request: {
        body: {
          role: "selected_context_review",
          task: "Review the adopted-history probe payload in a read-only sub-agent.",
          displayName: adoptDisplayName,
          parentChatId,
          parentSessionId,
          projectPath: "",
          params: { selectedText: adoptHistoryMarker, source: "packaged-subagent-handoff-probe" },
        },
        timeoutMs: 60000,
      },
    });
    const dismissCreated = await tauriInvoke(app.cdp, "create_sub_agent", {
      request: {
        body: {
          role: "selected_context_review",
          task: "Review the dismissed-history probe payload in a read-only sub-agent.",
          displayName: dismissDisplayName,
          parentChatId,
          parentSessionId,
          projectPath: "",
          params: { selectedText: dismissHistoryMarker, source: "packaged-subagent-handoff-probe" },
        },
        timeoutMs: 60000,
      },
    });
    const adoptTaskId = adoptCreated.task.id;
    const dismissTaskId = dismissCreated.task.id;
    const orphanTaskId = `sub_orphan_${shortMarker}`;
    const initialTaskIds = [adoptTaskId, dismissTaskId];
    const taskIds = [adoptTaskId, dismissTaskId, orphanTaskId];
    report.created = {
      adopt: compactTask(adoptCreated.task),
      dismiss: compactTask(dismissCreated.task),
    };

    const [adoptMaterialized, dismissMaterialized] = await Promise.all([
      waitForMaterializedCard(parentChatId, adoptTaskId),
      waitForMaterializedCard(parentChatId, dismissTaskId),
    ]);
    const materializedChats = await appApi("/api/app/chats");
    const materializedList = await appApi("/api/app/sub-agents?includeEvents=true&limit=100");
    report.afterMaterialization = {
      adopt: compactTask(adoptMaterialized.task),
      dismiss: compactTask(dismissMaterialized.task),
      chat: compactChat(findChat(materializedChats, parentChatId), initialTaskIds),
      taskProjectionCounts: {
        adopt: taskProjectionCount(materializedList, adoptTaskId),
        dismiss: taskProjectionCount(materializedList, dismissTaskId),
      },
      cardLocations: {
        adopt: taskCardLocations(materializedChats, adoptTaskId),
        dismiss: taskCardLocations(materializedChats, dismissTaskId),
      },
    };
    for (const [label, materialized, expectedMarker] of [
      ["adopt", adoptMaterialized, adoptHistoryMarker],
      ["dismiss", dismissMaterialized, dismissHistoryMarker],
    ]) {
      if (materialized.task.parentChatId !== parentChatId) {
        addAssertion(report, `${label} task lost its original parent chat`);
      }
      if (materialized.task.result?.readOnly !== true || materialized.task.result?.selectedTextPreview !== expectedMarker) {
        addAssertion(report, `${label} task did not produce the deterministic read-only result`);
      }
    }

    await closeForRestart(report, app, "after-materialization");
    app = null;
    report.syntheticOrphan = await injectSyntheticOrphanTask({
      taskId: orphanTaskId,
      parentChatId,
      parentSessionId,
    });
    app = await launchPackagedApp();
    report.package.recoveryAuthenticatedHealth = await assertIsolatedRuntime(sourceVersion, "orphan recovery restart");
    const orphanMaterialized = await waitForMaterializedCard(parentChatId, orphanTaskId);
    const firstRestartList = await appApi("/api/app/sub-agents?includeEvents=true&limit=100");
    const firstRestartChats = await appApi("/api/app/chats");
    const firstRestartDom = await selectChatAndInspect(app.cdp, savedSeed.chat.title, [
      adoptDisplayName,
      dismissDisplayName,
      adoptHistoryMarker,
      dismissHistoryMarker,
      orphanDisplayName,
      orphanHistoryMarker,
    ]);
    report.afterMaterializationRestart = {
      taskProjectionCounts: {
        adopt: taskProjectionCount(firstRestartList, adoptTaskId),
        dismiss: taskProjectionCount(firstRestartList, dismissTaskId),
        orphan: taskProjectionCount(firstRestartList, orphanTaskId),
      },
      cardLocations: {
        adopt: taskCardLocations(firstRestartChats, adoptTaskId),
        dismiss: taskCardLocations(firstRestartChats, dismissTaskId),
        orphan: taskCardLocations(firstRestartChats, orphanTaskId),
      },
      chat: compactChat(findChat(firstRestartChats, parentChatId), taskIds),
      renderer: firstRestartDom,
      recovered: compactTask(orphanMaterialized.task),
    };
    if (
      orphanMaterialized.task.status !== "completed" ||
      orphanMaterialized.task.parentChatId !== parentChatId ||
      orphanMaterialized.task.result?.selectedTextPreview !== orphanHistoryMarker
    ) {
      addAssertion(report, "synthetic orphan did not recover exactly into its original chat with the valid sidecar result");
    }

    const latestAdopt = await waitForTask(adoptTaskId, (task) => task.handoffStatus === "materialized");
    const latestDismiss = await waitForTask(dismissTaskId, (task) => task.handoffStatus === "materialized");
    const latestOrphan = await waitForTask(orphanTaskId, (task) => task.handoffStatus === "materialized");
    await tauriInvoke(app.cdp, "merge_sub_agent", {
      request: {
        id: adoptTaskId,
        body: { decision: "adopted", chatId: parentChatId, expectedRevision: latestAdopt.revision },
        timeoutMs: 30000,
      },
    });
    await tauriInvoke(app.cdp, "merge_sub_agent", {
      request: {
        id: dismissTaskId,
        body: { decision: "dismissed", chatId: parentChatId, expectedRevision: latestDismiss.revision },
        timeoutMs: 30000,
      },
    });
    await tauriInvoke(app.cdp, "merge_sub_agent", {
      request: {
        id: orphanTaskId,
        body: { decision: "adopted", chatId: parentChatId, expectedRevision: latestOrphan.revision },
        timeoutMs: 30000,
      },
    });
    const [adoptMerged, dismissMerged, orphanMerged] = await Promise.all([
      waitForMergedCard(parentChatId, adoptTaskId, "adopted"),
      waitForMergedCard(parentChatId, dismissTaskId, "dismissed"),
      waitForMergedCard(parentChatId, orphanTaskId, "adopted"),
    ]);
    report.afterDecisions = {
      adopt: compactTask(adoptMerged.task),
      dismiss: compactTask(dismissMerged.task),
      orphan: compactTask(orphanMerged.task),
      chat: compactChat(findChat(await appApi("/api/app/chats"), parentChatId), taskIds),
      renderer: await selectChatAndInspect(app.cdp, savedSeed.chat.title, [adoptDisplayName, dismissDisplayName, orphanDisplayName]),
    };
    if (
      adoptMerged.task.mergedChatId !== parentChatId ||
      dismissMerged.task.mergedChatId !== parentChatId ||
      orphanMerged.task.mergedChatId !== parentChatId
    ) {
      addAssertion(report, "merge decisions were not bound to the immutable original chat");
    }

    const followupOne = `FOLLOWUP_ONE_${marker}`;
    report.followupOneSubmit = await typeAndSubmit(app.cdp, followupOne);
    const followupOneRequest = await waitForProviderRequest(provider, followupOne);
    const followupOneEvidence = providerHistoryEvidence(followupOneRequest);
    validateHistoryEvidence(report, "first post-merge turn", followupOneEvidence);
    const savedFollowupOne = await waitForSavedChatContaining(followupOneRequest.replyText);
    if (savedFollowupOne.chat.id !== parentChatId) {
      addAssertion(report, "first adopted-history reply was persisted outside the original parent chat");
    }
    report.firstHistoryProof = followupOneEvidence;
    const sibling = await persistSiblingChat(parentChatId);
    report.crossChat = { sibling };

    const requestCountBeforeFinalRestart = provider.chatRequests.length;
    await closeForRestart(report, app, "after-merge-history-proof");
    app = null;
    app = await launchPackagedApp();
    report.package.decisionRestartAuthenticatedHealth = await assertIsolatedRuntime(sourceVersion, "decision restart");
    await sleep(5000);
    if (provider.chatRequests.length !== requestCountBeforeFinalRestart) {
      addAssertion(report, "sub-agent restart triggered an unexpected provider request");
    }
    const finalList = await appApi("/api/app/sub-agents?includeEvents=true&limit=100");
    const finalChatsBeforeTurn = await appApi("/api/app/chats");
    const finalDomBeforeTurn = await selectChatAndInspect(app.cdp, savedSeed.chat.title, [
      adoptDisplayName,
      dismissDisplayName,
      orphanDisplayName,
      adoptHistoryMarker,
      dismissHistoryMarker,
      orphanHistoryMarker,
    ]);
    report.afterDecisionRestart = {
      taskProjectionCounts: {
        adopt: taskProjectionCount(finalList, adoptTaskId),
        dismiss: taskProjectionCount(finalList, dismissTaskId),
        orphan: taskProjectionCount(finalList, orphanTaskId),
      },
      cardLocations: {
        adopt: taskCardLocations(finalChatsBeforeTurn, adoptTaskId),
        dismiss: taskCardLocations(finalChatsBeforeTurn, dismissTaskId),
        orphan: taskCardLocations(finalChatsBeforeTurn, orphanTaskId),
      },
      chat: compactChat(findChat(finalChatsBeforeTurn, parentChatId), taskIds),
      renderer: finalDomBeforeTurn,
      providerRequestCountBeforeRestart: requestCountBeforeFinalRestart,
      providerRequestCountAfterIdle: provider.chatRequests.length,
    };

    const siblingDom = await selectChatAndInspect(app.cdp, sibling.title, [sibling.seed]);
    const crossChatFollowup = `CROSS_CHAT_${marker}`;
    report.crossChat.submit = await typeAndSubmit(app.cdp, crossChatFollowup);
    const crossChatRequest = await waitForProviderRequest(provider, crossChatFollowup);
    const crossChatEvidence = providerHistoryEvidence(crossChatRequest);
    validateHistoryEvidence(report, "cross-chat isolation turn", crossChatEvidence, { adopt: 0, orphan: 0, dismiss: 0 });
    const savedCrossChat = await waitForSavedChatContaining(crossChatRequest.replyText);
    if (savedCrossChat.chat.id !== sibling.id) {
      addAssertion(report, "cross-chat isolation reply was persisted outside the sibling chat");
    }
    report.crossChat.renderer = siblingDom;
    report.crossChat.historyProof = crossChatEvidence;

    await selectChatAndInspect(app.cdp, savedSeed.chat.title, [adoptDisplayName, dismissDisplayName, orphanDisplayName]);
    const followupTwo = `FOLLOWUP_TWO_${marker}`;
    report.followupTwoSubmit = await typeAndSubmit(app.cdp, followupTwo);
    const followupTwoRequest = await waitForProviderRequest(provider, followupTwo);
    const followupTwoEvidence = providerHistoryEvidence(followupTwoRequest);
    validateHistoryEvidence(report, "post-restart turn", followupTwoEvidence);
    const savedFollowupTwo = await waitForSavedChatContaining(followupTwoRequest.replyText);
    if (savedFollowupTwo.chat.id !== parentChatId) {
      addAssertion(report, "post-restart adopted-history reply was persisted outside the original parent chat");
    }
    report.secondHistoryProof = followupTwoEvidence;

    const [finalAdoptPayload, finalDismissPayload, finalOrphanPayload, finalChats, finalTaskList] = await Promise.all([
      appApi(`/api/app/sub-agents/${encodeURIComponent(adoptTaskId)}`),
      appApi(`/api/app/sub-agents/${encodeURIComponent(dismissTaskId)}`),
      appApi(`/api/app/sub-agents/${encodeURIComponent(orphanTaskId)}`),
      appApi("/api/app/chats"),
      appApi("/api/app/sub-agents?includeEvents=true&limit=100"),
    ]);
    report.final = {
      adopt: compactTask(finalAdoptPayload.task),
      dismiss: compactTask(finalDismissPayload.task),
      orphan: compactTask(finalOrphanPayload.task),
      chat: compactChat(findChat(finalChats, parentChatId), taskIds),
      taskProjectionCounts: {
        adopt: taskProjectionCount(finalTaskList, adoptTaskId),
        dismiss: taskProjectionCount(finalTaskList, dismissTaskId),
        orphan: taskProjectionCount(finalTaskList, orphanTaskId),
      },
      cardLocations: {
        adopt: taskCardLocations(finalChats, adoptTaskId),
        dismiss: taskCardLocations(finalChats, dismissTaskId),
        orphan: taskCardLocations(finalChats, orphanTaskId),
      },
      lifecycle: {
        adopt: validateTaskEvents(report, "adopt task", finalAdoptPayload.task, "adopted"),
        dismiss: validateTaskEvents(report, "dismiss task", finalDismissPayload.task, "dismissed"),
        orphan: validateTaskEvents(report, "synthetic orphan task", finalOrphanPayload.task, "adopted", "recovered"),
      },
    };

    for (const phase of [report.afterMaterializationRestart, report.afterDecisionRestart, report.final]) {
      if (phase.taskProjectionCounts.adopt !== 1 || phase.taskProjectionCounts.dismiss !== 1 || phase.taskProjectionCounts.orphan !== 1) {
        addAssertion(report, "a task projection was missing or duplicated across restart");
      }
      if (phase.cardLocations.adopt.length !== 1 || phase.cardLocations.dismiss.length !== 1 || phase.cardLocations.orphan.length !== 1) {
        addAssertion(report, "a stable result card was missing or duplicated across chats");
      }
      if (
        phase.cardLocations.adopt[0]?.chatId !== parentChatId ||
        phase.cardLocations.dismiss[0]?.chatId !== parentChatId ||
        phase.cardLocations.orphan[0]?.chatId !== parentChatId
      ) {
        addAssertion(report, "a stable result card moved away from its original chat");
      }
      for (const [taskId, location] of [
        [adoptTaskId, phase.cardLocations.adopt[0]],
        [dismissTaskId, phase.cardLocations.dismiss[0]],
        [orphanTaskId, phase.cardLocations.orphan[0]],
      ]) {
        if (location?.itemId !== `subagent-${taskId}`) {
          addAssertion(report, `stable result card id changed for ${taskId}`);
        }
      }
    }
    for (const [taskId, location] of [
      [adoptTaskId, report.afterMaterialization.cardLocations.adopt[0]],
      [dismissTaskId, report.afterMaterialization.cardLocations.dismiss[0]],
    ]) {
      if (location?.chatId !== parentChatId || location?.itemId !== `subagent-${taskId}`) {
        addAssertion(report, `initial stable result card identity was invalid for ${taskId}`);
      }
    }
    for (const [label, dom] of [
      ["materialization restart", firstRestartDom],
      ["decision restart", finalDomBeforeTurn],
    ]) {
      if (
        dom.adoptDisplayCount !== 1 ||
        dom.dismissDisplayCount !== 1 ||
        dom.orphanDisplayCount !== 1 ||
        dom.adoptResultMarkerCount !== 1 ||
        dom.dismissResultMarkerCount !== 1 ||
        dom.orphanResultMarkerCount !== 1
      ) {
        addAssertion(report, `${label}: packaged chat renderer did not show exactly one copy of each stable card`);
      }
    }
    report.provider.requests = provider.chatRequests.map((request) => ({
      index: request.index,
      model: request.model,
      stream: request.stream,
      messageCount: Array.isArray(request.body?.messages) ? request.body.messages.length : null,
      replyText: request.replyText,
    }));
    const expectedProviderTurns = {
      seed: seedPrompt,
      ownerFollowupOne: followupOne,
      siblingCrossChat: crossChatFollowup,
      ownerFollowupTwo: followupTwo,
    };
    report.provider.expectedTurnCount = Object.keys(expectedProviderTurns).length;
    report.provider.actualTurnCount = provider.chatRequests.length;
    const observedProviderTurns = {
      seed: seedProviderRequest,
      ownerFollowupOne: followupOneRequest,
      siblingCrossChat: crossChatRequest,
      ownerFollowupTwo: followupTwoRequest,
    };
    report.provider.turnMatches = Object.fromEntries(
      Object.entries(expectedProviderTurns).map(([name, text]) => {
        const request = observedProviderTurns[name];
        return [name, {
          requestIndex: request?.index ?? -1,
          chatOrdinal: provider.chatRequests.indexOf(request),
          currentUserMarkerCount: currentUserTurnMarkerCount(request, text),
        }];
      }),
    );
    if (provider.chatRequests.length !== report.provider.expectedTurnCount) {
      addAssertion(
        report,
        `provider observed ${provider.chatRequests.length} chat requests instead of ${report.provider.expectedTurnCount}`,
      );
    }
    const observedOrdinals = Object.values(report.provider.turnMatches).map((match) => match.chatOrdinal);
    if (new Set(observedOrdinals).size !== report.provider.expectedTurnCount || observedOrdinals.some((value) => value < 0)) {
      addAssertion(report, "provider turn observations did not map one-to-one onto the four expected chat requests");
    }
    for (const [name, match] of Object.entries(report.provider.turnMatches)) {
      if (match.currentUserMarkerCount !== 1) {
        addAssertion(report, `provider current-user marker ${name} occurred ${match.currentUserMarkerCount} time(s) instead of 1 in its observed request`);
      }
    }
  } catch (error) {
    report.error = String(error?.stack || error);
    addAssertion(report, `probe execution error: ${String(error?.message || error)}`);
  } finally {
    if (app) {
      try {
        await closeForRestart(report, app, "final-close");
        app = null;
      } catch (error) {
        addAssertion(report, `final graceful close failed: ${String(error?.message || error)}`);
        await forceCloseLaunch(app).catch((cleanupError) => {
          addAssertion(report, `final scoped cleanup failed: ${String(cleanupError?.message || cleanupError)}`);
        });
      }
    }
    if (provider) {
      await provider.close().catch((error) => {
        addAssertion(report, `provider cleanup failed: ${String(error?.message || error)}`);
      });
      const portReleased = providerPort > 0
        ? await proveLoopbackPortReleased(providerPort)
        : true;
      if (report.provider) {
        report.provider.portReleased = portReleased;
      }
      if (!portReleased) {
        addAssertion(report, "isolated fake-provider loopback port remained occupied after cleanup");
      }
    }
    try {
      report.finalSnapshot = await processSnapshot();
      if (!snapshotIsClear(report.finalSnapshot)) {
        addAssertion(report, "packaged processes or ports 8757/CDP remained after final cleanup");
      }
    } catch (error) {
      addAssertion(report, `final process snapshot failed: ${String(error?.message || error)}`);
    }
    report.ok = report.assertions.length === 0;
    report.completedAt = new Date().toISOString();
    await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  }

  console.log(reportPath);
  if (report.assertions.length > 0) {
    console.error(`Packaged sub-agent handoff probe failed: ${report.assertions.join("; ")}`);
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
