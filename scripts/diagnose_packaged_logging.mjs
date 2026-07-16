import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants as fsConstants, createReadStream } from "node:fs";
import {
  access,
  copyFile,
  mkdir,
  open,
  readFile,
  readdir,
  realpath,
  stat,
  unlink,
  utimes,
  writeFile,
} from "node:fs/promises";
import { basename, dirname, isAbsolute, relative, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const EXPECTED_VERSION = "1.3.0";
const allowUnpushed = process.argv.includes("--allow-unpushed");
const selfTest = process.argv.includes("--self-test");
const cdpPort = Number(process.env.VRCFORGE_LOGGING_PROBE_CDP_PORT || "9354");
const evidenceId = `LOGGING_PROBE_${Date.now()}_${randomBytes(4).toString("hex")}`;
const evidenceRoot = resolve(repoRoot, "artifacts", "actual-app-logging", evidenceId);
const packagedRoot = resolve(evidenceRoot, "package");
const exe = resolve(packagedRoot, "VRCForge.exe");
const backendExe = resolve(packagedRoot, "backend", "vrcforge_backend.exe");
const userDataRoot = resolve(evidenceRoot, "user-data");
const configRoot = resolve(userDataRoot, "config");
const logRoot = resolve(userDataRoot, "logs");
const artifactRoot = resolve(userDataRoot, "artifacts");
const webviewDataRoot = resolve(evidenceRoot, "webview2-user-data");
const reportPath = resolve(evidenceRoot, "report.json");
const appOrigin = "http://127.0.0.1:8757";
const appRequestOrigin = "http://tauri.localhost";
const privateMappingName = `${["diagnostic", "identities"].join("-")}.json`;
const privateKeyName = `${["diagnostic", "alias"].join("-")}.key`;
const canonicalLogName = /^vrcforge_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_\d+\.log$/;
const physicalLogLine = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}) \[(ERROR|WARN|INFO|DEBUG|TRACE)\] \[((?:\\.|[^\]])*)\] (.*) \| data=(\{.*\})$/;
const legacyLogNames = ["backend_stdout.log", "backend_stderr.log", "dashboard.log", "interactions.jsonl"];
const requiredSupportMembers = [
  "metadata.json",
  "bootstrap.json",
  "doctor.json",
  "diagnostics.json",
  "diagnostic-log.txt",
  "agent-audit.json",
  "sub-agent-events.json",
  "sub-agent-tasks.json",
  "checkpoints.json",
];
const sensitiveFragments = new Set([privateMappingName, privateKeyName]);
const privateStoreFragments = new Set([privateMappingName, privateKeyName]);
const trackedProcesses = new Map();
let packageExecutableNames = [];
let appSessionToken = "";
const powershellCompressionPrelude = [
  "System.IO.Compression",
  "System.IO.Compression.FileSystem",
].map((assembly) => `Add-Type -AssemblyName ${assembly}`).join("\n");

const allowedOptions = new Set(["--allow-unpushed", "--self-test", "--help", "-h"]);
if (process.argv.slice(2).some((item) => !allowedOptions.has(item))) {
  console.error("Unknown packaged logging probe option.");
  process.exit(2);
}

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`Usage: node scripts/diagnose_packaged_logging.mjs [--allow-unpushed] [--self-test]

Runs the manifest-bound packaged logging and Developer Options acceptance.

Default mode requires a clean strict release binding: HEAD, origin/main, and
release-manifest.json commit must match. --allow-unpushed permits an explicitly
non-release local pre-acceptance while still requiring manifest commit == HEAD,
VERSION 1.3.0, and exact portable ZIP/payload hashes.

--self-test exercises pure provenance, log parser, privacy, report, timing, and
process-scope helpers. It does not read a package, reserve a port, or start VRCForge.

Optional environment:
  VRCFORGE_LOGGING_PROBE_CDP_PORT=<unused port> (default: ${cdpPort})`);
  process.exit(0);
}

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function addAssertion(report, message) {
  const safe = String(message || "packaged logging assertion failed");
  if (!report.assertions.includes(safe)) report.assertions.push(safe);
}

function escapePowerShellLiteral(value) {
  return String(value).replaceAll("'", "''");
}

function toBase64Json(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

function normalizedPath(value) {
  return String(value || "").replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase();
}

function processPathInScope(candidatePath, rootPath) {
  const candidate = normalizedPath(candidatePath);
  const root = normalizedPath(rootPath);
  return Boolean(candidate && root && candidate.startsWith(`${root}/`));
}

function challengeReady(elapsedMs) {
  return Number(elapsedMs) >= 5_000;
}

function strictBuildPolicyFromManifest(manifest) {
  const raw = manifest?.buildPolicy && typeof manifest.buildPolicy === "object"
    ? manifest.buildPolicy
    : {};
  const normalized = {
    mode: String(raw.mode || ""),
    releaseEligible: raw.releaseEligible === true,
    allowDirty: raw.allowDirty === true,
    allowUnpushed: raw.allowUnpushed === true,
    allowVersionMismatch: raw.allowVersionMismatch === true,
  };
  return {
    normalized,
    strict: normalized.mode === "strict"
      && normalized.releaseEligible === true
      && normalized.allowDirty === false
      && normalized.allowUnpushed === false
      && normalized.allowVersionMismatch === false,
  };
}

function releaseBindingDecision({
  localMode,
  sourceVersion,
  manifestVersion,
  headCommit,
  originMainCommit,
  manifestCommit,
  worktreeClean,
  strictBuildPolicy,
}) {
  const validCommits = [headCommit, originMainCommit, manifestCommit]
    .every((value) => /^[0-9a-f]{40}$/.test(String(value || "")));
  const baseValid = sourceVersion === EXPECTED_VERSION
    && manifestVersion === sourceVersion
    && validCommits
    && manifestCommit === headCommit;
  const strict = baseValid
    && localMode !== true
    && headCommit === originMainCommit
    && worktreeClean === true
    && strictBuildPolicy === true;
  return {
    valid: localMode === true ? baseValid : strict,
    strict,
  };
}

function selectManifestPortableArtifact(manifest, portableName) {
  if (
    !portableName
    || isAbsolute(portableName)
    || basename(portableName) !== portableName
    || portableName.includes("/")
    || portableName.includes("\\")
  ) {
    throw new Error("Portable artifact name was unsafe.");
  }
  const matches = (Array.isArray(manifest?.artifacts) ? manifest.artifacts : [])
    .filter((item) => item?.name === portableName);
  if (matches.length !== 1) throw new Error("Portable manifest artifact was not unique.");
  const artifact = matches[0];
  if (Object.hasOwn(artifact, "path") && artifact.path !== portableName) {
    throw new Error("Portable manifest path was not its exact basename.");
  }
  if (!/^[0-9a-f]{64}$/i.test(String(artifact.sha256 || ""))) {
    throw new Error("Portable manifest digest was invalid.");
  }
  return artifact;
}

function parseLogLine(line) {
  const match = physicalLogLine.exec(String(line).replace(/\r?\n$/, ""));
  if (!match) return null;
  const timestamp = Date.parse(match[1].replace(" ", "T"));
  if (!Number.isFinite(timestamp)) return null;
  let data;
  try {
    data = JSON.parse(match[5]);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  return {
    timestamp: match[1],
    level: match[2].toLowerCase(),
    scope: match[3],
    message: match[4],
    data,
  };
}

function registerSensitive(...values) {
  for (const value of values.flat(Infinity)) {
    const text = String(value || "");
    if (text.length >= 4) sensitiveFragments.add(text);
  }
}

function privacyFindings(text, forbidden = sensitiveFragments, { rejectAbsolutePaths = true } = {}) {
  const value = String(text || "");
  const folded = value.toLowerCase();
  const findings = [];
  for (const item of forbidden) {
    const fragment = String(item || "");
    if (!fragment) continue;
    const variants = new Set([
      fragment,
      encodeURIComponent(fragment),
      encodeURIComponent(fragment).replaceAll("%20", "+"),
    ]);
    if ([...variants].some((variant) => folded.includes(variant.toLowerCase()))) {
      findings.push("forbidden-fragment");
      break;
    }
  }
  if (/\b(?:private[_-]?log[_-]?probe|sentinel)\b/i.test(value)) findings.push("sentinel-marker");
  if (/\bBearer\s+[A-Za-z0-9._~+/=-]{4,}/i.test(value)) findings.push("bearer-value");
  if (rejectAbsolutePaths && /(?:^|[\s"'])(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+\\)/m.test(value)) {
    findings.push("absolute-path");
  }
  return [...new Set(findings)];
}

function reportPrivacyFindings(text, forbidden = sensitiveFragments) {
  const findings = privacyFindings(text, forbidden);
  if (/"(?:challengeId|developerChallengeId|appSessionToken|sessionToken)"\s*:/i.test(String(text || ""))) {
    findings.push("private-report-field");
  }
  return [...new Set(findings)];
}

function sanitizeString(value) {
  let safe = String(value || "");
  for (const item of sensitiveFragments) {
    const fragment = String(item || "");
    if (!fragment) continue;
    for (const variant of [
      fragment,
      encodeURIComponent(fragment),
      encodeURIComponent(fragment).replaceAll("%20", "+"),
    ]) {
      safe = safe.replaceAll(variant, "[REDACTED]");
    }
  }
  safe = safe.replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{4,}/gi, "Bearer [REDACTED]");
  safe = safe.replace(/(?:[A-Za-z]:[\\/]|\\\\)[^\s"',;]+/g, "[LOCAL_PATH]");
  safe = safe.replace(/\b(?:private[_-]?log[_-]?probe|sentinel)[A-Za-z0-9_-]*/gi, "[PRIVATE_MARKER]");
  return safe;
}

function sanitizeReportValue(value, depth = 0) {
  if (depth > 20) return "[TRUNCATED]";
  if (Array.isArray(value)) return value.slice(0, 2_000).map((item) => sanitizeReportValue(item, depth + 1));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).slice(0, 2_000).map(([key, item]) => [key, sanitizeReportValue(item, depth + 1)]),
    );
  }
  if (typeof value === "string") return sanitizeString(value);
  return value;
}

function safeReportContract(report) {
  const safe = sanitizeReportValue(report);
  const serialized = `${JSON.stringify(safe, null, 2)}\n`;
  return {
    safe,
    serialized,
    findings: reportPrivacyFindings(serialized),
  };
}

function createPrivateFixture() {
  const slug = randomBytes(8).toString("hex");
  const windowsUser = `probe-user-${slug}`;
  const projectName = `probe-project-${slug}`;
  const avatarName = `probe-avatar-${slug}`;
  const projectPath = [["C", ":"].join(""), "Users", windowsUser, "Documents", projectName].join("\\");
  const avatarPath = [projectPath, "Assets", "Avatars", `${avatarName}.prefab`].join("\\");
  const blueprintId = ["avtr", slug, randomBytes(4).toString("hex")].join("_");
  const apiKey = ["probe", "api", "key", randomBytes(18).toString("base64url")].join("-");
  const authorization = ["Bearer", randomBytes(24).toString("base64url")].join(" ");
  const privateIp = [10, 20 + (randomBytes(1)[0] % 200), randomBytes(1)[0], 1 + (randomBytes(1)[0] % 253)].join(".");
  const privateMac = [...randomBytes(6)].map((item) => item.toString(16).padStart(2, "0")).join(":");
  const sentinel = ["PRIVATE", "LOG", "PROBE", randomBytes(12).toString("hex")].join("_");
  const fixture = {
    windowsUser,
    projectName,
    avatarName,
    projectPath,
    avatarPath,
    blueprintId,
    apiKey,
    authorization,
    privateIp,
    privateMac,
    sentinel,
  };
  registerSensitive(Object.values(fixture));
  return fixture;
}

function runProcess(command, args, options = {}) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(command, args, {
      windowsHide: true,
      cwd: options.cwd || repoRoot,
      env: options.env || process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    const timeoutMs = Number(options.timeoutMs || 120_000);
    const timer = setTimeout(() => {
      if (settled) return;
      timedOut = true;
      child.kill();
    }, timeoutMs);
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      rejectRun(error);
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (timedOut) rejectRun(new Error("Scoped helper timed out."));
      else if (code !== 0) rejectRun(new Error(stderr.trim() || stdout.trim() || "Scoped helper failed."));
      else resolveRun({ stdout: stdout.trim(), stderr: stderr.trim(), code });
    });
  });
}

