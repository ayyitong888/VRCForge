import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const allowUnpushed = process.argv.includes("--allow-unpushed");
const selfTest = process.argv.includes("--self-test");
const cdpPort = Number(process.env.VRCFORGE_QUESTION_PROBE_CDP_PORT || "9350");
const marker = `QUESTION_RESTART_PROBE_${Date.now()}`;
const evidenceRoot = resolve(repoRoot, "artifacts", "actual-app-progress", marker);
const packagedRoot = resolve(evidenceRoot, "package");
const exe = resolve(packagedRoot, "VRCForge.exe");
const userDataRoot = resolve(evidenceRoot, "user-data");
const configRoot = resolve(userDataRoot, "config");
const projectCachePath = resolve(userDataRoot, "project-cache.json");
const webviewDataRoot = resolve(evidenceRoot, "webview2-user-data");
const hostProfileRoot = resolve(evidenceRoot, "host-profile");
const projectARoot = resolve(evidenceRoot, "projects", "project-a");
const projectBRoot = resolve(evidenceRoot, "projects", "project-b");
const questionLogPath = resolve(
  userDataRoot,
  "artifacts",
  "dashboard",
  "agent_gateway",
  "agent-questions.jsonl",
);
const reportPath = resolve(evidenceRoot, "report.json");
const appOrigin = "http://127.0.0.1:8757";
const appRequestOrigin = "http://tauri.localhost";
const sensitiveToken = ["s", "k", "-", "1145141919810"].join("");
const privateProfileName = "PrivateName";
const sensitivePath = ["C:", "\\", "Users", "\\", privateProfileName, "\\", `${marker}.txt`].join("");
const sensitiveAnswer = `Use ${sensitiveToken} from ${sensitivePath}`;
const wrongAuthToken = `wrong-${marker}`;
const probeSecrets = new Set([sensitiveToken, privateProfileName, sensitivePath, sensitiveAnswer, wrongAuthToken]);
let appSessionToken = "";

const allowedOptions = new Set(["--allow-unpushed", "--self-test", "--help", "-h"]);
if (process.argv.slice(2).some((item) => !allowedOptions.has(item))) {
  console.error("Unknown packaged Question lifecycle probe option.");
  process.exit(2);
}

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`Usage: node scripts/diagnose_packaged_progress_questions.mjs [--allow-unpushed] [--self-test]

Runs the packaged AgentQuestionService create/list/answer/restart lifecycle gate.
Default mode requires strict release evidence. --allow-unpushed is non-release
local acceptance only: it still requires a clean worktree, manifest commit ==
HEAD, VERSION and manifest-bound ZIP/executable hashes, plus an explicit
local-acceptance, release-ineligible build policy.

Coverage:
  - isolated user-data, config, host AppData and WebView2 data
  - fail-closed existing VRCForge process/backend/CDP-port preflight
  - missing and wrong app-session authentication rejection
  - authenticated create/list/answer with exact session/project scope
  - cross-session and cross-project answer rejection with HTTP 404
  - sensitive answer redaction in answer/list/restart projections and JSONL
  - answered-state recovery after a graceful packaged restart
  - tracked packaged-root process cleanup only

Blocked Goal rearm remains a separate packaged Goal lifecycle gate because it
requires a genuine scheduled delivery, provider turn and blocked state. This
probe does not seed private Goal storage or claim that coverage.

--self-test validates pure policy, isolation and projection helpers without
reading a package, reserving a port or starting VRCForge.

Optional environment:
  VRCFORGE_QUESTION_PROBE_CDP_PORT=<unused port> (default: ${cdpPort})`);
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

function containsSensitiveText(value, secrets = probeSecrets) {
  const serialized = typeof value === "string" ? value : JSON.stringify(value);
  return [...secrets].some((secret) => secret && serialized.includes(secret));
}

function redactProbeSecrets(value, seen = new WeakSet()) {
  if (typeof value === "string") {
    let redacted = value;
    for (const secret of probeSecrets) {
      if (secret) redacted = redacted.split(secret).join("<redacted-probe-value>");
    }
    return redacted;
  }
  if (Array.isArray(value)) {
    if (seen.has(value)) return "<circular>";
    seen.add(value);
    const redacted = value.map((item) => redactProbeSecrets(item, seen));
    seen.delete(value);
    return redacted;
  }
  if (value && typeof value === "object") {
    if (seen.has(value)) return "<circular>";
    seen.add(value);
    const redacted = Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, redactProbeSecrets(item, seen)]),
    );
    seen.delete(value);
    return redacted;
  }
  return value;
}

function projectQuestion(payload, questionId) {
  const candidates = Array.isArray(payload?.questions)
    ? payload.questions
    : payload?.question && typeof payload.question === "object"
      ? [payload.question]
      : [];
  const question = candidates.find((item) => String(item?.questionId || item?.id || "") === questionId);
  return question ? {
    found: true,
    questionId: String(question.questionId || question.id || ""),
    status: String(question.status || ""),
    selectedOptionId: String(question.selectedOptionId || ""),
    answerPresent: Boolean(String(question.answer || "")),
    optionCount: Array.isArray(question.options) ? question.options.length : 0,
  } : {
    found: false,
    questionId: "",
    status: "",
    selectedOptionId: "",
    answerPresent: false,
    optionCount: 0,
  };
}

function isolatedLaunchEnvironment() {
  const inherited = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.toUpperCase().startsWith("VRCFORGE_")),
  );
  for (const key of [
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "OLLAMA_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS", "LLM_API_KEY",
  ]) {
    delete inherited[key];
  }
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
  ) {
    throw new Error("self-test: unsafe local-acceptance policy was accepted.");
  }
  const isolatedCredentialKeys = [
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "OLLAMA_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS", "LLM_API_KEY",
  ];
  const previousEnvironment = new Map(
    ["VRCFORGE_DISABLE_APP_AUTH", "USERPROFILE", "HOME", ...isolatedCredentialKeys]
      .map((key) => [key, process.env[key]]),
  );
  process.env.VRCFORGE_DISABLE_APP_AUTH = "1";
  process.env.USERPROFILE = "host-user-profile-preserved";
  process.env.HOME = "host-home-preserved";
  for (const key of isolatedCredentialKeys) process.env[key] = `must-not-escape-${key}`;
  try {
    const env = isolatedLaunchEnvironment();
    if (
      Object.hasOwn(env, "VRCFORGE_DISABLE_APP_AUTH")
      || isolatedCredentialKeys.some((key) => Object.hasOwn(env, key))
      || env.VRCFORGE_USER_DATA_DIR !== userDataRoot
      || env.VRCFORGE_CONFIG_PATH !== resolve(configRoot, "config.json")
      || env.VRCFORGE_DESKTOP_EXECUTOR !== "0"
      || env.APPDATA !== hostProfileRoot
      || env.LOCALAPPDATA !== hostProfileRoot
      || env.USERPROFILE !== "host-user-profile-preserved"
      || env.HOME !== "host-home-preserved"
      || env.WEBVIEW2_USER_DATA_FOLDER !== webviewDataRoot
    ) {
      throw new Error("self-test: packaged runtime paths or credentials were not isolated.");
    }
  } finally {
    for (const [key, previous] of previousEnvironment) {
      if (previous === undefined) delete process.env[key];
      else process.env[key] = previous;
    }
  }
  const projected = projectQuestion(
    { questions: [{ questionId: "question-1", status: "answered", answer: "<redacted>" }] },
    "question-1",
  );
  if (!projected.found || projected.status !== "answered" || !projected.answerPresent) {
    throw new Error("self-test: Question projection classification failed.");
  }
  if (!containsSensitiveText({ answer: sensitiveAnswer }) || containsSensitiveText({ answer: "<redacted>" })) {
    throw new Error("self-test: sensitive answer disclosure classification failed.");
  }
  const redacted = redactProbeSecrets({ answer: sensitiveAnswer });
  if (containsSensitiveText(redacted)) {
    throw new Error("self-test: report redaction retained a probe secret.");
  }
  console.log("Question lifecycle probe self-test passed");
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
      throw new Error(`Packaged Question lifecycle probe requires ${manifestPath}.`);
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
  const escapedPortable = escapePowerShellLiteral(portablePath);
  const innerExeSha256 = (await runPowerShell(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead('${escapedPortable}')
    try {
      $entries = @($archive.Entries | Where-Object {
        $name = $_.FullName.Replace('\\', '/')
        $name.Equals('VRCForge.exe', [StringComparison]::OrdinalIgnoreCase)
      })
      if ($entries.Count -ne 1) { throw 'Portable package did not contain exactly one VRCForge.exe entry.' }
      $sha = [Security.Cryptography.SHA256]::Create()
      $stream = $entries[0].Open()
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
    exeSha256,
  };
}

async function processSnapshot() {
  const escapedRoot = escapePowerShellLiteral(packagedRoot);
  const raw = await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${escapedRoot}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $vrcforge = @(foreach ($process in Get-Process -Name VRCForge -ErrorAction SilentlyContinue) {
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { $path = '' }
      [pscustomobject]@{ Id = $process.Id; ProcessName = $process.ProcessName; Path = $path }
    })
    $rooted = @(foreach ($process in Get-Process -ErrorAction SilentlyContinue) {
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { continue }
      if ($path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        [pscustomobject]@{ Id = $process.Id; ProcessName = $process.ProcessName; Path = $path }
      }
    })
    $ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
      Where-Object { $_.LocalPort -eq 8757 -or $_.LocalPort -eq ${cdpPort} } |
      Select-Object LocalAddress,LocalPort,State,OwningProcess)
    [pscustomobject]@{ vrcforgeProcesses = $vrcforge; packagedRootProcesses = $rooted; ports = $ports } |
      ConvertTo-Json -Depth 5 -Compress
  `);
  return raw ? JSON.parse(raw) : { vrcforgeProcesses: [], packagedRootProcesses: [], ports: [] };
}

async function hostUnityProcesses() {
  const raw = await runPowerShell(`
    @(Get-Process -Name Unity -ErrorAction SilentlyContinue |
      Select-Object Id,ProcessName) | ConvertTo-Json -Depth 3 -Compress
  `);
  if (!raw) return [];
  const parsed = JSON.parse(raw);
  return Array.isArray(parsed) ? parsed : [parsed];
}

function snapshotIsClear(snapshot) {
  return (snapshot.vrcforgeProcesses || []).length === 0
    && (snapshot.packagedRootProcesses || []).length === 0
    && (snapshot.ports || []).length === 0;
}

async function waitForPackagedClear(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let latest = await processSnapshot();
  while (Date.now() < deadline) {
    if (snapshotIsClear(latest)) return { ok: true, snapshot: latest };
    await sleep(500);
    latest = await processSnapshot();
  }
  return { ok: snapshotIsClear(latest), snapshot: latest };
}

async function forceCloseLaunch(launch) {
  if (!launch?.childPid) return processSnapshot();
  const escapedRoot = escapePowerShellLiteral(packagedRoot);
  const escapedExe = escapePowerShellLiteral(exe);
  const rootPid = Number(launch.childPid);
  await runPowerShell(`
    $root = [IO.Path]::GetFullPath('${escapedRoot}').TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    $prefix = $root + [IO.Path]::DirectorySeparatorChar
    $exe = [IO.Path]::GetFullPath('${escapedExe}')
    $rootProcessId = [int]${rootPid}
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
    $targets = @(foreach ($processId in $ids) {
      $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
      if (-not $process) { continue }
      try { $path = [IO.Path]::GetFullPath([string]$process.Path) } catch { continue }
      $allowed = if ($processId -eq $rootProcessId) {
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
  const cleared = await waitForPackagedClear();
  if (!cleared.ok) {
    throw new Error(`Tracked packaged launch did not clear without touching other instances: ${JSON.stringify(cleared.snapshot)}`);
  }
  return cleared.snapshot;
}

async function closePackagedApp(launch) {
  if (!launch?.childPid) throw new Error("Tracked packaged launch was unavailable for close.");
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
  const targets = Array.isArray(requested?.targets)
    ? requested.targets
    : requested?.targets
      ? [requested.targets]
      : [];
  const closeAccepted = targets.length === 1
    && Number(targets[0]?.pid) === rootPid
    && Number(targets[0]?.mainWindowHandle) !== 0
    && targets[0]?.closeRequested === true;
  const graceful = await waitForPackagedClear();
  if (graceful.ok) {
    return { requested, trackedPid: rootPid, closeAccepted, graceful: closeAccepted, forced: false, finalSnapshot: graceful.snapshot };
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
  const targets = Array.isArray(closure?.requested?.targets)
    ? closure.requested.targets
    : closure?.requested?.targets
      ? [closure.requested.targets]
      : [];
  if (!closure?.graceful) addAssertion(report, `packaged app did not complete an accepted graceful close ${label}`);
  if (targets.length !== 1 || Number(targets[0]?.pid) !== Number(closure?.trackedPid)) {
    addAssertion(report, `packaged app did not target exactly its tracked main process ${label}`);
  }
  if (!snapshotIsClear(closure?.finalSnapshot || {})) {
    addAssertion(report, `packaged processes or probe ports remained ${label}`);
  }
}

async function waitForJson(url, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.json();
      lastError = new Error(`${response.status} ${response.statusText}`);
    } catch (error) {
      lastError = error;
    }
    await sleep(150);
  }
  const cause = String(lastError?.message || lastError || "endpoint unavailable");
  throw new Error(`Timed out waiting for ${url}; last=${cause}`);
}

function connectCdp(webSocketDebuggerUrl) {
  const ws = new WebSocket(webSocketDebuggerUrl);
  let nextId = 1;
  const pending = new Map();
  ws.addEventListener("message", (event) => {
    const payload = JSON.parse(String(event.data));
    if (!payload.id || !pending.has(payload.id)) return;
    const request = pending.get(payload.id);
    pending.delete(payload.id);
    if (payload.error) request.reject(new Error(payload.error.message || JSON.stringify(payload.error)));
    else request.resolve(payload.result);
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
      return new Promise((resolveSend, rejectSend) => pending.set(id, { resolve: resolveSend, reject: rejectSend }));
    },
  };
}

async function evalValue(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) {
    throw new Error(
      result.exceptionDetails.exception?.description
      || result.exceptionDetails.text
      || "Runtime.evaluate failed",
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
      if (last === true || last?.ok) return last;
    } catch (error) {
      last = String(error);
    }
    await sleep(150);
  }
  throw new Error(`Timed out waiting for packaged WebView state; last=${JSON.stringify(last)}`);
}

async function waitForRenderer(cdp, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    try {
      last = await evalValue(cdp, `(() => ({
        ok: Boolean(document.body?.innerText?.length && window.__TAURI_INTERNALS__?.invoke),
        bodyLength: document.body?.innerText?.length || 0,
        tauriInvoke: typeof window.__TAURI_INTERNALS__?.invoke,
      }))()`);
      if (last?.ok) return last;
    } catch (error) {
      last = String(error);
    }
    await sleep(150);
  }
  throw new Error(`Timed out waiting for packaged WebView renderer; last=${JSON.stringify(last)}`);
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
        const detail = error?.stack
          || error?.message
          || (typeof error === "string" ? error : JSON.stringify(error));
        return { ok: false, error: String(detail || "unknown error") };
      }
    })()`,
  );
  if (!envelope?.ok) {
    throw new Error(`Tauri ${command} failed: ${envelope?.error || "unknown error"}`);
  }
  return envelope.value;
}

async function reloadRenderer(cdp) {
  await cdp.send("Page.reload", { ignoreCache: true }).catch((error) => {
    if (!/Promise was collected/i.test(String(error))) throw error;
  });
  await waitForRenderer(cdp);
  await sleep(500);
}

async function activatePersistedChat(cdp, title) {
  const clicked = await waitForEval(
    cdp,
    `(() => {
      const wanted = ${JSON.stringify(title)};
      const sidebar = document.querySelector("aside");
      const leaf = Array.from((sidebar || document).querySelectorAll("*"))
        .find((node) => node.children.length === 0 && String(node.textContent || "").trim() === wanted);
      const target = leaf?.closest("button, [role='button'], a, li, div");
      if (!target) return { ok: false, reason: "saved chat was not rendered in the sidebar" };
      target.click();
      return { ok: true, tag: target.tagName };
    })()`,
  );
  const activeHeader = await waitForEval(
    cdp,
    `(() => {
      const wanted = ${JSON.stringify(title)};
      const labels = Array.from(document.querySelectorAll("header > div:first-child > span"))
        .map((node) => String(node.textContent || "").trim());
      return { ok: labels.includes(wanted), labels };
    })()`,
  );
  return { clicked: clicked?.ok === true, headerMatched: activeHeader?.ok === true };
}

function sameLocalPath(left, right) {
  const normalize = (value) => String(value || "").replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase();
  return Boolean(normalize(left)) && normalize(left) === normalize(right);
}

async function prepareQuestionUiProjectFixture() {
  const projectSettings = resolve(projectARoot, "ProjectSettings");
  await Promise.all([
    mkdir(resolve(projectARoot, "Assets"), { recursive: true }),
    mkdir(resolve(projectARoot, "Packages"), { recursive: true }),
    mkdir(projectSettings, { recursive: true }),
  ]);
  await writeFile(
    resolve(projectSettings, "ProjectVersion.txt"),
    "m_EditorVersion: 2022.3.22f1\n",
    "utf8",
  );
  const now = new Date().toISOString();
  await writeFile(
    projectCachePath,
    `${JSON.stringify({
      schema: "vrcforge.project_snapshot_cache.v1",
      updatedAt: now,
      durationMs: 1,
      snapshot: {
        selectedProjectPath: "",
        unityEditorPath: "",
        projects: [{
          name: "project-a",
          path: projectARoot,
          editorVersion: "2022.3.22f1",
          hasVrcForge: false,
          hasUnityMcpPackage: false,
          selected: false,
          sources: ["configured-root"],
          source: "configured-root",
          activeMcp: false,
          sessionId: "",
          cliInstanceId: "",
          unityVersion: "",
          selectable: true,
        }],
      },
    }, null, 2)}\n`,
    "utf8",
  );
  await writeFile(
    resolve(userDataRoot, "custom-projects.json"),
    `${JSON.stringify({ version: 1, customPaths: [projectARoot], hiddenPaths: [] }, null, 2)}\n`,
    "utf8",
  );
}

async function registerQuestionUiProject(cdp) {
  const bootstrap = await appApi("/api/app/bootstrap");
  const projects = Array.isArray(bootstrap?.health?.projects?.projects)
    ? bootstrap.health.projects.projects
    : [];
  const projectPaths = Array.from(new Set(
    projects.map((project) => String(project?.path || "")).filter(Boolean),
  ));
  if (projectPaths.length !== 1 || !sameLocalPath(projectPaths[0], projectARoot)) {
    throw new Error("Packaged Question UI project snapshot was not isolated to the fixture project.");
  }
  const before = await tauriInvoke(cdp, "fetch_project_prefs", {
    request: { timeoutMs: 30000 },
  });
  if (
    !Array.isArray(before?.customPaths)
    || before.customPaths.length !== 1
    || !sameLocalPath(before.customPaths[0], projectARoot)
    || (before?.hiddenPaths || []).length !== 0
  ) {
    throw new Error("Packaged Question UI fixture project was not read from isolated project preferences.");
  }
  const saved = await tauriInvoke(cdp, "save_project_prefs", {
    request: { customPaths: [projectARoot], hiddenPaths: [], timeoutMs: 30000 },
  });
  if (
    !Array.isArray(saved?.customPaths)
    || saved.customPaths.length !== 1
    || !sameLocalPath(saved.customPaths[0], projectARoot)
    || (saved?.hiddenPaths || []).length !== 0
  ) {
    throw new Error("Packaged Question UI fixture project was not registered through Tauri project preferences.");
  }
  return { cached: true, registered: true, hiddenCount: 0 };
}

async function seedAndActivateQuestionUiChat(cdp) {
  const now = new Date().toISOString();
  const chat = {
    id: `${marker}-ui-chat`,
    sessionId: "",
    title: `${marker} Question UI`,
    projectPath: projectARoot,
    createdAt: now,
    updatedAt: now,
    revision: 1,
    items: [],
  };
  // Discard the renderer's unsaved blank Quick Chat, then persist through the
  // same Tauri command used by the product before hydrating and selecting it.
  await reloadRenderer(cdp);
  const current = await appApi("/api/app/chats");
  const foreignProjectSources = (Array.isArray(current?.sources) ? current.sources : [])
    .filter((source) => String(source?.scope || "") === "project")
    .filter((source) => !sameLocalPath(source?.projectPath, projectARoot));
  if (
    current?.writeBlocked === true
    || (Array.isArray(current?.recoveries) && current.recoveries.length > 0)
    || foreignProjectSources.length > 0
  ) {
    throw new Error("Packaged Question UI chat store was not isolated to the fixture project; no chat write was attempted.");
  }
  const saved = await tauriInvoke(cdp, "save_chats", {
    request: {
      body: {
        chats: [chat],
        sourceRevisions: Array.isArray(current?.sources) ? current.sources : [],
      },
      timeoutMs: 60000,
    },
  });
  const readback = await appApi("/api/app/chats");
  const stored = (Array.isArray(readback?.chats) ? readback.chats : [])
    .find((candidate) => String(candidate?.id || "") === chat.id);
  if (
    String(stored?.sessionId || "") !== chat.sessionId
    || String(stored?.title || "") !== chat.title
    || String(stored?.projectPath || "") !== projectARoot
  ) {
    throw new Error("Packaged Question UI seed was not durably read back with its exact app scope.");
  }
  await reloadRenderer(cdp);
  const activation = await activatePersistedChat(cdp, chat.title);
  return {
    chatId: chat.id,
    sessionId: chat.sessionId,
    title: chat.title,
    projectScoped: true,
    saved: Boolean(saved),
    restReadback: true,
    activation,
  };
}

async function launchPackagedApp() {
  appSessionToken = "";
  const child = spawn(exe, [], { detached: false, stdio: "ignore", env: isolatedLaunchEnvironment() });
  const launch = { childPid: child.pid, launchedAt: new Date().toISOString(), cdp: null };
  const spawnFailure = new Promise((_, rejectSpawn) => child.once("error", rejectSpawn));
  const childExit = new Promise((_, rejectExit) => child.once("exit", (code, signal) => {
    rejectExit(new Error(`Packaged app exited before launch completed: code=${code}, signal=${signal || "none"}.`));
  }));
  const attemptedCdpUrl = `http://127.0.0.1:${cdpPort}/json/list`;
  try {
    const targets = await Promise.race([
      waitForJson(attemptedCdpUrl),
      spawnFailure,
      childExit,
    ]);
    const page = Array.isArray(targets)
      ? targets.find((target) => target?.type === "page" && target?.webSocketDebuggerUrl)
      : undefined;
    if (!page) throw new Error("Packaged WebView2 page target was not found.");
    const cdp = connectCdp(page.webSocketDebuggerUrl);
    launch.cdp = cdp;
    await cdp.opened;
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    const renderer = await waitForRenderer(cdp);
    const health = await Promise.race([
      waitForJson(`${appOrigin}/api/health`),
      childExit,
    ]);
    return { ...launch, cdp, renderer, health };
  } catch (error) {
    try { launch.cdp?.close(); } catch { /* The renderer may not have connected. */ }
    const beforeCleanup = await processSnapshot().catch((snapshotError) => ({
      error: String(snapshotError?.stack || snapshotError),
    }));
    const cleanup = await forceCloseLaunch(launch).catch((cleanupError) => ({
      error: String(cleanupError?.stack || cleanupError),
      finalSnapshot: null,
    }));
    const wrapped = new Error(`Packaged launch failed for ${attemptedCdpUrl}: ${String(error?.message || error)}`);
    wrapped.launchDiagnostics = {
      childPid: child.pid,
      attemptedCdpUrl,
      cause: String(error?.message || error),
      beforeCleanup,
      cleanup,
    };
    throw wrapped;
  }
}