async function runPowerShell(script, options = {}) {
  const result = await runProcess(
    "powershell",
    ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
    options,
  );
  return result.stdout;
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

async function gitBindingSnapshot() {
  const [head, originMain, statusResult, sourceVersion] = await Promise.all([
    runProcess("git", ["-C", repoRoot, "rev-parse", "HEAD"]).then((item) => item.stdout.toLowerCase()),
    runProcess("git", ["-C", repoRoot, "rev-parse", "origin/main"]).then((item) => item.stdout.toLowerCase()),
    runProcess("git", ["-C", repoRoot, "status", "--porcelain=v1", "--untracked-files=all"]),
    readFile(resolve(repoRoot, "VERSION"), "utf8").then((item) => item.replace(/^\uFEFF/, "").trim()),
  ]);
  return {
    head,
    originMain,
    worktreeClean: statusResult.stdout.length === 0,
    sourceVersion,
  };
}

async function prepareManifestBoundPackage() {
  const manifestPath = resolve(repoRoot, "dist", "release", "release-manifest.json");
  let manifest;
  try {
    manifest = JSON.parse((await readFile(manifestPath, "utf8")).replace(/^\uFEFF/, ""));
  } catch {
    throw new Error("Release manifest was unavailable or invalid.");
  }
  const git = await gitBindingSnapshot();
  const manifestCommit = String(manifest?.commit || "").trim().toLowerCase();
  const policy = strictBuildPolicyFromManifest(manifest);
  const decision = releaseBindingDecision({
    localMode: allowUnpushed,
    sourceVersion: git.sourceVersion,
    manifestVersion: String(manifest?.version || ""),
    headCommit: git.head,
    originMainCommit: git.originMain,
    manifestCommit,
    worktreeClean: git.worktreeClean,
    strictBuildPolicy: policy.strict,
  });
  if (!decision.valid) throw new Error("Release manifest/Git/version binding failed.");

  const portableName = `VRCForge_Windows_x64_${EXPECTED_VERSION}.zip`;
  const portableArtifact = selectManifestPortableArtifact(manifest, portableName);
  const portableSource = resolve(dirname(manifestPath), portableName);
  const portableSnapshot = resolve(evidenceRoot, portableName);
  await copyFile(portableSource, portableSnapshot, fsConstants.COPYFILE_EXCL);
  const portableSha256 = await sha256File(portableSnapshot);
  if (portableSha256 !== String(portableArtifact.sha256).toLowerCase()) {
    throw new Error("Portable ZIP digest did not match the release manifest.");
  }

  const archivePath = escapePowerShellLiteral(portableSnapshot);
  const destination = escapePowerShellLiteral(packagedRoot);
  const archiveRaw = await runPowerShell(`
    ${powershellCompressionPrelude}
    $stream = [IO.File]::Open('${archivePath}', [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $archive = $null
    try {
      $archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read, $false)
      $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
      foreach ($entry in $archive.Entries) {
        $name = $entry.FullName.Replace('\\', '/')
        if (
          [string]::IsNullOrWhiteSpace($name) -or
          $name.StartsWith('/') -or
          $name -match '^[A-Za-z]:' -or
          @($name.Split('/') | Where-Object { $_ -eq '.' -or $_ -eq '..' }).Count -gt 0 -or
          -not $seen.Add($name)
        ) { throw 'Portable ZIP entry contract failed.' }
      }
      $main = @($archive.Entries | Where-Object { $_.FullName.Replace('\\', '/').Equals('VRCForge.exe', [StringComparison]::Ordinal) })
      $backend = @($archive.Entries | Where-Object { $_.FullName.Replace('\\', '/').Equals('backend/vrcforge_backend.exe', [StringComparison]::Ordinal) })
      if ($main.Count -ne 1 -or $backend.Count -ne 1) { throw 'Portable payload entry contract failed.' }
      function Get-EntryDigest([IO.Compression.ZipArchiveEntry]$Entry) {
        $sha = [Security.Cryptography.SHA256]::Create()
        $input = $Entry.Open()
        try { $bytes = $sha.ComputeHash($input) } finally { $input.Dispose(); $sha.Dispose() }
        [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
      }
      $mainHash = Get-EntryDigest $main[0]
      $backendHash = Get-EntryDigest $backend[0]
      if (Test-Path -LiteralPath '${destination}') { throw 'Isolated extraction root already exists.' }
      [IO.Compression.ZipFileExtensions]::ExtractToDirectory($archive, '${destination}')
      [pscustomobject]@{ mainSha256 = $mainHash; backendSha256 = $backendHash } | ConvertTo-Json -Compress
    } finally {
      if ($archive) { $archive.Dispose() }
      $stream.Dispose()
    }
  `, { timeoutMs: 180_000 });
  const archive = JSON.parse(archiveRaw || "{}");
  const embeddedVersion = (await readFile(resolve(packagedRoot, "VERSION"), "utf8"))
    .replace(/^\uFEFF/, "")
    .trim();
  const mainSha256 = await sha256File(exe);
  const backendSha256 = await sha256File(backendExe);
  if (
    embeddedVersion !== EXPECTED_VERSION
    || mainSha256 !== String(archive.mainSha256 || "").toLowerCase()
    || backendSha256 !== String(archive.backendSha256 || "").toLowerCase()
  ) {
    throw new Error("Extracted package payload binding failed.");
  }
  return {
    version: EXPECTED_VERSION,
    manifestCommit,
    headCommit: git.head,
    originMainCommit: git.originMain,
    worktreeClean: git.worktreeClean,
    buildPolicy: policy.normalized,
    strictReleaseBinding: decision.strict,
    portableName,
    portableSha256,
    mainSha256,
    backendSha256,
    embeddedVersion,
  };
}

async function executableBasenames(root) {
  const names = new Set();
  async function walk(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const target = resolve(current, entry.name);
      if (entry.isDirectory()) await walk(target);
      else if (entry.isFile() && entry.name.toLowerCase().endsWith(".exe")) names.add(entry.name);
    }
  }
  await walk(root);
  return [...names].sort((left, right) => left.localeCompare(right));
}

function normalizeProcessRows(value) {
  if (Array.isArray(value)) return value;
  return value ? [value] : [];
}

function processIdentity(value) {
  return {
    pid: Number(value?.pid || value?.ProcessId || 0),
    parentPid: Number(value?.parentPid || value?.ParentProcessId || 0),
    name: String(value?.name || value?.Name || ""),
    path: String(value?.path || value?.ExecutablePath || ""),
    creationDate: String(value?.creationDate || value?.CreationDate || ""),
    commandLine: String(value?.commandLine || value?.CommandLine || ""),
  };
}

function processIdentityKey(value) {
  const identity = processIdentity(value);
  return JSON.stringify([identity.pid, identity.creationDate, normalizedPath(identity.path)]);
}

function processIdentityMatches(left, right) {
  const candidate = processIdentity(left);
  const recorded = processIdentity(right);
  return candidate.pid > 0
    && candidate.pid === recorded.pid
    && candidate.creationDate === recorded.creationDate
    && normalizedPath(candidate.path) === normalizedPath(recorded.path);
}

async function processSnapshot({ track = false } = {}) {
  const namesPayload = toBase64Json(packageExecutableNames);
  const root = escapePowerShellLiteral(packagedRoot);
  const raw = await runPowerShell(`
    $names = @((
      [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${namesPayload}')) |
        ConvertFrom-Json
    ))
    $root = [IO.Path]::GetFullPath('${root}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $package = @()
    foreach ($name in $names) {
      $safeName = ([string]$name).Replace("'", "''")
      $matches = @(Get-CimInstance Win32_Process -Filter "Name = '$safeName'" -ErrorAction SilentlyContinue)
      foreach ($item in $matches) {
        try { $path = [IO.Path]::GetFullPath([string]$item.ExecutablePath) } catch { continue }
        if ($path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
          $package += [pscustomobject]@{
            pid = [int]$item.ProcessId
            parentPid = [int]$item.ParentProcessId
            name = [string]$item.Name
            path = $path
            creationDate = [string]$item.CreationDate
          }
        }
      }
    }
    $portQuerySucceeded = $true
    try {
      $ports = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object {
        $_.LocalPort -eq 8757 -or $_.LocalPort -eq ${cdpPort}
      } | Select-Object LocalAddress, LocalPort, State, OwningProcess)
    } catch {
      $ports = @()
      $portQuerySucceeded = $false
    }
    $portOwners = @()
    foreach ($owner in @($ports | Select-Object -ExpandProperty OwningProcess -Unique)) {
      $item = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$owner)" -ErrorAction SilentlyContinue
      if ($item -and $item.ExecutablePath) {
        $portOwners += [pscustomobject]@{
          pid = [int]$item.ProcessId
          parentPid = [int]$item.ParentProcessId
          name = [string]$item.Name
          path = [string]$item.ExecutablePath
          creationDate = [string]$item.CreationDate
          commandLine = [string]$item.CommandLine
        }
      }
    }
    [pscustomobject]@{
      packageProcesses = @($package)
      ports = @($ports)
      portOwners = @($portOwners)
      portQuerySucceeded = $portQuerySucceeded
    } | ConvertTo-Json -Depth 6 -Compress
  `);
  const payload = raw ? JSON.parse(raw) : {};
  const snapshot = {
    packageProcesses: normalizeProcessRows(payload.packageProcesses).map(processIdentity),
    ports: normalizeProcessRows(payload.ports),
    portOwners: normalizeProcessRows(payload.portOwners).map(processIdentity),
    portQuerySucceeded: payload.portQuerySucceeded === true,
  };
  if (track) {
    for (const identity of snapshot.packageProcesses) {
      if (identity.pid > 0 && identity.path && identity.creationDate) {
        trackedProcesses.set(processIdentityKey(identity), identity);
      }
    }
  }
  return snapshot;
}

function trackIsolatedCdpOwner(snapshot) {
  const ownerPids = new Set(
    normalizeProcessRows(snapshot?.ports)
      .filter((item) => Number(item?.LocalPort || 0) === cdpPort)
      .map((item) => Number(item?.OwningProcess || 0))
      .filter((item) => item > 0),
  );
  const owners = new Map(
    normalizeProcessRows(snapshot?.portOwners)
      .map(processIdentity)
      .filter((item) => ownerPids.has(item.pid))
      .map((item) => [processIdentityKey(item), item]),
  );
  if (owners.size !== 1) throw new Error("Isolated CDP listener ownership was not unique.");
  const owner = [...owners.values()][0];
  if (
    !owner.path
    || !owner.creationDate
    || !normalizedPath(owner.commandLine).includes(normalizedPath(webviewDataRoot))
  ) {
    throw new Error("CDP listener was not bound to the isolated WebView2 profile.");
  }
  trackedProcesses.set(processIdentityKey(owner), owner);
  return owner;
}

function snapshotIsClear(snapshot) {
  return snapshot?.portQuerySucceeded === true
    && normalizeProcessRows(snapshot?.packageProcesses).length === 0
    && normalizeProcessRows(snapshot?.ports).length === 0;
}

async function waitForProcessClear(timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let latest = await processSnapshot();
  while (Date.now() < deadline && !snapshotIsClear(latest)) {
    await sleep(300);
    latest = await processSnapshot();
  }
  return { ok: snapshotIsClear(latest), snapshot: latest };
}

async function scopedCleanup() {
  const current = await processSnapshot();
  const candidates = [...current.packageProcesses, ...current.portOwners]
    .filter((item) => trackedProcesses.has(processIdentityKey(item)))
    .filter((item) => processIdentityMatches(item, trackedProcesses.get(processIdentityKey(item))));
  if (candidates.length === 0) return waitForProcessClear(5_000);
  const payload = toBase64Json(candidates);
  await runPowerShell(`
    $rows = @(([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${payload}')) | ConvertFrom-Json))
    $verified = @()
    foreach ($row in $rows) {
      $item = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$row.pid)" -ErrorAction SilentlyContinue
      if (-not $item -or -not $item.ExecutablePath) { continue }
      $sameCreation = ([string]$item.CreationDate).Equals([string]$row.creationDate, [StringComparison]::Ordinal)
      try {
        $actualPath = [IO.Path]::GetFullPath([string]$item.ExecutablePath)
        $expectedPath = [IO.Path]::GetFullPath([string]$row.path)
        $samePath = $actualPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)
      } catch { $samePath = $false }
      if ($sameCreation -and $samePath) { $verified += [int]$row.pid }
    }
    foreach ($id in $verified) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
  `);
  return waitForProcessClear(20_000);
}

async function closePackagedApp(app) {
  if (!app?.rootIdentity) throw new Error("Tracked package root was unavailable for close.");
  const identityPayload = toBase64Json(app.rootIdentity);
  const raw = await runPowerShell(`
    $row = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${identityPayload}')) | ConvertFrom-Json
    $item = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$row.pid)" -ErrorAction SilentlyContinue
    $matched = $false
    $windowHandle = 0
    $requested = $false
    if ($item -and $item.ExecutablePath) {
      try {
        $actualPath = [IO.Path]::GetFullPath([string]$item.ExecutablePath)
        $expectedPath = [IO.Path]::GetFullPath([string]$row.path)
        $matched = ([string]$item.CreationDate).Equals([string]$row.creationDate, [StringComparison]::Ordinal) -and
          $actualPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)
      } catch { $matched = $false }
      if ($matched) {
        $process = Get-Process -Id ([int]$row.pid) -ErrorAction SilentlyContinue
        if ($process) {
          $windowHandle = [int64]$process.MainWindowHandle
          $requested = [bool]$process.CloseMainWindow()
        }
      }
    }
    [pscustomobject]@{ matched = $matched; mainWindowHandle = $windowHandle; closeRequested = $requested } |
      ConvertTo-Json -Compress
  `);
  const request = raw ? JSON.parse(raw) : {};
  const graceful = await waitForProcessClear(30_000);
  if (graceful.ok) {
    return {
      identityMatched: request.matched === true,
      mainWindowPresent: Number(request.mainWindowHandle || 0) !== 0,
      closeRequested: request.closeRequested === true,
      graceful: request.matched === true && request.closeRequested === true,
      forced: false,
      clear: true,
    };
  }
  const forced = await scopedCleanup();
  return {
    identityMatched: request.matched === true,
    mainWindowPresent: Number(request.mainWindowHandle || 0) !== 0,
    closeRequested: request.closeRequested === true,
    graceful: false,
    forced: true,
    clear: forced.ok,
  };
}

function formatCanonicalTimestamp(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join("-") + "_" + [pad(date.getHours()), pad(date.getMinutes()), pad(date.getSeconds())].join("-");
}

async function preseedLogRetentionFixtures(privateFixture) {
  await mkdir(logRoot, { recursive: true });
  const legacy = [];
  for (const name of legacyLogNames) {
    const target = resolve(logRoot, name);
    await writeFile(target, `${privateFixture.sentinel}\n`, "utf8");
    legacy.push(target);
  }
  const expired = [];
  const oldDate = new Date(Date.now() - 8 * 24 * 60 * 60 * 1_000);
  for (let index = 0; index < 3; index += 1) {
    const target = resolve(logRoot, `vrcforge_${formatCanonicalTimestamp(oldDate)}_${800 + index}.log`);
    await writeFile(target, `${privateFixture.sentinel}\n`, "utf8");
    await utimes(target, oldDate, oldDate);
    expired.push(target);
  }
  const sparse = [];
  const now = new Date();
  for (let index = 0; index < 42; index += 1) {
    const target = resolve(logRoot, `vrcforge_${formatCanonicalTimestamp(now)}_${1_000 + index}.log`);
    const handle = await open(target, "wx");
    try {
      await handle.truncate(2 * 1_048_576);
    } finally {
      await handle.close();
    }
    const modified = new Date(Date.now() - (42 - index) * 1_000);
    await utimes(target, modified, modified);
    sparse.push(target);
  }
  return { legacy, expired, sparse, logicalBytes: sparse.length * 2 * 1_048_576 };
}

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function logDirectoryStats() {
  const entries = await readdir(logRoot, { withFileTypes: true });
  const files = [];
  let totalBytes = 0;
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const target = resolve(logRoot, entry.name);
    const details = await stat(target);
    files.push({ name: entry.name, path: target, bytes: details.size });
    totalBytes += details.size;
  }
  return { files, totalBytes };
}

async function waitForStartupRetention(fixtures, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    const [legacyPresent, expiredPresent, stats] = await Promise.all([
      Promise.all(fixtures.legacy.map(exists)),
      Promise.all(fixtures.expired.map(exists)),
      logDirectoryStats(),
    ]);
    const sparseSurvivors = stats.files.filter((item) => fixtures.sparse.includes(item.path));
    latest = { legacyPresent, expiredPresent, stats, sparseSurvivors };
    if (
      !legacyPresent.some(Boolean)
      && !expiredPresent.some(Boolean)
      && stats.files.length <= 40
      && stats.totalBytes <= 52_428_800
      && sparseSurvivors.length < fixtures.sparse.length
    ) return latest;
    await sleep(200);
  }
  throw new Error("Startup log retention cleanup did not converge.");
}

async function removeKnownSparseFixtures(fixtures) {
  let removed = 0;
  for (const target of fixtures.sparse) {
    try {
      await unlink(target);
      removed += 1;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  return removed;
}

async function waitForJson(url, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return response.json();
    } catch {
      // The isolated process has not opened its endpoint yet.
    }
    await sleep(150);
  }
  throw new Error("Timed out waiting for isolated JSON endpoint.");
}

function connectCdp(webSocketDebuggerUrl) {
  const socket = new WebSocket(webSocketDebuggerUrl);
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(String(event.data));
    if (!payload.id || !pending.has(payload.id)) return;
    const request = pending.get(payload.id);
    pending.delete(payload.id);
    if (payload.error) request.reject(new Error("CDP request failed."));
    else request.resolve(payload.result);
  });
  const opened = new Promise((resolveOpen, rejectOpen) => {
    socket.addEventListener("open", resolveOpen, { once: true });
    socket.addEventListener("error", rejectOpen, { once: true });
  });
  return {
    opened,
    close: () => socket.close(),
    send(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
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
  if (result.exceptionDetails) throw new Error("Packaged WebView evaluation failed.");
  return result.result?.value;
}

async function waitForEval(cdp, expression, timeoutMs = 45_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const value = await evalValue(cdp, expression);
      if (value === true || value?.ok === true) return value;
    } catch {
      // Renderer state is still settling.
    }
    await sleep(100);
  }
  throw new Error("Timed out waiting for packaged WebView state.");
}

function isolatedLaunchEnvironment() {
  const env = { ...process.env };
  delete env.VRCFORGE_APP_SESSION_TOKEN;
  env.VRCFORGE_USER_DATA_DIR = userDataRoot;
  env.VRCFORGE_CONFIG_DIR = configRoot;
  env.VRCFORGE_CONFIG_PATH = resolve(configRoot, "config.json");
  env.VRCFORGE_SETTINGS_PATH = resolve(configRoot, "settings.json");
  env.VRCFORGE_LOG_DIR = logRoot;
  env.VRCFORGE_ARTIFACTS_DIR = artifactRoot;
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
  const launch = { childPid: Number(child.pid || 0), cdp: null, rootIdentity: null };
  const failed = new Promise((_, rejectFailure) => child.once("error", rejectFailure));
  try {
    const rootIdentityPromise = (async () => {
      const deadline = Date.now() + 15_000;
      while (Date.now() < deadline) {
        const snapshot = await processSnapshot({ track: true });
        const identity = snapshot.packageProcesses.find((item) =>
          item.pid === launch.childPid && normalizedPath(item.path) === normalizedPath(exe));
        if (identity) return identity;
        await sleep(50);
      }
      throw new Error("Spawned packaged root identity was not observed exactly.");
    })();
    const [targets, rootIdentity] = await Promise.all([
      Promise.race([
        waitForJson(`http://127.0.0.1:${cdpPort}/json/list`),
        failed,
      ]),
      rootIdentityPromise,
    ]);
    launch.rootIdentity = rootIdentity;
    const page = targets.find((item) => item?.type === "page" && item?.webSocketDebuggerUrl);
    if (!page) throw new Error("Packaged WebView target was unavailable.");
    const cdp = connectCdp(page.webSocketDebuggerUrl);
    launch.cdp = cdp;
    await cdp.opened;
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    const renderer = await waitForEval(cdp, `(() => ({
      ok: Boolean(document.body && window.__TAURI_INTERNALS__ &&
        typeof window.__TAURI_INTERNALS__.invoke === "function"),
      invoke: typeof window.__TAURI_INTERNALS__?.invoke,
    }))()`);
    const health = await waitForJson(`${appOrigin}/api/health`);
    const snapshot = await processSnapshot({ track: true });
    const cdpOwner = trackIsolatedCdpOwner(snapshot);
    const liveRootIdentity = snapshot.packageProcesses.find((item) => processIdentityMatches(item, rootIdentity));
    if (!liveRootIdentity) throw new Error("Spawned packaged root identity did not remain stable through launch.");
    return { ...launch, renderer, health, initialSnapshot: snapshot, cdpOwner };
  } catch (error) {
    try { launch.cdp?.close(); } catch { /* CDP may not be open. */ }
    await scopedCleanup().catch(() => undefined);
    throw error;
  }
}

async function readAppToken() {
  const tokenPath = resolve(configRoot, "app-session-token");
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const value = (await readFile(tokenPath, "utf8")).trim();
      if (value) {
        registerSensitive(value);
        return value;
      }
    } catch {
      // Backend token has not been persisted yet.
    }
    await sleep(100);
  }
  throw new Error("Isolated app session token was not created.");
}

async function appApiRaw(path, options = {}) {
  if (!appSessionToken) appSessionToken = await readAppToken();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Number(options.timeoutMs || 30_000));
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
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch { payload = {}; }
    return { status: response.status, ok: response.ok, payload };
  } finally {
    clearTimeout(timer);
  }
}

async function appApi(path, options = {}) {
  const response = await appApiRaw(path, options);
  if (!response.ok) throw new Error("Authenticated app request failed.");
  return response.payload;
}

async function tauriInvoke(cdp, command, args = {}) {
  const envelope = await evalValue(cdp, `(async () => {
    try {
      const value = await window.__TAURI_INTERNALS__.invoke(
        ${JSON.stringify(command)},
        ${JSON.stringify(args)},
      );
      return { ok: true, value };
    } catch {
      return { ok: false };
    }
  })()`);
  if (!envelope?.ok) throw new Error("Packaged Tauri command failed.");
  return envelope.value;
}

async function verifyRuntimeBinding(binding) {
  const wrongToken = `invalid-${randomBytes(20).toString("base64url")}`;
  registerSensitive(wrongToken);
  const missing = await fetch(`${appOrigin}/api/app/diagnostics`, {
    headers: { Origin: appRequestOrigin, "Content-Type": "application/json" },
  });
  const wrong = await fetch(`${appOrigin}/api/app/diagnostics`, {
    headers: {
      Origin: appRequestOrigin,
      Authorization: `Bearer ${wrongToken}`,
      "Content-Type": "application/json",
    },
  });
  if (![401, 403].includes(missing.status) || ![401, 403].includes(wrong.status)) {
    throw new Error("Packaged app authentication negative controls failed.");
  }
  const health = await appApi("/api/health");
  if (health?.version !== EXPECTED_VERSION || health?.portableMode !== true) {
    throw new Error("Authenticated packaged health/version contract failed.");
  }
  const expectedPaths = {
    programDir: packagedRoot,
    userDataDir: userDataRoot,
    configDir: configRoot,
    logsDir: logRoot,
    artifactsDir: artifactRoot,
  };
  for (const [key, expected] of Object.entries(expectedPaths)) {
    const actual = String(health?.paths?.[key] || "");
    if (!actual || normalizedPath(await realpath(actual)) !== normalizedPath(await realpath(expected))) {
      throw new Error("Authenticated packaged path binding failed.");
    }
  }
  const snapshot = await processSnapshot({ track: true });
  const backendOwners = snapshot.portOwners.filter((owner) =>
    snapshot.ports.some((port) => Number(port?.LocalPort || 0) === 8757
      && Number(port?.OwningProcess || 0) === owner.pid));
  const uniqueBackendOwners = new Map(backendOwners.map((item) => [processIdentityKey(item), item]));
  if (uniqueBackendOwners.size !== 1) throw new Error("Packaged backend listener ownership was not unique.");
  const backend = [...uniqueBackendOwners.values()][0];
  if (
    normalizedPath(await realpath(backend.path)) !== normalizedPath(await realpath(backendExe))
    || await sha256File(backend.path) !== binding.backendSha256
  ) {
    throw new Error("Packaged backend listener was not manifest-bound.");
  }
  return {
    authNegativeControls: true,
    authenticatedVersion: true,
    portableMode: true,
    isolatedPaths: true,
    backendListenerBound: true,
    backendIdentity: backend,
  };
}

async function openGeneralSettings(cdp) {
  await evalValue(cdp, `(() => {
    if (document.querySelector("[data-vrcforge-diagnostics-settings]")) return true;
    const aside = document.querySelector("aside");
    if (!aside) return false;
    const navs = aside.querySelectorAll("nav");
    if (navs.length >= 2) {
      navs[1].querySelector("button")?.click();
      return true;
    }
    const buttons = aside.querySelectorAll("button");
    buttons[buttons.length - 1]?.click();
    return true;
  })()`);
  await waitForEval(cdp, `(() => ({
    ok: Boolean(
      document.querySelector("[data-vrcforge-diagnostics-settings]") &&
      document.querySelector("[data-vrcforge-developer-toggle]")
    ),
  }))()`);
}

async function inspectGeneralDiagnosticsUi(cdp) {
  return evalValue(cdp, `(() => {
    const panel = document.querySelector("[data-vrcforge-diagnostics-settings]");
    const range = panel?.querySelector('input[type="range"]');
    const navs = document.querySelectorAll("aside nav");
    return {
      panelPresent: Boolean(panel),
      rangePresent: Boolean(range),
      rangeMin: range?.min || "",
      rangeMax: range?.max || "",
      rangeStep: range?.step || "",
      rangeValue: range?.value || "",
      rangeLevel: range?.getAttribute("data-vrcforge-log-level") || "",
      ariaValueTextPresent: Boolean(range?.getAttribute("aria-valuetext")),
      openButtonPresent: Boolean(panel?.querySelector("[data-vrcforge-open-logs]")),
      exportButtonPresent: Boolean(panel?.querySelector("[data-vrcforge-export-support]")),
      developerTogglePresent: Boolean(document.querySelector("[data-vrcforge-developer-toggle]")),
      settingsNavCount: navs.length >= 2 ? navs[1].querySelectorAll("button").length : 0,
    };
  })()`);
}