async function readAppToken() {
  const tokenPath = resolve(configRoot, "app-session-token");
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const token = (await readFile(tokenPath, "utf8")).trim();
      if (token) {
        probeSecrets.add(token);
        return token;
      }
    } catch {
      // The managed packaged backend has not written the isolated token yet.
    }
    await sleep(150);
  }
  throw new Error("Packaged app session token was not created in the isolated config root.");
}

async function rawAppRequest(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs || 30000);
  try {
    const headers = { Origin: appRequestOrigin, "Content-Type": "application/json" };
    if (options.token !== undefined) headers.Authorization = `Bearer ${options.token}`;
    const response = await fetch(`${appOrigin}${path}`, {
      method: options.method || "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
    const text = await response.text();
    let payload;
    try { payload = text ? JSON.parse(text) : {}; }
    catch { payload = { text: text.slice(0, 2000) }; }
    return { ok: response.ok, status: response.status, payload };
  } finally {
    clearTimeout(timeout);
  }
}

async function appApi(path, options = {}) {
  if (!appSessionToken) appSessionToken = await readAppToken();
  const result = await rawAppRequest(path, { ...options, token: appSessionToken });
  if (!result.ok) {
    const error = new Error(`${result.status} ${path}: ${JSON.stringify(result.payload)}`);
    error.status = result.status;
    error.payload = result.payload;
    throw error;
  }
  return result.payload;
}

function exactQuestion(payload, questionId) {
  return (Array.isArray(payload?.questions) ? payload.questions : [])
    .find((item) => String(item?.questionId || item?.id || "") === questionId);
}

function pendingQuestionIsUntouched(payload, questionId) {
  const question = exactQuestion(payload, questionId);
  return Number(payload?.count || 0) === 1
    && String(question?.status || "") === "pending"
    && String(question?.answer || "") === "";
}

function answerIsRedactedNonEmpty(question) {
  const answer = String(question?.answer || "");
  return answer.length > 0 && !containsSensitiveText(answer);
}

async function resolveActiveChatScope(cdp, bootstrap) {
  const header = await evalValue(
    cdp,
    `(() => {
      const labels = Array.from(document.querySelectorAll("header > div:first-child > span"))
        .map((node) => (node.textContent || "").trim())
        .filter(Boolean);
      return { projectLabel: labels[0] || "", title: labels.at(-1) || "" };
    })()`,
  );
  const projectRows = bootstrap?.health?.projects?.projects || [];
  const projectPaths = Array.from(new Set(
    projectRows.map((item) => String(item?.path || "").trim()).filter(Boolean),
  ));
  const query = projectPaths.map((path) => `projectPath=${encodeURIComponent(path)}`).join("&");
  const chatsPayload = await appApi(`/api/app/chats${query ? `?${query}` : ""}`);
  const chats = Array.isArray(chatsPayload?.chats) ? chatsPayload.chats : [];
  const titleMatches = chats.filter(
    (chat) =>
      (String(chat?.sessionId || "").trim() || String(chat?.projectPath || "").trim())
      && String(chat?.title || "").trim() === String(header?.title || "").trim(),
  );
  const projectMatches = titleMatches.filter((chat) => {
    const projectRoot = String(chat?.projectPath || "").replace(/[\\/]+$/, "");
    const projectName = projectRoot.split(/[\\/]/).at(-1) || "";
    return projectName === String(header?.projectLabel || "").trim();
  });
  const candidates = projectMatches.length ? projectMatches : titleMatches;
  candidates.sort((left, right) => String(right?.updatedAt || "").localeCompare(String(left?.updatedAt || "")));
  const chat = candidates[0];
  return {
    ok: Boolean(chat?.sessionId || chat?.projectPath),
    projectLabel: String(header?.projectLabel || ""),
    title: String(header?.title || ""),
    chatId: String(chat?.id || ""),
    sessionId: String(chat?.sessionId || ""),
    projectRoot: String(chat?.projectPath || ""),
    candidateCount: candidates.length,
  };
}

async function waitForActiveChatScope(cdp, bootstrap, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    latest = await resolveActiveChatScope(cdp, bootstrap);
    if (latest.ok) return latest;
    await sleep(500);
  }
  return latest || {
    ok: false,
    projectLabel: "",
    title: "",
    chatId: "",
    sessionId: "",
    projectRoot: "",
    candidateCount: 0,
  };
}