async function installWebViewInstrumentation(cdp) {
  const result = await evalValue(cdp, `(() => {
    const internal = window.__TAURI_INTERNALS__;
    if (!internal || typeof internal.invoke !== "function" || typeof internal.runCallback !== "function") {
      return { ok: false };
    }
    if (window.__VRCFORGE_LOGGING_PROBE__) return { ok: true, reused: true };
    const state = { logEvents: [], challengeIds: [] };
    const originalInvoke = internal.invoke;
    const originalRunCallback = internal.runCallback;
    internal.invoke = async function(...callArgs) {
      const [command, args] = callArgs;
      const value = await originalInvoke.apply(internal, callArgs);
      try {
        const candidate = value?.challengeId ||
          args?.request?.challengeId ||
          args?.request?.developerChallengeId ||
          args?.request?.body?.developerChallengeId;
        if (candidate && !state.challengeIds.includes(String(candidate))) {
          state.challengeIds.push(String(candidate));
        }
      } catch { /* Probe observation must not affect product behavior. */ }
      return value;
    };
    internal.runCallback = function(id, payload) {
      try {
        const encoded = JSON.stringify(payload);
        if (encoded.includes('"vrcforge-backend-event"') && encoded.includes('"type":"log"')) {
          state.logEvents.push(payload);
          if (state.logEvents.length > 1_000) state.logEvents.shift();
        }
      } catch { /* Probe observation must not affect product behavior. */ }
      return originalRunCallback.call(internal, id, payload);
    };
    window.__VRCFORGE_LOGGING_PROBE__ = state;
    return {
      ok: internal.invoke !== originalInvoke && internal.runCallback !== originalRunCallback,
      reused: false,
    };
  })()`);
  if (!result?.ok) throw new Error("Packaged WebView event instrumentation could not be installed.");
  return result;
}

async function webViewInstrumentationSnapshot(cdp) {
  const snapshot = await evalValue(cdp, `(() => {
    const state = window.__VRCFORGE_LOGGING_PROBE__ || {};
    return {
      logEvents: Array.isArray(state.logEvents) ? state.logEvents.slice(-1_000) : [],
      challengeIds: Array.isArray(state.challengeIds) ? [...state.challengeIds] : [],
    };
  })()`);
  return snapshot || { logEvents: [], challengeIds: [] };
}

async function setLogSlider(cdp, index) {
  const result = await evalValue(cdp, `(() => {
    const input = document.querySelector('[data-vrcforge-diagnostics-settings] input[type="range"]');
    if (!input) return { ok: false };
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    if (!setter) return { ok: false };
    setter.call(input, ${JSON.stringify(String(index))});
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true };
  })()`);
  if (!result?.ok) throw new Error("Packaged diagnostics slider could not be changed.");
}

async function waitForDiagnosticsLevel(cdp, level, index, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  let status;
  while (Date.now() < deadline) {
    status = await appApi("/api/app/diagnostics");
    const dom = await evalValue(cdp, `(() => {
      const input = document.querySelector('[data-vrcforge-diagnostics-settings] input[type="range"]');
      return {
        level: input?.getAttribute("data-vrcforge-log-level") || "",
        value: input?.value || "",
        aria: input?.getAttribute("aria-valuetext") || "",
      };
    })()`);
    if (status?.logLevel === level && dom?.level === level && dom?.value === String(index) && dom?.aria) {
      return { status, dom };
    }
    await sleep(100);
  }
  throw new Error("Diagnostics level did not converge across DOM and REST.");
}

async function rapidlySetLogSlider(cdp, indices) {
  const result = await evalValue(cdp, `(async () => {
    const input = document.querySelector('[data-vrcforge-diagnostics-settings] input[type="range"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    if (!input || !setter) return { ok: false };
    for (const value of ${JSON.stringify(indices.map(String))}) {
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((resolveWait) => setTimeout(resolveWait, 15));
    }
    return { ok: true };
  })()`);
  if (!result?.ok) throw new Error("Rapid diagnostics slider sequence failed.");
}

async function scanLogDirectory() {
  const directoryEntries = await readdir(logRoot, { withFileTypes: true });
  const parsed = [];
  const aliases = new Set();
  let totalBytes = 0;
  let nonblankLines = 0;
  for (const entry of directoryEntries) {
    if (!entry.isFile() || !canonicalLogName.test(entry.name)) {
      throw new Error("Log directory contained a non-canonical entry.");
    }
    const buffer = await readFile(resolve(logRoot, entry.name));
    totalBytes += buffer.length;
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
    } catch {
      throw new Error("Canonical log was not valid UTF-8.");
    }
    if (privacyFindings(text).length > 0 || text.includes("\uFFFD")) {
      throw new Error("Canonical log privacy scan failed.");
    }
    for (const match of text.matchAll(/\b(?:usr|prj|avt|net)_[0-9a-f]{16}\b/g)) aliases.add(match[0]);
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue;
      nonblankLines += 1;
      const item = parseLogLine(line);
      if (!item) throw new Error("Canonical log physical-line grammar failed.");
      parsed.push(item);
    }
  }
  return {
    fileCount: directoryEntries.length,
    totalBytes,
    nonblankLines,
    parsed,
    aliases: [...aliases].sort(),
  };
}

function httpInteractionCount(scan) {
  return scan.parsed.filter((item) => item.scope === "http" && item.message === "HTTP interaction.").length;
}

function buildPrivateQuery(privateFixture, phase) {
  const params = new URLSearchParams();
  params.set("refreshProjects", "false");
  params.set("phase", phase);
  params.set("windowsUser", privateFixture.windowsUser);
  params.set("projectPath", privateFixture.projectPath);
  params.set("avatarPath", privateFixture.avatarPath);
  params.set("avatarName", privateFixture.avatarName);
  params.set("blueprintId", privateFixture.blueprintId);
  params.set("api_key", privateFixture.apiKey);
  params.set("authorization", privateFixture.authorization);
  params.set("privateIp", privateFixture.privateIp);
  params.set("mac", privateFixture.privateMac);
  params.set("secret", privateFixture.sentinel);
  const encoded = params.toString();
  const roundTrip = new URLSearchParams(encoded);
  const expected = Object.entries({
    windowsUser: privateFixture.windowsUser,
    projectPath: privateFixture.projectPath,
    avatarPath: privateFixture.avatarPath,
    avatarName: privateFixture.avatarName,
    blueprintId: privateFixture.blueprintId,
    api_key: privateFixture.apiKey,
    authorization: privateFixture.authorization,
    privateIp: privateFixture.privateIp,
    mac: privateFixture.privateMac,
    secret: privateFixture.sentinel,
  });
  const encodingVerified = expected.every(([key, value]) => roundTrip.get(key) === value)
    && !encoded.includes(privateFixture.projectPath)
    && /%5C/i.test(encoded);
  return { encoded, encodingVerified };
}

function extractIdentityChain(diagnostics, privateFixture) {
  const rows = Array.isArray(diagnostics?.identities) ? diagnostics.identities : [];
  const user = rows.find((item) =>
    /^usr_[0-9a-f]{16}$/.test(String(item?.alias || ""))
      && item?.windowsUser === privateFixture.windowsUser);
  const project = rows.find((item) =>
    /^prj_[0-9a-f]{16}$/.test(String(item?.alias || ""))
      && item?.userAlias === user?.alias
      && item?.windowsUser === privateFixture.windowsUser
      && item?.projectName === privateFixture.projectName);
  const avatar = rows.find((item) =>
    /^avt_[0-9a-f]{16}$/.test(String(item?.alias || ""))
      && item?.userAlias === user?.alias
      && item?.projectAlias === project?.alias
      && item?.windowsUser === privateFixture.windowsUser
      && item?.projectName === privateFixture.projectName
      && item?.avatarName === privateFixture.avatarName);
  const serialized = JSON.stringify(rows);
  if (
    !user || !project || !avatar
    || serialized.includes(privateFixture.projectPath)
    || serialized.includes(privateFixture.avatarPath)
    || serialized.includes(privateFixture.blueprintId)
    || serialized.includes(privateFixture.apiKey)
    || serialized.includes(privateFixture.authorization)
    || serialized.includes(privateFixture.sentinel)
    || serialized.includes(privateFixture.privateIp)
    || serialized.includes(privateFixture.privateMac)
  ) {
    throw new Error("Diagnostics identity chain was unsafe or incomplete.");
  }
  return {
    userAlias: user.alias,
    projectAlias: project.alias,
    avatarAlias: avatar.alias,
  };
}

async function injectPrivateContext(level, privateFixture, phase) {
  const query = buildPrivateQuery(privateFixture, phase);
  if (!query.encodingVerified) throw new Error("Private diagnostics query encoding failed.");
  await appApi(`/api/app/bootstrap?${query.encoded}`);
  const diagnostics = await appApi("/api/app/diagnostics", {
    method: "POST",
    body: { logLevel: level },
  });
  if (diagnostics?.logLevel !== level) throw new Error("Diagnostics POST did not return the live level.");
  return {
    queryEncodingVerified: true,
    diagnostics,
    chain: extractIdentityChain(diagnostics, privateFixture),
  };
}

async function inspectPrivateIdentityStore(privateFixture, expectedChain) {
  const mappingPath = resolve(configRoot, privateMappingName);
  const keyPath = resolve(configRoot, privateKeyName);
  const [mappingText, key] = await Promise.all([
    readFile(mappingPath, "utf8"),
    readFile(keyPath),
  ]);
  const mapping = JSON.parse(mappingText.replace(/^\uFEFF/, ""));
  const records = Array.isArray(mapping?.records) ? mapping.records : [];
  const values = new Set(records.map((item) => String(item?.value || "")));
  const aliases = new Set(records.map((item) => String(item?.alias || "")));
  const networkAliases = records
    .filter((item) => item?.kind === "network" && [privateFixture.privateIp, privateFixture.privateMac].includes(item?.value))
    .map((item) => String(item.alias || ""))
    .filter((item) => /^net_[0-9a-f]{16}$/.test(item))
    .sort();
  const requiredRawValues = [
    privateFixture.windowsUser,
    privateFixture.projectPath,
    privateFixture.blueprintId,
    privateFixture.privateIp,
    privateFixture.privateMac,
  ];
  const forbiddenPrivateStoreValues = [
    privateFixture.apiKey,
    privateFixture.authorization,
    privateFixture.sentinel,
    appSessionToken,
  ];
  const userRecord = records.find((item) => item?.alias === expectedChain.userAlias);
  const projectRecord = records.find((item) => item?.alias === expectedChain.projectAlias);
  const avatarRecord = records.find((item) => item?.alias === expectedChain.avatarAlias);
  if (
    key.length !== 32
    || !requiredRawValues.every((value) => values.has(value))
    || forbiddenPrivateStoreValues.some((value) => mappingText.includes(value))
    || !Object.values(expectedChain).every((alias) => aliases.has(alias))
    || userRecord?.value !== privateFixture.windowsUser
    || projectRecord?.value !== privateFixture.projectPath
    || projectRecord?.userAlias !== expectedChain.userAlias
    || avatarRecord?.value !== privateFixture.blueprintId
    || avatarRecord?.userAlias !== expectedChain.userAlias
    || avatarRecord?.projectAlias !== expectedChain.projectAlias
    || avatarRecord?.avatarName !== privateFixture.avatarName
    || networkAliases.length < 2
  ) {
    throw new Error("Private identity store proof failed.");
  }
  const keyHex = key.toString("hex");
  const keyBase64 = key.toString("base64");
  registerSensitive(keyHex, keyBase64);
  privateStoreFragments.add(keyHex);
  privateStoreFragments.add(keyBase64);
  return {
    mappingPresent: true,
    rawIdentityMappingPresent: true,
    secretsExcludedFromMapping: true,
    keyPresent: true,
    keyBytes: key.length,
    aliasesBound: true,
    networkAliases,
  };
}

async function assertPrivateStoreProjectionAbsent(cdp, diagnostics) {
  const dom = await evalValue(cdp, "document.documentElement?.outerHTML || ''");
  const observable = `${String(dom || "")}\n${JSON.stringify(diagnostics || {})}`;
  const folded = observable.toLowerCase();
  const leaked = [...privateStoreFragments].some((fragment) => {
    const variants = [
      fragment,
      encodeURIComponent(fragment),
      encodeURIComponent(fragment).replaceAll("%20", "+"),
    ];
    return variants.some((variant) => folded.includes(String(variant).toLowerCase()));
  });
  if (leaked) throw new Error("Private diagnostic store material reached an observable surface.");
  return true;
}

async function readSupportBundleMembers(bundlePath) {
  const archive = escapePowerShellLiteral(bundlePath);
  const raw = await runPowerShell(`
    ${powershellCompressionPrelude}
    $stream = [IO.File]::Open('${archive}', [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $zip = $null
    try {
      $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read, $false)
      if ($zip.Entries.Count -gt 100) { throw 'Support bundle entry count exceeded probe bound.' }
      $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
      $total = [int64]0
      $members = @()
      foreach ($entry in $zip.Entries) {
        $name = $entry.FullName.Replace('\\', '/')
        if (
          [string]::IsNullOrWhiteSpace($name) -or $name.EndsWith('/') -or
          $name.StartsWith('/') -or $name -match '^[A-Za-z]:' -or
          @($name.Split('/') | Where-Object { $_ -eq '.' -or $_ -eq '..' }).Count -gt 0 -or
          -not $seen.Add($name)
        ) { throw 'Support bundle entry contract failed.' }
        $total += [int64]$entry.Length
        if ($total -gt 104857600) { throw 'Support bundle expansion exceeded probe bound.' }
        $input = $entry.Open()
        $reader = [IO.StreamReader]::new($input, [Text.UTF8Encoding]::new($false, $true), $true)
        try { $text = $reader.ReadToEnd() } finally { $reader.Dispose(); $input.Dispose() }
        $members += [pscustomobject]@{ name = $name; text = $text }
      }
      [pscustomobject]@{ members = @($members); totalBytes = $total } | ConvertTo-Json -Depth 5 -Compress
    } finally {
      if ($zip) { $zip.Dispose() }
      $stream.Dispose()
    }
  `, { timeoutMs: 120_000 });
  const payload = JSON.parse(raw || "{}");
  return {
    members: normalizeProcessRows(payload.members),
    totalBytes: Number(payload.totalBytes || 0),
  };
}

async function validateSupportBundle(bundlePath, privateStore) {
  const canonicalBundle = normalizedPath(await realpath(bundlePath));
  const canonicalArtifacts = normalizedPath(await realpath(artifactRoot));
  if (!canonicalBundle.startsWith(`${canonicalArtifacts}/`) || !/^vrcforge-support-\d{8}-\d{6}\.zip$/.test(basename(bundlePath))) {
    throw new Error("Support bundle path was outside the isolated artifact root.");
  }
  const bundle = await readSupportBundleMembers(bundlePath);
  const byName = new Map(bundle.members.map((item) => [String(item.name || ""), String(item.text || "")]));
  if (
    byName.size !== requiredSupportMembers.length
    || !requiredSupportMembers.every((name) => byName.has(name))
  ) throw new Error("Support bundle member set was incomplete.");
  const combined = bundle.members.map((item) => `${item.name}\n${item.text}`).join("\n");
  if (privacyFindings(combined).length > 0) throw new Error("Support bundle privacy scan failed.");
  const diagnostics = JSON.parse(byName.get("diagnostics.json"));
  const metadata = JSON.parse(byName.get("metadata.json"));
  if (metadata?.includeFullPathsRequested !== true || metadata?.includeFullPaths !== false) {
    throw new Error("Support bundle did not preserve the safe full-path compatibility contract.");
  }
  if (Object.hasOwn(diagnostics, "identities") || JSON.stringify(diagnostics).includes(privateMappingName)) {
    throw new Error("Support diagnostics exposed private identity state.");
  }
  const logText = byName.get("diagnostic-log.txt");
  const logLines = logText.split(/\r?\n/).filter((line) => line.trim());
  if (
    logLines.length === 0
    || !logLines.every((line) => parseLogLine(line))
    || !/\b(?:usr|prj|avt|net)_[0-9a-f]{16}\b/.test(logText)
  ) throw new Error("Support bundle log excerpt was not readable/redacted.");
  if (!privateStore.networkAliases.every((alias) => combined.includes(alias))) {
    throw new Error("Support bundle omitted the redacted network evidence.");
  }
  return {
    includeFullPathsRequested: true,
    includeFullPathsEffective: false,
    memberCount: byName.size,
    totalBytes: bundle.totalBytes,
    identityProjectionExcluded: true,
    privateStoreFilesExcluded: true,
    readableRedactedLogExcerpt: true,
    privacyScanClean: true,
  };
}

async function advancedSettingsState() {
  const payload = await appApi("/api/app/advanced-settings");
  return payload?.settings || {};
}

async function waitForDeveloperBackend(enabled, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  let state;
  while (Date.now() < deadline) {
    state = await advancedSettingsState();
    if (state?.developerOptionsEnabled === enabled) return state;
    await sleep(100);
  }
  throw new Error("Developer Options backend state did not converge.");
}

async function waitForDeveloperUi(cdp, enabled, timeoutMs = 20_000) {
  const expected = enabled ? "true" : "false";
  return waitForEval(cdp, `(() => ({
    ok: window.localStorage.getItem("vrcforge_developer_options_enabled") === ${JSON.stringify(expected)},
  }))()`, timeoutMs);
}

async function clickDeveloperToggle(cdp) {
  const result = await evalValue(cdp, `(() => {
    const button = document.querySelector("[data-vrcforge-developer-toggle]");
    if (!button || button.disabled) return { ok: false };
    const hostStartedAt = Date.now();
    const performanceStartedAt = performance.now();
    button.click();
    return { ok: true, hostStartedAt, performanceStartedAt };
  })()`);
  if (!result?.ok) throw new Error("Developer Options toggle was unavailable.");
  return result;
}

async function modalSample(cdp, start) {
  return evalValue(cdp, `(() => {
    const modal = document.querySelector('[data-vrcforge-developer-warning="true"]');
    const cancel = modal?.querySelector("[data-vrcforge-developer-cancel]");
    const confirm = modal?.querySelector("[data-vrcforge-developer-confirm]");
    return {
      modalPresent: Boolean(modal),
      cancelEnabled: Boolean(cancel && !cancel.disabled),
      confirmDisabled: Boolean(confirm?.disabled),
      performanceElapsedMs: performance.now() - ${Number(start.performanceStartedAt)},
      hostElapsedMs: Date.now() - ${Number(start.hostStartedAt)},
    };
  })()`);
}

async function waitForModal(cdp, start, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const sample = await modalSample(cdp, start);
    if (sample?.modalPresent) return sample;
    await sleep(25);
  }
  throw new Error("Developer Options warning modal did not open.");
}

async function sampleModalAfter(cdp, start, targetPerformanceMs) {
  const deadline = Date.now() + 10_000;
  let sample = await modalSample(cdp, start);
  while (Date.now() < deadline && Number(sample?.performanceElapsedMs || 0) < targetPerformanceMs) {
    await sleep(Math.min(50, Math.max(5, targetPerformanceMs - Number(sample?.performanceElapsedMs || 0))));
    sample = await modalSample(cdp, start);
  }
  return sample;
}

async function waitForModalReady(cdp, start) {
  const deadline = Date.now() + 8_000;
  while (Date.now() < deadline) {
    const sample = await modalSample(cdp, start);
    if (sample?.modalPresent && sample?.confirmDisabled === false) return sample;
    await sleep(20);
  }
  throw new Error("Developer Options warning modal never became ready.");
}

async function cancelDeveloperModal(cdp) {
  const clicked = await evalValue(cdp, `(() => {
    const button = document.querySelector("[data-vrcforge-developer-cancel]");
    if (!button || button.disabled) return false;
    button.click();
    return true;
  })()`);
  if (!clicked) throw new Error("Developer Options Cancel was unavailable.");
  await waitForEval(cdp, `(() => ({
    ok: !document.querySelector('[data-vrcforge-developer-warning="true"]'),
  }))()`);
}

async function confirmDeveloperModalOnce(cdp) {
  const clicked = await evalValue(cdp, `(() => {
    const button = document.querySelector("[data-vrcforge-developer-confirm]");
    if (!button || button.disabled) return false;
    button.click();
    return true;
  })()`);
  if (!clicked) throw new Error("Developer Options Confirm was unavailable.");
  await waitForEval(cdp, `(() => ({
    ok: !document.querySelector('[data-vrcforge-developer-warning="true"]'),
  }))()`);
}

async function settingsNavCount(cdp) {
  return evalValue(cdp, `(() => {
    const navs = document.querySelectorAll("aside nav");
    return navs.length >= 2 ? navs[1].querySelectorAll("button").length : 0;
  })()`);
}

async function openDeveloperSettingsSection(cdp) {
  const result = await evalValue(cdp, `(() => {
    const navs = document.querySelectorAll("aside nav");
    if (navs.length < 2) return { ok: false };
    const buttons = navs[1].querySelectorAll("button");
    if (!buttons.length) return { ok: false };
    buttons[buttons.length - 1].click();
    return { ok: true };
  })()`);
  if (!result?.ok) throw new Error("Developer settings navigation was unavailable.");
  return waitForEval(cdp, `(() => ({
    ok: !document.querySelector("[data-vrcforge-diagnostics-settings]") &&
      !document.querySelector("[data-vrcforge-developer-toggle]"),
  }))()`);
}

async function disableDeveloperThroughUi(cdp) {
  await openGeneralSettings(cdp);
  await waitForDeveloperUi(cdp, true);
  await clickDeveloperToggle(cdp);
  await Promise.all([
    waitForDeveloperBackend(false),
    waitForDeveloperUi(cdp, false),
  ]);
  const modalAbsent = await evalValue(cdp, `!document.querySelector('[data-vrcforge-developer-warning="true"]')`);
  if (!modalAbsent) throw new Error("Developer Options disable unexpectedly opened a warning.");
}

async function exerciseDeveloperOptions(cdp, baselineNavCount) {
  await openGeneralSettings(cdp);
  const initialState = await waitForDeveloperBackend(false);
  await waitForDeveloperUi(cdp, false);

  const firstStart = await clickDeveloperToggle(cdp);
  const immediate = await waitForModal(cdp, firstStart);
  const immediateBackend = await advancedSettingsState();
  const aroundTwoSeconds = await sampleModalAfter(cdp, firstStart, 2_000);
  const twoSecondBackend = await advancedSettingsState();
  if (
    !immediate.modalPresent
    || !immediate.cancelEnabled
    || !immediate.confirmDisabled
    || immediateBackend?.developerOptionsEnabled !== false
    || !aroundTwoSeconds.modalPresent
    || !aroundTwoSeconds.cancelEnabled
    || !aroundTwoSeconds.confirmDisabled
    || twoSecondBackend?.developerOptionsEnabled !== false
  ) throw new Error("Developer Options first warning round failed.");
  await cancelDeveloperModal(cdp);
  await waitForDeveloperBackend(false);

  const secondStart = await clickDeveloperToggle(cdp);
  const secondImmediate = await waitForModal(cdp, secondStart);
  const timedSamples = [secondImmediate];
  for (const target of [1_000, 2_500, 4_500]) {
    timedSamples.push(await sampleModalAfter(cdp, secondStart, target));
  }
  const preReadyValid = timedSamples.every((sample) =>
    sample?.modalPresent
      && sample?.confirmDisabled === true
      && sample?.cancelEnabled === true
      && Number(sample?.performanceElapsedMs || 0) < 5_000);
  const ready = await waitForModalReady(cdp, secondStart);
  if (
    !preReadyValid
    || Number(ready?.performanceElapsedMs || 0) < 5_000
    || Number(ready?.hostElapsedMs || 0) < 5_000
    || ready?.cancelEnabled !== true
  ) throw new Error("Developer Options five-second timing proof failed.");
  await confirmDeveloperModalOnce(cdp);
  await Promise.all([
    waitForDeveloperBackend(true),
    waitForDeveloperUi(cdp, true),
  ]);
  const enabledNavCount = await settingsNavCount(cdp);
  if (enabledNavCount !== baselineNavCount + 1) {
    throw new Error("Developer-only settings navigation visibility failed.");
  }
  await openDeveloperSettingsSection(cdp);
  await openGeneralSettings(cdp);
  await disableDeveloperThroughUi(cdp);

  const noChallenge = await appApiRaw("/api/app/advanced-settings", {
    method: "POST",
    body: { developerOptionsEnabled: true, computerUseEnabled: false },
  });
  if (noChallenge.status !== 409 || (await advancedSettingsState()).developerOptionsEnabled !== false) {
    throw new Error("Developer Options missing-challenge negative control failed.");
  }

  const earlyBegin = await appApiRaw("/api/app/advanced-settings/developer-challenge", { method: "POST" });
  const earlyId = String(earlyBegin.payload?.challengeId || "");
  registerSensitive(earlyId);
  const early = await appApiRaw("/api/app/advanced-settings", {
    method: "POST",
    body: { developerOptionsEnabled: true, computerUseEnabled: false, developerChallengeId: earlyId },
  });
  if (earlyBegin.status !== 200 || !earlyId || early.status !== 409) {
    throw new Error("Developer Options early-challenge negative control failed.");
  }
  await appApiRaw(`/api/app/advanced-settings/developer-challenge/${encodeURIComponent(earlyId)}`, {
    method: "DELETE",
  });

  const consumedBegin = await appApiRaw("/api/app/advanced-settings/developer-challenge", { method: "POST" });
  const consumedId = String(consumedBegin.payload?.challengeId || "");
  registerSensitive(consumedId);
  const waitMs = Number(consumedBegin.payload?.waitMs || 0);
  if (consumedBegin.status !== 200 || !consumedId || waitMs < 5_000) {
    throw new Error("Developer Options reusable challenge setup failed.");
  }
  const directWaitStartedAt = Date.now();
  await sleep(waitMs + 75);
  const consumed = await appApiRaw("/api/app/advanced-settings", {
    method: "POST",
    body: { developerOptionsEnabled: true, computerUseEnabled: false, developerChallengeId: consumedId },
  });
  if (consumed.status !== 200 || Date.now() - directWaitStartedAt < 5_000) {
    throw new Error("Developer Options valid direct challenge failed.");
  }
  await Promise.all([
    waitForDeveloperBackend(true),
    waitForDeveloperUi(cdp, true),
  ]);
  await disableDeveloperThroughUi(cdp);
  const reused = await appApiRaw("/api/app/advanced-settings", {
    method: "POST",
    body: { developerOptionsEnabled: true, computerUseEnabled: false, developerChallengeId: consumedId },
  });
  const finalState = await waitForDeveloperBackend(false);
  if (reused.status !== 409 || finalState?.developerOptionsEnabled !== false) {
    throw new Error("Developer Options consumed-challenge reuse control failed.");
  }
  const instrumentation = await webViewInstrumentationSnapshot(cdp);
  registerSensitive(instrumentation.challengeIds);
  return {
    initialDisabled: initialState?.developerOptionsEnabled === false,
    firstRound: {
      immediateCancelEnabled: immediate.cancelEnabled === true,
      immediateConfirmDisabled: immediate.confirmDisabled === true,
      backendStayedDisabled: immediateBackend?.developerOptionsEnabled === false
        && twoSecondBackend?.developerOptionsEnabled === false,
      aroundTwoSecondsStillDisabled: aroundTwoSeconds.confirmDisabled === true
        && Number(aroundTwoSeconds.performanceElapsedMs || 0) >= 1_900,
      cancelKeptBackendDisabled: true,
    },
    secondRound: {
      preReadySampleCount: timedSamples.length,
      allPre5000Disabled: preReadyValid,
      rendererReadyElapsedMs: Math.round(Number(ready.performanceElapsedMs || 0)),
      hostReadyElapsedMs: Math.round(Number(ready.hostElapsedMs || 0)),
      readyAtOrAfter5000: Number(ready.performanceElapsedMs || 0) >= 5_000
        && Number(ready.hostElapsedMs || 0) >= 5_000,
      confirmedOnce: true,
      backendEnabled: true,
    },
    developerNavHiddenByDefault: enabledNavCount === baselineNavCount + 1,
    developerSectionReachableAfterEnable: true,
    directNegativeControls: {
      missingRejected: noChallenge.status === 409,
      earlyRejected: early.status === 409,
      consumedReuseRejected: reused.status === 409,
      realWaitObserved: Date.now() - directWaitStartedAt >= 5_000,
    },
    finalDisabled: finalState?.developerOptionsEnabled === false,
    observedUiChallengeCount: instrumentation.challengeIds.length,
  };
}