async function waitForQuestionAnswered(questionId, scopeQuery, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  while (Date.now() < deadline) {
    latest = await appApi(`/api/app/agent/questions?${scopeQuery}`);
    const question = exactQuestion(latest, questionId);
    if (String(question?.status || "") === "answered") return latest;
    await sleep(200);
  }
  throw new Error(`Timed out waiting for UI Question answer projection: ${questionId}`);
}

async function runPackagedProgressQuestionUiGate(report, cdp) {
  const bootstrap = await appApi("/api/app/bootstrap");
  const activeScope = await waitForActiveChatScope(cdp, bootstrap);
  if (!activeScope.ok) {
    throw new Error(`Could not resolve the packaged WebView active chat scope: ${JSON.stringify(activeScope)}`);
  }
  const progressIds = [
    `${marker}-ui-progress-1`,
    `${marker}-ui-progress-2`,
    `${marker}-ui-progress-3`,
  ];
  const progressTitle = `${marker} progress active`;
  const questionText = `${marker} choose acceptance path`;
  const optionLabel = `${marker} option one`;
  const secondOptionLabel = `${marker} option two`;
  const eighthOptionLabel = `${marker} option eight`;
  const optionDescription = `${marker} recommended explanation`;
  let questionId = "";
  const phase = { activeScope, progressCleanup: [] };
  try {
    const progressCreate = await appApi("/api/app/agent/progress/replace", {
      method: "POST",
      body: {
        sessionId: activeScope.sessionId,
        projectRoot: activeScope.projectRoot,
        items: [
          { id: progressIds[0], title: `${marker} read old todos`, status: "completed", order: 1, owner: "agent" },
          { id: progressIds[1], title: progressTitle, status: "in_progress", order: 2, owner: "agent" },
          { id: progressIds[2], title: `${marker} final check`, status: "pending", order: 3, owner: "agent" },
        ],
      },
    });
    const questionCreate = await appApi("/api/app/agent/questions", {
      method: "POST",
      body: {
        sessionId: activeScope.sessionId,
        projectRoot: activeScope.projectRoot,
        header: "Acceptance",
        question: questionText,
        options: Array.from({ length: 8 }, (_, index) => ({
          id: `option-${index + 1}`,
          label: index === 0
            ? optionLabel
            : index === 1
              ? secondOptionLabel
              : index === 7
                ? eighthOptionLabel
                : `${marker} option ${index + 1}`,
          value: `accept option ${index + 1}`,
          description: index === 0 ? optionDescription : `${marker} explanation ${index + 1}`,
        })),
      },
    });
    questionId = String(questionCreate?.question?.questionId || "");
    if (!questionId) throw new Error("Packaged UI Question creation returned no questionId.");
    phase.created = {
      progressCount: Array.isArray(progressCreate?.items) ? progressCreate.items.length : 0,
      question: projectQuestion(questionCreate, questionId),
    };
    phase.visible = await waitForEval(
      cdp,
      `(() => {
        const bodyText = document.body.innerText;
        const asides = Array.from(document.querySelectorAll("aside")).map((node) => node.innerText || "");
        const rightRailText = asides[asides.length - 1] || "";
        const questionInRightRail = rightRailText.includes(${JSON.stringify(questionText)});
        const progressInRightRail = rightRailText.includes(${JSON.stringify(progressTitle)});
        const questionVisible = bodyText.includes(${JSON.stringify(questionText)}) && bodyText.includes(${JSON.stringify(optionLabel)});
        const secondOptionVisible = bodyText.includes(${JSON.stringify(secondOptionLabel)});
        const eighthOptionVisible = bodyText.includes(${JSON.stringify(eighthOptionLabel)});
        const recommendedVisible = /Recommended|推荐|推薦|推奨/.test(bodyText);
        const explanationButton = Array.from(document.querySelectorAll("button")).find((button) =>
          (button.title || "").includes(${JSON.stringify(optionDescription)})
        );
        const explanationTitle = Boolean(explanationButton);
        const optionScroller = explanationButton?.parentElement;
        const optionsAreScrollable = Boolean(
          optionScroller && optionScroller.scrollHeight > optionScroller.clientHeight && optionScroller.clientHeight <= 270
        );
        const hasSomethingElse = /Something else|其他回答|別の回答|その他/.test(bodyText);
        const hasSkip = /Skip|跳过|跳過|スキップ/.test(bodyText);
        const hasAwaitingRail = /待回答|Questions/.test(rightRailText);
        return {
          ok: questionVisible && secondOptionVisible && eighthOptionVisible && recommendedVisible &&
            explanationTitle && optionsAreScrollable && !questionInRightRail && progressInRightRail &&
            hasSomethingElse && hasSkip && !hasAwaitingRail,
          questionVisible,
          secondOptionVisible,
          eighthOptionVisible,
          recommendedVisible,
          explanationTitle,
          optionsAreScrollable,
          optionScrollerClientHeight: optionScroller?.clientHeight || 0,
          optionScrollerScrollHeight: optionScroller?.scrollHeight || 0,
          questionInRightRail,
          progressInRightRail,
          hasSomethingElse,
          hasSkip,
          hasAwaitingRail,
        };
      })()`,
      30000,
    );
    phase.clickOption = await evalValue(
      cdp,
      `(() => {
        const buttons = Array.from(document.querySelectorAll("button"));
        const target = buttons.find((button) => (button.innerText || "").includes(${JSON.stringify(optionLabel)}));
        if (!target) return { ok: false, reason: "option button not found" };
        target.click();
        return { ok: true };
      })()`,
    );
    phase.cardDismissed = await waitForEval(
      cdp,
      `(() => ({ ok: !document.body.innerText.includes(${JSON.stringify(questionText)}) }))()`,
      15000,
    );
    const answeredQuery = new URLSearchParams({
      includeAnswered: "true",
      limit: "20",
      sessionId: activeScope.sessionId,
      projectRoot: activeScope.projectRoot,
    });
    const answeredPayload = await waitForQuestionAnswered(questionId, answeredQuery);
    phase.answered = projectQuestion(answeredPayload, questionId);
    if (!phase.visible?.ok) addAssertion(report, "packaged Question card/Progress rail UI layout gate failed");
    if (!phase.clickOption?.ok) addAssertion(report, "packaged Question option button could not be clicked through CDP");
    if (!phase.cardDismissed?.ok) addAssertion(report, "answered packaged Question card did not leave the composer-adjacent surface");
    if (phase.answered.status !== "answered" || phase.answered.selectedOptionId !== "option-1") {
      addAssertion(report, "packaged UI click did not durably answer the exact Question option");
    }
    return phase;
  } finally {
    const cleanupQuery = new URLSearchParams({
      sessionId: activeScope.sessionId,
      projectRoot: activeScope.projectRoot,
    });
    for (const progressId of progressIds) {
      try {
        const cleanup = await appApi(`/api/app/agent/progress/${encodeURIComponent(progressId)}?${cleanupQuery}`, {
          method: "DELETE",
        });
        phase.progressCleanup.push({ id: progressId, ok: cleanup?.ok === true });
      } catch (error) {
        phase.progressCleanup.push({ id: progressId, ok: false, error: String(error?.message || error) });
      }
    }
    if (phase.progressCleanup.some((item) => !item.ok)) {
      addAssertion(report, "packaged Progress UI fixtures were not removed through their scoped API");
    }
  }
}