async function invokeOpenLogsFolderOnce(cdp) {
  const buttonPresent = await evalValue(cdp, `Boolean(
    document.querySelector("[data-vrcforge-diagnostics-settings] [data-vrcforge-open-logs]")
  )`);
  if (!buttonPresent) throw new Error("Open logs button was unavailable.");
  await tauriInvoke(cdp, "open_logs_folder", {});
  return {
    buttonPresent: true,
    tauriCommandRegistered: true,
    invokedExactlyOnce: true,
    explorerProcessesInspected: false,
    explorerProcessesTerminated: false,
  };
}

function validateEventEvidence(snapshot, requiredAliases = []) {
  const events = Array.isArray(snapshot?.logEvents) ? snapshot.logEvents : [];
  const serialized = JSON.stringify(events);
  if (
    events.length === 0
    || privacyFindings(serialized).length > 0
    || !requiredAliases.every((alias) => serialized.includes(alias))
  ) {
    throw new Error("Packaged backend log-event privacy evidence failed.");
  }
  return {
    capturedLogEventCount: events.length,
    privacyScanClean: true,
    requiredAliasesObserved: true,
    privateStoreFilesExcluded: !serialized.includes(privateMappingName) && !serialized.includes(privateKeyName),
  };
}

function identityChainsEqual(left, right) {
  return left?.userAlias === right?.userAlias
    && left?.projectAlias === right?.projectAlias
    && left?.avatarAlias === right?.avatarAlias;
}

function closureSucceeded(closure) {
  return closure?.identityMatched === true
    && closure?.mainWindowPresent === true
    && closure?.closeRequested === true
    && closure?.graceful === true
    && closure?.forced === false
    && closure?.clear === true;
}

function validateFinalContract(report) {
  if (report.version !== EXPECTED_VERSION) addAssertion(report, "VERSION 1.3.0 was not proven");
  if (allowUnpushed) {
    if (report.releaseBinding?.strictReleaseBinding !== false || report.mode !== "local-preacceptance") {
      addAssertion(report, "allow-unpushed mode was incorrectly marked as strict release evidence");
    }
  } else if (
    report.releaseBinding?.strictReleaseBinding !== true
    || report.releaseBinding?.headEqualsOriginMain !== true
    || report.releaseBinding?.manifestEqualsHead !== true
    || report.releaseBinding?.worktreeClean !== true
    || report.releaseBinding?.strictBuildPolicy !== true
  ) {
    addAssertion(report, "strict release binding was incomplete");
  }
  for (const transport of [
    "packaged-webview-dom",
    "packaged-webview-tauri-ipc",
    "authenticated-loopback-rest",
  ]) {
    if (!report.transports.includes(transport)) addAssertion(report, "required packaged transport was not proven");
  }
  const requiredTrue = [
    report.runtimeBinding?.authNegativeControls,
    report.runtimeBinding?.backendListenerBound,
    report.runtimeBinding?.sameBackendImmediateReadback,
    report.defaultState?.developerOptionsDisabled,
    report.defaultState?.logLevelInfo,
    report.defaultState?.developerLowLevelHidden,
    report.diagnosticsUi?.panelPresent,
    report.diagnosticsUi?.fiveLevelRange,
    report.diagnosticsUi?.openButtonPresent,
    report.diagnosticsUi?.exportButtonPresent,
    report.logLevels?.debugReachedViaDom,
    report.logLevels?.traceReachedViaDom,
    report.logLevels?.rapidFinalServerMatches,
    report.logLevels?.rapidFinalAriaMatches,
    report.logLevels?.infoInteractionSuppressed,
    report.logLevels?.debugInteractionRecorded,
    report.logFiles?.legacyDeleted,
    report.logFiles?.expiredDeleted,
    report.logFiles?.countBounded,
    report.logFiles?.totalBytesBounded,
    report.logFiles?.activeRetained,
    report.logFiles?.canonicalNamesOnly,
    report.logFiles?.utf8AndPhysicalGrammar,
    report.privacy?.queryEncodingVerified,
    report.privacy?.diagnosticsPostIdentityReadback,
    report.privacy?.rawValuesAbsentFromLogs,
    report.privacy?.allAliasKindsObserved,
    report.privacy?.eventPrivacyClean,
    report.identityStability?.stableAcrossRestart,
    report.privateStore?.rawIdentityMappingPresent,
    report.privateStore?.secretsExcludedFromMapping,
    report.privateStore?.keyIs32Bytes,
    report.privateStore?.observableProjectionExcluded,
    report.supportBundle?.includeFullPathsRequested,
    report.supportBundle?.effectiveFullPathsDisabled,
    report.supportBundle?.privacyScanClean,
    report.supportBundle?.identityProjectionExcluded,
    report.supportBundle?.readableRedactedLogExcerpt,
    report.developerOptions?.firstRound?.immediateCancelEnabled,
    report.developerOptions?.firstRound?.immediateConfirmDisabled,
    report.developerOptions?.firstRound?.backendStayedDisabled,
    report.developerOptions?.firstRound?.aroundTwoSecondsStillDisabled,
    report.developerOptions?.secondRound?.allPre5000Disabled,
    report.developerOptions?.secondRound?.readyAtOrAfter5000,
    report.developerOptions?.secondRound?.confirmedOnce,
    report.developerOptions?.developerNavHiddenByDefault,
    report.developerOptions?.developerSectionReachableAfterEnable,
    report.developerOptions?.directNegativeControls?.missingRejected,
    report.developerOptions?.directNegativeControls?.earlyRejected,
    report.developerOptions?.directNegativeControls?.consumedReuseRejected,
    report.developerOptions?.directNegativeControls?.realWaitObserved,
    report.developerOptions?.finalDisabled,
    report.openLogsFolder?.buttonPresent,
    report.openLogsFolder?.tauriCommandRegistered,
    report.openLogsFolder?.invokedExactlyOnce,
    report.openLogsFolder?.explorerProcessesInspected === false,
    report.openLogsFolder?.explorerProcessesTerminated === false,
    report.completionBinding?.stable,
    report.completionBinding?.manifestAndPortableHashUnchanged,
    report.cleanup?.firstClosureGraceful,
    report.cleanup?.secondClosureGraceful,
    report.cleanup?.packageProcessesClear,
    report.cleanup?.portsClear,
    report.cleanup?.scopedOnly,
  ];
  if (requiredTrue.some((item) => item !== true)) addAssertion(report, "one or more packaged logging gates were incomplete");
}

async function writeSafeReport(report) {
  const rawSerialized = JSON.stringify(report);
  if (reportPrivacyFindings(rawSerialized).length > 0) {
    addAssertion(report, "report privacy contract required redaction");
  }
  report.ok = report.assertions.length === 0;
  const contract = safeReportContract(report);
  if (contract.findings.length > 0) {
    const minimal = {
      schema: "vrcforge.packaged_logging_probe.v1",
      version: EXPECTED_VERSION,
      mode: allowUnpushed ? "local-preacceptance" : "strict-release",
      ok: false,
      assertions: ["safe report serialization failed closed"],
    };
    await writeFile(reportPath, `${JSON.stringify(minimal, null, 2)}\n`, "utf8");
    return minimal;
  }
  await writeFile(reportPath, contract.serialized, "utf8");
  return contract.safe;
}

function runSelfTest() {
  const commit = "a".repeat(40);
  const strictManifest = {
    buildPolicy: {
      mode: "strict",
      releaseEligible: true,
      allowDirty: false,
      allowUnpushed: false,
      allowVersionMismatch: false,
    },
  };
  const localManifest = {
    buildPolicy: {
      mode: "local-acceptance",
      releaseEligible: false,
      allowDirty: true,
      allowUnpushed: true,
      allowVersionMismatch: false,
    },
  };
  const binding = (overrides = {}) => releaseBindingDecision({
    localMode: false,
    sourceVersion: EXPECTED_VERSION,
    manifestVersion: EXPECTED_VERSION,
    headCommit: commit,
    originMainCommit: commit,
    manifestCommit: commit,
    worktreeClean: true,
    strictBuildPolicy: true,
    ...overrides,
  });
  const portableName = `VRCForge_Windows_x64_${EXPECTED_VERSION}.zip`;
  const artifact = { name: portableName, sha256: "b".repeat(64) };
  const rejectsArtifact = (manifest) => {
    try {
      selectManifestPortableArtifact(manifest, portableName);
      return false;
    } catch {
      return true;
    }
  };
  const validLine = "2026-07-16 12:34:56.789+09:00 [DEBUG] [http] HTTP interaction. | data={\"path\":\"prj_0123456789abcdef\",\"user\":\"usr_fedcba9876543210\"}";
  const selfRaw = ["SELF", "TEST", "PRIVATE", randomBytes(5).toString("hex")].join("_");
  const selfPath = [["Q", ":"].join(""), "probe", "private", "item"].join("\\");
  const selfForbidden = new Set([selfRaw, privateMappingName, privateKeyName]);
  const scopedRoot = [["D", ":"].join(""), "probe", "package"].join("\\");
  const scopedExe = [scopedRoot, "backend", "worker.exe"].join("\\");
  const siblingExe = [`${scopedRoot}-other`, "worker.exe"].join("\\");
  const safeReport = {
    schema: "vrcforge.packaged_logging_probe.self_test_fixture.v1",
    alias: "usr_0123456789abcdef",
    ok: true,
  };
  const checks = {
    strictPolicyAccepted: strictBuildPolicyFromManifest(strictManifest).strict === true,
    missingPolicyRejected: strictBuildPolicyFromManifest({}).strict === false,
    localPolicyNotStrict: strictBuildPolicyFromManifest(localManifest).strict === false,
    strictBindingAccepted: binding().valid === true && binding().strict === true,
    strictDirtyMutationRejected: binding({ worktreeClean: false }).valid === false,
    strictOriginMutationRejected: binding({ originMainCommit: "c".repeat(40) }).valid === false,
    strictBuildPolicyMutationRejected: binding({ strictBuildPolicy: false }).valid === false,
    manifestCommitMutationRejected: binding({ manifestCommit: "d".repeat(40) }).valid === false,
    versionMutationRejected: binding({ manifestVersion: "1.3.1" }).valid === false,
    localDirtyUnpushedAcceptedButNeverStrict: (() => {
      const value = binding({
        localMode: true,
        worktreeClean: false,
        originMainCommit: "e".repeat(40),
        strictBuildPolicy: false,
      });
      return value.valid === true && value.strict === false;
    })(),
    localManifestDriftRejected: binding({
      localMode: true,
      worktreeClean: false,
      originMainCommit: "e".repeat(40),
      manifestCommit: "f".repeat(40),
      strictBuildPolicy: false,
    }).valid === false,
    uniquePortableArtifactAccepted: selectManifestPortableArtifact({ artifacts: [artifact] }, portableName) === artifact,
    duplicatePortableArtifactRejected: rejectsArtifact({ artifacts: [artifact, { ...artifact }] }),
    traversalPortablePathRejected: rejectsArtifact({ artifacts: [{ ...artifact, path: `../${portableName}` }] }),
    absolutePortablePathRejected: rejectsArtifact({
      artifacts: [{ ...artifact, path: [["Q", ":"].join(""), "release", portableName].join("\\") }],
    }),
    logParserAcceptsCanonicalLine: parseLogLine(validLine)?.level === "debug",
    logParserRejectsMissingMilliseconds: parseLogLine(validLine.replace(".789", "")) === null,
    logParserRejectsLowercaseLevel: parseLogLine(validLine.replace("[DEBUG]", "[debug]")) === null,
    logParserRejectsMalformedJson: parseLogLine(validLine.replace(/\{.*\}$/, "{bad}")) === null,
    logParserRejectsNonObjectData: parseLogLine(validLine.replace(/\{.*\}$/, "[1,2]")) === null,
    privacyScannerDetectsRaw: privacyFindings(`value=${selfRaw}`, selfForbidden, { rejectAbsolutePaths: false }).length > 0,
    privacyScannerAllowsAliases: privacyFindings(
      "usr_0123456789abcdef prj_0123456789abcdef avt_0123456789abcdef net_0123456789abcdef",
      selfForbidden,
      { rejectAbsolutePaths: false },
    ).length === 0,
    privacyScannerDetectsPrivateStoreNames: privacyFindings(privateMappingName, selfForbidden).length > 0,
    safeReportAccepted: reportPrivacyFindings(JSON.stringify(safeReport), selfForbidden).length === 0,
    unsafeReportRawRejected: reportPrivacyFindings(JSON.stringify({ value: selfRaw }), selfForbidden).length > 0,
    unsafeReportPathRejected: reportPrivacyFindings(JSON.stringify({ value: selfPath }), selfForbidden).length > 0,
    unsafeReportChallengeRejected: reportPrivacyFindings(
      JSON.stringify({ challengeId: "opaque-self-test-value" }),
      selfForbidden,
    ).length > 0,
    timing4999Disabled: challengeReady(4_999) === false,
    timing5000Enabled: challengeReady(5_000) === true,
    compressionCoreLoadsBeforeFileSystem: powershellCompressionPrelude.indexOf("System.IO.Compression\n")
      < powershellCompressionPrelude.indexOf("System.IO.Compression.FileSystem"),
    scopedPackagePathAccepted: processPathInScope(scopedExe, scopedRoot) === true,
    siblingPrefixRejected: processPathInScope(siblingExe, scopedRoot) === false,
    unrelatedShellPathRejected: processPathInScope(
      [[["C", ":"].join(""), "Windows"].join("\\"), "explorer.exe"].join("\\"),
      scopedRoot,
    ) === false,
  };
  const failed = Object.entries(checks).filter(([, passed]) => passed !== true).map(([name]) => name);
  console.log(JSON.stringify({
    schema: "vrcforge.packaged_logging_probe.self_test.v1",
    ok: failed.length === 0,
    checks,
    failed,
  }, null, 2));
  if (failed.length > 0) process.exitCode = 1;
}