async function main() {
  await mkdir(evidenceRoot, { recursive: true });
  const report = {
    schema: "vrcforge.packaged_question_lifecycle_probe.v2",
    marker,
    mode: allowUnpushed ? "local-preacceptance" : "strict-release",
    strictReleaseBinding: false,
    releaseEligible: false,
    releaseEvidence: allowUnpushed
      ? "non-release local-preacceptance only"
      : "strict release binding pending completion",
    cdpPort,
    evidenceRoot,
    transports: ["packaged-webview", "authenticated-loopback-rest"],
    ownership: {
      process: "one tracked VRCForge.exe PID and packaged-root descendants",
      lifetime: "two bounded launches; graceful close after each; forced fallback only for tracked packaged-root processes",
      auth: "isolated app-session token read from isolated config root",
    },
    goalQuestionRearm: {
      status: "not-exercised",
      reason: "Requires a genuine scheduled delivery, provider turn and blocked-question transition; retained as the separate packaged Goal lifecycle gate. No private Goal storage was seeded.",
    },
    assertions: [],
    phases: {},
    closures: {},
  };
  let app;
  try {
    if (!Number.isInteger(cdpPort) || cdpPort < 1024 || cdpPort > 65535 || cdpPort === 8757) {
      throw new Error(`Invalid VRCFORGE_QUESTION_PROBE_CDP_PORT: ${process.env.VRCFORGE_QUESTION_PROBE_CDP_PORT || cdpPort}`);
    }
    report.beforePackage = await processSnapshot();
    if (!snapshotIsClear(report.beforePackage)) {
      throw new Error(`Preflight found an existing VRCForge process or occupied backend/CDP port; nothing was terminated: ${JSON.stringify(report.beforePackage)}`);
    }
    const sourceVersion = (await readFile(resolve(repoRoot, "VERSION"), "utf8")).trim();
    const binding = await prepareManifestBoundPackage(sourceVersion);
    report.strictReleaseBinding = binding.strictReleaseBinding === true;
    report.releaseEligible = binding.buildPolicy.releaseEligible === true;
    report.releaseBinding = {
      manifestCommit: binding.commit,
      headCommit: binding.headCommit,
      originMainCommit: binding.originMainCommit,
      worktreeClean: binding.worktreeClean,
      buildPolicy: binding.buildPolicy,
      strictBuildPolicy: binding.strictBuildPolicy,
      localAcceptanceBuildPolicy: binding.localAcceptanceBuildPolicy,
      portableSha256: binding.portableSha256,
      innerExeSha256: binding.innerExeSha256,
      extractedExeSha256: binding.exeSha256,
      embeddedVersion: binding.embeddedVersion,
    };
    if (allowUnpushed && (report.strictReleaseBinding || report.releaseEligible)) {
      addAssertion(report, "allow-unpushed mode was incorrectly marked strict or release-eligible");
    }
    if (!allowUnpushed && (!report.strictReleaseBinding || !report.releaseEligible)) {
      addAssertion(report, "strict mode did not retain strict release binding and release eligibility");
    }
    const packageStat = await stat(exe);
    report.package = { sourceVersion, size: packageStat.size, modifiedAt: packageStat.mtime.toISOString() };
    await Promise.all([
      mkdir(configRoot, { recursive: true }),
      mkdir(webviewDataRoot, { recursive: true }),
      mkdir(hostProfileRoot, { recursive: true }),
      mkdir(projectARoot, { recursive: true }),
      mkdir(projectBRoot, { recursive: true }),
    ]);
    await prepareQuestionUiProjectFixture();
    report.beforeFirstLaunch = await processSnapshot();
    if (!snapshotIsClear(report.beforeFirstLaunch)) {
      throw new Error(`Launch preflight found an existing VRCForge process or occupied backend/CDP port; nothing was terminated: ${JSON.stringify(report.beforeFirstLaunch)}`);
    }
    report.beforeFirstLaunchUnity = await hostUnityProcesses();
    if (report.beforeFirstLaunchUnity.length) {
      throw new Error(`Launch preflight found a running Unity editor, so external project discovery was not isolated; nothing was terminated: ${JSON.stringify(report.beforeFirstLaunchUnity)}`);
    }

    app = await launchPackagedApp();
    const healthVersion = String(app.health?.version || "");
    if (healthVersion !== sourceVersion) addAssertion(report, "packaged backend version did not match VERSION");
    const missingAuth = await rawAppRequest("/api/app/agent/questions", { token: undefined });
    const wrongAuth = await rawAppRequest("/api/app/agent/questions", { token: wrongAuthToken });
    report.phases.auth = { missingTokenStatus: missingAuth.status, wrongTokenStatus: wrongAuth.status };
    if (missingAuth.status !== 401) addAssertion(report, "missing app-session token was not rejected with HTTP 401");
    if (wrongAuth.status !== 401) addAssertion(report, "wrong app-session token was not rejected with HTTP 401");
    report.phases.packagedUiProject = await registerQuestionUiProject(app.cdp);
    report.phases.packagedUiSeed = await seedAndActivateQuestionUiChat(app.cdp);
    report.phases.packagedUi = await runPackagedProgressQuestionUiGate(report, app.cdp);

    const sessionA = `session-a-${marker}`;
    const sessionB = `session-b-${marker}`;
    const queryA = new URLSearchParams({ sessionId: sessionA, projectRoot: projectARoot });
    const querySessionBProjectA = new URLSearchParams({ sessionId: sessionB, projectRoot: projectARoot });
    const querySessionAProjectB = new URLSearchParams({ sessionId: sessionA, projectRoot: projectBRoot });
    const createdPayload = await appApi("/api/app/agent/questions", {
      method: "POST",
      body: {
        header: "Packaged acceptance",
        question: `${marker} choose a bounded path`,
        options: [
          { id: "bounded", label: "Bounded", value: "Use bounded path" },
          { id: "defer", label: "Defer", value: "Defer this path" },
        ],
        sessionId: sessionA,
        projectRoot: projectARoot,
      },
    });
    const questionId = String(createdPayload?.question?.questionId || "");
    if (!questionId) throw new Error("Authenticated Question creation returned no questionId.");
    const pendingPayload = await appApi(`/api/app/agent/questions?${queryA}`);
    const pendingQuestion = exactQuestion(pendingPayload, questionId);
    const foreignSessionList = await appApi(`/api/app/agent/questions?${querySessionBProjectA}`);
    const foreignProjectList = await appApi(`/api/app/agent/questions?${querySessionAProjectB}`);
    report.phases.createAndList = {
      created: projectQuestion(createdPayload, questionId),
      pending: projectQuestion(pendingPayload, questionId),
      foreignSessionCount: Number(foreignSessionList?.count || 0),
      foreignProjectCount: Number(foreignProjectList?.count || 0),
    };
    if (!pendingQuestion || pendingPayload.count !== 1) {
      addAssertion(report, "authenticated scoped list did not return the created pending Question exactly once");
    }
    if (Number(foreignSessionList?.count || 0) !== 0) {
      addAssertion(report, "session-B/project-A list exposed the session-A Question");
    }
    if (Number(foreignProjectList?.count || 0) !== 0) {
      addAssertion(report, "session-A/project-B list exposed the project-A Question");
    }

    const wrongSession = await rawAppRequest(`/api/app/agent/questions/${encodeURIComponent(questionId)}/answer`, {
      method: "POST",
      token: appSessionToken,
      body: { answer: "wrong session", selectedOptionId: "bounded", sessionId: sessionB, projectRoot: projectARoot },
    });
    const afterWrongSession = await appApi(`/api/app/agent/questions?${queryA}`);
    const wrongProject = await rawAppRequest(`/api/app/agent/questions/${encodeURIComponent(questionId)}/answer`, {
      method: "POST",
      token: appSessionToken,
      body: { answer: "wrong project", selectedOptionId: "bounded", sessionId: sessionA, projectRoot: projectBRoot },
    });
    const afterWrongProject = await appApi(`/api/app/agent/questions?${queryA}`);
    report.phases.crossScope = {
      wrongSessionStatus: wrongSession.status,
      afterWrongSession: projectQuestion(afterWrongSession, questionId),
      afterWrongSessionCount: Number(afterWrongSession?.count || 0),
      wrongProjectStatus: wrongProject.status,
      afterWrongProject: projectQuestion(afterWrongProject, questionId),
      afterWrongProjectCount: Number(afterWrongProject?.count || 0),
    };
    if (wrongSession.status !== 404) addAssertion(report, "cross-session Question answer was not rejected with HTTP 404");
    if (!pendingQuestionIsUntouched(afterWrongSession, questionId)) {
      addAssertion(report, "cross-session 404 mutated or removed the original pending Question");
    }
    if (wrongProject.status !== 404) addAssertion(report, "cross-project Question answer was not rejected with HTTP 404");
    if (!pendingQuestionIsUntouched(afterWrongProject, questionId)) {
      addAssertion(report, "cross-project 404 mutated or removed the original pending Question");
    }

    const answeredPayload = await appApi(`/api/app/agent/questions/${encodeURIComponent(questionId)}/answer`, {
      method: "POST",
      body: {
        answer: sensitiveAnswer,
        selectedOptionId: "bounded",
        sessionId: sessionA,
        projectRoot: projectARoot,
      },
    });
    const pendingAfterAnswer = await appApi(`/api/app/agent/questions?${queryA}`);
    const answeredQuery = new URLSearchParams({
      includeAnswered: "true",
      limit: "20",
      sessionId: sessionA,
      projectRoot: projectARoot,
    });
    const answeredList = await appApi(`/api/app/agent/questions?${answeredQuery}`);
    const answeredResponseQuestion = answeredPayload?.question;
    const answeredQuestion = exactQuestion(answeredList, questionId);
    const answerDisclosures = {
      answerResponse: containsSensitiveText(answeredPayload),
      pendingList: containsSensitiveText(pendingAfterAnswer),
      answeredList: containsSensitiveText(answeredList),
    };
    report.phases.answer = {
      answered: projectQuestion(answeredPayload, questionId),
      pendingCount: Number(pendingAfterAnswer?.count || 0),
      answeredCount: Number(answeredList?.count || 0),
      responseAnswerRedactedNonEmpty: answerIsRedactedNonEmpty(answeredResponseQuestion),
      listAnswerRedactedNonEmpty: answerIsRedactedNonEmpty(answeredQuestion),
      disclosures: answerDisclosures,
    };
    if (Object.values(answerDisclosures).some(Boolean)) {
      addAssertion(report, "a sensitive Question answer was echoed by an authenticated API projection");
    }
    if (
      Number(pendingAfterAnswer?.count || 0) !== 0
      || Number(answeredList?.count || 0) !== 1
      || String(answeredQuestion?.status || "") !== "answered"
      || String(answeredQuestion?.selectedOptionId || "") !== "bounded"
      || !answerIsRedactedNonEmpty(answeredResponseQuestion)
      || !answerIsRedactedNonEmpty(answeredQuestion)
    ) {
      addAssertion(report, "authenticated answer did not preserve one redacted non-empty answered Question");
    }

    app.cdp.close();
    report.closures.first = await closePackagedApp(app);
    assertGracefulClosure(report, report.closures.first, "before restart");
    app = undefined;
    report.beforeRestart = await processSnapshot();
    if (!snapshotIsClear(report.beforeRestart)) {
      throw new Error(`Restart preflight found an existing process or occupied probe port; nothing was terminated: ${JSON.stringify(report.beforeRestart)}`);
    }

    app = await launchPackagedApp();
    const restartList = await appApi(`/api/app/agent/questions?${answeredQuery}`);
    const restartPending = await appApi(`/api/app/agent/questions?${queryA}`);
    const restartDisclosure = containsSensitiveText(restartList);
    const restartQuestion = exactQuestion(restartList, questionId);
    report.phases.restart = {
      answered: projectQuestion(restartList, questionId),
      answeredCount: Number(restartList?.count || 0),
      pendingCount: Number(restartPending?.count || 0),
      answerRedactedNonEmpty: answerIsRedactedNonEmpty(restartQuestion),
      sensitiveAnswerDisclosure: restartDisclosure,
    };
    if (
      Number(restartList?.count || 0) !== 1
      || Number(restartPending?.count || 0) !== 0
      || String(restartQuestion?.status || "") !== "answered"
      || String(restartQuestion?.selectedOptionId || "") !== "bounded"
      || !answerIsRedactedNonEmpty(restartQuestion)
    ) {
      addAssertion(report, "graceful restart did not recover the exact answered Question state");
    }
    if (restartDisclosure) addAssertion(report, "restart projection echoed the sensitive Question answer");

    app.cdp.close();
    report.closures.second = await closePackagedApp(app);
    assertGracefulClosure(report, report.closures.second, "after restart readback");
    app = undefined;
    const questionLog = await readFile(questionLogPath, "utf8");
    const questionEvents = questionLog.split(/\r?\n/)
      .filter(Boolean)
      .map((line) => {
        try { return JSON.parse(line); }
        catch { return null; }
      });
    const answeredEvents = questionEvents.filter(
      (event) => event
        && event.event === "question_answered"
        && String(event.questionId || "") === questionId,
    );
    const answeredEventCount = answeredEvents.length;
    const durableAnswerRedactedNonEmpty = answeredEvents.length === 1
      && answerIsRedactedNonEmpty(answeredEvents[0]);
    report.phases.persistence = {
      exists: true,
      answeredEventCount,
      answerRedactedNonEmpty: durableAnswerRedactedNonEmpty,
      sensitiveAnswerDisclosure: containsSensitiveText(questionLog),
    };
    if (answeredEventCount !== 1 || !durableAnswerRedactedNonEmpty) {
      addAssertion(report, "Question JSONL did not contain exactly one redacted non-empty durable answer event");
    }
    if (containsSensitiveText(questionLog)) addAssertion(report, "Question JSONL persisted the sensitive raw answer");
  } catch (error) {
    report.error = String(error?.stack || error);
    if (error?.launchDiagnostics) {
      report.phases.launchFailure = {
        childPid: error.launchDiagnostics.childPid,
        attemptedCdpUrl: error.launchDiagnostics.attemptedCdpUrl,
        cause: error.launchDiagnostics.cause,
        beforeCleanup: error.launchDiagnostics.beforeCleanup,
      };
      report.closures.launchFailure = error.launchDiagnostics.cleanup;
    }
    addAssertion(report, "probe threw before the Question lifecycle completed");
  } finally {
    if (app) {
      try { app.cdp?.close(); } catch { /* Renderer may already be gone. */ }
      try {
        report.closures.failureCleanup = await closePackagedApp(app);
      } catch (error) {
        report.cleanupError = String(error?.stack || error);
        await forceCloseLaunch(app).catch((forceError) => {
          report.forceCleanupError = String(forceError?.stack || forceError);
        });
      }
    }
    report.afterCleanup = await processSnapshot().catch((error) => ({ error: String(error?.stack || error) }));
    if (!snapshotIsClear(report.afterCleanup || {})) {
      addAssertion(report, "final packaged process or backend/CDP port cleanup was incomplete");
    }
    report.ok = report.assertions.length === 0
      && report.phases?.packagedUi?.visible?.ok === true
      && report.phases?.packagedUi?.cardDismissed?.ok === true
      && report.phases?.restart?.answered?.status === "answered"
      && report.phases?.restart?.answerRedactedNonEmpty === true
      && report.phases?.persistence?.answeredEventCount === 1
      && report.phases?.persistence?.answerRedactedNonEmpty === true;
    report.releaseEvidence = report.ok
      ? allowUnpushed
        ? "non-release local-preacceptance only"
        : "strict release-bound Question lifecycle evidence"
      : "failed evidence";
    const redactedReport = redactProbeSecrets(report);
    await writeFile(reportPath, `${JSON.stringify(redactedReport, null, 2)}\n`, "utf8");
    console.log(reportPath);
    if (!report.ok) {
      console.error(redactProbeSecrets(`Packaged Question lifecycle probe failed: ${report.assertions.join("; ")}`));
      process.exitCode = 1;
    }
  }
}

main();