async function main() {
  await mkdir(evidenceRoot, { recursive: true });
  const report = {
    schema: "vrcforge.packaged_logging_probe.v1",
    version: EXPECTED_VERSION,
    mode: allowUnpushed ? "local-preacceptance" : "strict-release",
    transports: [
      "packaged-webview-dom",
      "packaged-webview-tauri-ipc",
      "authenticated-loopback-rest",
    ],
    releaseBinding: {},
    runtimeBinding: {},
    defaultState: {},
    diagnosticsUi: {},
    logLevels: {},
    logFiles: {},
    privacy: {},
    identityStability: {},
    privateStore: {},
    supportBundle: {},
    developerOptions: {},
    openLogsFolder: {},
    completionBinding: {},
    cleanup: {
      firstClosureGraceful: false,
      secondClosureGraceful: false,
      packageProcessesClear: false,
      portsClear: false,
      scopedOnly: true,
    },
    assertions: [],
    ok: false,
  };
  const privateFixture = createPrivateFixture();
  let fixtures;
  let app;
  let firstClosure;
  let secondClosure;
  let binding;
  let stage = "preflight";
  try {
    if (!Number.isInteger(cdpPort) || cdpPort < 1_024 || cdpPort > 65_535 || cdpPort === 8_757) {
      throw new Error("Logging probe CDP port was invalid.");
    }

    stage = "manifest-binding";
    binding = await prepareManifestBoundPackage();
    packageExecutableNames = await executableBasenames(packagedRoot);
    if (!packageExecutableNames.some((name) => name.toLowerCase() === "vrcforge.exe")
      || !packageExecutableNames.some((name) => name.toLowerCase() === "vrcforge_backend.exe")) {
      throw new Error("Extracted package executable scope was incomplete.");
    }
    report.releaseBinding = {
      version: binding.version,
      manifestCommit: binding.manifestCommit,
      headCommit: binding.headCommit,
      originMainCommit: binding.originMainCommit,
      headEqualsOriginMain: binding.headCommit === binding.originMainCommit,
      manifestEqualsHead: binding.manifestCommit === binding.headCommit,
      worktreeClean: binding.worktreeClean,
      strictBuildPolicy: strictBuildPolicyFromManifest({ buildPolicy: binding.buildPolicy }).strict,
      buildPolicy: binding.buildPolicy,
      strictReleaseBinding: binding.strictReleaseBinding,
      portableName: binding.portableName,
      portableSha256: binding.portableSha256,
      mainSha256: binding.mainSha256,
      backendSha256: binding.backendSha256,
      embeddedVersionMatches: binding.embeddedVersion === EXPECTED_VERSION,
    };

    stage = "isolated-fixtures";
    await Promise.all([
      mkdir(configRoot, { recursive: true }),
      mkdir(artifactRoot, { recursive: true }),
      mkdir(webviewDataRoot, { recursive: true }),
    ]);
    const preflight = await processSnapshot();
    if (!snapshotIsClear(preflight)) {
      throw new Error("Preflight found an occupied package process or probe port; nothing was terminated.");
    }
    fixtures = await preseedLogRetentionFixtures(privateFixture);

    stage = "first-launch";
    app = await launchPackagedApp();
    const firstRuntime = await verifyRuntimeBinding(binding);
    const firstBackendKey = processIdentityKey(firstRuntime.backendIdentity);
    report.runtimeBinding = {
      authNegativeControls: firstRuntime.authNegativeControls,
      authenticatedVersion: firstRuntime.authenticatedVersion,
      portableMode: firstRuntime.portableMode,
      isolatedPaths: firstRuntime.isolatedPaths,
      backendListenerBound: firstRuntime.backendListenerBound,
      sameBackendImmediateReadback: false,
    };
    await openGeneralSettings(app.cdp);
    await installWebViewInstrumentation(app.cdp);
    const initialUi = await inspectGeneralDiagnosticsUi(app.cdp);
    const [initialTauriDiagnostics, initialRestDiagnostics, initialAdvanced] = await Promise.all([
      tauriInvoke(app.cdp, "fetch_diagnostics", { request: { timeoutMs: 30_000 } }),
      appApi("/api/app/diagnostics"),
      advancedSettingsState(),
    ]);
    if (
      initialTauriDiagnostics?.logLevel !== "info"
      || initialRestDiagnostics?.logLevel !== "info"
      || initialTauriDiagnostics?.debugLogging !== false
      || initialRestDiagnostics?.debugLogging !== false
      || initialAdvanced?.developerOptionsEnabled !== false
    ) throw new Error("Isolated diagnostics/Developer Options defaults were incorrect.");
    const fiveLevelRange = initialUi.rangePresent
      && initialUi.rangeMin === "0"
      && initialUi.rangeMax === "4"
      && initialUi.rangeStep === "1"
      && initialUi.rangeValue === "2"
      && initialUi.rangeLevel === "info"
      && initialUi.ariaValueTextPresent;
    report.defaultState = {
      developerOptionsDisabled: true,
      logLevelInfo: true,
      developerLowLevelHidden: false,
    };
    report.diagnosticsUi = {
      panelPresent: initialUi.panelPresent === true,
      fiveLevelRange,
      openButtonPresent: initialUi.openButtonPresent === true,
      exportButtonPresent: initialUi.exportButtonPresent === true,
      developerTogglePresent: initialUi.developerTogglePresent === true,
      baselineSettingsNavCount: Number(initialUi.settingsNavCount || 0),
    };
    if (!report.diagnosticsUi.panelPresent || !fiveLevelRange
      || !report.diagnosticsUi.openButtonPresent || !report.diagnosticsUi.exportButtonPresent) {
      throw new Error("Settings General diagnostics DOM contract failed.");
    }

    stage = "retention-cleanup";
    const startupRetention = await waitForStartupRetention(fixtures);
    const infoViaTauri = await tauriInvoke(app.cdp, "update_diagnostics", {
      request: { logLevel: "info", timeoutMs: 30_000 },
    });
    const infoViaRest = await appApi("/api/app/diagnostics", {
      method: "POST",
      body: { logLevel: "info" },
    });
    if (infoViaTauri?.logLevel !== "info" || infoViaRest?.logLevel !== "info") {
      throw new Error("Initial diagnostics same-process readback failed.");
    }
    const activeName = String(infoViaRest?.activeLogFile || "");
    if (!canonicalLogName.test(activeName) || basename(activeName) !== activeName) {
      throw new Error("Active log filename was not canonical.");
    }
    const activePath = resolve(logRoot, activeName);
    const activeRetained = await exists(activePath);
    const boundedStats = await logDirectoryStats();
    if (
      !activeRetained
      || boundedStats.files.length > 40
      || boundedStats.totalBytes > 52_428_800
      || startupRetention.sparseSurvivors.length >= fixtures.sparse.length
    ) throw new Error("Active log/count/size retention contract failed.");
    const sparseSurvivorCount = startupRetention.sparseSurvivors.length;
    await removeKnownSparseFixtures(fixtures);
    if (!(await exists(activePath))) throw new Error("Active log was removed with probe fixtures.");
    report.logFiles = {
      legacyDeleted: !startupRetention.legacyPresent.some(Boolean),
      expiredDeleted: !startupRetention.expiredPresent.some(Boolean),
      sparseInitialCount: fixtures.sparse.length,
      sparseInitialLogicalBytes: fixtures.logicalBytes,
      sparseSurvivorCount,
      countBounded: boundedStats.files.length <= 40,
      totalBytesBounded: boundedStats.totalBytes <= 52_428_800,
      boundedFileCount: boundedStats.files.length,
      boundedTotalBytes: boundedStats.totalBytes,
      activeRetained: true,
      activeFilenameCanonical: true,
      canonicalNamesOnly: false,
      utf8AndPhysicalGrammar: false,
    };

    stage = "live-levels";
    const beforeInfoControl = await scanLogDirectory();
    const infoHttpBefore = httpInteractionCount(beforeInfoControl);
    await appApi("/api/app/bootstrap?phase=info-control");
    await sleep(150);
    const afterInfoControl = await scanLogDirectory();
    const infoHttpAfter = httpInteractionCount(afterInfoControl);
    await setLogSlider(app.cdp, 3);
    const debugState = await waitForDiagnosticsLevel(app.cdp, "debug", 3);
    const afterDebug = await scanLogDirectory();
    const debugHttpCount = httpInteractionCount(afterDebug);
    await setLogSlider(app.cdp, 4);
    const traceState = await waitForDiagnosticsLevel(app.cdp, "trace", 4);
    const directTraceAria = traceState.dom.aria;

    stage = "first-privacy-chain";
    const firstPrivate = await injectPrivateContext("trace", privateFixture, "first-launch");
    const liveSnapshot = await processSnapshot({ track: true });
    const liveBackend = liveSnapshot.portOwners.find((owner) =>
      liveSnapshot.ports.some((port) => Number(port?.LocalPort || 0) === 8_757
        && Number(port?.OwningProcess || 0) === owner.pid));
    report.runtimeBinding.sameBackendImmediateReadback = Boolean(liveBackend)
      && processIdentityKey(liveBackend) === firstBackendKey;
    await rapidlySetLogSlider(app.cdp, [2, 0, 3, 1, 4]);
    const rapidFinal = await waitForDiagnosticsLevel(app.cdp, "trace", 4);
    report.logLevels = {
      debugReachedViaDom: debugState.status?.logLevel === "debug" && debugState.status?.debugLogging === true,
      traceReachedViaDom: traceState.status?.logLevel === "trace" && traceState.status?.debugLogging === true,
      rapidChangeCount: 5,
      rapidFinalServerMatches: rapidFinal.status?.logLevel === "trace",
      rapidFinalDomMatches: rapidFinal.dom?.level === "trace" && rapidFinal.dom?.value === "4",
      rapidFinalAriaMatches: rapidFinal.dom?.aria === directTraceAria,
      infoInteractionSuppressed: infoHttpBefore === infoHttpAfter,
      debugInteractionRecorded: debugHttpCount > infoHttpAfter,
    };
    if (!report.runtimeBinding.sameBackendImmediateReadback
      || !report.logLevels.rapidFinalServerMatches || !report.logLevels.rapidFinalAriaMatches
      || !report.logLevels.infoInteractionSuppressed || !report.logLevels.debugInteractionRecorded) {
      throw new Error("Live diagnostics level/readback contract failed.");
    }

    const firstStore = await inspectPrivateIdentityStore(privateFixture, firstPrivate.chain);
    const privateStoreProjectionExcluded = await assertPrivateStoreProjectionAbsent(
      app.cdp,
      firstPrivate.diagnostics,
    );
    const firstLogScan = await scanLogDirectory();
    const requiredAliases = [
      firstPrivate.chain.userAlias,
      firstPrivate.chain.projectAlias,
      firstPrivate.chain.avatarAlias,
      ...firstStore.networkAliases,
    ];
    if (!requiredAliases.every((alias) => firstLogScan.aliases.includes(alias))) {
      throw new Error("Canonical logs omitted a required redacted identity alias.");
    }
    const firstInstrumentation = await webViewInstrumentationSnapshot(app.cdp);
    const firstEventEvidence = validateEventEvidence(firstInstrumentation, requiredAliases);
    report.privacy = {
      queryEncodingVerified: firstPrivate.queryEncodingVerified,
      diagnosticsPostIdentityReadback: true,
      rawValuesAbsentFromLogs: true,
      allAliasKindsObserved: true,
      aliases: {
        user: firstPrivate.chain.userAlias,
        project: firstPrivate.chain.projectAlias,
        avatar: firstPrivate.chain.avatarAlias,
        network: firstStore.networkAliases,
      },
      eventPrivacyClean: firstEventEvidence.privacyScanClean,
      firstLaunchEventCount: firstEventEvidence.capturedLogEventCount,
    };
    report.privateStore = {
      mappingPresent: firstStore.mappingPresent,
      rawIdentityMappingPresent: firstStore.rawIdentityMappingPresent,
      secretsExcludedFromMapping: firstStore.secretsExcludedFromMapping,
      keyPresent: firstStore.keyPresent,
      keyIs32Bytes: firstStore.keyBytes === 32,
      aliasesBound: firstStore.aliasesBound,
      observableProjectionExcluded: privateStoreProjectionExcluded,
    };
    report.logFiles.canonicalNamesOnly = true;
    report.logFiles.utf8AndPhysicalGrammar = firstLogScan.nonblankLines > 0;
    report.logFiles.finalCanonicalFileCountFirstLaunch = firstLogScan.fileCount;
    report.logFiles.finalCanonicalBytesFirstLaunch = firstLogScan.totalBytes;

    stage = "first-close";
    app.cdp.close();
    app.cdp = null;
    firstClosure = await closePackagedApp(app);
    report.cleanup.firstClosureGraceful = closureSucceeded(firstClosure);
    if (!report.cleanup.firstClosureGraceful) throw new Error("First packaged launch did not close gracefully.");
    app = undefined;

    stage = "second-launch";
    app = await launchPackagedApp();
    const secondRuntime = await verifyRuntimeBinding(binding);
    await openGeneralSettings(app.cdp);
    await installWebViewInstrumentation(app.cdp);
    const [restartTauriDiagnostics, restartRestDiagnostics] = await Promise.all([
      tauriInvoke(app.cdp, "fetch_diagnostics", { request: { timeoutMs: 30_000 } }),
      appApi("/api/app/diagnostics"),
    ]);
    if (restartTauriDiagnostics?.logLevel !== "trace" || restartRestDiagnostics?.logLevel !== "trace") {
      throw new Error("Diagnostics level was not durable across restart.");
    }
    const secondPrivate = await injectPrivateContext("trace", privateFixture, "second-launch");
    const secondStore = await inspectPrivateIdentityStore(privateFixture, secondPrivate.chain);
    const stableAcrossRestart = identityChainsEqual(firstPrivate.chain, secondPrivate.chain)
      && firstStore.networkAliases.join("|") === secondStore.networkAliases.join("|");
    if (!stableAcrossRestart) throw new Error("Diagnostic aliases changed across restart.");
    report.identityStability = {
      sameUserDataRestarted: true,
      sameValuesReinjected: true,
      stableAcrossRestart,
      userProjectAvatarChainStable: identityChainsEqual(firstPrivate.chain, secondPrivate.chain),
      networkAliasesStable: firstStore.networkAliases.join("|") === secondStore.networkAliases.join("|"),
    };
    report.runtimeBinding.secondLaunchBackendListenerBound = secondRuntime.backendListenerBound;

    stage = "developer-options";
    report.developerOptions = await exerciseDeveloperOptions(app.cdp, Number(initialUi.settingsNavCount || 0));
    report.defaultState.developerLowLevelHidden = report.developerOptions.developerNavHiddenByDefault === true;
    const developerInstrumentation = await webViewInstrumentationSnapshot(app.cdp);
    const developerEventEvidence = validateEventEvidence(developerInstrumentation, requiredAliases);
    report.privacy.eventPrivacyClean = developerEventEvidence.privacyScanClean;
    report.privacy.secondLaunchEventCount = developerEventEvidence.capturedLogEventCount;

    stage = "support-bundle";
    const bundleResponse = await appApi("/api/app/support-bundle", {
      method: "POST",
      timeoutMs: 120_000,
      body: { includeFullPaths: true, logLimit: 500 },
    });
    if (bundleResponse?.ok !== true || bundleResponse?.redacted !== true || !bundleResponse?.bundlePath) {
      throw new Error("Support bundle creation failed.");
    }
    const bundleProof = await validateSupportBundle(String(bundleResponse.bundlePath), secondStore);
    report.supportBundle = {
      includeFullPathsRequested: bundleProof.includeFullPathsRequested,
      effectiveFullPathsDisabled: bundleProof.includeFullPathsEffective === false,
      memberCount: bundleProof.memberCount,
      totalBytes: bundleProof.totalBytes,
      identityProjectionExcluded: bundleProof.identityProjectionExcluded,
      privateStoreFilesExcluded: bundleProof.privateStoreFilesExcluded,
      readableRedactedLogExcerpt: bundleProof.readableRedactedLogExcerpt,
      privacyScanClean: bundleProof.privacyScanClean,
    };

    stage = "open-logs-folder";
    await openGeneralSettings(app.cdp);
    report.openLogsFolder = await invokeOpenLogsFolderOnce(app.cdp);

    stage = "final-log-event-scan";
    const finalLogScan = await scanLogDirectory();
    if (!requiredAliases.every((alias) => finalLogScan.aliases.includes(alias))) {
      throw new Error("Final canonical log scan omitted required aliases.");
    }
    const finalInstrumentation = await webViewInstrumentationSnapshot(app.cdp);
    const finalEventEvidence = validateEventEvidence(finalInstrumentation, requiredAliases);
    report.privacy.eventPrivacyClean = finalEventEvidence.privacyScanClean;
    report.privacy.finalEventCount = finalEventEvidence.capturedLogEventCount;
    report.logFiles.finalCanonicalFileCount = finalLogScan.fileCount;
    report.logFiles.finalCanonicalBytes = finalLogScan.totalBytes;
    report.logFiles.finalNonblankLineCount = finalLogScan.nonblankLines;
    report.logFiles.canonicalNamesOnly = true;
    report.logFiles.utf8AndPhysicalGrammar = finalLogScan.nonblankLines > 0;

    stage = "second-close";
    app.cdp.close();
    app.cdp = null;
    secondClosure = await closePackagedApp(app);
    report.cleanup.secondClosureGraceful = closureSucceeded(secondClosure);
    if (!report.cleanup.secondClosureGraceful) throw new Error("Second packaged launch did not close gracefully.");
    app = undefined;

    stage = "completion-binding";
    const completionGit = await gitBindingSnapshot();
    const completionManifest = JSON.parse((await readFile(
      resolve(repoRoot, "dist", "release", "release-manifest.json"),
      "utf8",
    )).replace(/^\uFEFF/, ""));
    const completionArtifact = selectManifestPortableArtifact(completionManifest, binding.portableName);
    const [completionMainHash, completionBackendHash, completionPortableHash] = await Promise.all([
      sha256File(exe),
      sha256File(backendExe),
      sha256File(resolve(evidenceRoot, binding.portableName)),
    ]);
    const completionStable = completionGit.sourceVersion === EXPECTED_VERSION
      && completionGit.head === binding.headCommit
      && completionGit.originMain === binding.originMainCommit
      && binding.manifestCommit === binding.headCommit
      && String(completionManifest?.version || "") === EXPECTED_VERSION
      && String(completionManifest?.commit || "").toLowerCase() === binding.manifestCommit
      && String(completionArtifact?.sha256 || "").toLowerCase() === binding.portableSha256
      && completionPortableHash === binding.portableSha256
      && completionMainHash === binding.mainSha256
      && completionBackendHash === binding.backendSha256
      && (allowUnpushed || completionGit.worktreeClean === true);
    report.completionBinding = {
      stable: completionStable,
      headUnchanged: completionGit.head === binding.headCommit,
      originMainUnchanged: completionGit.originMain === binding.originMainCommit,
      versionUnchanged: completionGit.sourceVersion === EXPECTED_VERSION,
      executableHashesUnchanged: completionMainHash === binding.mainSha256
        && completionBackendHash === binding.backendSha256,
      manifestAndPortableHashUnchanged: String(completionManifest?.commit || "").toLowerCase() === binding.manifestCommit
        && String(completionArtifact?.sha256 || "").toLowerCase() === binding.portableSha256
        && completionPortableHash === binding.portableSha256,
      worktreeClean: completionGit.worktreeClean,
    };
    if (!completionStable) throw new Error("Completion package/Git binding drifted.");
  } catch {
    report.failureStage = stage;
    addAssertion(report, `probe aborted during ${stage}`);
  } finally {
    if (app?.cdp) {
      try { app.cdp.close(); } catch { /* Renderer may already be gone. */ }
      app.cdp = null;
    }
    if (app) {
      try {
        const closure = await closePackagedApp(app);
        if (!firstClosure) {
          firstClosure = closure;
          report.cleanup.firstClosureGraceful = closureSucceeded(closure);
        } else if (!secondClosure) {
          secondClosure = closure;
          report.cleanup.secondClosureGraceful = closureSucceeded(closure);
        }
      } catch {
        addAssertion(report, "graceful final cleanup failed");
        const forced = await scopedCleanup().catch(() => ({ ok: false }));
        if (!forced.ok) addAssertion(report, "exact scoped fallback cleanup failed");
      }
    }
    let finalSnapshot;
    try {
      finalSnapshot = await processSnapshot();
      if (!snapshotIsClear(finalSnapshot) && trackedProcesses.size > 0) {
        const exactFallback = await scopedCleanup();
        if (!exactFallback.ok) addAssertion(report, "exact scoped final cleanup failed");
        finalSnapshot = await processSnapshot();
      }
      report.cleanup.packageProcessesClear = finalSnapshot.packageProcesses.length === 0;
      report.cleanup.portsClear = finalSnapshot.portQuerySucceeded === true && finalSnapshot.ports.length === 0;
      report.cleanup.finalPackageProcessCount = finalSnapshot.packageProcesses.length;
      report.cleanup.finalPortListenerCount = finalSnapshot.ports.length;
      if (!snapshotIsClear(finalSnapshot)) addAssertion(report, "package processes or probe ports remained after cleanup");
    } catch {
      addAssertion(report, "final process/port verification failed");
    }
    validateFinalContract(report);
    const written = await writeSafeReport(report);
    report.ok = written.ok === true;
  }
  console.log(relative(repoRoot, reportPath).replaceAll("\\", "/"));
  if (!report.ok) {
    console.error("Packaged logging probe failed; inspect the safe report.");
    process.exitCode = 1;
  }
}

if (selfTest) {
  runSelfTest();
} else {
  main().catch(async () => {
    await mkdir(evidenceRoot, { recursive: true }).catch(() => undefined);
    await writeFile(reportPath, `${JSON.stringify({
      schema: "vrcforge.packaged_logging_probe.v1",
      version: EXPECTED_VERSION,
      mode: allowUnpushed ? "local-preacceptance" : "strict-release",
      ok: false,
      assertions: ["unhandled packaged logging probe failure"],
    }, null, 2)}\n`, "utf8").catch(() => undefined);
    console.error("Packaged logging probe failed before normal report finalization.");
    process.exit(1);
  });
}
