import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { createReadStream } from "node:fs";
import {
  mkdir,
  readFile,
  realpath,
  readdir,
  rm,
  stat,
  unlink,
  writeFile,
} from "node:fs/promises";
import { basename, dirname, isAbsolute, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const allowUnpushed = process.argv.includes("--allow-unpushed");
const selfTest = process.argv.includes("--self-test");
const cdpPort = Number(process.env.VRCFORGE_SKILL_PROBE_CDP_PORT || "9353");
const marker = `SKILL_ECOSYSTEM_PROBE_${Date.now()}`;
const evidenceRoot = resolve(repoRoot, "artifacts", "actual-app-skill-ecosystem", marker);
const packagedRoot = resolve(evidenceRoot, "package");
const exe = resolve(packagedRoot, "VRCForge.exe");
const backendExe = resolve(packagedRoot, "backend", "vrcforge_backend.exe");
const userDataRoot = resolve(evidenceRoot, "user-data");
const configRoot = resolve(userDataRoot, "config");
const webviewDataRoot = resolve(evidenceRoot, "webview2-user-data");
const projectRoot = resolve(evidenceRoot, "fixture-project");
const packageFixtureRoot = resolve(evidenceRoot, "signed-example-packages");
const pathToSkillRoot = resolve(evidenceRoot, "path-to-skill");
const committedFixtureRoot = resolve(evidenceRoot, "committed-fixture-source");
const fakeUnityCliPath = resolve(evidenceRoot, "fixtures", "fake-unity-cli.ps1");
const ephemeralSigningKeyPath = resolve(evidenceRoot, "ephemeral-ed25519-private.pem");
const reportPath = resolve(evidenceRoot, "report.json");
const processStartEventsPath = resolve(evidenceRoot, "process-start-events.jsonl");
const zipSlipOutsideName = `vrcforge-${marker.toLowerCase()}-outside.txt`;
const zipSlipOutsidePath = resolve(process.env.TEMP || evidenceRoot, zipSlipOutsideName);
const appOrigin = "http://127.0.0.1:8757";
const appRequestOrigin = "http://tauri.localhost";
const agentGatewayToken = randomBytes(32).toString("base64url");
const agentApprovalToken = randomBytes(32).toString("base64url");
const paidPayloadSentinel = `PAID_PAYLOAD_SENTINEL_${randomBytes(12).toString("hex")}`;
const negativeTestSecret = "test-only-secret-value-123456789";
const privateUrlSentinel = `https://private-assets.invalid/${randomBytes(12).toString("hex")}/SecretOutfit.zip`;
const requiredPackageIds = [
  "community.examples.validation-report-extension",
  "community.examples.material-preset-pack",
  "community.examples.outfit-naming-helper",
  "community.examples.optimizer-report-helper",
];
const apkSemanticPackageId = "community.apk-semantic-probe";
const apkSemanticSkillName = "apk-semantic-probe";
const apkSemanticAuthorId = "VRCForge User";
const requiredPackageSkillNames = new Map([
  ["community.examples.validation-report-extension", "validation-report-extension"],
  ["community.examples.material-preset-pack", "material-preset-pack"],
  ["community.examples.outfit-naming-helper", "outfit-naming-helper"],
  ["community.examples.optimizer-report-helper", "optimizer-report-helper"],
]);
const requiredPackageEntrypoints = new Map([
  ["validation-report-extension", "vrcforge_run_validation_report"],
  ["outfit-naming-helper", "vrcforge_scan_animation_bindings"],
  ["optimizer-report-helper", "vrcforge_optimization_plan"],
]);
const requiredPackageSupportFiles = new Map([
  ["validation-report-extension", ["workflows/validation-report-extension.json"]],
  ["material-preset-pack", ["presets/material-presets.json", "workflows/material-preset-pack.json"]],
  ["outfit-naming-helper", ["workflows/outfit-naming-helper.json"]],
  ["optimizer-report-helper", ["workflows/optimizer-report-helper.json"]],
]);
const exampleSlugs = [
  "validation-report-extension",
  "material-preset-pack",
  "outfit-naming-helper",
  "optimizer-report-helper",
];
const recipeReportKeys = {
  ttt_material_group: "tttMaterialGroup",
  booth_import_preflight: "boothImportPreflight",
  parameter_compression: "parameterCompression",
  pc_quest_upload_pass: "pcQuestUploadPass",
};
const recipeExpectations = {
  ttt_material_group: {
    shape: "approval_gated_material_group",
    writePath: "request_only",
    permissionMode: "approval_required",
    riskLevel: "high",
    argumentHint: "projectPath={{projectPath}} avatarPath={{avatarPath}} rendererPath=<path> materialSlots=<indices>",
    detectorRules: [
      "Require a detected TexTransTool dependency before proposing an apply request.",
      "Capture explicit renderer and material-slot membership; never guess an atlas group.",
      "Flag special shaders or material settings for manual review.",
    ],
    requiredEvidence: [
      "material-slot audit",
      "user-confirmed renderer and material group",
      "validation delta and rollback verification after an approved request",
    ],
    permissions: [
      "read_project",
      "unity_modify_components",
      "unity_modify_materials",
      "unity_run_validation",
      "unity_scan_scene",
    ],
    entrypointTool: "vrcforge_optimization_ttt_atlas_plan",
    allowedTools: [
      "vrcforge_health",
      "vrcforge_unity_status",
      "vrcforge_optimization_material_slot_audit",
      "vrcforge_optimization_ttt_atlas_plan",
      "vrcforge_optimization_ttt_atlas_apply_request",
      "vrcforge_optimization_validation_delta",
    ],
    requiredVariables: ["projectPath"],
    validation: { requiresApproval: true, requiresCheckpoint: true, requiresRollback: true },
  },
  booth_import_preflight: {
    shape: "read_only_package_preflight",
    writePath: "read_only",
    permissionMode: "read_only",
    riskLevel: "low",
    argumentHint: "projectPath={{projectPath}} packagePath={{packagePath}}",
    detectorRules: [
      "Accept a local Booth folder, ZIP pathname, or UnityPackage pathname as a remapped input only.",
      "Inspect structure and pathname metadata without embedding package contents.",
      "Stop at a supervised import plan; this recipe never imports or writes assets.",
    ],
    requiredEvidence: [
      "package structure summary",
      "candidate UnityPackage or prefab selection",
      "warnings and expected project targets",
    ],
    permissions: ["read_project", "unity_run_validation", "unity_scan_scene"],
    entrypointTool: "vrcforge_inspect_outfit_package",
    allowedTools: [
      "vrcforge_health",
      "vrcforge_unity_status",
      "vrcforge_scan_project_index",
      "vrcforge_inspect_outfit_package",
      "vrcforge_plan_outfit_import",
      "vrcforge_build_test_readiness",
    ],
    requiredVariables: ["projectPath", "packagePath"],
    validation: { requiresApproval: false, requiresCheckpoint: false, requiresRollback: false },
  },
  parameter_compression: {
    shape: "blocked_parameter_compression_plan",
    writePath: "blocked_preview",
    permissionMode: "preview",
    riskLevel: "high",
    argumentHint: "projectPath={{projectPath}} avatarPath={{avatarPath}}",
    detectorRules: [
      "Inventory Expression Parameters, menu controls, and FX animator usage before classifying candidates.",
      "Exclude puppets, OSC or face-tracking inputs, continuous floats, and unknown usages by default.",
      "Keep apply blocked until behavior-regression and rollback proof exist for the selected primitive.",
    ],
    requiredEvidence: [
      "parameter budget and usage inventory",
      "menu, FX, puppet, OSC, and face-tracking regression plan",
      "explicit hard-gate results for every compression candidate",
    ],
    permissions: ["read_project", "unity_run_validation", "unity_scan_scene"],
    entrypointTool: "vrcforge_optimization_parameter_path_to_skill",
    allowedTools: [
      "vrcforge_health",
      "vrcforge_unity_status",
      "vrcforge_optimization_parameter_budget_audit",
      "vrcforge_optimization_parameter_inventory",
      "vrcforge_optimization_parameter_menu_map",
      "vrcforge_optimization_parameter_animator_usage",
      "vrcforge_optimization_parameter_compressibility_plan",
      "vrcforge_optimization_parameter_vrcfury_compressor_plan",
      "vrcforge_optimization_parameter_behavior_regression",
      "vrcforge_optimization_parameter_path_to_skill",
      "vrcforge_optimization_validation_delta",
    ],
    requiredVariables: ["projectPath"],
    validation: { requiresApproval: false, requiresCheckpoint: false, requiresRollback: false },
    futureApplyGate: {
      status: "blocked",
      applyToolExposed: false,
      requiresApproval: true,
      requiresCheckpoint: true,
      requiresRollback: true,
    },
  },
  pc_quest_upload_pass: {
    shape: "read_only_upload_gate",
    writePath: "read_only",
    permissionMode: "read_only",
    riskLevel: "low",
    argumentHint: "projectPath={{projectPath}} avatarPath={{avatarPath}} platforms=pc,quest",
    detectorRules: [
      "Evaluate PC and Quest/Android limits independently from the same remapped project context.",
      "Separate hard upload blockers from performance-rank offenders and risky fixes.",
      "Keep missing SDK metrics unknown; never infer an upload pass from absent data.",
    ],
    requiredEvidence: [
      "PC upload-gate audit",
      "Quest/Android upload-gate audit",
      "build-test readiness with unknown metrics called out",
    ],
    permissions: ["read_project", "unity_run_validation", "unity_scan_scene"],
    entrypointTool: "vrcforge_optimization_upload_gate_audit",
    allowedTools: [
      "vrcforge_health",
      "vrcforge_unity_status",
      "vrcforge_run_validation_report",
      "vrcforge_build_test_readiness",
      "vrcforge_optimization_upload_gate_audit",
      "vrcforge_optimization_upload_gate_fix_plan",
    ],
    requiredVariables: ["projectPath"],
    validation: { requiresApproval: false, requiresCheckpoint: false, requiresRollback: false },
  },
};
let appSessionToken = "";
let trackedRootPid = 0;
let trackedRootObserved = false;
let trackedRootIdentityKey = "";
let processTrackingTimer;
let processSnapshotInFlight;
let processTrackingErrorCount = 0;
let processStartWatcherVerified = false;
let processStartWatcherMode = "";
let processStartWatcherSettleVerified = false;
let processStartWatcherSettleMs = 0;
let packageFilesystemBaseline;
const trackedProcessIdentities = new Map();
const trackedProcessNamesEver = new Set();
const observedProcessStartEvents = new Map();
const observedProcessStartEventsBySequence = new Map();

const unknownOptions = process.argv.slice(2).filter((item) =>
  !["--allow-unpushed", "--self-test", "--help", "-h"].includes(item));
if (unknownOptions.length > 0) {
  console.error(`Unknown option(s): ${unknownOptions.join(", ")}`);
  process.exit(2);
}

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(`Usage: node scripts/diagnose_packaged_skill_ecosystem.mjs [--allow-unpushed] [--self-test]

Runs the manifest-bound packaged Skill Ecosystem acceptance probe.

Default mode is release-strict and requires all of the following:
  * HEAD == origin/main == release-manifest.json commit
  * Git worktree is clean before repository-backed fixtures/builders can run
  * release-manifest.json buildPolicy is strict/releaseEligible with every Allow* flag false
  * VERSION, manifest version, portable ZIP name/hash, and embedded VERSION agree

--allow-unpushed is an explicit local pre-acceptance mode. It still binds the
portable ZIP to the current HEAD and VERSION, but the report is marked
strictReleaseBinding=false and cannot be used as strict release evidence.

--self-test runs side-effect-free provenance/contract assertions without
requiring a package, starting the app, or reserving a port.

Optional environment:
  VRCFORGE_SKILL_PROBE_CDP_PORT=<unused port> (default: ${cdpPort})`);
  process.exit(0);
}

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function addAssertion(report, message) {
  const value = String(message || "probe assertion failed");
  if (!report.assertions.includes(value)) {
    report.assertions.push(value);
  }
}

function escapePowerShellLiteral(value) {
  return String(value).replaceAll("'", "''");
}

const powershellDmtfCreationDateHelper = String.raw`
function Convert-VrcForgeCreationDateToDmtf([object]$Value) {
  if ($null -eq $Value) { return '' }
  if ($Value -is [DateTime]) {
    return [System.Management.ManagementDateTimeConverter]::ToDmtfDateTime([DateTime]$Value)
  }
  $text = [string]$Value
  if ($text -match '^\d{14}\.\d{6}[+-]\d{3}$') { return $text }
  try {
    return [System.Management.ManagementDateTimeConverter]::ToDmtfDateTime([DateTime]$Value)
  } catch {
    return ''
  }
}
`;

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
    let timeoutError;
    let timeoutFallback;
    const timeoutMs = Number(options.timeoutMs || 120000);
    const timer = setTimeout(() => {
      if (settled) return;
      timeoutError = new Error(`${command} timed out after ${timeoutMs} ms`);
      child.kill();
      timeoutFallback = setTimeout(() => {
        if (settled) return;
        settled = true;
        rejectRun(timeoutError);
      }, 5000);
    }, timeoutMs);
    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearTimeout(timeoutFallback);
      rejectRun(error);
    });
    child.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearTimeout(timeoutFallback);
      if (timeoutError) {
        rejectRun(timeoutError);
      } else if (code === 0) {
        resolveRun({ stdout: stdout.trim(), stderr: stderr.trim(), code });
      } else {
        rejectRun(new Error(stderr.trim() || stdout.trim() || `${command} exited ${code}`));
      }
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

async function runPythonJson(code, args = [], options = {}) {
  const result = await runProcess("python", ["-c", code, ...args], options);
  try {
    return JSON.parse(result.stdout);
  } catch (error) {
    throw new Error(`Python helper returned invalid JSON: ${String(error?.message || error)}`);
  }
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

async function archiveEntryDigests(archivePath, expectedPayloadNames) {
  const code = String.raw`
import hashlib
import json
import sys
import zipfile

archive_path = sys.argv[1]
expected_payload = json.loads(sys.argv[2])
expected = expected_payload + ["skill.lock.json"]
result = {"ok": False, "digests": {}, "jsonDocuments": {}}
with zipfile.ZipFile(archive_path) as archive:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    regular_files = all(
        not info.is_dir()
        and "\\" not in info.filename
        and ((info.external_attr >> 16) & 0o170000) in {0, 0o100000}
        for info in infos
    )
    if len(names) != len(set(names)) or set(names) != set(expected) or not regular_files:
        result["error"] = "archive entry set/type did not exactly match the expected payload plus dev lock"
    else:
        payload_bytes = {name: archive.read(name) for name in expected_payload}
        result["digests"] = {
            name: hashlib.sha256(value).hexdigest()
            for name, value in payload_bytes.items()
        }
        result["jsonDocuments"] = {
            name: json.loads(archive.read(name))
            for name in expected_payload
            if name.lower().endswith(".json")
        }
        lock = json.loads(archive.read("skill.lock.json"))
        if (
            lock.get("schema") != "vrcforge.skill-lock.v1"
            or lock.get("algorithm") != "sha256"
            or lock.get("package_mode") != "dev"
            or lock.get("files") != result["digests"]
            or set(lock) != {"schema", "algorithm", "package_mode", "files"}
        ):
            result["error"] = "dev package lock did not exactly bind the expected payload entries"
        else:
            result["lockVerified"] = True
            result["ok"] = True
print(json.dumps(result, separators=(",", ":")))
`;
  return runPythonJson(code, [archivePath, JSON.stringify(expectedPayloadNames)]);
}

async function inspectSignedVskArchive(archivePath) {
  const code = String.raw`
import base64
import hashlib
import json
import re
import stat
import sys
import zipfile
from pathlib import PurePosixPath
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

path = sys.argv[1]
reserved = {"skill.lock.json", "skill.sig", "author.pub"}
canonical = lambda value: json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
).encode("utf-8")
result = {"ok": False, "checks": {}}
try:
    archive_bytes = open(path, "rb").read()
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        collision_keys = set()
        safe = True
        regular = True
        for info in infos:
            name = info.filename
            canonical_name = PurePosixPath(name).as_posix()
            parts = PurePosixPath(name).parts
            collision = name.casefold()
            if (
                not name
                or info.is_dir()
                or "\\" in name
                or name.startswith("/")
                or re.match(r"^[A-Za-z]:", name)
                or canonical_name != name
                or any(part in {"", ".", ".."} for part in parts)
                or collision in collision_keys
            ):
                safe = False
            collision_keys.add(collision)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                regular = False
        entries = {name: archive.read(name) for name in names}
    required = {"manifest.json", "skill.lock.json", "skill.sig", "author.pub"}
    manifest = json.loads(entries["manifest.json"])
    lock = json.loads(entries["skill.lock.json"])
    payload = {name: data for name, data in entries.items() if name not in reserved}
    payload_hashes = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in sorted(payload.items())
    }
    public_key = base64.b64decode(entries["author.pub"], validate=True)
    signature = base64.b64decode(entries["skill.sig"], validate=True)
    Ed25519PublicKey.from_public_bytes(public_key).verify(signature, entries["skill.lock.json"])
    checks = {
        "archivePathSafetyVerified": safe,
        "archiveEntriesRegular": regular,
        "archiveEntrySetVerified": set(entries) == set(payload) | reserved and required.issubset(entries),
        "manifestCanonical": entries["manifest.json"] == canonical(manifest),
        "lockCanonical": entries["skill.lock.json"] == canonical(lock),
        "releaseMode": lock.get("package_mode") == "release",
        "lockExactPayload": lock.get("files") == payload_hashes,
        "lockIncludesCanonicalManifest": lock.get("files", {}).get("manifest.json")
            == hashlib.sha256(canonical(manifest)).hexdigest(),
        "payloadDigestsVerified": all(
            lock.get("files", {}).get(name) == hashlib.sha256(data).hexdigest()
            for name, data in payload.items()
        ),
        "signatureVerified": True,
        "privateKeyMaterialAbsent": all(
            b"-----BEGIN PRIVATE KEY-----" not in data
            and b"-----BEGIN OPENSSH PRIVATE KEY-----" not in data
            for data in entries.values()
        ),
    }
    result = {
        "ok": all(checks.values()),
        "manifest": manifest,
        "archiveSha256": hashlib.sha256(archive_bytes).hexdigest(),
        "lockSha256": hashlib.sha256(entries["skill.lock.json"]).hexdigest(),
        "payloadSetSha256": hashlib.sha256(canonical(payload_hashes)).hexdigest(),
        "publicKeyBase64": base64.b64encode(public_key).decode("ascii"),
        "signerFingerprint": hashlib.sha256(public_key).hexdigest(),
        "checks": checks,
    }
except Exception as exc:
    result["errorType"] = type(exc).__name__
print(json.dumps(result, separators=(",", ":")))
`;
  return runPythonJson(code, [archivePath], {
    timeoutMs: 120000,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
  });
}

async function gitWorktreeIsClean() {
  const result = await runProcess(
    "git",
    ["-C", repoRoot, "status", "--porcelain=v1", "--untracked-files=all"],
  );
  return result.stdout.length === 0;
}

async function currentGitBindingSnapshot() {
  const [head, originMain, version, worktreeClean] = await Promise.all([
    runProcess("git", ["-C", repoRoot, "rev-parse", "HEAD"]).then((result) => result.stdout.toLowerCase()),
    runProcess("git", ["-C", repoRoot, "rev-parse", "origin/main"]).then((result) => result.stdout.toLowerCase()),
    readFile(resolve(repoRoot, "VERSION"), "utf8").then((value) => value.replace(/^\uFEFF/, "").trim()),
    gitWorktreeIsClean(),
  ]);
  return { head, originMain, version, worktreeClean };
}

async function verifyFixtureSource(root, commit = "") {
  const code = String.raw`
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
root = Path(sys.argv[2]).resolve()
commit = sys.argv[3]
errors = []
actual = {}
targets = [root / "skill_packages.py", root / "examples" / "skill-packages"]
for target in targets:
    if not target.exists():
        errors.append(f"missing:{target.name}")
        continue
    candidates = [target] if target.is_file() else sorted(target.rglob("*"))
    for item in candidates:
        if item.is_symlink():
            errors.append(f"symlink:{item.relative_to(root).as_posix()}")
        elif item.is_file():
            relative = item.relative_to(root).as_posix()
            actual[relative] = hashlib.sha256(item.read_bytes()).hexdigest()
        elif not item.is_dir():
            errors.append(f"unsupported:{item.relative_to(root).as_posix()}")

expected = {}
if commit:
    tree = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", commit, "--", "skill_packages.py", "examples/skill-packages"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout
    for raw in tree.split(b"\0"):
        if not raw:
            continue
        metadata, raw_path = raw.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        path = raw_path.decode("utf-8")
        if kind != "blob" or mode not in {"100644", "100755"}:
            errors.append(f"unsupported-git-entry:{path}")
            continue
        blob = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "blob", object_id],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        expected[path] = hashlib.sha256(blob).hexdigest()

canonical = lambda value: hashlib.sha256(
    json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
result = {
    "ok": not errors and bool(actual) and (not commit or actual == expected),
    "digest": canonical(actual),
    "expectedDigest": canonical(expected) if commit else "",
    "fileCount": len(actual),
    "errors": errors,
    "missing": sorted(set(expected) - set(actual)) if commit else [],
    "unexpected": sorted(set(actual) - set(expected)) if commit else [],
    "mismatched": sorted(path for path in set(actual) & set(expected) if actual[path] != expected[path]) if commit else [],
}
print(json.dumps(result, separators=(",", ":")))
`;
  return runPythonJson(code, [repoRoot, root, commit]);
}

async function prepareFixtureSource(headCommit) {
  if (allowUnpushed) {
    const verification = await verifyFixtureSource(repoRoot);
    if (verification?.ok !== true) throw new Error("Local fixture source tree could not be snapshotted.");
    return {
      root: repoRoot,
      mode: "working-tree-local-preacceptance",
      commit: headCommit,
      digest: verification.digest,
    };
  }
  const archivePath = resolve(evidenceRoot, "committed-fixture-source.zip");
  await runProcess(
    "git",
    [
      "-C",
      repoRoot,
      "archive",
      "--format=zip",
      `--output=${archivePath}`,
      headCommit,
      "--",
      "skill_packages.py",
      "examples/skill-packages",
    ],
  );
  const escapedArchive = escapePowerShellLiteral(archivePath);
  const escapedDestination = escapePowerShellLiteral(committedFixtureRoot);
  await runPowerShell(`
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archivePath = '${escapedArchive}'
    $destination = '${escapedDestination}'
    if (Test-Path -LiteralPath $destination) { throw 'Committed fixture extraction root already exists.' }
    [IO.Compression.ZipFile]::ExtractToDirectory($archivePath, $destination)
  `);
  await unlink(archivePath).catch(() => {});
  for (const required of [
    resolve(committedFixtureRoot, "skill_packages.py"),
    ...exampleSlugs.map((slug) => resolve(committedFixtureRoot, "examples", "skill-packages", slug, "manifest.json")),
  ]) {
    if (!(await pathExists(required))) {
      throw new Error(`Committed fixture snapshot omitted ${basename(required)}.`);
    }
  }
  const verification = await verifyFixtureSource(committedFixtureRoot, headCommit);
  if (verification?.ok !== true || verification.digest !== verification.expectedDigest) {
    throw new Error("Extracted fixture source did not exactly match Git blobs for the bound commit.");
  }
  return {
    root: committedFixtureRoot,
    mode: "immutable-git-object-snapshot",
    commit: headCommit,
    digest: verification.digest,
  };
}

function strictBuildPolicyFromManifest(manifest) {
  const buildPolicy = manifest?.buildPolicy && typeof manifest.buildPolicy === "object"
    ? manifest.buildPolicy
    : {};
  return {
    normalized: {
      mode: String(buildPolicy.mode || ""),
      releaseEligible: buildPolicy.releaseEligible === true,
      allowDirty: buildPolicy.allowDirty === true,
      allowUnpushed: buildPolicy.allowUnpushed === true,
      allowVersionMismatch: buildPolicy.allowVersionMismatch === true,
    },
    strict: buildPolicy.mode === "strict"
      && buildPolicy.releaseEligible === true
      && buildPolicy.allowDirty === false
      && buildPolicy.allowUnpushed === false
      && buildPolicy.allowVersionMismatch === false,
  };
}

function releaseBindingIsStrict({
  localMode,
  strictBuildPolicy,
  headCommit,
  originMainCommit,
  manifestCommit,
  worktreeClean,
}) {
  return localMode !== true
    && strictBuildPolicy === true
    && headCommit === originMainCommit
    && manifestCommit === headCommit
    && worktreeClean === true;
}

function selectPortablePayloadEntries(entryNames) {
  const names = Array.isArray(entryNames) ? entryNames.map((item) => String(item || "").replaceAll("\\", "/")) : [];
  const expected = ["VRCForge.exe", "backend/vrcforge_backend.exe"];
  const selected = {};
  for (const name of expected) {
    const matches = names.filter((candidate) => candidate.toLowerCase() === name.toLowerCase());
    if (matches.length !== 1 || matches[0] !== name) {
      throw new Error(`Portable package must contain exactly one exact ${name} archive entry.`);
    }
    selected[name] = matches[0];
  }
  return selected;
}

function selectManifestPortableArtifact(manifest, portableName) {
  if (
    !portableName
    || isAbsolute(portableName)
    || basename(portableName) !== portableName
    || portableName.includes("/")
    || portableName.includes("\\")
  ) {
    throw new Error("Portable package name must be one safe basename.");
  }
  const artifacts = Array.isArray(manifest?.artifacts) ? manifest.artifacts : [];
  const matches = artifacts.filter((artifact) => artifact?.name === portableName);
  if (matches.length !== 1) {
    throw new Error(`Release manifest must contain exactly one ${portableName} artifact entry.`);
  }
  const portable = matches[0];
  if (Object.hasOwn(portable, "path") && portable.path !== portableName) {
    throw new Error(`Release manifest ${portableName} path must equal its exact basename when present.`);
  }
  if (!/^[0-9a-f]{64}$/i.test(String(portable.sha256 || ""))) {
    throw new Error(`Release manifest did not contain a valid ${portableName} digest.`);
  }
  return portable;
}

async function prepareManifestBoundPackage(sourceVersion) {
  const manifestPath = resolve(repoRoot, "dist", "release", "release-manifest.json");
  let manifest;
  try {
    manifest = JSON.parse((await readFile(manifestPath, "utf8")).replace(/^\uFEFF/, ""));
  } catch (error) {
    if (error?.code === "ENOENT") {
      throw new Error("A release manifest is required under dist/release.");
    }
    throw new Error(`Release manifest could not be read: ${String(error?.message || error)}`);
  }
  if (String(manifest?.version || "") !== sourceVersion) {
    throw new Error(
      `Release manifest version ${String(manifest?.version || "<missing>")} did not match VERSION ${sourceVersion}.`,
    );
  }

  const escapedRepoRoot = escapePowerShellLiteral(repoRoot);
  const headCommit = (await runPowerShell(`git -C '${escapedRepoRoot}' rev-parse HEAD`)).trim().toLowerCase();
  const originMainCommit = (await runPowerShell(`git -C '${escapedRepoRoot}' rev-parse origin/main`)).trim().toLowerCase();
  const worktreeStatus = (await runPowerShell(
    `git -C '${escapedRepoRoot}' status --porcelain=v1 --untracked-files=all`,
  )).trim();
  const worktreeClean = worktreeStatus.length === 0;
  const manifestCommit = String(manifest?.commit || "").trim().toLowerCase();
  for (const [label, commit] of Object.entries({ headCommit, originMainCommit, manifestCommit })) {
    if (!/^[0-9a-f]{40}$/.test(commit)) {
      throw new Error(`${label} was missing or invalid.`);
    }
  }
  if (manifestCommit !== headCommit) {
    throw new Error("Release manifest commit did not match current HEAD.");
  }
  if (!allowUnpushed && headCommit !== originMainCommit) {
    throw new Error("Strict packaged probe requires HEAD to equal origin/main.");
  }
  if (!allowUnpushed && !worktreeClean) {
    throw new Error(
      "Strict packaged probe requires a clean Git worktree before loose-repository fixtures or package builders may run.",
    );
  }
  const policy = strictBuildPolicyFromManifest(manifest);
  const buildPolicy = policy.normalized;
  const strictBuildPolicy = policy.strict;
  if (!allowUnpushed && !strictBuildPolicy) {
    throw new Error(
      "Strict packaged probe requires buildPolicy mode=strict, releaseEligible=true, and every Allow* flag false; missing or local-acceptance provenance is not release evidence.",
    );
  }

  const portableName = `VRCForge_Windows_x64_${sourceVersion}.zip`;
  const portable = selectManifestPortableArtifact(manifest, portableName);
  const portablePath = resolve(dirname(manifestPath), portableName);
  const escapedPortable = escapePowerShellLiteral(portablePath);
  const escapedPackageRoot = escapePowerShellLiteral(packagedRoot);
  const expectedPortableSha256 = String(portable.sha256).toLowerCase();
  const immutablePortablePath = resolve(evidenceRoot, portableName);
  const escapedImmutablePortable = escapePowerShellLiteral(immutablePortablePath);
  const archivePayload = JSON.parse(await runPowerShell(`
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $sourceStream = [IO.File]::Open(
      '${escapedPortable}',
      [IO.FileMode]::Open,
      [IO.FileAccess]::Read,
      [IO.FileShare]::Read
    )
    $snapshotStream = $null
    $archive = $null
    try {
      $snapshotStream = [IO.File]::Open(
        '${escapedImmutablePortable}',
        [IO.FileMode]::CreateNew,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::Read
      )
      $sourceStream.CopyTo($snapshotStream)
      $sourceStream.Dispose()
      $sourceStream = $null
      $snapshotStream.Position = 0
      $outerSha = [Security.Cryptography.SHA256]::Create()
      try {
        $portableDigest = [BitConverter]::ToString(
          $outerSha.ComputeHash($snapshotStream)
        ).Replace('-', '').ToLowerInvariant()
      } finally {
        $outerSha.Dispose()
      }
      if ($portableDigest -ne '${expectedPortableSha256}') {
        throw 'Portable package digest did not match release-manifest.json.'
      }
      $snapshotStream.Position = 0
      $archive = [IO.Compression.ZipArchive]::new(
        $snapshotStream,
        [IO.Compression.ZipArchiveMode]::Read,
        $true
      )
      $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
      foreach ($entry in $archive.Entries) {
        $normalized = $entry.FullName.Replace('\\', '/')
        if (
          [string]::IsNullOrWhiteSpace($normalized) -or
          $normalized.StartsWith('/') -or
          $normalized -match '^[A-Za-z]:' -or
          @($normalized.Split('/') | Where-Object { $_ -eq '.' -or $_ -eq '..' }).Count -gt 0 -or
          -not $seen.Add($normalized)
        ) {
          throw "Portable package contained an unsafe or duplicate entry: $normalized"
        }
      }
      $candidateNames = @($archive.Entries | ForEach-Object { $_.FullName.Replace('\\', '/') } | Where-Object {
        $_.Equals('VRCForge.exe', [StringComparison]::OrdinalIgnoreCase) -or
        $_.Equals('backend/vrcforge_backend.exe', [StringComparison]::OrdinalIgnoreCase)
      })
      $mainEntries = @($archive.Entries | Where-Object {
        $_.FullName.Replace('\\', '/').Equals('VRCForge.exe', [StringComparison]::Ordinal)
      })
      $backendEntries = @($archive.Entries | Where-Object {
        $_.FullName.Replace('\\', '/').Equals('backend/vrcforge_backend.exe', [StringComparison]::Ordinal)
      })
      if ($mainEntries.Count -ne 1 -or $backendEntries.Count -ne 1) {
        throw 'Portable package payload entries were missing, duplicated, or mis-cased.'
      }
      function Get-EntrySha256([IO.Compression.ZipArchiveEntry]$Entry) {
        $sha = [Security.Cryptography.SHA256]::Create()
        $stream = $Entry.Open()
        try { $digest = $sha.ComputeHash($stream) } finally { $stream.Dispose(); $sha.Dispose() }
        [BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant()
      }
      $innerExeSha256 = Get-EntrySha256 $mainEntries[0]
      $innerBackendSha256 = Get-EntrySha256 $backendEntries[0]
      if (Test-Path -LiteralPath '${escapedPackageRoot}') {
        throw 'Isolated package extraction root already exists.'
      }
      [IO.Compression.ZipFileExtensions]::ExtractToDirectory($archive, '${escapedPackageRoot}')
      [pscustomobject]@{
        portableSha256 = $portableDigest
        entryNames = @($candidateNames)
        innerExeSha256 = $innerExeSha256
        innerBackendSha256 = $innerBackendSha256
      } | ConvertTo-Json -Depth 4 -Compress
    } finally {
      if ($archive) { $archive.Dispose() }
      if ($snapshotStream) { $snapshotStream.Dispose() }
      if ($sourceStream) { $sourceStream.Dispose() }
    }
  `));
  const portableSha256 = String(archivePayload?.portableSha256 || "").toLowerCase();
  if (portableSha256 !== expectedPortableSha256) {
    throw new Error(`Portable package digest did not match release-manifest.json for ${portableName}.`);
  }
  const archiveEntryNames = Array.isArray(archivePayload?.entryNames)
    ? archivePayload.entryNames
    : archivePayload?.entryNames
      ? [archivePayload.entryNames]
      : [];
  selectPortablePayloadEntries(archiveEntryNames);
  const innerExeSha256 = String(archivePayload?.innerExeSha256 || "").toLowerCase();
  const innerBackendSha256 = String(archivePayload?.innerBackendSha256 || "").toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(innerExeSha256) || !/^[0-9a-f]{64}$/.test(innerBackendSha256)) {
    throw new Error("Portable package payload digest extraction failed.");
  }

  const embeddedVersion = (await readFile(resolve(packagedRoot, "VERSION"), "utf8"))
    .replace(/^\uFEFF/, "")
    .trim();
  if (embeddedVersion !== sourceVersion) {
    throw new Error(`Manifest-bound portable VERSION ${embeddedVersion || "<missing>"} did not match ${sourceVersion}.`);
  }
  const exeSha256 = await sha256File(exe);
  if (innerExeSha256 !== exeSha256) {
    throw new Error("Extracted VRCForge.exe did not match the manifest-bound portable executable.");
  }
  const extractedBackendSha256 = await sha256File(backendExe);
  if (innerBackendSha256 !== extractedBackendSha256) {
    throw new Error("Extracted backend executable did not match the manifest-bound portable backend entry.");
  }
  return {
    version: String(manifest.version),
    manifestCommit,
    headCommit,
    originMainCommit,
    manifestReleaseEligible: buildPolicy.releaseEligible,
    buildPolicy,
    strictBuildPolicy,
    worktreeClean,
    strictReleaseBinding: releaseBindingIsStrict({
      localMode: allowUnpushed,
      strictBuildPolicy,
      headCommit,
      originMainCommit,
      manifestCommit,
      worktreeClean,
    }),
    portableName,
    portableManifestEntryUnique: true,
    portableManifestPathSafe: true,
    portableSha256,
    innerExeSha256,
    innerBackendSha256,
    embeddedVersion,
    exeSha256,
    extractedBackendSha256,
  };
}

function processIdentity(raw) {
  return {
    pid: Number(raw?.pid || raw?.ProcessId || raw?.Id || 0),
    parentPid: Number(raw?.parentPid || raw?.ParentProcessId || 0),
    name: String(raw?.name || raw?.Name || raw?.ProcessName || ""),
    path: String(raw?.path || raw?.ExecutablePath || raw?.Path || ""),
    creationDate: String(raw?.creationDate || raw?.CreationDate || ""),
    startEventSequence: Number(raw?.startEventSequence || 0),
  };
}

function isDmtfCreationDate(value) {
  return /^\d{14}\.\d{6}[+-]\d{3}$/.test(String(value || ""));
}

function processIdentityMatches(candidate, recorded) {
  return isDmtfCreationDate(candidate.creationDate)
    && isDmtfCreationDate(recorded.creationDate)
    && candidate.pid === recorded.pid
    && candidate.creationDate === recorded.creationDate
    && candidate.name.toLowerCase() === recorded.name.toLowerCase()
    && Boolean(candidate.path)
    && Boolean(recorded.path)
    && normalizedPath(candidate.path) === normalizedPath(recorded.path);
}

function processGenerationKey(identity) {
  return JSON.stringify([
    Number(identity?.pid || 0),
    String(identity?.creationDate || ""),
    Number(identity?.startEventSequence || 0),
  ]);
}

function trackedIdentitiesForPid(pid) {
  return [...trackedProcessIdentities.values()].filter((identity) => identity.pid === pid);
}

function matchingTrackedIdentity(candidate) {
  return trackedIdentitiesForPid(candidate.pid).find((identity) => processIdentityMatches(candidate, identity));
}

function storeTrackedProcessIdentity(identity, { previousKey = "", isRoot = false } = {}) {
  const normalized = processIdentity(identity);
  if (!isDmtfCreationDate(normalized.creationDate)) {
    throw new Error("Tracked process identity did not contain a canonical DMTF creation date.");
  }
  const key = processGenerationKey(normalized);
  if (previousKey && previousKey !== key) trackedProcessIdentities.delete(previousKey);
  trackedProcessIdentities.set(key, normalized);
  if (isRoot || (previousKey && previousKey === trackedRootIdentityKey)) {
    trackedRootIdentityKey = key;
    trackedRootObserved = true;
  }
  return normalized;
}

function trackedIdentityForStartEvent(event) {
  if (!event) return undefined;
  return trackedIdentitiesForPid(event.pid).find((identity) =>
    identity.startEventSequence === event.sequence
    && identity.creationDate === event.creationDate
    && identity.name.toLowerCase() === event.name.toLowerCase());
}

async function refreshObservedProcessStartEvents() {
  let text;
  try {
    text = await readFile(processStartEventsPath, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT" && !processStartWatcherVerified) return;
    throw error;
  }
  const lines = text.split(/\r?\n/);
  if (text && !/\r?\n$/.test(text)) lines.pop();
  for (const line of lines) {
    if (!line.trim()) continue;
    const event = JSON.parse(line);
    const pid = Number(event?.processId || 0);
    const parentPid = Number(event?.parentProcessId || 0);
    const sequence = Number(event?.sequence || 0);
    if (pid <= 0 || parentPid < 0 || !String(event?.processName || "")) {
      throw new Error("Process-start watcher returned an invalid event row.");
    }
    if (!Number.isInteger(sequence) || sequence <= 0) {
      throw new Error("Process-start watcher returned an invalid event sequence.");
    }
    const normalizedEvent = {
      pid,
      parentPid,
      name: String(event.processName),
      sequence,
      creationDate: String(event.creationDate || ""),
    };
    if (!isDmtfCreationDate(normalizedEvent.creationDate)) {
      throw new Error("Process-start watcher returned a non-DMTF creation date.");
    }
    const existingSequence = observedProcessStartEventsBySequence.get(sequence);
    if (existingSequence && JSON.stringify(existingSequence) !== JSON.stringify(normalizedEvent)) {
      throw new Error("Process-start watcher reused an event sequence for different identities.");
    }
    observedProcessStartEvents.set(pid, normalizedEvent);
    observedProcessStartEventsBySequence.set(sequence, normalizedEvent);
  }
}

function observedStartEventBefore(pid, sequence) {
  let found;
  for (const event of observedProcessStartEventsBySequence.values()) {
    if (event.pid === pid && event.sequence < sequence && (!found || event.sequence > found.sequence)) {
      found = event;
    }
  }
  return found;
}

function matchingObservedStartEvent(candidate) {
  const matches = [...observedProcessStartEventsBySequence.values()].filter((event) =>
    event.pid === candidate.pid
    && event.parentPid === candidate.parentPid
    && event.name.toLowerCase() === candidate.name.toLowerCase()
    && event.creationDate
    && event.creationDate === candidate.creationDate
    && Number.isInteger(event.sequence)
    && event.sequence > 0);
  return matches.sort((left, right) => right.sequence - left.sequence)[0];
}

function bindObservedStartEvent(candidate) {
  const event = matchingObservedStartEvent(candidate);
  return {
    ...candidate,
    startEventSequence: event?.sequence || 0,
  };
}

function observedStartChainReachesTracked(candidate, liveTrackedByPid) {
  let event = matchingObservedStartEvent(candidate);
  if (!event) return false;
  const visited = new Set([candidate.pid]);
  while (event && event.parentPid > 0 && !visited.has(event.parentPid)) {
    const recordedLiveParent = liveTrackedByPid.get(event.parentPid);
    const parentStart = observedStartEventBefore(event.parentPid, event.sequence);
    if (
      recordedLiveParent
      && parentStart
      && recordedLiveParent.startEventSequence > 0
      && parentStart.sequence === recordedLiveParent.startEventSequence
      && event.sequence > recordedLiveParent.startEventSequence
    ) {
      return true;
    }
    if (parentStart && trackedIdentityForStartEvent(parentStart)) {
      return true;
    }
    visited.add(event.parentPid);
    if (!parentStart || parentStart.sequence >= event.sequence) return false;
    event = parentStart;
  }
  return false;
}

function observedStartDirectlyFollowsTracked(candidate, liveTrackedByPid) {
  const recordedParent = liveTrackedByPid.get(candidate.parentPid);
  if (!recordedParent) return false;
  const event = matchingObservedStartEvent(candidate);
  const parentStart = event ? observedStartEventBefore(candidate.parentPid, event.sequence) : undefined;
  return Boolean(
    event
    && parentStart
    && recordedParent.startEventSequence > 0
    && parentStart.sequence === recordedParent.startEventSequence
    && event.sequence > recordedParent.startEventSequence,
  );
}

function updateTrackedProcessTree(allProcesses) {
  const processes = allProcesses.map(processIdentity).filter((item) =>
    item.pid > 0 && isDmtfCreationDate(item.creationDate));
  const byPid = new Map(processes.map((item) => [item.pid, item]));
  if (trackedRootPid > 0 && !trackedRootObserved) {
    const root = byPid.get(trackedRootPid);
    if (root && normalizedPath(root.path) === normalizedPath(exe)) {
      storeTrackedProcessIdentity(bindObservedStartEvent(root), { isRoot: true });
      trackedProcessNamesEver.add(root.name);
    }
  }

  const liveTrackedByPid = new Map();
  for (const [key, recorded] of [...trackedProcessIdentities.entries()]) {
    const candidate = byPid.get(recorded.pid);
    if (candidate && processIdentityMatches(candidate, recorded)) {
      let liveIdentity = recorded;
      if (recorded.startEventSequence <= 0) {
        const rebound = bindObservedStartEvent(candidate);
        if (rebound.startEventSequence > 0) {
          liveIdentity = storeTrackedProcessIdentity(rebound, { previousKey: key });
        }
      }
      liveTrackedByPid.set(recorded.pid, liveIdentity);
    }
  }
  let added = true;
  while (added) {
    added = false;
    for (const candidate of processes) {
      if (
        !matchingTrackedIdentity(candidate)
        && (
          observedStartDirectlyFollowsTracked(candidate, liveTrackedByPid)
          || observedStartChainReachesTracked(candidate, liveTrackedByPid)
        )
      ) {
        const bound = bindObservedStartEvent(candidate);
        if (bound.startEventSequence > 0) {
          storeTrackedProcessIdentity(bound);
          trackedProcessNamesEver.add(candidate.name);
          liveTrackedByPid.set(candidate.pid, bound);
          added = true;
        }
      }
    }
  }
  return processes.filter((candidate) => Boolean(matchingTrackedIdentity(candidate)));
}

async function collectProcessSnapshot() {
  const raw = await runPowerShell(`
    ${powershellDmtfCreationDateHelper}
    $all = @(Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object {
      [pscustomobject]@{
        pid = [int]$_.ProcessId
        parentPid = [int]$_.ParentProcessId
        name = [string]$_.Name
        path = [string]$_.ExecutablePath
        creationDate = Convert-VrcForgeCreationDateToDmtf $_.CreationDate
      }
    })
    $ports = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
      Where-Object { $_.LocalPort -eq 8757 -or $_.LocalPort -eq ${cdpPort} } |
      ForEach-Object {
        [pscustomobject]@{
          localPort = [int]$_.LocalPort
          owningProcess = [int]$_.OwningProcess
        }
      })
    [pscustomobject]@{ all = $all; ports = $ports; portQuerySucceeded = $true } | ConvertTo-Json -Depth 5 -Compress
  `);
  const parsed = raw ? JSON.parse(raw) : { all: [], ports: [] };
  const all = Array.isArray(parsed?.all) ? parsed.all : parsed?.all ? [parsed.all] : [];
  const ports = Array.isArray(parsed?.ports) ? parsed.ports : parsed?.ports ? [parsed.ports] : [];
  await refreshObservedProcessStartEvents();
  const trackedProcesses = updateTrackedProcessTree(all);
  const rootPrefix = `${normalizedPath(packagedRoot)}/`;
  const packagedProcesses = all
    .map(processIdentity)
    .filter((item) => {
      const path = normalizedPath(item.path);
      return path === normalizedPath(exe) || path.startsWith(rootPrefix);
    });
  const processByPid = new Map();
  for (const item of [...packagedProcesses, ...trackedProcesses]) processByPid.set(item.pid, item);
  return {
    processes: [...processByPid.values()],
    packagedProcesses,
    trackedProcesses,
    ports,
    portQuerySucceeded: parsed?.portQuerySucceeded === true,
  };
}

async function processSnapshot() {
  if (!processSnapshotInFlight) {
    processSnapshotInFlight = collectProcessSnapshot().finally(() => {
      processSnapshotInFlight = undefined;
    });
  }
  return processSnapshotInFlight;
}

function startProcessTracking(rootPid) {
  trackedRootPid = Number(rootPid || 0);
  trackedRootObserved = false;
  trackedRootIdentityKey = "";
  trackedProcessIdentities.clear();
  trackedProcessNamesEver.clear();
  if (processTrackingTimer) clearInterval(processTrackingTimer);
  processTrackingTimer = setInterval(() => {
    void processSnapshot().catch(() => { processTrackingErrorCount += 1; });
  }, 500);
  processTrackingTimer.unref?.();
}

function stopProcessTracking() {
  if (processTrackingTimer) clearInterval(processTrackingTimer);
  processTrackingTimer = undefined;
}

async function waitForTrackedRoot(timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await processSnapshot();
    if (trackedRootObserved) return;
    await sleep(100);
  }
  throw new Error("Packaged root process identity could not be bound to the extracted VRCForge.exe.");
}

function snapshotIsClear(snapshot) {
  return snapshot?.portQuerySucceeded === true
    && (snapshot.packagedProcesses || []).length === 0
    && (snapshot.trackedProcesses || []).length === 0
    && (snapshot.ports || []).length === 0;
}

function summarizeSnapshot(snapshot) {
  const processes = snapshot.processes || [];
  return {
    processCount: processes.length,
    processNames: [...new Set(processes.map((item) => String(item.name || item.ProcessName || "")).filter(Boolean))].sort(),
    packagedProcessCount: (snapshot.packagedProcesses || []).length,
    trackedPidCount: (snapshot.trackedProcesses || []).length,
    trackedProcessNames: [...new Set((snapshot.trackedProcesses || []).map((item) => String(item.name || "")).filter(Boolean))].sort(),
    portCount: (snapshot.ports || []).length,
    portQuerySucceeded: snapshot?.portQuerySucceeded === true,
    ports: [...new Set((snapshot.ports || []).map((item) => Number(item.localPort || item.LocalPort || 0)).filter(Boolean))].sort(),
  };
}

async function waitForPackagedClear(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let latest = await processSnapshot();
  while (Date.now() < deadline) {
    if (snapshotIsClear(latest)) return { ok: true, snapshot: latest };
    await sleep(500);
    latest = await processSnapshot();
  }
  return { ok: snapshotIsClear(latest), snapshot: latest };
}

function buildTrackedCleanupCandidates(rootPid) {
  return [...trackedProcessIdentities.entries()]
    .filter(([key, item]) => {
      const path = normalizedPath(item.path);
      // Only the exact original generation may be treated as the root. A real
      // descendant can reuse the old root PID after that generation exits.
      return key === trackedRootIdentityKey ? path === normalizedPath(exe) : true;
    })
    .map(([key, item]) => ({
      generationKey: key,
      pid: item.pid,
      path: item.path,
      creationDate: item.creationDate,
      startEventSequence: item.startEventSequence,
      isRoot: key === trackedRootIdentityKey && item.pid === rootPid,
    }));
}

async function forceCloseLaunch(launch) {
  if (!launch?.childPid) return processSnapshot();
  const rootPid = Number(launch.childPid);
  try {
    await processSnapshot();
  } catch {
    processTrackingErrorCount += 1;
  }
  if (!trackedRootObserved) {
    const escapedExe = escapePowerShellLiteral(exe);
    const rootRaw = await runPowerShell(`
      ${powershellDmtfCreationDateHelper}
      $expected = [IO.Path]::GetFullPath('${escapedExe}')
      $current = Get-CimInstance Win32_Process -Filter "ProcessId = ${rootPid}" -ErrorAction SilentlyContinue
      if ($current -and ([string]$current.ExecutablePath).Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
        [pscustomobject]@{
          pid = [int]$current.ProcessId
          parentPid = [int]$current.ParentProcessId
          name = [string]$current.Name
          path = [string]$current.ExecutablePath
          creationDate = Convert-VrcForgeCreationDateToDmtf $current.CreationDate
        } | ConvertTo-Json -Compress
      }
    `).catch(() => "");
    if (rootRaw) {
      const identity = processIdentity(JSON.parse(rootRaw));
      storeTrackedProcessIdentity(bindObservedStartEvent(identity), { isRoot: true });
      trackedProcessNamesEver.add(identity.name);
      try {
        await processSnapshot();
      } catch {
        processTrackingErrorCount += 1;
      }
    }
  }
  // Every non-root generation here was observed as a transitive child of the
  // exact packaged root. Keep all generations: the PowerShell side matches
  // PID + creation time + path before terminating the currently live one.
  const candidates = buildTrackedCleanupCandidates(rootPid);
  if (candidates.length === 0) {
    const terminated = launch.childProcess?.kill?.() === true;
    if (!terminated) {
      throw new Error("Spawned packaged root could not be identity-bound or terminated through its child handle.");
    }
    const cleared = await waitForPackagedClear(20000);
    if (!cleared.ok) throw new Error("Spawned packaged root handle terminated but its process boundary did not clear.");
    return cleared.snapshot;
  }
  const encodedCandidates = Buffer.from(JSON.stringify(candidates), "utf8").toString("base64");
  await runPowerShell(`
    ${powershellDmtfCreationDateHelper}
    $json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${encodedCandidates}'))
    $candidates = @($json | ConvertFrom-Json)
    foreach ($candidate in @($candidates | Sort-Object @{ Expression = { if ($_.isRoot) { 1 } else { 0 } } })) {
      $current = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$candidate.pid)" -ErrorAction SilentlyContinue
      if (-not $current) { continue }
      $currentCreationDate = Convert-VrcForgeCreationDateToDmtf $current.CreationDate
      $sameCreation = ([string]$currentCreationDate).Equals([string]$candidate.creationDate, [StringComparison]::Ordinal)
      $samePath = ([string]$current.ExecutablePath).Equals([string]$candidate.path, [StringComparison]::OrdinalIgnoreCase)
      if ($sameCreation -and $samePath) {
        Stop-Process -Id ([int]$candidate.pid) -Force -ErrorAction SilentlyContinue
      }
    }
  `);
  const cleared = await waitForPackagedClear(20000);
  if (!cleared.ok) {
    throw new Error("Tracked packaged launch did not clear without touching other instances.");
  }
  return cleared.snapshot;
}

async function closePackagedApp(launch) {
  if (!launch?.childPid) throw new Error("Tracked packaged launch was unavailable for close.");
  const escapedExe = escapePowerShellLiteral(exe);
  const rootPid = Number(launch.childPid);
  const rootIdentity = trackedProcessIdentities.get(trackedRootIdentityKey);
  if (
    !rootIdentity
    || rootIdentity.pid !== rootPid
    || normalizedPath(rootIdentity.path) !== normalizedPath(exe)
  ) {
    throw new Error("Tracked packaged root generation was unavailable for graceful close.");
  }
  const escapedRootCreationDate = escapePowerShellLiteral(rootIdentity.creationDate);
  const requestedRaw = await runPowerShell(`
    ${powershellDmtfCreationDateHelper}
    $exe = [IO.Path]::GetFullPath('${escapedExe}')
    $expectedCreationDate = '${escapedRootCreationDate}'
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = ${rootPid}" -ErrorAction SilentlyContinue
    $identityMatched = $false
    if ($current) {
      $currentCreationDate = Convert-VrcForgeCreationDateToDmtf $current.CreationDate
      $sameCreation = ([string]$currentCreationDate).Equals($expectedCreationDate, [StringComparison]::Ordinal)
      $samePath = ([string]$current.ExecutablePath).Equals($exe, [StringComparison]::OrdinalIgnoreCase)
      $identityMatched = [bool]($sameCreation -and $samePath)
    }
    $targets = @()
    if ($identityMatched) {
      $targets = @(Get-Process -Id ${rootPid} -ErrorAction SilentlyContinue)
    }
    $results = @(foreach ($target in $targets) {
      [pscustomobject]@{
        pid = $target.Id
        mainWindowHandle = [int64]$target.MainWindowHandle
        closeRequested = [bool]$target.CloseMainWindow()
      }
    })
    [pscustomobject]@{ identityMatched = $identityMatched; targets = $results } | ConvertTo-Json -Depth 4 -Compress
  `);
  const requested = requestedRaw ? JSON.parse(requestedRaw) : { targets: [] };
  const targets = Array.isArray(requested?.targets)
    ? requested.targets
    : requested?.targets
      ? [requested.targets]
      : [];
  const closeAccepted = requested?.identityMatched === true
    && targets.length === 1
    && Number(targets[0]?.pid) === rootPid
    && Number(targets[0]?.mainWindowHandle) !== 0
    && targets[0]?.closeRequested === true;
  const graceful = await waitForPackagedClear(30000);
  if (graceful.ok) {
    return {
      trackedPidCount: 1,
      targetedCount: targets.length,
      closeAccepted,
      graceful: closeAccepted,
      forced: false,
      finalSnapshot: summarizeSnapshot(graceful.snapshot),
    };
  }
  const beforeForce = summarizeSnapshot(graceful.snapshot);
  await forceCloseLaunch(launch);
  return {
    trackedPidCount: 1,
    targetedCount: targets.length,
    closeAccepted,
    graceful: false,
    forced: true,
    beforeForce,
    finalSnapshot: summarizeSnapshot(await processSnapshot()),
  };
}

async function waitForFileJson(path, child, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      return JSON.parse(await readFile(path, "utf8"));
    } catch (error) {
      lastError = error;
      if (error?.code !== "ENOENT" && !(error instanceof SyntaxError)) throw error;
    }
    if (child.exitCode !== null) {
      throw new Error(`Executable lock helper exited before readiness (code ${child.exitCode}).`);
    }
    await sleep(100);
  }
  throw lastError || new Error("Timed out waiting for executable launch lock readiness.");
}

async function releaseExecutableLaunchLock(lock) {
  if (!lock) return;
  const exitedBeforeRelease = lock.child.exitCode !== null;
  const releasedAt = Date.now();
  await writeFile(lock.releasePath, "release\n", "utf8").catch(() => {});
  if (lock.child.exitCode === null) {
    await Promise.race([
      new Promise((resolveExit) => lock.child.once("exit", resolveExit)),
      sleep(15000),
    ]);
  }
  const timedOut = lock.child.exitCode === null;
  if (timedOut) lock.child.kill();
  const badExit = lock.child.exitCode !== null && lock.child.exitCode !== 0;
  const settledForMs = Date.now() - releasedAt;
  lock.settleVerified = !exitedBeforeRelease
    && !timedOut
    && !badExit
    && Number.isInteger(lock.settleWindowMs)
    && lock.settleWindowMs >= 5000
    && settledForMs >= lock.settleWindowMs - 250;
  processStartWatcherSettleVerified = lock.settleVerified;
  processStartWatcherSettleMs = lock.settleWindowMs || 0;
  await Promise.all([
    unlink(lock.readyPath).catch(() => {}),
    unlink(lock.releasePath).catch(() => {}),
  ]);
  if (exitedBeforeRelease || timedOut || badExit || !lock.settleVerified) {
    throw new Error("Executable/process-start lock helper did not remain healthy through its bounded cleanup window.");
  }
  await processSnapshot();
}

function executableLaunchLockReadiness(ready, expectedSha256, childExitCode) {
  const sha256 = String(ready?.sha256 || "").toLowerCase();
  const expected = String(expectedSha256 || "").toLowerCase();
  const watcherMode = String(ready?.processStartWatcherMode || "");
  const settleWindowMs = Number(ready?.settleWindowMs || 0);
  if (ready?.ok !== true) {
    return { ok: false, reason: "watcher-failed", sha256, watcherMode, settleWindowMs };
  }
  if (!/^[0-9a-f]{64}$/.test(expected) || sha256 !== expected) {
    return { ok: false, reason: "digest-mismatch", sha256, watcherMode, settleWindowMs };
  }
  if (
    ready?.processStartWatcher !== true
    || watcherMode !== "wmi-instance-creation-poll-100ms"
    || !Number.isInteger(settleWindowMs)
    || settleWindowMs < 5000
    || childExitCode !== null
  ) {
    return { ok: false, reason: "watcher-not-ready", sha256, watcherMode, settleWindowMs };
  }
  return { ok: true, reason: "", sha256, watcherMode, settleWindowMs };
}

async function acquireExecutableLaunchLock(expectedSha256) {
  const readyPath = resolve(evidenceRoot, "executable-launch-lock.ready.json");
  const releasePath = resolve(evidenceRoot, "executable-launch-lock.release");
  await Promise.all([
    unlink(readyPath).catch(() => {}),
    unlink(releasePath).catch(() => {}),
    unlink(processStartEventsPath).catch(() => {}),
  ]);
  const script = String.raw`
$ErrorActionPreference = 'Stop'
${powershellDmtfCreationDateHelper}
$readyPath = [string]$env:VRCFORGE_PROBE_LOCK_READY
$releasePath = [string]$env:VRCFORGE_PROBE_LOCK_RELEASE
$targetPath = [string]$env:VRCFORGE_PROBE_LOCK_TARGET
$eventsPath = [string]$env:VRCFORGE_PROBE_PROCESS_EVENTS
$ownerPid = [int]$env:VRCFORGE_PROBE_OWNER_PID
$deadline = [DateTime]::UtcNow.AddMinutes(30)
$settleWindowMs = 5000
$sourceIdentifier = "VRCForgeProbeProcessStart_$PID"
$sequence = 0
function Write-ProcessStartEvent([object]$eventRecord) {
  $script:sequence += 1
  $started = $eventRecord.SourceEventArgs.NewEvent.TargetInstance
  $row = [pscustomobject]@{
    sequence = $script:sequence
    processId = [int]$started.ProcessID
    parentProcessId = [int]$started.ParentProcessID
    processName = [string]$started.Name
    creationDate = Convert-VrcForgeCreationDateToDmtf $started.CreationDate
    observedAt = [DateTime]::UtcNow.ToString('o')
  }
  [System.IO.File]::AppendAllText(
    $eventsPath,
    (($row | ConvertTo-Json -Compress) + [Environment]::NewLine),
    [System.Text.UTF8Encoding]::new($false)
  )
  Remove-Event -EventIdentifier $eventRecord.EventIdentifier -ErrorAction SilentlyContinue
}
try {
  $stream = [System.IO.File]::Open(
    $targetPath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::Read
  )
  try {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
      $hash = ([System.BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
      $stream.Position = 0
    } finally {
      $sha.Dispose()
    }
    # Win32_ProcessStartTrace requires elevated WMI trace access on some normal
    # desktop sessions. The polling creation indication exposes the same
    # process identity fields without that elevation requirement. Keep the
    # executable stream open while this watcher is active so the digest-bound
    # launch target cannot be replaced between verification and spawn.
    $watcherQuery = "SELECT * FROM __InstanceCreationEvent WITHIN 0.1 WHERE TargetInstance ISA 'Win32_Process'"
    Register-WmiEvent -Query $watcherQuery -SourceIdentifier $sourceIdentifier | Out-Null
    try {
      [System.IO.File]::WriteAllText($eventsPath, '', [System.Text.UTF8Encoding]::new($false))
      $payload = [pscustomobject]@{
        ok = $true
        sha256 = $hash
        helperPid = $PID
        processStartWatcher = $true
        processStartWatcherMode = 'wmi-instance-creation-poll-100ms'
        settleWindowMs = $settleWindowMs
      }
      [System.IO.File]::WriteAllText($readyPath, ($payload | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false))
      while (-not [System.IO.File]::Exists($releasePath)) {
        if (-not (Get-Process -Id $ownerPid -ErrorAction SilentlyContinue)) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Executable lock helper exceeded its bounded lifetime.' }
        $eventRecord = Wait-Event -SourceIdentifier $sourceIdentifier -Timeout 1
        if ($eventRecord) { Write-ProcessStartEvent $eventRecord }
      }
      $settleUntil = [DateTime]::UtcNow.AddMilliseconds($settleWindowMs)
      while ([DateTime]::UtcNow -lt $settleUntil) {
        $eventRecord = Wait-Event -SourceIdentifier $sourceIdentifier -Timeout 1
        if ($eventRecord) { Write-ProcessStartEvent $eventRecord }
      }
      while ($eventRecord = Get-Event -SourceIdentifier $sourceIdentifier -ErrorAction SilentlyContinue | Select-Object -First 1) {
        Write-ProcessStartEvent $eventRecord
      }
    } finally {
      Unregister-Event -SourceIdentifier $sourceIdentifier -ErrorAction SilentlyContinue
      Get-Event -SourceIdentifier $sourceIdentifier -ErrorAction SilentlyContinue | Remove-Event -ErrorAction SilentlyContinue
      Get-Job -Name $sourceIdentifier -ErrorAction SilentlyContinue | Remove-Job -Force -ErrorAction SilentlyContinue
    }
  } finally {
    $stream.Dispose()
  }
} catch {
  $payload = [pscustomobject]@{ ok = $false; errorType = $_.Exception.GetType().Name }
  [System.IO.File]::WriteAllText($readyPath, ($payload | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false))
  exit 1
}
`;
  const child = spawn("powershell.exe", [
    "-NoLogo",
    "-NoProfile",
    "-NonInteractive",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    script,
  ], {
    stdio: "ignore",
    env: {
      ...process.env,
      VRCFORGE_PROBE_LOCK_READY: readyPath,
      VRCFORGE_PROBE_LOCK_RELEASE: releasePath,
      VRCFORGE_PROBE_LOCK_TARGET: exe,
      VRCFORGE_PROBE_PROCESS_EVENTS: processStartEventsPath,
      VRCFORGE_PROBE_OWNER_PID: String(process.pid),
    },
  });
  const lock = {
    child,
    readyPath,
    releasePath,
    sha256: "",
    verified: false,
    processStartWatcher: false,
    processStartWatcherMode: "",
    settleWindowMs: 0,
    settleVerified: false,
  };
  try {
    const ready = await waitForFileJson(readyPath, child);
    const readiness = executableLaunchLockReadiness(ready, expectedSha256, child.exitCode);
    lock.sha256 = readiness.sha256;
    lock.processStartWatcher = ready?.processStartWatcher === true;
    lock.processStartWatcherMode = readiness.watcherMode;
    lock.settleWindowMs = readiness.settleWindowMs;
    if (readiness.reason === "watcher-failed") {
      throw new Error(`Executable launch lock watcher failed before readiness (${String(ready?.errorType || "unknown")}).`);
    }
    if (readiness.reason === "digest-mismatch") {
      throw new Error("Executable launch lock did not bind the manifest executable digest.");
    }
    lock.verified = readiness.ok;
    if (!lock.verified) throw new Error("Executable launch lock process watcher did not remain ready.");
    processStartWatcherVerified = true;
    processStartWatcherMode = lock.processStartWatcherMode;
    return lock;
  } catch (error) {
    await releaseExecutableLaunchLock(lock).catch(() => {});
    throw error;
  }
}

function assertGracefulClosure(report, closure, label) {
  if (!closure.graceful) addAssertion(report, `packaged app did not complete an accepted graceful close ${label}`);
  if (closure.targetedCount !== 1) addAssertion(report, `packaged app did not target exactly its tracked process ${label}`);
  if (closure.finalSnapshot?.processCount || closure.finalSnapshot?.portCount) {
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
  throw lastError || new Error(`Timed out waiting for ${url}`);
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
      result.exceptionDetails.exception?.description
      || result.exceptionDetails.text
      || "Runtime.evaluate failed",
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
      if (last === true || last?.ok) return last;
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
  delete env.VRCFORGE_DISABLE_APP_AUTH;
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

async function launchPackagedApp(releaseBinding) {
  appSessionToken = "";
  const executableLock = await acquireExecutableLaunchLock(releaseBinding.innerExeSha256);
  const child = spawn(exe, [], {
    detached: false,
    stdio: "ignore",
    env: isolatedLaunchEnvironment(),
  });
  const launch = {
    childPid: child.pid,
    childProcess: child,
    launchedAt: new Date().toISOString(),
    cdp: null,
    executableLock,
  };
  startProcessTracking(child.pid);
  const spawnFailure = new Promise((_, rejectSpawn) => child.once("error", rejectSpawn));
  try {
    await Promise.race([waitForTrackedRoot(), spawnFailure]);
    const targets = await Promise.race([
      waitForJson(`http://127.0.0.1:${cdpPort}/json/list`, 45000),
      spawnFailure,
    ]);
    const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
    if (!page) throw new Error("Packaged WebView2 page target was not found.");
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
    await processSnapshot();
    return { ...launch, cdp, health, renderer };
  } catch (error) {
    try { launch.cdp?.close(); } catch { /* Renderer may not have connected. */ }
    let cleanupError;
    try {
      await forceCloseLaunch(launch);
    } catch (caughtCleanupError) {
      cleanupError = caughtCleanupError;
    }
    await releaseExecutableLaunchLock(executableLock);
    launch.executableLock = null;
    if (cleanupError) {
      throw new AggregateError([error, cleanupError], "Packaged launch failed and scoped cleanup did not complete.");
    }
    throw error;
  }
}

async function readAppToken() {
  const tokenPath = resolve(configRoot, "app-session-token");
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const value = (await readFile(tokenPath, "utf8")).trim();
      if (value) return value;
    } catch {
      // The isolated managed backend has not written its token yet.
    }
    await sleep(150);
  }
  throw new Error("Packaged app session token was not created in the isolated user-data root.");
}

async function requestJsonRaw(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs || 120000);
  try {
    const response = await fetch(url, {
      method: options.method || "GET",
      headers: options.headers || {},
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
    const text = await response.text();
    let payload;
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = { text: text.slice(0, 1000) };
    }
    return { ok: response.ok, status: response.status, payload };
  } finally {
    clearTimeout(timeout);
  }
}

async function appApiRaw(path, options = {}) {
  if (!appSessionToken) appSessionToken = await readAppToken();
  return requestJsonRaw(`${appOrigin}${path}`, {
    ...options,
    headers: {
      Origin: appRequestOrigin,
      Authorization: `Bearer ${appSessionToken}`,
      "Content-Type": "application/json",
    },
  });
}

async function appApi(path, options = {}) {
  const response = await appApiRaw(path, options);
  if (!response.ok) throw new Error(`${response.status} ${path}: ${JSON.stringify(response.payload)}`);
  return response.payload;
}

async function agentApiRaw(path, options = {}) {
  return requestJsonRaw(`${appOrigin}${path}`, {
    ...options,
    headers: {
      Origin: appRequestOrigin,
      Authorization: `Bearer ${agentGatewayToken}`,
      "Content-Type": "application/json",
    },
  });
}

async function agentApi(path, options = {}) {
  const response = await agentApiRaw(path, options);
  if (!response.ok) throw new Error(`${response.status} ${path}: ${JSON.stringify(response.payload)}`);
  return response.payload;
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
  if (!envelope?.ok) throw new Error(`Tauri ${command} failed: ${envelope?.error || "unknown error"}`);
  return envelope.value;
}

function normalizedPath(value) {
  return String(value || "").replaceAll("\\", "/").replace(/\/+$/, "").toLowerCase();
}

async function assertRuntimeBinding(sourceVersion, releaseBinding) {
  const missingToken = await requestJsonRaw(`${appOrigin}/api/app/skill-packages`, {
    headers: {
      Origin: appRequestOrigin,
      "Content-Type": "application/json",
    },
  });
  const wrongToken = await requestJsonRaw(`${appOrigin}/api/app/skill-packages`, {
    headers: {
      Origin: appRequestOrigin,
      Authorization: `Bearer invalid-${randomBytes(24).toString("base64url")}`,
      "Content-Type": "application/json",
    },
  });
  const appAuthMissingTokenRejected = [401, 403].includes(missingToken.status);
  const appAuthWrongTokenRejected = [401, 403].includes(wrongToken.status);
  if (!appAuthMissingTokenRejected || !appAuthWrongTokenRejected) {
    throw new Error("Packaged app API did not reject missing and incorrect session tokens.");
  }
  const health = await appApi("/api/health");
  const expected = {
    userDataDir: userDataRoot,
    configDir: configRoot,
    artifactsDir: resolve(userDataRoot, "artifacts"),
  };
  if (String(health?.version || "") !== sourceVersion) {
    throw new Error("Packaged backend version did not match VERSION.");
  }
  if (health?.portableMode !== true) throw new Error("Authenticated health did not report portableMode=true.");
  const healthProgramDir = String(health?.paths?.programDir || "");
  const canonicalPackagedRoot = normalizedPath(await realpath(packagedRoot));
  const canonicalProgramDir = healthProgramDir ? normalizedPath(await realpath(healthProgramDir)) : "";
  if (
    !healthProgramDir
    || normalizedPath(healthProgramDir) !== normalizedPath(packagedRoot)
    || canonicalProgramDir !== canonicalPackagedRoot
  ) {
    throw new Error("Authenticated health programDir did not canonically equal the isolated package extraction root.");
  }
  const canonicalEvidenceRoot = normalizedPath(await realpath(evidenceRoot));
  for (const [key, expectedPath] of Object.entries(expected)) {
    const actualPath = health?.paths?.[key];
    const canonicalExpected = normalizedPath(await realpath(expectedPath));
    const canonicalActual = actualPath ? normalizedPath(await realpath(actualPath)) : "";
    if (
      !actualPath
      || normalizedPath(actualPath) !== normalizedPath(expectedPath)
      || canonicalActual !== canonicalExpected
      || !canonicalActual.startsWith(`${canonicalEvidenceRoot}/`)
    ) {
      throw new Error(`Authenticated health ${key} did not match the isolated evidence root.`);
    }
  }

  const listenerRaw = await runPowerShell(`
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8757 -ErrorAction SilentlyContinue)
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $process = if ($owners.Count -eq 1) {
      Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$owners[0])" -ErrorAction SilentlyContinue
    } else { $null }
    [pscustomobject]@{
      listenerCount = $listeners.Count
      ownerCount = $owners.Count
      executableName = if ($process) { [string]$process.Name } else { '' }
      executablePath = if ($process) { [string]$process.ExecutablePath } else { '' }
    } | ConvertTo-Json -Depth 3 -Compress
  `);
  const listener = listenerRaw ? JSON.parse(listenerRaw) : {};
  if (Number(listener?.listenerCount || 0) < 1 || Number(listener?.ownerCount || 0) !== 1) {
    throw new Error("Port 8757 did not have exactly one listener owner.");
  }
  const listenerExecutablePath = String(listener?.executablePath || "");
  if (!listenerExecutablePath || String(listener?.executableName || "").toLowerCase() !== "vrcforge_backend.exe") {
    throw new Error("Port 8757 listener was not the packaged backend executable.");
  }
  const canonicalListenerExecutable = normalizedPath(await realpath(listenerExecutablePath));
  const canonicalExpectedBackend = normalizedPath(await realpath(backendExe));
  if (canonicalListenerExecutable !== canonicalExpectedBackend) {
    throw new Error("Port 8757 listener executable did not canonically equal the extracted packaged backend.");
  }
  const listenerBackendSha256 = await sha256File(listenerExecutablePath);
  if (
    listenerBackendSha256 !== releaseBinding.innerBackendSha256
    || listenerBackendSha256 !== releaseBinding.extractedBackendSha256
  ) {
    throw new Error("Port 8757 listener executable digest did not match the manifest-bound ZIP backend entry.");
  }
  return {
    authenticatedHealth: true,
    appAuthMissingTokenRejected,
    appAuthWrongTokenRejected,
    portableMode: true,
    versionMatches: true,
    programDirMatchesExtraction: true,
    isolatedDataPathsVerified: true,
    listenerUnique: true,
    listenerExecutableExact: true,
    backendDigestVerified: true,
    executableName: "vrcforge_backend.exe",
    backendSha256: listenerBackendSha256,
  };
}

async function writeIsolatedFixtures() {
  await Promise.all([
    mkdir(configRoot, { recursive: true }),
    mkdir(webviewDataRoot, { recursive: true }),
    mkdir(resolve(projectRoot, "Assets"), { recursive: true }),
    mkdir(resolve(projectRoot, "Packages"), { recursive: true }),
    mkdir(resolve(projectRoot, "ProjectSettings"), { recursive: true }),
    mkdir(dirname(fakeUnityCliPath), { recursive: true }),
    mkdir(packageFixtureRoot, { recursive: true }),
    mkdir(pathToSkillRoot, { recursive: true }),
  ]);
  await Promise.all([
    writeFile(resolve(projectRoot, "Packages", "manifest.json"), "{\"dependencies\":{}}\n", "utf8"),
    writeFile(resolve(projectRoot, "ProjectSettings", "ProjectVersion.txt"), "m_EditorVersion: packaged-fixture\n", "utf8"),
  ]);

  const fakeCli = `param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CliArgs)
$toolName = ''
for ($index = 0; $index -lt $CliArgs.Count; $index++) {
  if ($CliArgs[$index] -eq 'custom-tool' -and $index + 1 -lt $CliArgs.Count) {
    $toolName = $CliArgs[$index + 1]
    break
  }
}
if (($CliArgs -join ' ') -match 'tool list') {
  [pscustomobject]@{ ok = $true; tools = @('vrc_scan_animation_bindings','vrc_scan_avatar_items','vrc_scan_fx_animator','vrc_scan_wardrobe','vrc_scan_shader_materials','vrc_scan_avatar_performance') } | ConvertTo-Json -Depth 8 -Compress
  exit 0
}
$payload = [ordered]@{
  ok = $true
  fixture = 'packaged-transport-only'
  tool = $toolName
  avatars = @()
  bindings = @()
  clips = @()
  items = @()
  materials = @()
  findings = @()
  errors = @()
  warnings = @()
  controls = @()
  parameters = @()
  layers = @()
  residueCount = 0
  projectReadable = $true
}
$payload | ConvertTo-Json -Depth 8 -Compress
`;
  await writeFile(fakeUnityCliPath, fakeCli, "utf8");

  const settings = {
    unity_mcp: {
      command: [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        fakeUnityCliPath,
      ],
      host: "127.0.0.1",
      port: 65534,
      instance: "packaged-static-fixture",
      retries: 1,
      retry_backoff_seconds: 0,
      timeout_seconds: 10,
    },
    paths: {},
    planning: {},
    dashboard: { project_roots: [projectRoot] },
  };
  await Promise.all([
    writeFile(resolve(configRoot, "config.json"), `${JSON.stringify(settings, null, 2)}\n`, "utf8"),
    writeFile(resolve(configRoot, "settings.json"), `${JSON.stringify(settings, null, 2)}\n`, "utf8"),
    writeFile(
      resolve(configRoot, "agent_gateway.json"),
      `${JSON.stringify({
        enabled: true,
        require_token: true,
        token: agentGatewayToken,
        approval_token: agentApprovalToken,
        allow_write_requests: true,
        allow_roslyn_advanced: false,
        approval_timeout_seconds: 600,
        execution_mode: "approval",
        roslyn_risk_acknowledged: false,
        developer_options_enabled: false,
        developer_options_ever_enabled: false,
        computer_use_enabled: false,
        computer_use_ever_enabled: false,
        checkpoint_archive_max_size_mb: 64,
        checkpoint_archive_dir: "",
      }, null, 2)}\n`,
      "utf8",
    ),
  ]);
}

async function buildSignedExamplePackages(sourceVersion, fixtureSource) {
  const fixtureSourceRoot = fixtureSource.root;
  const builderStore = resolve(packageFixtureRoot, ".builder-store");
  const builderCode = String.raw`
import base64
import io
import json
import hashlib
import shutil
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from skill_packages import (
    LOCK_NAME,
    MANIFEST_NAME,
    PUBLIC_KEY_NAME,
    SIGNATURE_NAME,
    SkillPackageService,
    canonical_json_bytes,
)

repo = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
key_path = Path(sys.argv[3]).resolve()
version = sys.argv[4]
slugs = json.loads(sys.argv[5])
source_repo = Path(sys.argv[6]).resolve()
source_commit = sys.argv[7]
zip_slip_outside_name = sys.argv[8]
if Path(zip_slip_outside_name).name != zip_slip_outside_name:
    raise RuntimeError("Zip-slip fixture outside name must be one basename")
output.mkdir(parents=True, exist_ok=True)
service = SkillPackageService(output / ".builder-store", vrcforge_version=version)
pair = service.generate_signing_keypair()
wrong_pair = service.generate_signing_keypair()
service.save_signing_keypair(pair, key_path)
packages = []
reserved = {LOCK_NAME, SIGNATURE_NAME, PUBLIC_KEY_NAME}

def archive_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_archive(path, entries, duplicate_name=None):
    path = Path(path)
    with zipfile.ZipFile(path, "w", allowZip64=False) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100644 << 16)
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        if duplicate_name:
            info = zipfile.ZipInfo(duplicate_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100644 << 16)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(
                    info,
                    entries[duplicate_name],
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )

def read_archive(path):
    with zipfile.ZipFile(path, "r") as archive:
        return {info.filename: archive.read(info) for info in archive.infolist() if not info.is_dir()}

def verify_signed_archive(path):
    with zipfile.ZipFile(path, "r") as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise RuntimeError(f"Duplicate signed archive entry in {Path(path).name}")
        entries = {name: archive.read(name) for name in names}
    if not {MANIFEST_NAME, LOCK_NAME, SIGNATURE_NAME, PUBLIC_KEY_NAME}.issubset(entries):
        raise RuntimeError(f"Signed archive metadata is incomplete: {Path(path).name}")
    manifest = json.loads(entries[MANIFEST_NAME])
    lock = json.loads(entries[LOCK_NAME])
    payload = {name: data for name, data in entries.items() if name not in reserved}
    payload_hashes = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in sorted(payload.items())
    }
    public_key = base64.b64decode(entries[PUBLIC_KEY_NAME], validate=True)
    signature = base64.b64decode(entries[SIGNATURE_NAME], validate=True)
    Ed25519PublicKey.from_public_bytes(public_key).verify(signature, entries[LOCK_NAME])
    checks = {
        "archiveEntrySetVerified": set(entries) == set(payload) | reserved,
        "manifestCanonical": entries[MANIFEST_NAME] == canonical_json_bytes(manifest),
        "lockCanonical": entries[LOCK_NAME] == canonical_json_bytes(lock),
        "lockExactPayload": lock.get("files") == payload_hashes,
        "lockIncludesCanonicalManifest": lock.get("files", {}).get(MANIFEST_NAME)
            == hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        "payloadDigestsVerified": all(
            lock.get("files", {}).get(name) == hashlib.sha256(data).hexdigest()
            for name, data in payload.items()
        ),
        "signatureVerified": True,
        "privateKeyMaterialAbsent": all(
            pair.private_key_pem not in data
            and wrong_pair.private_key_pem not in data
            and b"-----BEGIN PRIVATE KEY-----" not in data
            for data in entries.values()
        ),
        "publicKeyFingerprintVerified": hashlib.sha256(public_key).hexdigest(),
    }
    if not all(value is True for key, value in checks.items() if key != "publicKeyFingerprintVerified"):
        raise RuntimeError(f"Signed archive coverage verification failed: {Path(path).name}")
    return {
        "name": Path(path).name,
        "archiveSha256": archive_sha256(path),
        "lockSha256": hashlib.sha256(entries[LOCK_NAME]).hexdigest(),
        "payloadSetSha256": hashlib.sha256(canonical_json_bytes(payload_hashes)).hexdigest(),
        "publicKeyBase64": base64.b64encode(public_key).decode("ascii"),
        "signerFingerprint": hashlib.sha256(public_key).hexdigest(),
        "manifest": manifest,
        "checks": checks,
    }

def expected_payload(slug):
    prefix = f"examples/skill-packages/{slug}/"
    payload = {}
    if source_commit:
        tree = subprocess.run(
            ["git", "-C", str(source_repo), "ls-tree", "-r", "-z", source_commit, "--", prefix.rstrip("/")],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        for raw in tree.split(b"\0"):
            if not raw:
                continue
            metadata, raw_path = raw.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
            if kind != "blob" or mode not in {"100644", "100755"} or not path.startswith(prefix):
                raise RuntimeError(f"Unsupported committed fixture entry: {path}")
            relative = path[len(prefix):]
            if not relative or relative in payload:
                raise RuntimeError(f"Invalid duplicate committed fixture entry: {path}")
            payload[relative] = subprocess.run(
                ["git", "-C", str(source_repo), "cat-file", "blob", object_id],
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
    else:
        source = repo / prefix
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise RuntimeError(f"Symlink local fixture entry: {path}")
            if path.is_file():
                relative = path.relative_to(source).as_posix()
                payload[relative] = path.read_bytes()
            elif not path.is_dir():
                raise RuntimeError(f"Unsupported local fixture entry: {path}")
    if "manifest.json" not in payload:
        raise RuntimeError(f"Fixture {slug} has no manifest.json")
    manifest = json.loads(payload["manifest.json"].decode("utf-8-sig"))
    payload["manifest.json"] = canonical_json_bytes(service.validate_manifest(manifest))
    return payload

def verify_exported_payload(package_path, expected):
    with zipfile.ZipFile(package_path, "r") as archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        if len(names) != len(set(names)):
            raise RuntimeError(f"Duplicate archive entry in {package_path.name}")
        if set(names) != set(expected) | reserved:
            raise RuntimeError(f"Signed package entries do not exactly match bound source for {package_path.name}")
        for relative, expected_bytes in expected.items():
            if archive.read(relative) != expected_bytes:
                raise RuntimeError(f"Signed package payload differs from bound source: {package_path.name}:{relative}")
        lock = json.loads(archive.read(LOCK_NAME))
        expected_hashes = {
            relative: hashlib.sha256(value).hexdigest()
            for relative, value in sorted(expected.items())
        }
        if lock.get("files") != expected_hashes:
            raise RuntimeError(f"Signed package lock is not bound to expected source: {package_path.name}")
    return {
        "sourceVerified": True,
        "sourceFileCount": len(expected),
        "sourcePayloadDigest": hashlib.sha256(canonical_json_bytes(expected_hashes)).hexdigest(),
        "sourceFileSha256": expected_hashes,
    }

for slug in slugs:
    expected = expected_payload(slug)
    result = service.export_release(
        repo / "examples" / "skill-packages" / slug,
        output / f"{slug}.vsk",
        pair.private_key_pem,
        overwrite=False,
    )
    packages.append({
        "slug": slug,
        "id": result.manifest["id"],
        "version": result.manifest["version"],
        "signatureStatus": result.signature_status,
        "signerFingerprint": result.signer_fingerprint,
        "lockSha256": result.lock_sha256,
        "fileCount": result.file_count,
        **verify_exported_payload(result.package_path, expected),
    })

matrix_root = output / ".builder-store" / "apk-semantic-source"
matrix_id = "community.apk-semantic-probe"
matrix_skill_name = "apk-semantic-probe"
matrix_author = "VRCForge User"

def matrix_manifest(*, package_id=matrix_id, package_version="1.0.0", author=matrix_author):
    return {
        "id": package_id,
        "name": "APK Semantic VSK Probe",
        "skill_name": matrix_skill_name,
        "version": package_version,
        "author": author,
        "description": "Ephemeral packaged acceptance fixture for immutable signed VSK updates.",
        "min_vrcforge_version": version,
        "permissions": ["read_project"],
        "entrypoints": {"skill": "SKILL.md"},
    }

def matrix_skill_text(revision):
    return (
        "---\n"
        f"name: {matrix_skill_name}\n"
        "title: APK Semantic VSK Probe\n"
        "description: Read-only packaged update identity fixture.\n"
        "permission-mode: read_only\n"
        "risk-level: low\n"
        "allowed-tools:\n"
        "  - vrcforge_health\n"
        "---\n\n"
        f"Immutable signed package revision {revision}. Read health only; never write.\n"
    )

def write_matrix_source(manifest, revision):
    shutil.rmtree(matrix_root, ignore_errors=True)
    matrix_root.mkdir(parents=True, exist_ok=False)
    (matrix_root / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (matrix_root / "SKILL.md").write_text(matrix_skill_text(revision), encoding="utf-8")
    return hashlib.sha256(canonical_json_bytes({
        MANIFEST_NAME: hashlib.sha256(canonical_json_bytes(manifest)).hexdigest(),
        "SKILL.md": hashlib.sha256(matrix_skill_text(revision).encode("utf-8")).hexdigest(),
    })).hexdigest()

def export_matrix(name, *, package_version, revision, author=matrix_author, package_id=matrix_id, key_pair=pair):
    manifest = matrix_manifest(package_id=package_id, package_version=package_version, author=author)
    source_digest = write_matrix_source(manifest, revision)
    result = service.export_release(
        matrix_root,
        output / name,
        key_pair.private_key_pem,
        overwrite=False,
    )
    verified = verify_signed_archive(result.package_path)
    verified["sourceDigest"] = source_digest
    return verified

fixture_base = export_matrix(
    "apk-semantic-builder-base-template.vsk",
    package_version="1.0.0",
    revision="base-1.0.0",
)
update = export_matrix(
    "apk-semantic-update.vsk",
    package_version="1.1.0",
    revision="update-1.1.0",
)
wrong_author = export_matrix(
    "apk-semantic-wrong-author.vsk",
    package_version="1.2.0",
    revision="wrong-author-1.2.0",
    author="Different Packaged Probe Author",
)
wrong_signer = export_matrix(
    "apk-semantic-wrong-signer.vsk",
    package_version="1.2.0",
    revision="wrong-signer-1.2.0",
    key_pair=wrong_pair,
)
downgrade = export_matrix(
    "apk-semantic-downgrade.vsk",
    package_version="0.9.0",
    revision="downgrade-0.9.0",
)
same_version_different = export_matrix(
    "apk-semantic-same-version-different.vsk",
    package_version="1.0.0",
    revision="different-content-1.0.0",
)
different_id = export_matrix(
    "apk-semantic-distinct-id.vsk",
    package_version="1.2.0",
    revision="distinct-package-id-1.2.0",
    package_id="community.apk-semantic-probe-other",
)

base_entries = read_archive(output / fixture_base["name"])
update_entries = read_archive(output / update["name"])

def mutated_archive(name, entries, *, expected_error_token):
    path = output / name
    write_archive(path, entries)
    return {
        "name": name,
        "archiveSha256": archive_sha256(path),
        "expectedErrorToken": expected_error_token,
    }

payload_tamper_entries = dict(base_entries)
payload_tamper_entries["SKILL.md"] += b"\nTampered after signing.\n"
payload_tamper = mutated_archive(
    "apk-semantic-payload-tamper.vsk",
    payload_tamper_entries,
    expected_error_token="SHA-256 mismatch for SKILL.md",
)

manifest_tamper_entries = dict(base_entries)
tampered_manifest = json.loads(manifest_tamper_entries[MANIFEST_NAME])
tampered_manifest["author"] = "Tampered Unsigned Author"
manifest_tamper_entries[MANIFEST_NAME] = canonical_json_bytes(tampered_manifest)
manifest_tamper = mutated_archive(
    "apk-semantic-manifest-tamper.vsk",
    manifest_tamper_entries,
    expected_error_token="SHA-256 mismatch for manifest.json",
)

wrong_public_key_entries = dict(base_entries)
wrong_public_key_entries[PUBLIC_KEY_NAME] = base64.b64encode(wrong_pair.public_key)
wrong_public_key = mutated_archive(
    "apk-semantic-wrong-public-key.vsk",
    wrong_public_key_entries,
    expected_error_token="Ed25519 signature verification failed",
)

invalid_signature_entries = dict(base_entries)
invalid_signature = bytearray(base64.b64decode(invalid_signature_entries[SIGNATURE_NAME], validate=True))
invalid_signature[0] ^= 1
invalid_signature_entries[SIGNATURE_NAME] = base64.b64encode(bytes(invalid_signature))
invalid_signature_package = mutated_archive(
    "apk-semantic-invalid-signature.vsk",
    invalid_signature_entries,
    expected_error_token="Ed25519 signature verification failed",
)

zip_slip_entries = dict(base_entries)
zip_slip_entries[f"../{zip_slip_outside_name}"] = b"must never extract"
zip_slip = mutated_archive(
    "apk-semantic-zip-slip.vsk",
    zip_slip_entries,
    expected_error_token="Traversal is not allowed",
)

duplicate_path = output / "apk-semantic-duplicate-path.vsk"
write_archive(duplicate_path, base_entries, duplicate_name="SKILL.md")
duplicate = {
    "name": duplicate_path.name,
    "archiveSha256": archive_sha256(duplicate_path),
    "expectedErrorToken": "Duplicate archive member",
}

signing_key = serialization.load_pem_private_key(pair.private_key_pem, password=None)

def signed_invalid_manifest(name, mutate, expected_error_token):
    entries = dict(update_entries)
    manifest = json.loads(entries[MANIFEST_NAME])
    mutate(manifest)
    entries[MANIFEST_NAME] = canonical_json_bytes(manifest)
    lock = json.loads(entries[LOCK_NAME])
    lock["files"][MANIFEST_NAME] = hashlib.sha256(entries[MANIFEST_NAME]).hexdigest()
    entries[LOCK_NAME] = canonical_json_bytes(lock)
    entries[SIGNATURE_NAME] = base64.b64encode(signing_key.sign(entries[LOCK_NAME]))
    return mutated_archive(name, entries, expected_error_token=expected_error_token)

invalid_semver = signed_invalid_manifest(
    "apk-semantic-invalid-semver.vsk",
    lambda manifest: manifest.__setitem__("version", "not-semver"),
    "version must be a valid semantic version",
)
invalid_package_id = signed_invalid_manifest(
    "apk-semantic-invalid-package-id.vsk",
    lambda manifest: manifest.__setitem__("id", "Invalid Package ID"),
    "id must be a lowercase reverse-domain style identifier",
)
(output / fixture_base["name"]).unlink(missing_ok=True)

print(json.dumps({
    "fingerprint": pair.fingerprint,
    "publicKeyBase64": base64.b64encode(pair.public_key).decode("ascii"),
    "wrongSignerFingerprint": wrong_pair.fingerprint,
    "privateKeyFileBacked": True,
    "privateKeyMaterialReturned": False,
    "packages": packages,
    "apkSemantic": {
        "id": matrix_id,
        "skillName": matrix_skill_name,
        "authorId": matrix_author,
        "baseVersion": "1.0.0",
        "updateVersion": "1.1.0",
        "base": {"name": "apk-semantic-packaged-export.vsk"},
        "update": update,
        "differentId": different_id,
        "negatives": {
            "payloadTamper": payload_tamper,
            "manifestTamper": manifest_tamper,
            "wrongPublicKey": wrong_public_key,
            "invalidSignature": invalid_signature_package,
            "wrongAuthor": {**wrong_author, "expectedErrorToken": "Author identity"},
            "wrongSigner": {**wrong_signer, "expectedErrorToken": "Signer fingerprint"},
            "downgrade": {**downgrade, "expectedErrorToken": "downgrade"},
            "sameVersionDifferentContent": {
                **same_version_different,
                "expectedErrorToken": "published skill version is immutable",
            },
            "invalidSemver": invalid_semver,
            "invalidPackageId": invalid_package_id,
            "zipSlip": zip_slip,
            "duplicatePath": duplicate,
        },
        "zipSlipOutsideName": zip_slip_outside_name,
    },
}, separators=(",", ":")))
`;
  let built;
  let privateKeyDeleted = false;
  let privateKeyReady = false;
  let builderStoreDeleted = false;
  const strictFixtureCommit = fixtureSource.mode === "immutable-git-object-snapshot"
    ? fixtureSource.commit
    : "";
  const beforeSource = await verifyFixtureSource(fixtureSourceRoot, strictFixtureCommit);
  if (beforeSource?.ok !== true || beforeSource.digest !== fixtureSource.digest) {
    throw new Error("Fixture source changed before signed package export.");
  }
  try {
    built = await runPythonJson(
      builderCode,
      [
        fixtureSourceRoot,
        packageFixtureRoot,
        ephemeralSigningKeyPath,
        sourceVersion,
        JSON.stringify(exampleSlugs),
        repoRoot,
        strictFixtureCommit,
        zipSlipOutsideName,
      ],
      {
        timeoutMs: 120000,
        cwd: fixtureSourceRoot,
        env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
      },
    );
  } catch (error) {
    await unlink(ephemeralSigningKeyPath).catch(() => {});
    throw error;
  } finally {
    await rm(builderStore, { recursive: true, force: true }).catch(() => {});
    builderStoreDeleted = !(await pathExists(builderStore));
  }
  if (!built) throw new Error("Signed example builder did not return a result.");
  const afterSource = await verifyFixtureSource(fixtureSourceRoot, strictFixtureCommit);
  if (afterSource?.ok !== true || afterSource.digest !== fixtureSource.digest) {
    throw new Error("Fixture source changed during signed package export.");
  }
  const bySlug = new Map((built.packages || []).map((item) => [String(item.slug), item]));
  privateKeyReady = await pathExists(ephemeralSigningKeyPath);
  privateKeyDeleted = !privateKeyReady;
  if (!privateKeyReady) {
    throw new Error("Controlled ephemeral signing private key was unavailable for packaged export.");
  }
  if (builderStoreDeleted !== true) {
    throw new Error("Ephemeral signed-package builder store was not deleted after package creation.");
  }
  if (built.fingerprint && !/^[0-9a-f]{64}$/i.test(String(built.fingerprint))) {
    throw new Error("Signed example builder returned an invalid signer fingerprint.");
  }
  if (bySlug.size !== exampleSlugs.length) {
    throw new Error("Signed example builder did not produce all four packages.");
  }
  if ([...bySlug.values()].some((item) => item?.sourceVerified !== true)) {
    throw new Error("Signed example package bytes were not proven against their bound source payloads.");
  }
  const publicKeyBytes = Buffer.from(String(built.publicKeyBase64 || ""), "base64");
  if (
    publicKeyBytes.length !== 32
    || createHash("sha256").update(publicKeyBytes).digest("hex") !== String(built.fingerprint || "").toLowerCase()
    || built.privateKeyFileBacked !== true
    || built.privateKeyMaterialReturned !== false
  ) {
    throw new Error("Ephemeral Ed25519 author identity metadata was incomplete or private-key handling was unsafe.");
  }
  const matrix = built.apkSemantic && typeof built.apkSemantic === "object" ? built.apkSemantic : {};
  if (
    matrix.id !== apkSemanticPackageId
    || matrix.skillName !== apkSemanticSkillName
    || matrix.authorId !== apkSemanticAuthorId
    || matrix.baseVersion !== "1.0.0"
    || matrix.updateVersion !== "1.1.0"
  ) {
    throw new Error("APK-semantic VSK fixture identity/version metadata was incomplete.");
  }
  const materializeBuiltPath = (item) => {
    const name = String(item?.name || "");
    if (!name || basename(name) !== name || !name.toLowerCase().endsWith(".vsk")) {
      throw new Error("APK-semantic builder returned an unsafe package basename.");
    }
    return { ...item, path: resolve(packageFixtureRoot, name) };
  };
  const apkSemantic = {
    ...matrix,
    publicKeyBase64: String(built.publicKeyBase64 || ""),
    signerFingerprint: String(built.fingerprint || "").toLowerCase(),
    wrongSignerFingerprint: String(built.wrongSignerFingerprint || "").toLowerCase(),
    privateKeyFileBacked: built.privateKeyFileBacked === true,
    privateKeyMaterialReturned: built.privateKeyMaterialReturned === true,
    base: materializeBuiltPath(matrix.base),
    update: materializeBuiltPath(matrix.update),
    differentId: materializeBuiltPath(matrix.differentId),
    negatives: Object.fromEntries(Object.entries(matrix.negatives || {}).map(([key, item]) => [
      key,
      materializeBuiltPath(item),
    ])),
  };
  const builderMatrixPackageItems = [
    apkSemantic.update,
    apkSemantic.differentId,
    ...Object.values(apkSemantic.negatives),
  ];
  if (
    builderMatrixPackageItems.length !== 14
    || new Set(builderMatrixPackageItems.map((item) => item.path)).size !== builderMatrixPackageItems.length
    || !(await Promise.all(builderMatrixPackageItems.map((item) => pathExists(item.path)))).every(Boolean)
    || await pathExists(apkSemantic.base.path)
  ) {
    throw new Error("APK-semantic VSK fixture set was incomplete or contained duplicate output paths.");
  }
  return {
    fingerprint: String(built.fingerprint || "").toLowerCase(),
    privateKeyDeleted,
    privateKeyReady,
    builderStoreDeleted,
    sourceDigestVerified: true,
    sourcePackageBytesVerified: true,
    apkSemantic,
    packages: exampleSlugs.map((slug) => ({
      ...bySlug.get(slug),
      path: resolve(packageFixtureRoot, `${slug}.vsk`),
    })),
  };
}

function getField(value, ...keys) {
  for (const key of keys) {
    if (value && value[key] !== undefined && value[key] !== null) return value[key];
  }
  return undefined;
}

async function pathExists(path) {
  try {
    await stat(path);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function siblingTemporaryEntriesAbsent(target) {
  const parent = dirname(target);
  const name = basename(target);
  let entries;
  try {
    entries = await readdir(parent);
  } catch (error) {
    if (error?.code === "ENOENT") return true;
    throw error;
  }
  const lowerName = name.toLowerCase();
  return !entries.some((entry) => {
    const lower = entry.toLowerCase();
    if (lower === lowerName) return false;
    return (
      lower.startsWith(`.${lowerName}.`)
      && (lower.includes("vrcforge-stage-") || lower.endsWith(".tmp") || lower.includes(".stage"))
    );
  });
}

async function treeTemporaryEntriesAbsent(root) {
  const rows = await filesystemTreeSnapshot(root, "root");
  return rows.every((row) => row.path.split("/").slice(1).every((segment) => {
    const lower = segment.toLowerCase();
    return !lower.includes("vrcforge-stage-")
      && !lower.endsWith(".tmp")
      && !lower.endsWith(".stage");
  }));
}

async function directoryContainsOnly(root, expectedNames) {
  try {
    const entries = await readdir(root, { withFileTypes: true });
    const actual = entries.map((item) => item.name).sort();
    const expected = [...expectedNames].sort();
    return entries.every((item) => item.isFile()) && JSON.stringify(actual) === JSON.stringify(expected);
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function filesystemTreeSnapshot(root, label) {
  const rows = [];
  async function visit(current, relativePath) {
    let entries;
    try {
      entries = await readdir(current, { withFileTypes: true });
    } catch (error) {
      if (error?.code === "ENOENT" && !relativePath) {
        rows.push({ path: label, type: "missing" });
        return;
      }
      throw error;
    }
    rows.push({ path: relativePath ? `${label}/${relativePath}` : label, type: "directory" });
    entries.sort((left, right) => left.name.localeCompare(right.name));
    for (const entry of entries) {
      const childRelative = relativePath ? `${relativePath}/${entry.name}` : entry.name;
      const childPath = resolve(current, entry.name);
      if (entry.isDirectory()) {
        await visit(childPath, childRelative);
      } else if (entry.isFile()) {
        const info = await stat(childPath);
        rows.push({
          path: `${label}/${childRelative}`,
          type: "file",
          size: info.size,
          sha256: await sha256File(childPath),
        });
      } else {
        rows.push({
          path: `${label}/${childRelative}`,
          type: entry.isSymbolicLink() ? "symlink" : "other",
        });
      }
    }
  }
  await visit(root, "");
  return rows;
}

async function packageFilesystemState() {
  const [packageStore, projectedSkills] = await Promise.all([
    filesystemTreeSnapshot(resolve(userDataRoot, "skill-packages"), "skill-packages"),
    filesystemTreeSnapshot(resolve(userDataRoot, "skills"), "skills"),
  ]);
  return { packageStore, projectedSkills };
}

function cleanupFilesystemResiduals(baseline, current) {
  const dynamicPaths = new Map([
    ["skill-packages", "directory"],
    ["skill-packages/registry.json", "file"],
    ["skill-packages/.staging", "directory"],
    ["skill-packages/.uninstall-staging", "directory"],
    ["skills", "directory"],
    ["skills/.package-projection-staging", "directory"],
  ]);
  const baselineRows = [
    ...(baseline?.packageStore || []),
    ...(baseline?.projectedSkills || []),
  ];
  const currentRows = [
    ...(current?.packageStore || []),
    ...(current?.projectedSkills || []),
  ];
  const baselineByPath = new Map(baselineRows.map((row) => [row.path, row]));
  const currentByPath = new Map(currentRows.map((row) => [row.path, row]));
  const residuals = [];
  for (const row of currentRows) {
    const dynamicType = dynamicPaths.get(row.path);
    if (dynamicType) {
      if (row.type !== dynamicType) residuals.push(`${row.path}:unexpected-${row.type}`);
      continue;
    }
    const before = baselineByPath.get(row.path);
    if (!before || JSON.stringify(before) !== JSON.stringify(row)) {
      residuals.push(`${row.path}:new-or-changed`);
    }
  }
  for (const row of baselineRows) {
    if (row.type === "missing" || dynamicPaths.has(row.path)) continue;
    const after = currentByPath.get(row.path);
    if (!after || JSON.stringify(after) !== JSON.stringify(row)) {
      residuals.push(`${row.path}:missing-or-changed`);
    }
  }
  return [...new Set(residuals)].sort();
}

async function packageProjectionState(cdp) {
  const [restList, tauriList, skills, filesystem] = await Promise.all([
    appApi("/api/app/skill-packages"),
    tauriInvoke(cdp, "fetch_skill_packages", {}),
    agentApi("/api/agent/skills"),
    packageFilesystemState(),
  ]);
  const installedIds = (payload) => (payload?.installed || [])
    .map((item) => String(item.id || item.manifest?.id || ""))
    .filter(Boolean)
    .sort();
  return {
    restInstalledIds: installedIds(restList),
    tauriInstalledIds: installedIds(tauriList),
    restGovernance: restList?.governance || {},
    restAudit: restList?.audit || [],
    tauriGovernance: tauriList?.governance || {},
    tauriAudit: tauriList?.audit || [],
    projectedSkills: (skills?.skills || [])
      .map((item) => ({
        name: String(item.name || ""),
        source: String(item.source || ""),
        enabled: item.enabled !== false,
        available: item.available !== false,
      }))
      .sort((left, right) => left.name.localeCompare(right.name)),
    filesystem,
  };
}

function packageProjectionStateEquals(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function packagePreview(payload) {
  return payload?.preview || payload?.imported?.preview || payload?.installed?.preview || {};
}

function packageIdFromPreview(preview) {
  return String(preview?.manifest?.id || "");
}

function packageSkillName(preview) {
  return String(preview?.manifest?.skill_name || preview?.manifest?.skillName || "");
}

async function invokeRuntimeSkill(skillName, params = {}) {
  const payload = await agentApi("/api/agent/runtime/message", {
    method: "POST",
    body: {
      agent_name: "packaged-skill-ecosystem-probe",
      message: `Run the installed ${skillName} package fixture once.`,
      skill_tool: skillName,
      skill_params: {
        projectPath: projectRoot,
        projectRoot,
        avatarPath: "AvatarRoot",
        includeReadiness: false,
        includeQuest: false,
        ...params,
      },
    },
    timeoutMs: 180000,
  });
  return payload?.skill || {};
}

function packageAuditEventMatches(event, record, signerFingerprint) {
  const preview = record.preview;
  return event?.packageId === record.id
    && event?.packageVersion === String(preview?.manifest?.version || "")
    && event?.packageSha256 === String(getField(preview, "package_sha256", "packageSha256") || "")
    && event?.lockSha256 === String(getField(preview, "lock_sha256", "lockSha256") || "")
    && event?.signatureStatus === "signed"
    && event?.signerFingerprint === signerFingerprint
    && event?.signerTrustStatus === "trusted";
}

function verifyRuntimeSupportFiles(record, runtime) {
  const expectedPaths = requiredPackageSupportFiles.get(record.slug) || [];
  const supportFiles = Array.isArray(runtime?.result?.supportFiles) ? runtime.result.supportFiles : [];
  const actualPaths = supportFiles.map((item) => String(item?.path || ""));
  const exactShape = expectedPaths.length > 0
    && supportFiles.length === expectedPaths.length
    && supportFiles.every((item) => item && typeof item === "object" && typeof item.content === "string")
    && new Set(actualPaths).size === expectedPaths.length
    && [...actualPaths].sort().every((path, index) => path === expectedPaths[index]);
  const support = new Map(supportFiles.map((item) => [String(item?.path || ""), item?.content]));
  const sourceHashes = record.sourceFileSha256 && typeof record.sourceFileSha256 === "object"
    ? record.sourceFileSha256
    : {};
  const hashesMatch = exactShape && expectedPaths.every((path) => {
    const expectedHash = String(sourceHashes[path] || "").toLowerCase();
    const content = support.get(path);
    const actualHash = typeof content === "string"
      ? createHash("sha256").update(Buffer.from(content, "utf8")).digest("hex")
      : "";
    return /^[0-9a-f]{64}$/.test(expectedHash) && actualHash === expectedHash;
  });
  return { verified: hashesMatch, support };
}

async function runPackageLifecycle(report, cdp, signed) {
  const records = [];
  const beforePreflightState = await packageProjectionState(cdp);
  packageFilesystemBaseline = beforePreflightState.filesystem;
  for (let index = 0; index < signed.packages.length; index += 1) {
    const built = signed.packages[index];
    const preflight = await tauriInvoke(cdp, "preflight_skill_package", {
      request: { body: { packagePath: built.path }, timeoutMs: 120000 },
    });
    const preview = packagePreview(preflight);
    const id = packageIdFromPreview(preview);
    if (id !== built.id || !requiredPackageIds.includes(id)) {
      addAssertion(report, `preflight returned an unexpected package id for ${built.slug}`);
    }
    const signatureStatus = String(getField(preview, "signature_status", "signatureStatus") || "");
    const signerFingerprint = String(getField(preview, "signer_fingerprint", "signerFingerprint") || "").toLowerCase();
    const preflightSignatureVerified = signatureStatus === "signed"
      && signerFingerprint === signed.fingerprint
      && preview?.governance?.signatureVerified === true;
    const preflightSignerUntrusted = preview?.governance?.signerTrustStatus === "untrusted";
    const preflightUntrustedDefaultDisabled = preview?.governance?.importAllowed === true
      && preview?.governance?.safeMode?.defaultEnabled === false
      && preview?.dryRun?.wouldImport === true
      && preview?.dryRun?.wouldEnable === false;
    const preflightDryRunNoWrite = preview?.dryRun?.supported === true
      && preview?.dryRun?.willWrite === false;
    if (
      !preflightSignatureVerified
      || !preflightSignerUntrusted
      || !preflightUntrustedDefaultDisabled
      || !preflightDryRunNoWrite
    ) {
      addAssertion(report, `signed package preflight security/no-write contract failed for ${built.slug}`);
    }
    records.push({
      slug: built.slug,
      path: built.path,
      id,
      skillName: packageSkillName(preview),
      preview,
      signatureVerified: preflightSignatureVerified,
      preflightSignatureVerified,
      preflightSignerUntrusted,
      preflightUntrustedDefaultDisabled,
      preflightDryRunNoWrite,
      preflightStateUnchanged: false,
      trusted: false,
      imported: false,
      projected: false,
      runtimeStatus: "",
      runtimeResultVerified: false,
      entrypointVerified: false,
      runtimeAuditVerified: false,
      cleanupUninstalled: false,
      requestOnly: built.slug === "material-preset-pack" ? false : undefined,
      supportFilesVerified: false,
      directTargetCalls: built.slug === "material-preset-pack" ? -1 : undefined,
      sourceFileSha256: built.sourceFileSha256,
    });
  }
  const afterPreflightState = await packageProjectionState(cdp);
  const preflightStateUnchanged = packageProjectionStateEquals(beforePreflightState, afterPreflightState);
  for (const record of records) record.preflightStateUnchanged = preflightStateUnchanged;
  if (!preflightStateUnchanged) {
    addAssertion(report, "package preflight mutated REST/Tauri state, the projected registry, or package filesystem trees");
  }

  const trust = await appApi("/api/app/skill-packages/trust-signer", {
    method: "POST",
    body: { signerFingerprint: signed.fingerprint, reason: "packaged Skill Ecosystem acceptance fixture" },
  });
  if (trust?.ok !== true) addAssertion(report, "signed example signer trust action did not return ok=true");

  for (let index = 0; index < records.length; index += 1) {
    const record = records[index];
    const imported = index % 2 === 0
      ? await tauriInvoke(cdp, "import_skill_package", {
        request: {
          body: { packagePath: record.path, projectToUserSkills: true },
          timeoutMs: 120000,
        },
      })
      : await appApi("/api/app/skill-packages/import", {
        method: "POST",
        body: { packagePath: record.path, projectToUserSkills: true },
      });
    const importedPreview = packagePreview(imported);
    record.imported = imported?.ok === true && Boolean(imported?.imported);
    record.projected = Boolean(imported?.projectedSkill?.name)
      && String(imported.projectedSkill.name) === record.skillName;
    record.trusted = importedPreview?.governance?.signerTrustStatus === "trusted";
    if (!record.imported || !record.projected || !record.trusted) {
      addAssertion(report, `signed package import/projection/trust did not pass for ${record.id}`);
    }
  }

  const [restList, tauriList, skills] = await Promise.all([
    appApi("/api/app/skill-packages"),
    tauriInvoke(cdp, "fetch_skill_packages", {}),
    agentApi("/api/agent/skills"),
  ]);
  const restIds = new Set((restList?.installed || []).map((item) => String(item.id || item.manifest?.id || "")));
  const tauriIds = new Set((tauriList?.installed || []).map((item) => String(item.id || item.manifest?.id || "")));
  const projectedNames = new Set((skills?.skills || []).map((item) => String(item.name || "")));
  for (const record of records) {
    if (!restIds.has(record.id) || !tauriIds.has(record.id) || !projectedNames.has(record.skillName)) {
      record.projected = false;
      addAssertion(report, `REST/Tauri/skill registry projections disagreed for ${record.id}`);
    }
  }

  for (const record of records) {
    const runtime = await invokeRuntimeSkill(record.skillName);
    record.runtimeStatus = String(runtime?.status || "");
    const supportVerification = verifyRuntimeSupportFiles(record, runtime);
    record.supportFilesVerified = supportVerification.verified;
    if (record.slug === "material-preset-pack") {
      const support = supportVerification.support;
      let workflow = {};
      let presets = {};
      try {
        workflow = JSON.parse(String(support.get("workflows/material-preset-pack.json") || "{}"));
      } catch {
        workflow = {};
      }
      try {
        presets = JSON.parse(String(support.get("presets/material-presets.json") || "{}"));
      } catch {
        presets = {};
      }
      const steps = Array.isArray(workflow?.steps) ? workflow.steps : [];
      const step = steps[0] || {};
      const presetItems = Array.isArray(presets?.presets) ? presets.presets : [];
      const presetIds = presetItems.map((item) => String(item?.id || "")).sort();
      const presetsVerified = presets?.schema === "vrcforge.material-preset-pack.v1"
        && presetItems.length === 2
        && new Set(presetIds).size === 2
        && presetIds[0] === "balanced-toon"
        && presetIds[1] === "quest-conservative"
        && presetItems.every((item) => item?.values && typeof item.values === "object");
      record.supportFilesVerified = record.supportFilesVerified && presetsVerified;
      record.requestOnly = runtime?.status === "loaded"
        && runtime?.result?.permissionMode === "approval_required"
        && !runtime?.result?.entrypointTool
        && record.supportFilesVerified
        && workflow?.schema === "vrcforge.skill-package.workflow.v1"
        && workflow?.mode === "approval_required"
        && steps.length === 1
        && step?.name === "request_material_preset_apply"
        && step?.tool === "vrcforge_request_apply"
        && step?.writes === true
        && step?.request?.targetTool === "vrcforge_apply_shader_tuning"
        && step?.request?.presetSource === "presets/material-presets.json"
        && step?.request?.onePresetPerExecution === true
        && workflow?.approval?.required === true
        && workflow?.checkpoint?.required === true
        && workflow?.rollback?.required === true
        && workflow?.rollback?.tool === "vrcforge_restore_shader_tuning";
      record.runtimeResultVerified = record.requestOnly;
      record.entrypointVerified = record.requestOnly;
    } else {
      const expectedEntrypoint = requiredPackageEntrypoints.get(record.slug);
      const result = runtime?.entrypoint?.result;
      if (record.slug === "validation-report-extension") {
        record.runtimeResultVerified = result?.ok === true
          && result?.schema === "vrcforge.validation.v1"
          && result?.readOnly === true
          && result?.autoFix === false
          && Number.isInteger(result?.summary?.failedSourceCount)
          && result.summary.failedSourceCount === 0
          && Array.isArray(result?.findings)
          && result?.sources
          && typeof result.sources === "object";
      } else if (record.slug === "outfit-naming-helper") {
        record.runtimeResultVerified = result?.ok === true
          && result?.fixture === "packaged-transport-only"
          && result?.tool === "vrc_scan_animation_bindings"
          && Array.isArray(result?.bindings)
          && Array.isArray(result?.clips)
          && Array.isArray(result?.errors)
          && Array.isArray(result?.warnings);
      } else if (record.slug === "optimizer-report-helper") {
        record.runtimeResultVerified = result?.ok === true
          && result?.schema === "vrcforge.optimization.v1"
          && result?.readOnly === true
          && result?.planOnly === true
          && result?.noProjectWrites === true
          && result?.directApplyExposed === false
          && result?.targetProfile
          && typeof result.targetProfile === "object"
          && result?.dependencyDoctor
          && typeof result.dependencyDoctor === "object"
          && Array.isArray(result?.actionCards)
          && Array.isArray(result?.recommendedOrder);
      }
      record.entrypointVerified = runtime?.status === "executed"
        && runtime?.entrypointTool === expectedEntrypoint
        && runtime?.entrypoint?.tool === expectedEntrypoint
        && runtime?.entrypoint?.status === "executed"
        && result
        && typeof result === "object"
        && record.runtimeResultVerified;
      if (!record.entrypointVerified) {
        addAssertion(report, `runtime entrypoint/success schema did not match ${expectedEntrypoint} for ${record.id}`);
      }
    }
  }

  const logs = await agentApi("/api/agent/logs?limit=500");
  const events = Array.isArray(logs?.logs) ? logs.logs : [];
  const directTargetCalls = events.filter((event) =>
    event?.tool === "vrcforge_apply_shader_tuning"
    || event?.targetTool === "vrcforge_apply_shader_tuning"
    || event?.target_tool === "vrcforge_apply_shader_tuning").length;
  for (const record of records) {
    const packageEvents = events.filter((event) =>
      ["runtime_skill_package_loaded", "runtime_skill_entrypoint_executed"].includes(String(event?.event || ""))
      && event?.packageId === record.id);
    const loadedVerified = packageEvents.some((event) =>
      event?.event === "runtime_skill_package_loaded"
      && packageAuditEventMatches(event, record, signed.fingerprint));
    const entrypointVerified = record.slug === "material-preset-pack"
      || packageEvents.some((event) =>
        event?.event === "runtime_skill_entrypoint_executed"
        && event?.tool === requiredPackageEntrypoints.get(record.slug)
        && packageAuditEventMatches(event, record, signed.fingerprint));
    record.runtimeAuditVerified = loadedVerified && entrypointVerified;
    if (!record.runtimeAuditVerified) addAssertion(report, `exact package runtime audit attribution failed for ${record.id}`);
    if (record.slug === "material-preset-pack") {
      record.directTargetCalls = directTargetCalls;
      if (!record.requestOnly || directTargetCalls !== 0) {
        addAssertion(report, "material preset package was not request-only or called its direct target");
      }
    }
  }
  return records;
}

function sha256Json(value) {
  return createHash("sha256").update(Buffer.from(JSON.stringify(value), "utf8")).digest("hex");
}

async function vskTemporaryDirectories() {
  const tempRoot = String(process.env.TEMP || "");
  if (!tempRoot) return [];
  try {
    const entries = await readdir(tempRoot, { withFileTypes: true });
    return entries
      .filter((item) => item.isDirectory() && item.name.startsWith("vrcforge-vsk-"))
      .map((item) => item.name)
      .sort();
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
}

function installedPackageIds(payload) {
  return (payload?.installed || [])
    .map((item) => String(item?.id || item?.manifest?.id || ""))
    .filter(Boolean)
    .sort();
}

async function apkSemanticInstalledState(cdp) {
  const [rest, tauri, skills, packageTree, projectedTree] = await Promise.all([
    appApi("/api/app/skill-packages"),
    tauriInvoke(cdp, "fetch_skill_packages", {}),
    agentApi("/api/agent/skills"),
    filesystemTreeSnapshot(
      resolve(userDataRoot, "skill-packages", apkSemanticPackageId),
      "apk-semantic-package",
    ),
    filesystemTreeSnapshot(
      resolve(userDataRoot, "skills", apkSemanticSkillName),
      "apk-semantic-projection",
    ),
  ]);
  const restIds = installedPackageIds(rest);
  const tauriIds = installedPackageIds(tauri);
  const projected = (skills?.skills || []).some((item) => String(item?.name || "") === apkSemanticSkillName);
  const registryEntry = rest?.registry?.skills?.[apkSemanticPackageId] || null;
  let installedDocument = null;
  try {
    installedDocument = JSON.parse(await readFile(
      resolve(userDataRoot, "skill-packages", apkSemanticPackageId, "installed.json"),
      "utf8",
    ));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  const state = {
    restInstalled: restIds.includes(apkSemanticPackageId),
    tauriInstalled: tauriIds.includes(apkSemanticPackageId),
    projected,
    registryEntry,
    installedDocument,
    packageTree,
    projectedTree,
  };
  return { ...state, digest: sha256Json(state) };
}

async function readApkSemanticProvenance(cdp, expectedPackage, expectedVersions) {
  const state = await apkSemanticInstalledState(cdp);
  const registry = state.registryEntry && typeof state.registryEntry === "object"
    ? state.registryEntry
    : {};
  const installed = state.installedDocument && typeof state.installedDocument === "object"
    ? state.installedDocument
    : {};
  const version = String(registry.version || "");
  const versionRoot = resolve(
    userDataRoot,
    "skill-packages",
    apkSemanticPackageId,
    "versions",
    version,
  );
  let manifest = {};
  let lockBytes = Buffer.alloc(0);
  let publicKeyBase64 = "";
  try {
    [manifest, lockBytes, publicKeyBase64] = await Promise.all([
      readFile(resolve(versionRoot, "manifest.json"), "utf8").then((text) => JSON.parse(text)),
      readFile(resolve(versionRoot, "skill.lock.json")),
      readFile(resolve(versionRoot, "author.pub"), "utf8").then((text) => text.trim()),
    ]);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  const publicKeyBytes = Buffer.from(publicKeyBase64, "base64");
  const publicKeyFingerprint = publicKeyBytes.length === 32
    ? createHash("sha256").update(publicKeyBytes).digest("hex")
    : "";
  const versions = Array.isArray(installed.versions)
    ? installed.versions.map(String).sort()
    : [];
  const expectedVersion = String(expectedPackage?.manifest?.version || "");
  const expectedArchiveSha256 = String(expectedPackage?.archiveSha256 || "");
  const expectedLockSha256 = String(expectedPackage?.lockSha256 || "");
  const provenance = {
    verified: false,
    packageId: apkSemanticPackageId,
    skillName: apkSemanticSkillName,
    authorId: String(manifest.author || ""),
    authorIdSource: "manifest.author",
    version,
    versions,
    versionMonotonicityField: "VSK SemVer (semantic equivalent of Android versionCode for this 1.3.0 gate)",
    signatureStatus: String(registry.signature_status || ""),
    publicKeyBase64,
    signerFingerprintSha256: String(registry.signer_fingerprint || ""),
    archiveSha256: String(registry.package_sha256 || ""),
    payloadLockSha256: String(registry.lock_sha256 || ""),
    publicKeyPersisted: publicKeyBase64 === String(expectedPackage?.publicKeyBase64 || "")
      && publicKeyFingerprint === String(expectedPackage?.signerFingerprint || ""),
    packageIdPersisted: manifest.id === apkSemanticPackageId
      && registry.id === apkSemanticPackageId
      && installed.id === apkSemanticPackageId,
    authorIdPersisted: manifest.author === apkSemanticAuthorId
      && registry.author === apkSemanticAuthorId
      && installed.author === apkSemanticAuthorId,
    versionPersisted: version === expectedVersion
      && manifest.version === expectedVersion
      && installed.version === expectedVersion,
    versionsPersisted: JSON.stringify(versions) === JSON.stringify([...expectedVersions].sort()),
    signatureStatusPersisted: registry.signature_status === "signed"
      && installed.signature_status === "signed",
    fingerprintPersisted: registry.signer_fingerprint === expectedPackage?.signerFingerprint
      && installed.signer_fingerprint === expectedPackage?.signerFingerprint,
    archiveDigestPersisted: registry.package_sha256 === expectedArchiveSha256
      && installed.package_sha256 === expectedArchiveSha256,
    payloadDigestPersisted: registry.lock_sha256 === expectedLockSha256
      && installed.lock_sha256 === expectedLockSha256
      && createHash("sha256").update(lockBytes).digest("hex") === expectedLockSha256,
    restAndTauriPresent: state.restInstalled && state.tauriInstalled,
    projected: state.projected,
    stateDigest: state.digest,
  };
  provenance.verified = [
    "publicKeyPersisted",
    "packageIdPersisted",
    "authorIdPersisted",
    "versionPersisted",
    "versionsPersisted",
    "signatureStatusPersisted",
    "fingerprintPersisted",
    "archiveDigestPersisted",
    "payloadDigestPersisted",
    "restAndTauriPresent",
    "projected",
  ].every((key) => provenance[key] === true);
  return provenance;
}

function responseDetail(response) {
  return String(response?.payload?.detail || response?.payload?.error || "");
}

function apkSemanticNegativeEvidenceComplete(item) {
  return item?.rejected === true
    && item?.httpStatus === 400
    && item?.errorCategoryVerified === true
    && item?.installedStatePreserved === true
    && item?.temporaryExtractionClear === true
    && item?.stagingClear === true
    && item?.zipSlipOutsideAbsent === true;
}

async function rejectApkSemanticPackage(report, cdp, key, fixture) {
  const before = await apkSemanticInstalledState(cdp);
  const tempBefore = await vskTemporaryDirectories();
  const response = await appApiRaw("/api/app/skill-packages/import", {
    method: "POST",
    body: { packagePath: fixture.path, projectToUserSkills: true },
    timeoutMs: 120000,
  });
  const after = await apkSemanticInstalledState(cdp);
  const tempAfter = await vskTemporaryDirectories();
  const errorCategoryVerified = responseDetail(response)
    .toLowerCase()
    .includes(String(fixture.expectedErrorToken || "").toLowerCase());
  const result = {
    rejected: response.ok === false && response.status === 400,
    httpStatus: Number(response.status || 0),
    errorCategoryVerified,
    installedStatePreserved: before.digest === after.digest,
    temporaryExtractionClear: JSON.stringify(tempAfter) === JSON.stringify(tempBefore),
    stagingClear: await stagingDirectoriesClear(),
    zipSlipOutsideAbsent: key !== "zipSlip" || !(await pathExists(zipSlipOutsidePath)),
  };
  if (
    !result.rejected
    || !result.errorCategoryVerified
    || !result.installedStatePreserved
    || !result.temporaryExtractionClear
    || !result.stagingClear
    || !result.zipSlipOutsideAbsent
  ) {
    addAssertion(report, `APK-semantic negative package gate failed: ${key}`);
  }
  return result;
}

async function createPackagedExportBase(cdp, signed) {
  const fixture = signed.apkSemantic;
  const userSkillRoot = resolve(userDataRoot, "skills", apkSemanticSkillName);
  const controlledKeyBoundary = normalizedPath(ephemeralSigningKeyPath)
    .startsWith(`${normalizedPath(evidenceRoot)}/`);
  if (!controlledKeyBoundary || !signed.privateKeyReady || !(await pathExists(ephemeralSigningKeyPath))) {
    throw new Error("Controlled ephemeral signing key was not ready inside the isolated evidence boundary.");
  }
  if (await pathExists(userSkillRoot)) {
    throw new Error("APK-semantic packaged-export user Skill target already existed.");
  }
  if (await pathExists(fixture.base.path)) {
    throw new Error("APK-semantic packaged-export .vsk target already existed.");
  }

  const summary = {
    schema: "vrcforge.operation_summary.v1",
    source: { kind: "runtime_run" },
    workflow: "captured_runtime_operation",
    status: "completed",
    steps: [
      { kind: "validation", tool: "vrcforge_build_test_readiness", status: "executed" },
    ],
    evidence: { approvalRecorded: false, checkpointRecorded: false },
    validation: { requiresApproval: false, requiresCheckpoint: false, requiresRollback: false },
    projectPath: "{{projectPath}}",
  };
  let written;
  let exported;
  let sourceSeen = false;
  let deleted;
  let operationError;
  let sourceCleanupError;
  try {
    written = await tauriInvoke(cdp, "write_path_to_skill", {
      request: {
        body: {
          summary,
          packageId: apkSemanticPackageId,
          skillName: apkSemanticSkillName,
          outputPath: userSkillRoot,
          writeSource: true,
          useTempOutput: false,
          exportVsk: false,
        },
        timeoutMs: 120000,
      },
    });
    const skills = await agentApi("/api/agent/skills");
    sourceSeen = (skills?.skills || []).some((item) =>
      String(item?.name || "") === apkSemanticSkillName
      && String(item?.source || "") === "user");
    if (
      written?.ok !== true
      || written?.dryRun !== false
      || written?.manifest?.skill_name !== apkSemanticSkillName
      || !sourceSeen
    ) {
      throw new Error("Packaged Tauri Path-to-Skill did not create the exportable user Skill.");
    }
    exported = await tauriInvoke(cdp, "export_skill_package", {
      request: {
        body: {
          skillName: apkSemanticSkillName,
          outputPath: fixture.base.path,
          release: true,
          privateKeyPath: ephemeralSigningKeyPath,
        },
        timeoutMs: 120000,
      },
    });
  } catch (error) {
    operationError = error;
  } finally {
    await unlink(ephemeralSigningKeyPath).catch(() => {});
    signed.privateKeyDeleted = !(await pathExists(ephemeralSigningKeyPath).catch(() => true));
    signed.privateKeyReady = false;
    if (written?.ok === true) {
      try {
        deleted = await tauriInvoke(cdp, "delete_skill", {
          request: { id: apkSemanticSkillName, body: {}, timeoutMs: 60000 },
        });
      } catch (error) {
        sourceCleanupError = error;
      }
    }
  }
  if (operationError || sourceCleanupError || !signed.privateKeyDeleted) {
    await rm(userSkillRoot, { recursive: true, force: true }).catch(() => {});
    await unlink(fixture.base.path).catch(() => {});
    throw new AggregateError(
      [operationError, sourceCleanupError].filter(Boolean),
      "Packaged signed Skill export or immediate sensitive cleanup failed.",
    );
  }

  let inspected;
  try {
    inspected = await inspectSignedVskArchive(fixture.base.path);
  } catch (error) {
    await unlink(fixture.base.path).catch(() => {});
    throw error;
  }
  Object.assign(fixture.base, inspected);
  const response = exported?.exported || {};
  const responseExcludedSensitiveInput = !JSON.stringify(exported || {}).includes(ephemeralSigningKeyPath)
    && !JSON.stringify(exported || {}).includes("-----BEGIN PRIVATE KEY-----");
  const evidence = {
    userSkillCreatedViaTauri: written?.ok === true && sourceSeen,
    userSkillDeletedViaTauri: deleted?.ok === true
      && String(deleted?.deleted || "") === apkSemanticSkillName
      && !(await pathExists(userSkillRoot)),
    exportedViaTauri: exported?.ok === true && Boolean(exported?.exported),
    releaseExportRequested: true,
    responseSignatureStatusSigned: response?.signature_status === "signed",
    responseSignerFingerprintMatches: response?.signer_fingerprint === fixture.signerFingerprint,
    responseLockDigestMatches: response?.lock_sha256 === inspected.lockSha256,
    responseManifestIdentityMatches: response?.manifest?.id === apkSemanticPackageId
      && response?.manifest?.author === apkSemanticAuthorId
      && response?.manifest?.version === "1.0.0",
    outputCreated: await pathExists(fixture.base.path),
    archiveIndependentlyVerified: inspected?.ok === true,
    archivePublicKeyMatchesGenerated: inspected?.publicKeyBase64 === fixture.publicKeyBase64
      && inspected?.signerFingerprint === fixture.signerFingerprint,
    controlledTemporaryKeyUsed: fixture.privateKeyFileBacked === true && controlledKeyBoundary,
    privateKeyDeletedImmediatelyAfterExport: signed.privateKeyDeleted === true,
    privateKeyInputExcludedFromResponse: responseExcludedSensitiveInput,
  };
  if (Object.values(evidence).some((value) => value !== true)) {
    await unlink(fixture.base.path).catch(() => {});
    throw new Error("Packaged Tauri signed Skill export evidence was incomplete.");
  }
  return evidence;
}

function apkSemanticFixtureItems(signed) {
  const fixture = signed?.apkSemantic;
  if (!fixture) return [];
  return [
    fixture.base,
    fixture.update,
    fixture.differentId,
    ...Object.values(fixture.negatives || {}),
  ].filter((item) => item?.path);
}

async function cleanupApkSemanticFixtureArchives(signed) {
  const items = apkSemanticFixtureItems(signed);
  await Promise.all(items.map((item) => unlink(item.path).catch(() => {})));
  return (await Promise.all(items.map((item) => pathExists(item.path)))).every((exists) => !exists);
}

async function runApkSemanticLifecycle(report, cdp, signed, packagedExport) {
  const fixture = signed.apkSemantic;
  const matrix = {
    semanticModel: "Android APK-style immutable signed update semantics mapped to VRCForge .vsk",
    packageExtension: ".vsk",
    packageId: apkSemanticPackageId,
    skillName: apkSemanticSkillName,
    packagedExport,
    fixtureBuilder: {
      role: "same-signer update plus adversarial negative fixtures only",
      generatedUpdate: await pathExists(fixture.update.path),
      generatedNegativeCount: Object.keys(fixture.negatives).length,
      didNotSupplyInstalledBase: true,
      signerFingerprintMatchesPackagedExport: fixture.update?.signerFingerprint === fixture.signerFingerprint,
    },
    authorIdentity: {
      authorId: apkSemanticAuthorId,
      authorIdSource: "manifest.author",
      cryptographicIdentity: "author.pub Ed25519 public key plus SHA-256 fingerprint",
      publicKeyBase64: fixture.publicKeyBase64,
      signerFingerprintSha256: fixture.signerFingerprint,
      publicKeyMatchesFingerprint: createHash("sha256")
        .update(Buffer.from(fixture.publicKeyBase64, "base64"))
        .digest("hex") === fixture.signerFingerprint,
      signerContinuityModel: "one pinned Ed25519 signer; no certificate-rotation lineage claimed",
    },
    archive: {
      signedPayloadIdentityImmutable: true,
      signatureAlgorithm: "Ed25519",
      signatureCoverage: "canonical skill.lock.json, not raw ZIP container bytes",
      zipContainerMetadataCoverage: "strictly safety-validated but not covered by the Ed25519 signature",
      apkV2WholeArchiveEquivalence: "not claimed; APK v2+ also invalidates ZIP metadata changes",
      signatureCoversCanonicalLock: fixture.base?.checks?.signatureVerified === true
        && fixture.update?.checks?.signatureVerified === true,
      lockCoversCanonicalManifest: fixture.base?.checks?.lockIncludesCanonicalManifest === true
        && fixture.update?.checks?.lockIncludesCanonicalManifest === true,
      lockCoversAllPayloadDigests: fixture.base?.checks?.lockExactPayload === true
        && fixture.base?.checks?.payloadDigestsVerified === true
        && fixture.update?.checks?.lockExactPayload === true
        && fixture.update?.checks?.payloadDigestsVerified === true,
      canonicalManifestVerified: fixture.base?.checks?.manifestCanonical === true
        && fixture.update?.checks?.manifestCanonical === true,
      canonicalLockVerified: fixture.base?.checks?.lockCanonical === true
        && fixture.update?.checks?.lockCanonical === true,
      privateKeyMaterialAbsent: fixture.base?.checks?.privateKeyMaterialAbsent === true
        && fixture.update?.checks?.privateKeyMaterialAbsent === true,
      baseArchiveSha256: String(fixture.base?.archiveSha256 || ""),
      basePayloadSetSha256: String(fixture.base?.payloadSetSha256 || ""),
      updateArchiveSha256: String(fixture.update?.archiveSha256 || ""),
      updatePayloadSetSha256: String(fixture.update?.payloadSetSha256 || ""),
    },
    preInstallVerification: {},
    installProvenance: {},
    versionMonotonicity: {
      androidVersionCodeEquivalent: "VSK SemVer monotonic comparison (no versionCode field exists)",
      baseVersion: "1.0.0",
      updateVersion: "1.1.0",
      semverMonotonic: true,
    },
    signedUpdate: {},
    packageIdContinuity: {},
    negativeRejections: {},
    failedUpdateAtomicity: {},
    uninstall: {},
    privateKeyBoundary: {
      generatedEphemerally: true,
      storageMode: "controlled temporary PKCS8 PEM path used only by packaged Tauri release export",
      controlledTemporaryKeyUsed: packagedExport.controlledTemporaryKeyUsed === true,
      privateKeyDeletedImmediatelyAfterExport: packagedExport.privateKeyDeletedImmediatelyAfterExport === true,
      privateKeyPersistedAfterExport: !signed.privateKeyDeleted,
      privateKeyMaterialReturnedToProbe: fixture.privateKeyMaterialReturned === true,
      controlledKeyPathAbsent: !(await pathExists(ephemeralSigningKeyPath)),
      vskArchivesExcludePrivateKeyMaterial: fixture.base?.checks?.privateKeyMaterialAbsent === true
        && fixture.update?.checks?.privateKeyMaterialAbsent === true,
      fixtureArchivesDeleted: false,
      persistentRuntimeScanClear: false,
      supportBundleScanClear: false,
      reportScanClear: false,
    },
    boundaries: {
      apkCertificateRotationLineage: "not implemented and not claimed for 1.3.0",
      updateIdentityRule: "same manifest.id selects update; a different valid id is a distinct package, not an update",
      authorIdentityRule: "manifest.author is the authorId; author.pub fingerprint is the pinned cryptographic identity",
    },
  };
  report.apkSemanticMatrix = matrix;
  try {
    if (await pathExists(zipSlipOutsidePath)) {
      throw new Error("APK-semantic zip-slip outside sentinel already existed before validation.");
    }
    const beforePreflight = await apkSemanticInstalledState(cdp);
    const tempBefore = await vskTemporaryDirectories();
    const basePreflight = await appApi("/api/app/skill-packages/preflight", {
      method: "POST",
      body: { packagePath: fixture.base.path },
      timeoutMs: 120000,
    });
    const basePreview = packagePreview(basePreflight);
    const afterPreflight = await apkSemanticInstalledState(cdp);
    const tempAfter = await vskTemporaryDirectories();
    matrix.preInstallVerification = {
      accepted: basePreflight?.ok === true,
      packageIdVerified: packageIdFromPreview(basePreview) === apkSemanticPackageId,
      authorIdVerified: basePreview?.manifest?.author === apkSemanticAuthorId,
      semverVerified: basePreview?.manifest?.version === "1.0.0",
      signatureVerified: basePreview?.signature_status === "signed"
        && basePreview?.signer_fingerprint === fixture.signerFingerprint
        && basePreview?.governance?.signatureVerified === true,
      archiveDigestVerified: basePreview?.package_sha256 === fixture.base.archiveSha256,
      payloadDigestVerified: basePreview?.lock_sha256 === fixture.base.lockSha256,
      updateActionNew: basePreview?.update_action === "new",
      dryRunNoWrite: basePreview?.dryRun?.willWrite === false,
      stateUnchanged: beforePreflight.digest === afterPreflight.digest,
      temporaryExtractionClear: JSON.stringify(tempAfter) === JSON.stringify(tempBefore),
      pathSafetyVerified: false,
      duplicatePathRejected: false,
      invalidPackageIdRejected: false,
    };

    const baseImport = await tauriInvoke(cdp, "import_skill_package", {
      request: {
        body: { packagePath: fixture.base.path, projectToUserSkills: true },
        timeoutMs: 120000,
      },
    });
    const baseImportPreview = packagePreview(baseImport);
    const baseProvenance = await readApkSemanticProvenance(cdp, fixture.base, ["1.0.0"]);
    matrix.installProvenance = {
      installed: baseImport?.ok === true && Boolean(baseImport?.imported),
      projected: String(baseImport?.projectedSkill?.name || "") === apkSemanticSkillName,
      installActionNew: baseImportPreview?.update_action === "new",
      ...baseProvenance,
    };
    if (!matrix.installProvenance.installed || !matrix.installProvenance.verified) {
      addAssertion(report, "APK-semantic base VSK install/provenance gate failed");
    }

    for (const [key, negative] of Object.entries(fixture.negatives)) {
      matrix.negativeRejections[key] = await rejectApkSemanticPackage(report, cdp, key, negative);
    }
    matrix.preInstallVerification.pathSafetyVerified = matrix.negativeRejections.zipSlip?.rejected === true
      && matrix.negativeRejections.zipSlip?.zipSlipOutsideAbsent === true;
    matrix.preInstallVerification.duplicatePathRejected = matrix.negativeRejections.duplicatePath?.rejected === true;
    matrix.preInstallVerification.invalidPackageIdRejected = matrix.negativeRejections.invalidPackageId?.rejected === true;

    const beforeDistinctId = await apkSemanticInstalledState(cdp);
    const distinctPreflight = await appApi("/api/app/skill-packages/preflight", {
      method: "POST",
      body: { packagePath: fixture.differentId.path },
      timeoutMs: 120000,
    });
    const distinctPreview = packagePreview(distinctPreflight);
    const afterDistinctId = await apkSemanticInstalledState(cdp);
    matrix.packageIdContinuity = {
      updateLookupUsesExactManifestId: true,
      differentValidId: String(distinctPreview?.manifest?.id || ""),
      differentValidIdTreatedAsDistinctPackage: distinctPreview?.update_action === "new",
      differentValidIdNotClaimedAsUpdate: distinctPreview?.update_action !== "update",
      differentValidIdNotImported: true,
      installedPackagePreserved: beforeDistinctId.digest === afterDistinctId.digest,
    };

    const negativeResults = Object.values(matrix.negativeRejections);
    matrix.failedUpdateAtomicity = {
      rejectionCount: negativeResults.length,
      allRejected: negativeResults.every((item) => item.rejected === true),
      allErrorCategoriesVerified: negativeResults.every((item) => item.errorCategoryVerified === true),
      oldInstallPreservedAfterEveryFailure: negativeResults.every((item) => item.installedStatePreserved === true),
      temporaryExtractionClearAfterEveryFailure: negativeResults.every((item) => item.temporaryExtractionClear === true),
      stagingClearAfterEveryFailure: negativeResults.every((item) => item.stagingClear === true),
      zipSlipNeverEscaped: matrix.negativeRejections.zipSlip?.zipSlipOutsideAbsent === true,
    };

    const beforeUpdatePreflight = await apkSemanticInstalledState(cdp);
    const updatePreflight = await appApi("/api/app/skill-packages/preflight", {
      method: "POST",
      body: { packagePath: fixture.update.path },
      timeoutMs: 120000,
    });
    const updatePreview = packagePreview(updatePreflight);
    const afterUpdatePreflight = await apkSemanticInstalledState(cdp);
    const updateImport = await appApi("/api/app/skill-packages/import", {
      method: "POST",
      body: { packagePath: fixture.update.path, projectToUserSkills: true },
      timeoutMs: 120000,
    });
    const updateImportPreview = packagePreview(updateImport);
    const updateProvenance = await readApkSemanticProvenance(
      cdp,
      fixture.update,
      ["1.0.0", "1.1.0"],
    );
    matrix.versionMonotonicity.updateAction = String(updatePreview?.update_action || "");
    matrix.versionMonotonicity.preflightRecognizedMonotonicUpdate = updatePreview?.update_action === "update";
    matrix.signedUpdate = {
      samePackageId: updatePreview?.manifest?.id === apkSemanticPackageId,
      sameAuthorId: updatePreview?.manifest?.author === apkSemanticAuthorId,
      samePublicKeyFingerprint: updatePreview?.signer_fingerprint === fixture.signerFingerprint,
      signatureVerified: updatePreview?.signature_status === "signed"
        && updatePreview?.governance?.signatureVerified === true,
      preflightNoWrite: beforeUpdatePreflight.digest === afterUpdatePreflight.digest,
      updateAction: String(updatePreview?.update_action || ""),
      imported: updateImport?.ok === true && Boolean(updateImport?.imported),
      importActionUpdate: updateImportPreview?.update_action === "update",
      priorVersionRetainedUntilUninstall: updateProvenance.versions.includes("1.0.0"),
      ...updateProvenance,
    };
    if (!matrix.signedUpdate.imported || !matrix.signedUpdate.verified) {
      addAssertion(report, "APK-semantic same-author/same-key signed update/provenance gate failed");
    }

    const uninstall = await appApi(
      `/api/app/skill-packages/${encodeURIComponent(apkSemanticPackageId)}`,
      {
        method: "DELETE",
        body: { removeProjectedSkill: true },
        timeoutMs: 120000,
      },
    );
    const removedVersions = (uninstall?.uninstalled?.removed_versions || [])
      .map(String)
      .sort();
    const afterUninstall = await apkSemanticInstalledState(cdp);
    matrix.uninstall = {
      accepted: uninstall?.ok === true && Boolean(uninstall?.uninstalled),
      removedVersions,
      bothVersionsRemoved: JSON.stringify(removedVersions) === JSON.stringify(["1.0.0", "1.1.0"]),
      restClear: afterUninstall.restInstalled === false,
      tauriClear: afterUninstall.tauriInstalled === false,
      registryClear: afterUninstall.registryEntry === null,
      installedMetadataClear: afterUninstall.installedDocument === null,
      projectionClear: afterUninstall.projected === false,
      packageFilesystemClear: afterUninstall.packageTree.length === 1
        && afterUninstall.packageTree[0]?.type === "missing",
      projectedFilesystemClear: afterUninstall.projectedTree.length === 1
        && afterUninstall.projectedTree[0]?.type === "missing",
      stagingClear: await stagingDirectoriesClear(),
    };
  } finally {
    matrix.privateKeyBoundary.fixtureArchivesDeleted = await cleanupApkSemanticFixtureArchives(signed);
    report.cleanup.apkSemanticFixtureArchivesClear = matrix.privateKeyBoundary.fixtureArchivesDeleted;
  }
  for (const [groupName, group] of Object.entries({
    packagedExport: Object.fromEntries(
      Object.entries(matrix.packagedExport).filter(([, value]) => typeof value === "boolean"),
    ),
    fixtureBuilder: Object.fromEntries(
      Object.entries(matrix.fixtureBuilder).filter(([, value]) => typeof value === "boolean"),
    ),
    authorIdentity: Object.fromEntries(
      Object.entries(matrix.authorIdentity).filter(([, value]) => typeof value === "boolean"),
    ),
    archive: Object.fromEntries(Object.entries(matrix.archive).filter(([, value]) => typeof value === "boolean")),
    preInstallVerification: matrix.preInstallVerification,
    installProvenance: Object.fromEntries(
      Object.entries(matrix.installProvenance).filter(([, value]) => typeof value === "boolean"),
    ),
    versionMonotonicity: Object.fromEntries(
      Object.entries(matrix.versionMonotonicity).filter(([, value]) => typeof value === "boolean"),
    ),
    signedUpdate: Object.fromEntries(
      Object.entries(matrix.signedUpdate).filter(([, value]) => typeof value === "boolean"),
    ),
    packageIdContinuity: Object.fromEntries(
      Object.entries(matrix.packageIdContinuity).filter(([, value]) => typeof value === "boolean"),
    ),
    failedUpdateAtomicity: Object.fromEntries(
      Object.entries(matrix.failedUpdateAtomicity).filter(([, value]) => typeof value === "boolean"),
    ),
    uninstall: Object.fromEntries(Object.entries(matrix.uninstall).filter(([, value]) => typeof value === "boolean")),
  })) {
    for (const [key, value] of Object.entries(group)) {
      if (value !== true) addAssertion(report, `apkSemanticMatrix ${groupName} gate failed: ${key}`);
    }
  }
  if (matrix.privateKeyBoundary.privateKeyPersistedAfterExport !== false) {
    addAssertion(report, "APK-semantic private signing key persisted after packaged export");
  }
  if (matrix.privateKeyBoundary.privateKeyMaterialReturnedToProbe !== false) {
    addAssertion(report, "APK-semantic private signing key material escaped the in-memory builder");
  }
}

async function assertRuntimeStatus(report, skillName, expected, label) {
  const runtime = await invokeRuntimeSkill(skillName);
  const actual = String(runtime?.status || "");
  if (actual !== expected) addAssertion(report, `${label} runtime status was ${actual || "missing"}; expected ${expected}`);
  return actual === expected;
}

async function runGovernanceLifecycle(report, cdp, records, signerFingerprint) {
  const bySlug = new Map(records.map((item) => [item.slug, item]));
  const validation = bySlug.get("validation-report-extension");
  const material = bySlug.get("material-preset-pack");
  const optimizer = bySlug.get("optimizer-report-helper");
  const outfit = bySlug.get("outfit-naming-helper");

  await tauriInvoke(cdp, "set_skill_package_enabled", {
    request: { id: validation.id, body: { enabled: false, syncProjectedSkill: true }, timeoutMs: 60000 },
  });
  report.governance.disableBlockedExecution = await assertRuntimeStatus(
    report,
    validation.skillName,
    "blocked",
    "disabled package",
  );
  await appApi(`/api/app/skill-packages/${encodeURIComponent(validation.id)}`, {
    method: "PUT",
    body: { enabled: true, syncProjectedSkill: true },
  });
  report.governance.reenableWorked = await assertRuntimeStatus(
    report,
    validation.skillName,
    "executed",
    "re-enabled package",
  );

  await tauriInvoke(cdp, "set_skill_package_safe_mode", {
    request: { body: { enabled: true, reason: "packaged probe safe-mode gate" }, timeoutMs: 60000 },
  });
  const riskyEnable = await appApiRaw(`/api/app/skill-packages/${encodeURIComponent(material.id)}`, {
    method: "PUT",
    body: { enabled: true, syncProjectedSkill: true },
  });
  const materialBlocked = await assertRuntimeStatus(report, material.skillName, "blocked", "safe-mode risky package");
  report.governance.safeModeBlockedRiskyEnable = riskyEnable.status === 400 && materialBlocked;
  if (!report.governance.safeModeBlockedRiskyEnable) {
    addAssertion(report, "safe mode did not block risky package enable and execution");
  }
  await appApi("/api/app/skill-packages/safe-mode", {
    method: "POST",
    body: { enabled: false, reason: "packaged probe safe-mode release" },
  });
  for (const record of [outfit, optimizer]) {
    await appApi(`/api/app/skill-packages/${encodeURIComponent(record.id)}`, {
      method: "PUT",
      body: { enabled: true, syncProjectedSkill: true },
    });
  }
  const outfitRestored = await assertRuntimeStatus(
    report,
    outfit.skillName,
    "executed",
    "post-safe-mode outfit package",
  );
  const optimizerRestored = await assertRuntimeStatus(
    report,
    optimizer.skillName,
    "executed",
    "post-safe-mode optimizer package",
  );
  report.governance.safeModeTargetsRestored = outfitRestored && optimizerRestored;
  if (!report.governance.safeModeTargetsRestored) {
    addAssertion(report, "safe mode release did not explicitly restore later governance targets");
  }

  const beforeBlockSkills = await agentApi("/api/agent/skills");
  const beforeBlockProjection = (beforeBlockSkills?.skills || [])
    .find((item) => item?.name === outfit.skillName);

  const blocked = await appApi("/api/app/skill-packages/block-package", {
    method: "POST",
    body: { packageId: outfit.id, reason: "packaged probe blocklist gate" },
  });
  const blockedRuntime = await assertRuntimeStatus(report, outfit.skillName, "blocked", "blocklisted package");
  const skills = await agentApi("/api/agent/skills");
  const projected = (skills?.skills || []).find((item) => item?.name === outfit.skillName);
  report.governance.blockDisabledProjection = blocked?.ok === true
    && blockedRuntime
    && beforeBlockProjection?.enabled === true
    && beforeBlockProjection?.available === true
    && projected?.enabled === false
    && projected?.available === false;
  if (!report.governance.blockDisabledProjection) {
    addAssertion(report, "package blocklist did not disable its runtime projection");
  }

  const revoked = await tauriInvoke(cdp, "revoke_skill_package_signer", {
    request: {
      body: { signerFingerprint, reason: "packaged probe revocation gate" },
      timeoutMs: 60000,
    },
  });
  const revokedRuntimeBlocked = await assertRuntimeStatus(
    report,
    optimizer.skillName,
    "blocked",
    "revoked signer package",
  );
  report.governance.revokeBlockedExecution = revoked?.ok === true && revokedRuntimeBlocked;
  if (!report.governance.revokeBlockedExecution) {
    addAssertion(report, "signer revocation did not irreversibly disable package runtime execution");
  }
  const retrustRevoked = await appApiRaw("/api/app/skill-packages/trust-signer", {
    method: "POST",
    body: { signerFingerprint, reason: "packaged probe must not reverse revocation" },
  });
  report.governance.revokedSignerRetrustRejected = retrustRevoked.status === 400
    && /revoked/i.test(JSON.stringify(retrustRevoked.payload || {}));
  if (!report.governance.revokedSignerRetrustRejected) {
    addAssertion(report, "revoked signer was unexpectedly trusted again");
  }
}

function recipeSummaries() {
  const fixturePackage = resolve(evidenceRoot, "fixtures", "creator-outfit.zip");
  return {
    ttt_material_group: {
      workflow: "ttt_material_group",
      recipeType: "ttt_material_group",
      projectPath: projectRoot,
      avatarPath: "AvatarRoot",
      steps: [{ name: "plan-atlas", params: { projectRoot, rendererPath: "AvatarRoot/Body", slots: [0] } }],
    },
    booth_import_preflight: {
      workflow: "booth_import_preflight",
      recipeType: "booth_import_preflight",
      projectPath: projectRoot,
      packagePath: fixturePackage,
      steps: ["inspect-structure", "plan-import-without-writing"],
    },
    parameter_compression: {
      workflow: "parameter_compression",
      recipeType: "parameter_compression",
      projectPath: projectRoot,
      avatarPath: "AvatarRoot",
      steps: ["inventory", "menu-map", "blocked-preview"],
    },
    pc_quest_upload_pass: {
      workflow: "pc_quest_upload_pass",
      recipeType: "pc_quest_upload_pass",
      projectPath: projectRoot,
      avatarPath: "AvatarRoot",
      platforms: ["pc", "quest"],
      steps: ["pc-gate", "quest-gate", "report-unknowns"],
    },
  };
}

function exactJsonValue(actual, expected) {
  return JSON.stringify(actual) === JSON.stringify(expected);
}

function parseJsonValue(value) {
  try { return JSON.parse(String(value)); } catch { return null; }
}

function decodeFrontmatterScalar(raw) {
  const value = String(raw || "").trim();
  if (value.startsWith('"') && value.endsWith('"')) {
    try { return JSON.parse(value); } catch { return value; }
  }
  if (value.startsWith("'") && value.endsWith("'")) return value.slice(1, -1).replaceAll("''", "'");
  return value;
}

function skillMarkdownMetadataDiagnostics(markdown, manifest, expectation) {
  const lines = String(markdown || "").replaceAll("\r\n", "\n").split("\n");
  const diagnostics = {
    frontmatterOpened: lines[0] === "---",
    frontmatterClosed: false,
    name: false,
    permissionMode: false,
    riskLevel: false,
    entrypointTool: false,
    userInvocable: false,
    modelInvocationEnabled: false,
    allowedTools: false,
    disallowedTools: false,
  };
  if (!diagnostics.frontmatterOpened) return { ...diagnostics, matched: false };
  const closing = lines.indexOf("---", 1);
  diagnostics.frontmatterClosed = closing >= 0;
  if (!diagnostics.frontmatterClosed) return { ...diagnostics, matched: false };
  const frontmatter = lines.slice(1, closing);
  const scalar = (key) => {
    const prefix = `${key}:`;
    const matches = frontmatter.filter((line) => line.startsWith(prefix));
    return matches.length === 1 ? decodeFrontmatterScalar(matches[0].slice(prefix.length)) : undefined;
  };
  const list = (key) => {
    const marker = `${key}:`;
    const indexes = frontmatter.flatMap((line, index) => line === marker ? [index] : []);
    if (indexes.length !== 1) return undefined;
    const values = [];
    for (const line of frontmatter.slice(indexes[0] + 1)) {
      if (!line.startsWith("  - ")) break;
      values.push(decodeFrontmatterScalar(line.slice(4)));
    }
    return values;
  };
  diagnostics.name = scalar("name") === manifest?.skill_name;
  diagnostics.permissionMode = scalar("permission-mode") === expectation.permissionMode;
  diagnostics.riskLevel = scalar("risk-level") === expectation.riskLevel;
  diagnostics.entrypointTool = scalar("entrypoint-tool") === expectation.entrypointTool;
  diagnostics.userInvocable = scalar("user-invocable") === "true";
  diagnostics.modelInvocationEnabled = scalar("disable-model-invocation") === "false";
  diagnostics.allowedTools = exactJsonValue(list("allowed-tools"), expectation.allowedTools);
  diagnostics.disallowedTools = exactJsonValue(
    list("disallowed-tools"),
    ["vrcforge_execute_shell", "direct_unity_asset_write"],
  );
  return {
    ...diagnostics,
    matched: Object.values(diagnostics).every(Boolean),
  };
}

function serializedValueExcludes(value, forbiddenFragments) {
  const serialized = JSON.stringify(value);
  return typeof serialized === "string"
    && forbiddenFragments.every((fragment) => !serialized.includes(fragment));
}

function capturedRecipePrivatePathDiagnostics(preview, summary) {
  const serialized = JSON.stringify({ workflow: preview?.workflow, sourceFiles: preview?.sourceFiles || {} });
  const explicitPrivatePaths = [
    projectRoot,
    summary?.packagePath,
    evidenceRoot,
    repoRoot,
    userDataRoot,
    packageFixtureRoot,
    pathToSkillRoot,
  ].filter(Boolean);
  const explicitPathAbsent = explicitPrivatePaths.every((value) => {
    const raw = String(value);
    return !serialized.includes(raw) && !serialized.includes(raw.replaceAll("\\", "/"));
  });
  const diagnostics = {
    explicitPrivatePathsAbsent: explicitPathAbsent,
    windowsAbsolutePathsAbsent: !/[A-Za-z]:(?:\\\\|\/)/.test(serialized),
    macUserPathsAbsent: !serialized.includes("/Users/"),
    unixHomePathsAbsent: !serialized.includes("/home/"),
  };
  return {
    ...diagnostics,
    matched: Object.values(diagnostics).every(Boolean),
  };
}

function recipePreviewDiagnostics(preview, recipeType, summary, expectedDryRun = true) {
  const expectation = recipeExpectations[recipeType];
  const workflow = preview?.workflow || {};
  const recipe = workflow?.recipe || {};
  const required = new Set(workflow?.remapping?.required || []);
  const remappingFields = Array.isArray(workflow?.remapping?.fields) ? workflow.remapping.fields : [];
  const variableBindings = Object.fromEntries(expectation.requiredVariables.map((variable) => [variable,
    workflow?.variables?.[variable]?.placeholder === `{{${variable}}}`
    && workflow?.variables?.[variable]?.required === true
    && required.has(variable)
    && remappingFields.some((item) => item?.variable === variable && String(item?.field || "").startsWith("source.")),
  ]));
  const variablesBound = Object.values(variableBindings).every(Boolean);
  const projectSourcePlaceholder = workflow?.sourceSummary?.projectPath === "{{projectPath}}";
  const packageSourcePlaceholder = recipeType !== "booth_import_preflight"
    || workflow?.sourceSummary?.packagePath === "{{packagePath}}";
  const futureGateMatches = expectation.futureApplyGate
    ? exactJsonValue(recipe?.futureApplyGate, expectation.futureApplyGate)
    : !Object.hasOwn(recipe, "futureApplyGate");
  const skillMarkdown = skillMarkdownMetadataDiagnostics(preview?.skillMarkdown, preview?.manifest, expectation);
  const privatePaths = capturedRecipePrivatePathDiagnostics(preview, summary);
  const checks = {
    responseOk: preview?.ok === true,
    schema: preview?.schema === "vrcforge.path_to_skill.capture_result.v1",
    dryRun: preview?.dryRun === expectedDryRun,
    recipeType: recipe?.type === recipeType,
    shape: recipe?.shape === expectation.shape,
    writePath: recipe?.writePath === expectation.writePath,
    entrypointTool: recipe?.entrypointTool === expectation.entrypointTool,
    allowedTools: exactJsonValue(recipe?.allowedTools, expectation.allowedTools),
    recipeValidationDefaults: exactJsonValue(recipe?.validationDefaults, expectation.validation),
    workflowValidation: exactJsonValue(workflow?.validation, expectation.validation),
    manifestWritePath: preview?.manifest?.agent?.write_path === expectation.writePath,
    permissionMode: recipe?.permissionMode === expectation.permissionMode,
    riskLevel: recipe?.riskLevel === expectation.riskLevel,
    argumentHint: recipe?.argumentHint === expectation.argumentHint,
    detectorRules: exactJsonValue(recipe?.detectorRules, expectation.detectorRules),
    requiredEvidence: exactJsonValue(recipe?.requiredEvidence, expectation.requiredEvidence),
    recipePermissions: exactJsonValue(recipe?.permissions, expectation.permissions),
    manifestPermissions: exactJsonValue(preview?.manifest?.permissions, expectation.permissions),
    manifestAgentSchema: preview?.manifest?.agent?.schema === "vrcforge.path_to_skill.v1",
    manifestDryRunRequired: preview?.manifest?.agent?.dry_run_required === true,
    skillMarkdown: skillMarkdown.matched,
    variablesBound,
    projectSourcePlaceholder,
    packageSourcePlaceholder,
    futureApplyGate: futureGateMatches,
    privatePaths: privatePaths.matched,
  };
  return {
    matched: Object.values(checks).every(Boolean),
    checks,
    variableBindings,
    skillMarkdown,
    privatePaths,
  };
}

function recipePreviewMatchesExpectation(preview, recipeType, summary, expectedDryRun = true) {
  return recipePreviewDiagnostics(preview, recipeType, summary, expectedDryRun).matched;
}

async function runPathToSkillLifecycle(report, cdp) {
  const summaries = recipeSummaries();
  for (const [recipeType, summary] of Object.entries(summaries)) {
    const preview = await tauriInvoke(cdp, "preview_path_to_skill", {
      request: {
        body: {
          summary,
          packageId: `community.probe.${recipeType.replaceAll("_", "-")}`,
          skillName: `probe-${recipeType.replaceAll("_", "-")}`,
        },
        timeoutMs: 120000,
      },
    });
    const diagnostics = recipePreviewDiagnostics(preview, recipeType, summary);
    const passed = diagnostics.matched;
    report.diagnostics.pathToSkill.recipePreviews[recipeReportKeys[recipeType]] = diagnostics;
    report.pathToSkill.recipes[recipeReportKeys[recipeType]] = passed;
    if (!passed) addAssertion(report, `Path-to-Skill recipe preview failed for ${recipeType}`);
  }
  report.pathToSkill.previewViaTauri = Object.values(report.pathToSkill.recipes).every(Boolean);

  const genericReadinessSummary = {
    schema: "vrcforge.operation_summary.v1",
    source: { kind: "runtime_run" },
    workflow: "captured_runtime_operation",
    status: "completed",
    steps: [
      { kind: "validation", tool: "vrcforge_build_test_readiness", status: "executed" },
    ],
    evidence: { approvalRecorded: false, checkpointRecorded: false },
    validation: { requiresApproval: false, requiresCheckpoint: false, requiresRollback: false },
    projectPath: "{{projectPath}}",
  };
  const genericReadinessPreview = await appApi("/api/app/path-to-skill/preview", {
    method: "POST",
    body: {
      summary: genericReadinessSummary,
      packageId: "community.probe.captured-readiness",
      skillName: "captured-readiness",
    },
  });
  const genericSkillMarkdown = String(genericReadinessPreview?.sourceFiles?.["SKILL.md"] || "");
  report.pathToSkill.genericEntrypointPreserved = genericReadinessPreview?.ok === true
    && genericReadinessPreview?.dryRun === true
    && genericReadinessPreview?.workflow?.proofPassed === false
    && genericSkillMarkdown.includes("entrypoint-tool: vrcforge_build_test_readiness")
    && genericSkillMarkdown.includes("  - vrcforge_build_test_readiness\n")
    && !genericSkillMarkdown.includes("entrypoint-tool: vrcforge_health\n");
  if (!report.pathToSkill.genericEntrypointPreserved) {
    addAssertion(report, "generic contextual Path-to-Skill entrypoint/proof contract was not preserved");
  }

  const sourceOutput = resolve(pathToSkillRoot, "captured-source");
  const packageOutput = resolve(pathToSkillRoot, "captured-booth-preflight.vsk");
  const written = await appApi("/api/app/path-to-skill/write", {
    method: "POST",
    body: {
      summary: summaries.booth_import_preflight,
      packageId: "community.probe.captured-booth-preflight",
      skillName: "captured-booth-preflight",
      outputPath: sourceOutput,
      writeSource: true,
      useTempOutput: false,
      exportVsk: true,
      confirmExport: true,
      packageOutputPath: packageOutput,
    },
  });
  const capturedFiles = ["SKILL.md", "manifest.json", "workflows/captured-path.json"];
  const responseSourceFiles = written?.sourceFiles && typeof written.sourceFiles === "object"
    ? written.sourceFiles
    : {};
  const responseSourceNames = Object.keys(responseSourceFiles).sort();
  const diskSourceTexts = Object.fromEntries(await Promise.all(capturedFiles.map(async (relative) => [
    relative,
    await readFile(resolve(sourceOutput, ...relative.split("/")), "utf8"),
  ])));
  const sourceTreeShape = (await filesystemTreeSnapshot(sourceOutput, "source"))
    .map((row) => ({ path: row.path, type: row.type }))
    .sort((left, right) => left.path.localeCompare(right.path));
  const expectedSourceTreeShape = [
    { path: "source", type: "directory" },
    { path: "source/SKILL.md", type: "file" },
    { path: "source/manifest.json", type: "file" },
    { path: "source/workflows", type: "directory" },
    { path: "source/workflows/captured-path.json", type: "file" },
  ].sort((left, right) => left.path.localeCompare(right.path));
  const writtenSourceDiagnostics = {
    responseFileNamesExact: exactJsonValue(responseSourceNames, capturedFiles),
    diskTreeShapeExact: exactJsonValue(sourceTreeShape, expectedSourceTreeShape),
    diskBytesMatchResponse: capturedFiles.every((relative) =>
      diskSourceTexts[relative] === String(responseSourceFiles[relative] || "")),
    skillMarkdownMatchesResponse: responseSourceFiles["SKILL.md"] === written?.skillMarkdown,
    manifestMatchesResponse: exactJsonValue(parseJsonValue(responseSourceFiles["manifest.json"]), written?.manifest),
    workflowMatchesResponse: exactJsonValue(
      parseJsonValue(responseSourceFiles["workflows/captured-path.json"]),
      written?.workflow,
    ),
  };
  report.diagnostics.pathToSkill.writtenSource = writtenSourceDiagnostics;
  report.pathToSkill.writtenSourceMatchesResponse = Object.values(writtenSourceDiagnostics).every(Boolean);
  report.pathToSkill.writtenRecipeContract = recipePreviewMatchesExpectation(
    written,
    "booth_import_preflight",
    summaries.booth_import_preflight,
    false,
  );
  report.pathToSkill.writeViaRest = written?.ok === true
    && written?.dryRun === false
    && report.pathToSkill.writtenRecipeContract
    && report.pathToSkill.writtenSourceMatchesResponse
    && exactJsonValue(
      (written?.writtenSource?.files || []).map((item) => String(item?.path || "")).sort(),
      ["SKILL.md", "manifest.json", "workflows/captured-path.json"],
    );
  report.pathToSkill.exportedVsk = Boolean(written?.exported)
    && (await stat(packageOutput)).isFile();
  report.pathToSkill.positiveTemporaryResidueAbsent = await siblingTemporaryEntriesAbsent(sourceOutput)
    && await siblingTemporaryEntriesAbsent(packageOutput)
    && await treeTemporaryEntriesAbsent(pathToSkillRoot);
  if (!report.pathToSkill.writeViaRest || !report.pathToSkill.exportedVsk) {
    addAssertion(report, "Path-to-Skill REST write/export did not complete");
  }
  if (!report.pathToSkill.positiveTemporaryResidueAbsent) {
    addAssertion(report, "successful Path-to-Skill write/export left temporary staging residue");
  }
  const beforeExportPreflightState = await packageProjectionState(cdp);
  const exportedPreflight = await tauriInvoke(cdp, "preflight_skill_package", {
    request: { body: { packagePath: packageOutput }, timeoutMs: 120000 },
  });
  const exportedPreview = packagePreview(exportedPreflight);
  const afterExportPreflightState = await packageProjectionState(cdp);
  const sourceDigests = Object.fromEntries(await Promise.all(capturedFiles.map(async (relative) => [
    relative,
    await sha256File(resolve(sourceOutput, ...relative.split("/"))),
  ])));
  const exportedDigests = await archiveEntryDigests(packageOutput, capturedFiles);
  const sourceManifest = JSON.parse(await readFile(resolve(sourceOutput, "manifest.json"), "utf8"));
  const responseSkillSha256 = createHash("sha256").update(responseSourceFiles["SKILL.md"], "utf8").digest("hex");
  const responseWorkflowSha256 = createHash("sha256")
    .update(responseSourceFiles["workflows/captured-path.json"], "utf8")
    .digest("hex");
  report.pathToSkill.exportedVskContentMatches = exportedDigests?.ok === true
    && exportedDigests?.lockVerified === true
    && report.pathToSkill.writtenSourceMatchesResponse
    && exportedDigests.digests?.["SKILL.md"] === sourceDigests["SKILL.md"]
    && exportedDigests.digests?.["SKILL.md"] === responseSkillSha256
    && exportedDigests.digests?.["workflows/captured-path.json"] === sourceDigests["workflows/captured-path.json"]
    && exportedDigests.digests?.["workflows/captured-path.json"] === responseWorkflowSha256
    && exactJsonValue(exportedDigests.jsonDocuments?.["manifest.json"], sourceManifest)
    && exactJsonValue(exportedDigests.jsonDocuments?.["manifest.json"], written?.manifest)
    && exactJsonValue(exportedDigests.jsonDocuments?.["workflows/captured-path.json"], written?.workflow);
  report.pathToSkill.exportedVskPreflight = exportedPreflight?.ok === true
    && packageIdFromPreview(exportedPreview) === "community.probe.captured-booth-preflight"
    && exportedPreview?.dryRun?.supported === true
    && exportedPreview?.dryRun?.willWrite === false
    && report.pathToSkill.exportedVskContentMatches
    && packageProjectionStateEquals(beforeExportPreflightState, afterExportPreflightState);
  if (!report.pathToSkill.exportedVskPreflight) {
    addAssertion(report, "exported Path-to-Skill package preflight identity/no-write contract failed");
  }

  const rejectedSource = resolve(pathToSkillRoot, "existing-source");
  const rejectedSourceSentinel = resolve(rejectedSource, "unrelated-private-file.txt");
  await mkdir(rejectedSource, { recursive: false });
  await writeFile(rejectedSourceSentinel, "do not merge\n", "utf8");
  const existingSource = await appApiRaw("/api/app/path-to-skill/write", {
    method: "POST",
    body: {
      summary: summaries.ttt_material_group,
      packageId: "community.probe.existing-source",
      outputPath: rejectedSource,
      writeSource: true,
      useTempOutput: false,
    },
  });
  report.pathToSkill.existingSourceRejected = existingSource.status === 400
    && (await readFile(rejectedSourceSentinel, "utf8")) === "do not merge\n"
    && await directoryContainsOnly(rejectedSource, [basename(rejectedSourceSentinel)])
    && await siblingTemporaryEntriesAbsent(rejectedSource);

  const rejectedPackage = resolve(pathToSkillRoot, "existing-package.vsk");
  const rejectedPackageBytes = Buffer.from("do not replace\n", "utf8");
  await writeFile(rejectedPackage, rejectedPackageBytes);
  const existingPackage = await appApiRaw("/api/app/path-to-skill/write", {
    method: "POST",
    body: {
      summary: summaries.pc_quest_upload_pass,
      packageId: "community.probe.existing-package",
      exportVsk: true,
      confirmExport: true,
      packageOutputPath: rejectedPackage,
    },
  });
  report.pathToSkill.existingPackageRejected = existingPackage.status === 400
    && (await readFile(rejectedPackage)).equals(rejectedPackageBytes)
    && await siblingTemporaryEntriesAbsent(rejectedPackage);

  const traversalSource = resolve(pathToSkillRoot, "traversal-source");
  const traversalPackage = resolve(pathToSkillRoot, "traversal-package.vsk");
  const traversal = await appApiRaw("/api/app/path-to-skill/write", {
    method: "POST",
    body: {
      summary: {
        workflow: "booth_import_preflight",
        recipeType: "booth_import_preflight",
        projectPath: projectRoot,
        packagePath: "../PaidBooth/CreatorOutfit.zip",
        steps: ["inspect"],
      },
      packageId: "community.probe.traversal-negative",
      outputPath: traversalSource,
      writeSource: true,
      useTempOutput: false,
      exportVsk: true,
      confirmExport: true,
      packageOutputPath: traversalPackage,
    },
  });
  report.pathToSkill.parentTraversalRejected = traversal.status === 400
    && !(await pathExists(traversalSource))
    && !(await pathExists(traversalPackage))
    && await siblingTemporaryEntriesAbsent(traversalSource)
    && await siblingTemporaryEntriesAbsent(traversalPackage);

  const privateUrlPreview = await appApi("/api/app/path-to-skill/preview", {
    method: "POST",
    body: {
      summary: {
        workflow: "booth_import_preflight",
        recipeType: "booth_import_preflight",
        projectPath: projectRoot,
        packagePath: privateUrlSentinel,
        steps: ["inspect"],
      },
      packageId: "community.probe.private-url-redaction",
    },
  });
  const privateUrlDiagnostics = {
    responseOk: privateUrlPreview?.ok === true,
    dryRun: privateUrlPreview?.dryRun === true,
    sourcePlaceholder:
      privateUrlPreview?.workflow?.sourceSummary?.packagePath === "{{packagePath}}",
    variablePlaceholder:
      privateUrlPreview?.workflow?.variables?.packagePath?.placeholder === "{{packagePath}}",
    variableRequired: privateUrlPreview?.workflow?.variables?.packagePath?.required === true,
    remappingRequired:
      (privateUrlPreview?.workflow?.remapping?.required || []).includes("packagePath"),
    remappingField: (privateUrlPreview?.workflow?.remapping?.fields || []).some((item) =>
      item?.field === "source.packagePath" && item?.variable === "packagePath"),
    privateValueAbsent: serializedValueExcludes(privateUrlPreview, [privateUrlSentinel, "SecretOutfit"]),
  };
  report.diagnostics.pathToSkill.privateUrl = privateUrlDiagnostics;
  report.pathToSkill.privateUrlRedacted = Object.values(privateUrlDiagnostics).every(Boolean);

  const secretSource = resolve(pathToSkillRoot, "secret-negative-source");
  const secretPackage = resolve(pathToSkillRoot, "secret-negative.vsk");
  const secret = await appApiRaw("/api/app/path-to-skill/write", {
    method: "POST",
    body: {
      summary: {
        workflow: "secret-negative",
        recipeType: "booth_import_preflight",
        apiKey: negativeTestSecret,
      },
      packageId: "community.probe.secret-negative",
      outputPath: secretSource,
      writeSource: true,
      useTempOutput: false,
      exportVsk: true,
      confirmExport: true,
      packageOutputPath: secretPackage,
    },
  });
  const paidSource = resolve(pathToSkillRoot, "paid-negative-source");
  const paidPackage = resolve(pathToSkillRoot, "paid-negative.vsk");
  const paid = await appApiRaw("/api/app/path-to-skill/write", {
    method: "POST",
    body: {
      summary: {
        workflow: "paid-payload-negative",
        steps: ["inspect"],
        assetPayload: paidPayloadSentinel,
      },
      packageId: "community.probe.paid-negative",
      outputPath: paidSource,
      writeSource: true,
      useTempOutput: false,
      exportVsk: true,
      confirmExport: true,
      packageOutputPath: paidPackage,
    },
  });
  report.pathToSkill.secretRejected = secret.status === 400;
  report.pathToSkill.paidPayloadRejected = paid.status === 400;
  report.pathToSkill.secretRejectedNoOutput = report.pathToSkill.secretRejected
    && !(await pathExists(secretSource))
    && !(await pathExists(secretPackage))
    && await siblingTemporaryEntriesAbsent(secretSource)
    && await siblingTemporaryEntriesAbsent(secretPackage);
  report.pathToSkill.paidPayloadRejectedNoOutput = report.pathToSkill.paidPayloadRejected
    && !(await pathExists(paidSource))
    && !(await pathExists(paidPackage))
    && await siblingTemporaryEntriesAbsent(paidSource)
    && await siblingTemporaryEntriesAbsent(paidPackage);
  report.pathToSkill.negativeTemporaryResidueAbsent = await treeTemporaryEntriesAbsent(pathToSkillRoot);
  const negativeRootPrivacy = await scanSharedArtifactPrivacy([pathToSkillRoot]);
  report.pathToSkill.negativeSensitiveResidueAbsent = [
    "privateKeyFindings",
    "tokenFindings",
    "sourcePathFindings",
    "paidPayloadFindings",
    "unscannedFindings",
  ].every((key) => Array.isArray(negativeRootPrivacy?.[key]) && negativeRootPrivacy[key].length === 0);
  for (const key of [
    "existingSourceRejected",
    "existingPackageRejected",
    "parentTraversalRejected",
    "privateUrlRedacted",
    "secretRejected",
    "secretRejectedNoOutput",
    "paidPayloadRejected",
    "paidPayloadRejectedNoOutput",
    "negativeTemporaryResidueAbsent",
    "negativeSensitiveResidueAbsent",
  ]) {
    if (!report.pathToSkill[key]) addAssertion(report, `Path-to-Skill negative gate failed: ${key}`);
  }
  report.internalPaths = { sourceOutput, packageOutput, rejectedSource, rejectedPackage };
}

async function openSkillsWorkspace(cdp) {
  return waitForEval(
    cdp,
    `(async () => {
      const ready = () => Boolean(
        document.querySelector("textarea[data-vrcforge-path-to-skill-operation-summary]")
        && document.querySelector('[data-vrcforge-skill-audit="true"]')
      );
      if (!ready()) {
        const target = document.querySelector('button[data-vrcforge-sidebar-nav="skills"]');
        if (!target) return { ok: false, reason: "skills navigation button missing" };
        target.click();
      }
      const deadline = Date.now() + 45000;
      while (Date.now() < deadline) {
        if (ready()) {
          return { ok: true, bodyLength: document.body.innerText.length };
        }
        await new Promise((resolveWait) => setTimeout(resolveWait, 150));
      }
      return { ok: false, reason: "skills workspace did not render" };
    })()`,
    60000,
  );
}

async function exerciseFirstRunLanguageGate(report, cdp) {
  const result = await evalValue(
    cdp,
    `(async () => {
      const sleep = (ms) => new Promise((resolveWait) => setTimeout(resolveWait, ms));
      const deadline = Date.now() + 30000;
      let dialog;
      while (Date.now() < deadline) {
        dialog = document.querySelector('[data-vrcforge-onboarding-language-gate="true"]');
        if (dialog) break;
        await sleep(50);
      }
      const checks = {
        dialogVisible: Boolean(dialog),
        optionCountExact: false,
        optionSemanticsValid: false,
        exactlyOneDefaultSelected: false,
        continueButtonAvailable: false,
        gateDismissed: false,
        onboardingVisibleAfterContinue: false,
        selectedLanguageApplied: false,
        selectedLanguagePersisted: false,
        completionFlagPersisted: false,
        onboardingSkipControlFound: false,
        onboardingDismissedForAcceptance: false,
      };
      if (!dialog) return { ok: false, failureStage: "language-dialog", checks, optionCount: 0 };
      const expectedLocaleCodes = ["en-US", "zh-CN", "zh-TW", "ja-JP"];
      const options = [...dialog.querySelectorAll('button[data-vrcforge-onboarding-language-option]')];
      const optionLocaleCodes = options.map((option) =>
        option.getAttribute("data-vrcforge-onboarding-language-option") || "");
      checks.optionCountExact = options.length === 4;
      checks.optionSemanticsValid = JSON.stringify([...optionLocaleCodes].sort())
        === JSON.stringify([...expectedLocaleCodes].sort()) && options.every((option) => {
          const pressed = option.getAttribute("aria-pressed");
          const state = option.getAttribute("data-state");
          const localeCode = option.getAttribute("data-vrcforge-onboarding-language-option");
          return expectedLocaleCodes.includes(localeCode)
            && ["true", "false"].includes(pressed)
            && ["selected", "idle"].includes(state)
            && (pressed === "true") === (state === "selected");
        });
      const selectedIndexes = options.flatMap((option, index) =>
        option.getAttribute("aria-pressed") === "true" ? [index] : []);
      checks.exactlyOneDefaultSelected = selectedIndexes.length === 1;
      const selectedCode = selectedIndexes.length === 1
        ? options[selectedIndexes[0]].getAttribute("data-vrcforge-onboarding-language-option")
        : "";
      const continueButton = dialog.querySelector('button[data-vrcforge-onboarding-language-continue]');
      checks.continueButtonAvailable = Boolean(continueButton && !continueButton.disabled);
      if (
        !checks.optionCountExact
        || !checks.optionSemanticsValid
        || !checks.exactlyOneDefaultSelected
        || !checks.continueButtonAvailable
      ) {
        return { ok: false, failureStage: "language-semantics", checks, optionCount: options.length };
      }
      continueButton.click();
      const continueDeadline = Date.now() + 30000;
      let onboarding;
      while (Date.now() < continueDeadline) {
        const languageDialog = document.querySelector('[data-vrcforge-onboarding-language-gate="true"]');
        onboarding = document.querySelector('[data-vrcforge-onboarding="true"]');
        if (!languageDialog && onboarding) break;
        await sleep(50);
      }
      checks.gateDismissed = !document.querySelector('[data-vrcforge-onboarding-language-gate="true"]');
      checks.onboardingVisibleAfterContinue = Boolean(onboarding);
      const onboardingLanguage = onboarding?.querySelector('select[data-vrcforge-onboarding-language]');
      checks.selectedLanguageApplied = Boolean(
        onboardingLanguage
        && onboardingLanguage.options.length === options.length
        && onboardingLanguage.value === selectedCode,
      );
      try {
        checks.selectedLanguagePersisted = localStorage.getItem("vrcforge-locale") === selectedCode;
        checks.completionFlagPersisted = localStorage.getItem("vrcforge_onboarding_language_gate_completed") === "true";
      } catch { /* Persistence checks remain false. */ }
      const skipControl = onboarding?.querySelector('button[data-vrcforge-onboarding-skip]');
      checks.onboardingSkipControlFound = Boolean(skipControl && !skipControl.disabled);
      if (checks.onboardingSkipControlFound) {
        skipControl.click();
        const dismissDeadline = Date.now() + 5000;
        while (Date.now() < dismissDeadline && document.querySelector('[data-vrcforge-onboarding="true"]')) {
          await sleep(50);
        }
        checks.onboardingDismissedForAcceptance = !document.querySelector('[data-vrcforge-onboarding="true"]');
      }
      return {
        ok: checks.dialogVisible
          && checks.optionCountExact
          && checks.optionSemanticsValid
          && checks.exactlyOneDefaultSelected
          && checks.continueButtonAvailable
          && checks.gateDismissed
          && checks.onboardingVisibleAfterContinue
          && checks.selectedLanguageApplied
          && checks.selectedLanguagePersisted
          && checks.completionFlagPersisted
          && checks.onboardingSkipControlFound
          && checks.onboardingDismissedForAcceptance,
        checks,
        optionCount: options.length,
      };
    })()`,
  );
  const checks = result?.checks || {};
  report.ui.firstRunLanguageGateVisible = checks.dialogVisible === true;
  report.ui.firstRunLanguageDefaultLegal = checks.optionCountExact === true
    && checks.optionSemanticsValid === true
    && checks.exactlyOneDefaultSelected === true;
  report.ui.firstRunLanguageContinueApplied = result?.ok === true;
  report.diagnostics.ui.firstRunLanguageGate = {
    ...checks,
    optionCount: Number(result?.optionCount || 0),
    completed: result?.ok === true,
    failureStage: ["language-dialog", "language-semantics"].includes(result?.failureStage)
      ? result.failureStage
      : result?.ok === true ? "" : "language-completion",
  };
}

async function invokeContextualReadinessRuntime() {
  const payload = await appApi("/api/app/agent/message", {
    method: "POST",
    body: {
      agent_name: "packaged-skill-context-probe",
      clientTurnId: `contextual-skill-${Date.now()}`,
      message: "Contextual Path-to-Skill readiness capture.",
      skill_tool: "vrcforge_build_test_readiness",
      skill_params: { projectPath: projectRoot, projectRoot },
      projectPath: projectRoot,
      projectRoot,
    },
    timeoutMs: 180000,
  });
  return payload?.skill || {};
}

async function exerciseContextualPathToSkillUi(report, cdp) {
  const result = await evalValue(
    cdp,
    `(async () => {
      const sleep = (ms) => new Promise((resolveWait) => setTimeout(resolveWait, ms));
      const deadline = Date.now() + 60000;
      const checks = {
        saveOperationButtonFound: false,
        prefillBadgeFound: false,
        operationTextareaFound: false,
        summaryJsonParsed: false,
        portableShape: false,
        privatePathsAbsent: false,
        structuredSummary: false,
      };
      let target;
      while (Date.now() < deadline) {
        target = document.querySelector(
          'button[data-vrcforge-save-operation-as-skill][data-vrcforge-save-operation-tool="vrcforge_build_test_readiness"]',
        );
        if (target) break;
        await sleep(200);
      }
      checks.saveOperationButtonFound = Boolean(target);
      if (!target) return { ok: false, failureStage: "save-operation-button", checks };
      target.click();
      while (Date.now() < deadline) {
        const badge = document.querySelector('[data-vrcforge-path-to-skill-prefilled="true"]');
        const textarea = document.querySelector("textarea[data-vrcforge-path-to-skill-operation-summary]");
        if (badge && textarea) {
          checks.prefillBadgeFound = true;
          checks.operationTextareaFound = true;
          const text = String(textarea.value || "");
          let summary;
          try { summary = JSON.parse(text); } catch { summary = null; }
          checks.summaryJsonParsed = Boolean(summary);
          const exactKeys = (value, expected) => Boolean(value)
            && typeof value === "object"
            && !Array.isArray(value)
            && JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...expected].sort());
          const portableShape = exactKeys(
            summary,
            ["schema", "source", "workflow", "status", "steps", "evidence", "validation", "projectPath"],
          )
            && exactKeys(summary?.source, ["kind"])
            && exactKeys(summary?.evidence, ["approvalRecorded", "checkpointRecorded"])
            && exactKeys(summary?.validation, ["requiresApproval", "requiresCheckpoint", "requiresRollback"])
            && Array.isArray(summary?.steps)
            && summary.steps.length > 0
            && summary.steps.every((step) => exactKeys(step, ["kind", "tool", "status"]));
          checks.portableShape = portableShape;
          const privatePathsAbsent = !/[A-Za-z]:[\\\\/]/.test(text)
            && !text.includes(${JSON.stringify(projectRoot)})
            && !text.includes(${JSON.stringify(evidenceRoot)})
            && !text.includes(${JSON.stringify(repoRoot)});
          checks.privatePathsAbsent = privatePathsAbsent;
          const portable = portableShape && privatePathsAbsent;
          const structured = summary?.schema === "vrcforge.operation_summary.v1"
            && summary?.source?.kind === "runtime_run"
            && summary?.workflow === "captured_runtime_operation"
            && summary?.projectPath === "{{projectPath}}"
            && Array.isArray(summary?.steps)
            && summary.steps.some((step) => step?.tool === "vrcforge_build_test_readiness" && ["executed", "completed"].includes(step?.status));
          checks.structuredSummary = structured;
          return { ok: structured && portable, prefilled: true, structured, portable, checks };
        }
        await sleep(150);
      }
      return { ok: false, failureStage: "prefill-render", checks };
    })()`,
  );
  report.diagnostics.pathToSkill.contextualPrefill = {
    ...(result?.checks || {}),
    completed: result?.ok === true,
    failureStage: ["save-operation-button", "prefill-render"].includes(result?.failureStage)
      ? result.failureStage
      : "",
  };
  report.ui.contextualPathToSkillPrefill = result?.ok === true
    && result?.prefilled === true
    && result?.structured === true
    && result?.portable === true;
  if (!report.ui.contextualPathToSkillPrefill) {
    addAssertion(report, "structured runtime operation did not open Skills with a portable contextual Path-to-Skill prefill");
  }
}

function expectedImportedAuditGovernanceRows(payload) {
  const audit = Array.isArray(payload?.audit) ? payload.audit : [];
  return [...audit]
    .reverse()
    .filter((item) => String(getField(item, "event") || "") === "skill_package_imported")
    .slice(0, 10)
    .map((item) => {
      const skillId = String(getField(item, "skill_id", "skillId") || "").trim();
      const packageId = String(getField(item, "package_id", "packageId") || "").trim();
      return {
        identityValues: [skillId, packageId && packageId !== skillId ? packageId : ""].filter(Boolean),
        version: String(getField(item, "package_version", "packageVersion", "version") || "").trim(),
        signatureStatus: String(getField(item, "signature_status", "signatureStatus") || "").trim(),
        riskLevel: String(getField(item, "risk_level", "riskLevel") || "").trim(),
        signerFingerprint: String(getField(item, "signer_fingerprint", "signerFingerprint") || "").trim(),
      };
    });
}

async function exerciseAuditUi(
  report,
  cdp,
  signerFingerprint,
  uniquePackageQuery,
  expectedImportedGovernanceRows,
) {
  const result = await evalValue(
    cdp,
    `(async () => {
      const sleep = (ms) => new Promise((resolveWait) => setTimeout(resolveWait, ms));
      const waitFor = async (predicate, timeoutMs = 5000) => {
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
          if (predicate()) return true;
          await sleep(50);
        }
        return false;
      };
      const root = document.querySelector('[data-vrcforge-skill-audit="true"]');
      const select = root?.querySelector('select[data-vrcforge-skill-audit-event-filter]');
      const search = root?.querySelector('input[data-vrcforge-skill-audit-search]');
      const status = root?.querySelector('[data-vrcforge-skill-audit-status]');
      if (!select) return { ok: false, reason: "audit filter missing" };
      if (!root || !search || !status) return { ok: false, reason: "audit search/live region missing" };
      const rowElements = () => [...root.querySelectorAll('[data-vrcforge-skill-audit-row]')];
      const eventValues = () => rowElements()
        .map((row) => row.getAttribute("data-vrcforge-skill-audit-event") || "")
        .filter(Boolean);
      const fieldValue = (row, key) => {
        const field = [...row.querySelectorAll('[data-vrcforge-skill-audit-field]')]
          .find((item) => item.getAttribute("data-vrcforge-skill-audit-field") === key);
        return String(field?.querySelector('[data-vrcforge-skill-audit-field-value]')?.textContent || "").trim();
      };
      const rowSemanticValue = (row) => ({
        event: row.getAttribute("data-vrcforge-skill-audit-event") || "",
        skillId: row.getAttribute("data-vrcforge-skill-audit-skill-id") || "",
        packageId: row.getAttribute("data-vrcforge-skill-audit-package-id") || "",
        version: row.getAttribute("data-vrcforge-skill-audit-version") || "",
        signatureStatus: fieldValue(row, "signatureStatus"),
        riskLevel: fieldValue(row, "riskLevel"),
        signerFingerprint: fieldValue(row, "signerFingerprint"),
      });
      const rowSignatures = () => rowElements().map((row) => JSON.stringify(rowSemanticValue(row)));
      const statusText = () => String(status.textContent || "").trim();
      const next = root.querySelector('button[data-vrcforge-skill-audit-next]');
      const pageOne = rowSignatures();
      const initialStatus = statusText();
      const paginationAvailable = pageOne.length === 10 && next && !next.disabled;
      if (paginationAvailable) next.click();
      const paginationLiveUpdated = paginationAvailable
        && await waitFor(() => statusText() && statusText() !== initialStatus);
      const pageTwo = rowSignatures();
      const paginationExercised = paginationAvailable
        && pageTwo.length > 0
        && JSON.stringify(pageOne) !== JSON.stringify(pageTwo);

      const nativeSelectSetter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;
      const importOption = [...select.options].find((option) => option.value === "skill_package_imported");
      const beforeFilterStatus = statusText();
      if (importOption) {
        nativeSelectSetter.call(select, importOption.value);
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      const filterLiveUpdated = Boolean(importOption)
        && await waitFor(() => statusText() && statusText() !== beforeFilterStatus);
      const filteredTitles = eventValues();
      const filteredRows = rowSignatures();
      const filteredRowElements = rowElements();
      const filterExercised = Boolean(importOption)
        && filteredTitles.length > 0
        && filteredTitles.every((value) => value === "skill_package_imported")
        && JSON.stringify(filteredRows) !== JSON.stringify(pageOne);
      const expectedGovernanceRows = ${JSON.stringify(expectedImportedGovernanceRows)};
      const expectedGovernanceComplete = expectedGovernanceRows.length >= 4
        && expectedGovernanceRows.every((item) =>
          Array.isArray(item.identityValues)
          && item.identityValues.length > 0
          && Boolean(item.version)
          && Boolean(item.signatureStatus)
          && Boolean(item.riskLevel)
          && Boolean(item.signerFingerprint));
      const unmatchedExpectedRows = expectedGovernanceRows.map((item) => ({ ...item }));
      let matchedGovernanceRows = 0;
      for (const row of filteredRowElements) {
        const semantic = rowSemanticValue(row);
        const rowIdentities = new Set([semantic.skillId, semantic.packageId].filter(Boolean));
        const expectedIndex = unmatchedExpectedRows.findIndex((item) =>
          item.identityValues.every((value) => rowIdentities.has(String(value)))
          && semantic.version === item.version
          && semantic.signatureStatus === item.signatureStatus
          && semantic.riskLevel === item.riskLevel
          && semantic.signerFingerprint === item.signerFingerprint);
        if (expectedIndex >= 0) {
          unmatchedExpectedRows.splice(expectedIndex, 1);
          matchedGovernanceRows += 1;
        }
      }
      const governanceFieldsVisible = expectedGovernanceComplete
        && filteredRows.length === expectedGovernanceRows.length
        && matchedGovernanceRows === filteredRows.length
        && unmatchedExpectedRows.length === 0;

      nativeSelectSetter.call(select, "");
      select.dispatchEvent(new Event("change", { bubbles: true }));
      await waitFor(() => eventValues().some((value) => value !== "skill_package_imported"));
      const nativeInputSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      const beforeSearchRows = rowSignatures();
      const beforeSearchStatus = statusText();
      nativeInputSetter.call(search, ${JSON.stringify(uniquePackageQuery)});
      search.dispatchEvent(new Event("input", { bubbles: true }));
      const searchLiveUpdated = await waitFor(() => statusText() && statusText() !== beforeSearchStatus);
      const searchedRows = rowSignatures();
      const searchedRowElements = rowElements();
      const searchExercised = searchedRows.length > 0
        && searchedRows.length < beforeSearchRows.length
        && JSON.stringify(searchedRows) !== JSON.stringify(beforeSearchRows)
        && searchedRowElements.every((row) => {
          const semantic = rowSemanticValue(row);
          return [semantic.skillId, semantic.packageId].includes(${JSON.stringify(uniquePackageQuery)});
        });

      const beforeSignerStatus = statusText();
      nativeInputSetter.call(search, ${JSON.stringify(signerFingerprint.slice(0, 16))});
      search.dispatchEvent(new Event("input", { bubbles: true }));
      await waitFor(() => statusText() !== beforeSignerStatus || rowElements().some((row) =>
        fieldValue(row, "signerFingerprint").startsWith(${JSON.stringify(signerFingerprint.slice(0, 16))})));
      const signerVisible = rowElements().length > 0 && rowElements().every((row) =>
        fieldValue(row, "signerFingerprint").startsWith(${JSON.stringify(signerFingerprint.slice(0, 16))}));
      nativeInputSetter.call(search, "");
      search.dispatchEvent(new Event("input", { bubbles: true }));
      return {
        ok: true,
        searchVisible: Boolean(search),
        signerVisible,
        searchExercised,
        filterExercised,
        governanceFieldsVisible,
        paginationExercised,
        ariaLive: paginationLiveUpdated && filterLiveUpdated && searchLiveUpdated,
        firstPageRows: pageOne.length,
        secondPageRows: pageTwo.length,
        filteredRows: filteredRows.length,
        searchedRows: searchedRows.length,
        expectedGovernanceRows: expectedGovernanceRows.length,
        matchedGovernanceRows,
        unmatchedGovernanceRows: unmatchedExpectedRows.length,
      };
    })()`,
  );
  report.ui.auditSearchVisible = result?.searchVisible === true && result?.searchExercised === true;
  report.ui.auditSignerVisible = result?.signerVisible === true;
  report.ui.auditFilterExercised = result?.filterExercised === true;
  report.ui.auditGovernanceFieldsVisible = result?.governanceFieldsVisible === true;
  report.ui.auditPaginationExercised = result?.paginationExercised === true;
  report.ui.auditAriaLive = result?.ariaLive === true;
  report.diagnostics.ui.auditGovernance = {
    filteredRows: Number(result?.filteredRows || 0),
    expectedRows: Number(result?.expectedGovernanceRows || 0),
    matchedRows: Number(result?.matchedGovernanceRows || 0),
    unmatchedRows: Number(result?.unmatchedGovernanceRows || 0),
    everyFilteredRowComplete: result?.governanceFieldsVisible === true,
  };
  report.ui.auditRows = {
    firstPage: Number(result?.firstPageRows || 0),
    secondPage: Number(result?.secondPageRows || 0),
    filtered: Number(result?.filteredRows || 0),
    searched: Number(result?.searchedRows || 0),
  };
}

async function exercisePathToSkillUi(report, cdp) {
  const result = await evalValue(
    cdp,
    `(async () => {
      const sleep = (ms) => new Promise((resolveWait) => setTimeout(resolveWait, ms));
      const checks = {
        operationTextareaFound: false,
        panelFound: false,
        contextualSummaryPreserved: false,
        previewButtonFound: false,
        confirmationRendered: false,
        confirmationSelected: false,
        identityFieldFound: false,
        confirmationInvalidated: false,
      };
      const textarea = document.querySelector("textarea[data-vrcforge-path-to-skill-operation-summary]");
      checks.operationTextareaFound = Boolean(textarea);
      if (!textarea) return { ok: false, failureStage: "operation-textarea", checks };
      const panel = textarea.closest('[data-vrcforge-path-to-skill-panel="true"]');
      checks.panelFound = Boolean(panel);
      if (!panel) return { ok: false, failureStage: "capture-panel", checks };
      let contextualSummary;
      try { contextualSummary = JSON.parse(String(textarea.value || "")); } catch { contextualSummary = null; }
      checks.contextualSummaryPreserved = Boolean(
        contextualSummary?.source?.kind !== "runtime_run"
        ? false
        : contextualSummary?.workflow === "captured_runtime_operation"
      );
      if (!checks.contextualSummaryPreserved) {
        return { ok: false, failureStage: "contextual-summary", checks };
      }
      const previewButton = panel.querySelector('button[data-vrcforge-path-to-skill-preview]');
      checks.previewButtonFound = Boolean(previewButton);
      if (!previewButton) return { ok: false, failureStage: "preview-button", checks };
      previewButton.click();
      const deadline = Date.now() + 45000;
      let checkbox;
      while (Date.now() < deadline) {
        checkbox = panel.querySelector('input[data-vrcforge-path-to-skill-confirmation]');
        if (checkbox) break;
        await sleep(150);
      }
      checks.confirmationRendered = Boolean(checkbox);
      if (!checkbox) return { ok: false, failureStage: "confirmation-render", checks };
      checkbox.click();
      const confirmationDeadline = Date.now() + 5000;
      while (Date.now() < confirmationDeadline && checkbox.checked !== true) await sleep(50);
      const confirmedBeforeChange = checkbox.checked === true;
      checks.confirmationSelected = confirmedBeforeChange;
      const identity = panel.querySelector('input[data-vrcforge-path-to-skill-package-id]');
      checks.identityFieldFound = Boolean(identity);
      if (!identity) return { ok: false, failureStage: "identity-field", checks };
      const inputSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      inputSetter.call(identity, "community.probe.changed-after-preview");
      identity.dispatchEvent(new Event("input", { bubbles: true }));
      identity.dispatchEvent(new Event("change", { bubbles: true }));
      const invalidationDeadline = Date.now() + 5000;
      let currentCheckbox = panel.querySelector('input[data-vrcforge-path-to-skill-confirmation]');
      while (Date.now() < invalidationDeadline && currentCheckbox?.checked === true) {
        await sleep(50);
        currentCheckbox = panel.querySelector('input[data-vrcforge-path-to-skill-confirmation]');
      }
      const confirmationInvalidated = confirmedBeforeChange
        && (!currentCheckbox || currentCheckbox.checked === false);
      checks.confirmationInvalidated = confirmationInvalidated;
      return {
        ok: confirmationInvalidated,
        failureStage: confirmationInvalidated ? "" : "confirmation-invalidation",
        confirmationInvalidated,
        panelVisible: true,
        previewViaTauriUi: true,
        checks,
      };
    })()`,
  );
  report.ui.pathToSkillVisible = result?.panelVisible === true;
  report.pathToSkill.confirmationInvalidated = result?.confirmationInvalidated === true;
  report.diagnostics.pathToSkill.confirmationInvalidation = {
    ...(result?.checks || {}),
    completed: result?.ok === true,
    failureStage: [
      "operation-textarea",
      "capture-panel",
      "contextual-summary",
      "preview-button",
      "confirmation-render",
      "identity-field",
      "confirmation-invalidation",
    ].includes(result?.failureStage) ? result.failureStage : "",
  };
  if (!report.pathToSkill.confirmationInvalidated) {
    addAssertion(report, "Path-to-Skill UI did not invalidate confirmation after preview input changed");
  }
}

async function runUiAcceptance(report, cdp, signerFingerprint, records) {
  const contextualRuntime = await invokeContextualReadinessRuntime();
  const contextualReadiness = contextualRuntime?.result;
  if (
    contextualRuntime?.ok !== true
    || String(contextualRuntime?.status || "") !== "executed"
    || !contextualReadiness
    || typeof contextualReadiness !== "object"
  ) {
    addAssertion(report, "contextual Path-to-Skill readiness runtime did not execute successfully");
  }
  await exerciseContextualPathToSkillUi(report, cdp);
  const opened = await openSkillsWorkspace(cdp);
  report.ui.skillsWorkspaceVisible = opened?.ok === true;
  if (!report.ui.skillsWorkspaceVisible) {
    addAssertion(report, "packaged Skills workspace did not render");
    return;
  }
  const uniquePackageQuery = records.find((item) => item.slug === "outfit-naming-helper")?.id
    || "community.examples.outfit-naming-helper";
  const auditPayload = await appApi("/api/app/skill-packages");
  const expectedImportedGovernanceRows = expectedImportedAuditGovernanceRows(auditPayload);
  await exerciseAuditUi(
    report,
    cdp,
    signerFingerprint,
    uniquePackageQuery,
    expectedImportedGovernanceRows,
  );
  await exercisePathToSkillUi(report, cdp);
  for (const key of [
    "pathToSkillVisible",
    "contextualPathToSkillPrefill",
    "auditSearchVisible",
    "auditSignerVisible",
    "auditFilterExercised",
    "auditGovernanceFieldsVisible",
    "auditPaginationExercised",
    "auditAriaLive",
  ]) {
    if (!report.ui[key]) addAssertion(report, `packaged Skills UI gate failed: ${key}`);
  }
}

async function validateSupportBundle(bundlePath, sourceVersion) {
  const code = String.raw`
import json
import os
import re
import sys
import zipfile
from pathlib import Path

path = Path(sys.argv[1])
version = sys.argv[2]
exact_secrets = [item for item in json.loads(os.environ.get("VRCFORGE_PROBE_EXACT_SECRETS", "[]")) if item]
private_roots = [item for item in json.loads(os.environ.get("VRCFORGE_PROBE_PRIVATE_ROOTS", "[]")) if item]
required = {"metadata.json","bootstrap.json","doctor.json","diagnostics.json","agent-audit.json","checkpoints.json"}
result = {"ok": False, "missingMembers": [], "privacyFindings": []}
try:
    with zipfile.ZipFile(path) as bundle:
        names = set(bundle.namelist())
        result["missingMembers"] = sorted(required - names)
        result["badMember"] = bundle.testzip() or ""
        metadata = json.loads(bundle.read("metadata.json")) if "metadata.json" in names else {}
        bootstrap = json.loads(bundle.read("bootstrap.json")) if "bootstrap.json" in names else {}
        secret_value = re.compile(r'(?i)"(?:api[_-]?key|app[_-]?session[_-]?token|gateway[_-]?token|access[_-]?token|password|secret)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')
        secret_token = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/-]{16,})")
        user_path = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"']+")
        allowed = {"", "<redacted>", "[redacted]", "redacted", "***", "configured", "present"}
        max_scan_bytes = 64 * 1024 * 1024
        findings = []

        def inspect_bytes(label, data):
            text = data.decode("utf-8", errors="replace")
            if "-----BEGIN PRIVATE KEY-----" in text:
                findings.append(f"{label}:private-key")
            if secret_token.search(text):
                findings.append(f"{label}:token-pattern")
            if any(secret in text for secret in exact_secrets):
                findings.append(f"{label}:exact-secret")
            normalized = text.replace("\\\\", "\\")
            normalized_folded = re.sub(r"[\\/]+", "/", normalized).casefold()
            if user_path.search(normalized):
                findings.append(f"{label}:absolute-user-path")
            if any(re.sub(r"[\\/]+", "/", root).rstrip("/").casefold() in normalized_folded for root in private_roots):
                findings.append(f"{label}:private-root")
            for match in secret_value.finditer(text):
                if match.group(1).strip().lower() not in allowed:
                    findings.append(f"{label}:secret-value")
                    break

        inspect_bytes("archive-comment", bundle.comment or b"")
        canonical_names = set()
        for index, info in enumerate(bundle.infolist()):
            label = f"member-{index}"
            name_text = str(info.filename or "")
            inspect_bytes(f"{label}:name", name_text.encode("utf-8", errors="replace"))
            inspect_bytes(f"{label}:comment", info.comment or b"")
            inspect_bytes(f"{label}:extra", info.extra or b"")
            normalized_name = name_text.replace("\\", "/")
            comparable_name = normalized_name[:-1] if info.is_dir() and normalized_name.endswith("/") else normalized_name
            parts = comparable_name.split("/") if comparable_name else []
            unsafe_name = bool(
                not comparable_name
                or name_text.startswith(("/", "\\"))
                or re.match(r"(?i)^[a-z]:[\\/]", name_text)
                or "\x00" in name_text
                or any(part in {"", ".", ".."} for part in parts)
            )
            canonical_name = "/".join(parts).casefold()
            if unsafe_name:
                findings.append(f"{label}:unsafe-name")
            elif canonical_name in canonical_names:
                findings.append(f"{label}:duplicate-name")
            else:
                canonical_names.add(canonical_name)
            if info.is_dir():
                continue
            if info.file_size > max_scan_bytes:
                findings.append(f"{label}:oversized-unscanned-text")
                continue
            inspect_bytes(f"{label}:content", bundle.read(info))
        privacy = metadata.get("privacy") if isinstance(metadata.get("privacy"), dict) else {}
        result.update({
            "metadataSchema": metadata.get("schema"),
            "metadataVersion": metadata.get("version"),
            "metadataPortableMode": metadata.get("portableMode"),
            "redactsSecrets": privacy.get("redactsSecrets"),
            "includesFullPaths": privacy.get("includesFullPaths"),
            "bootstrapOk": bootstrap.get("ok"),
            "privacyFindings": sorted(set(findings)),
        })
        result["ok"] = bool(
            not result["missingMembers"]
            and not result["badMember"]
            and metadata.get("schema") == "vrcforge.support-bundle.v1"
            and metadata.get("version") == version
            and metadata.get("portableMode") is True
            and privacy.get("redactsSecrets") is True
            and not bool(privacy.get("includesFullPaths"))
            and bootstrap.get("ok") is True
            and not result["privacyFindings"]
        )
except Exception as exc:
    result["errorType"] = type(exc).__name__
print(json.dumps(result, separators=(",", ":")))
`;
  return runPythonJson(code, [bundlePath, sourceVersion], {
    env: {
      ...process.env,
      VRCFORGE_PROBE_EXACT_SECRETS: JSON.stringify([
        appSessionToken,
        agentGatewayToken,
        agentApprovalToken,
        negativeTestSecret,
        privateUrlSentinel,
        ephemeralSigningKeyPath,
      ]),
      VRCFORGE_PROBE_PRIVATE_ROOTS: JSON.stringify([
        repoRoot,
        evidenceRoot,
        projectRoot,
        userDataRoot,
        packageFixtureRoot,
        pathToSkillRoot,
      ]),
    },
  });
}

async function createAndValidateSupportBundle(report, sourceVersion) {
  const response = await appApi("/api/app/support-bundle", {
    method: "POST",
    body: { includeFullPaths: false, logLimit: 500 },
    timeoutMs: 60000,
  });
  const bundlePath = String(response?.bundlePath || "");
  if (!bundlePath) throw new Error("Support bundle response did not include a path.");
  const canonicalBundle = normalizedPath(await realpath(bundlePath));
  const canonicalUserData = normalizedPath(await realpath(userDataRoot));
  if (!canonicalBundle.startsWith(`${canonicalUserData}/`)) {
    throw new Error("Support bundle escaped the isolated user-data root.");
  }
  const validation = await validateSupportBundle(bundlePath, sourceVersion);
  report.privacy.supportBundleClean = response?.ok === true
    && response?.schema === "vrcforge.support-bundle.v1"
    && response?.redacted === true
    && validation?.ok === true;
  report.supportBundle = {
    responseOk: response?.ok === true,
    schema: String(response?.schema || ""),
    redacted: response?.redacted === true,
    validation,
  };
  if (!report.privacy.supportBundleClean) addAssertion(report, "packaged support bundle privacy contract failed");
  return bundlePath;
}

async function scanSharedArtifactPrivacy(paths) {
  const specPath = resolve(evidenceRoot, "privacy-scan-inputs.json");
  await writeFile(specPath, `${JSON.stringify(paths)}\n`, "utf8");
  const code = String.raw`
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
exact_secrets = [item for item in json.loads(os.environ.get("VRCFORGE_PROBE_EXACT_SECRETS", "[]")) if item]
private_roots = [item for item in json.loads(os.environ.get("VRCFORGE_PROBE_PRIVATE_ROOTS", "[]")) if item]
paid_marker = os.environ.get("VRCFORGE_PROBE_PAID_MARKER", "")
ephemeral_key_path = os.environ.get("VRCFORGE_PROBE_EPHEMERAL_KEY_PATH", "")
private_findings = []
ephemeral_key_path_findings = []
token_findings = []
source_findings = []
paid_findings = []
unscanned_findings = []
max_scan_bytes = 64 * 1024 * 1024
max_archive_entries = 10000
max_archive_depth = 3
max_archive_total_bytes = 256 * 1024 * 1024
token_pattern = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/-]{16,})")
secret_field = re.compile(r'(?i)"(?:api[_-]?key|app[_-]?session[_-]?token|gateway[_-]?token|access[_-]?token|password|secret)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')
user_path = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"']+")
allowed = {"", "<redacted>", "[redacted]", "redacted", "***", "configured", "present"}

def scan(label, data):
    text = data.decode("utf-8", errors="replace")
    if "-----BEGIN PRIVATE KEY-----" in text or "-----BEGIN OPENSSH PRIVATE KEY-----" in text:
        private_findings.append(label)
    if token_pattern.search(text) or any(secret in text for secret in exact_secrets):
        token_findings.append(label)
    for match in secret_field.finditer(text):
        if match.group(1).strip().lower() not in allowed:
            token_findings.append(label)
            break
    normalized = text.replace("\\\\", "\\")
    normalized_folded = re.sub(r"[\\/]+", "/", normalized).casefold()
    if ephemeral_key_path:
        normalized_key_path = re.sub(r"[\\/]+", "/", ephemeral_key_path).casefold()
        if normalized_key_path in normalized_folded:
            ephemeral_key_path_findings.append(label)
    private_root_leak = any(
        re.sub(r"[\\/]+", "/", root).rstrip("/").casefold() in normalized_folded
        for root in private_roots
    )
    if user_path.search(normalized) or private_root_leak:
        source_findings.append(label)
    if paid_marker and paid_marker in text:
        paid_findings.append(label)

def scan_archive(label, source, depth=0):
    if depth > max_archive_depth:
        unscanned_findings.append(f"{label}:archive-depth")
        return
    try:
        with zipfile.ZipFile(source) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            all_infos = archive.infolist()
            if len(all_infos) > max_archive_entries:
                unscanned_findings.append(f"{label}:archive-entry-count")
                return
            if sum(info.file_size for info in infos) > max_archive_total_bytes:
                unscanned_findings.append(f"{label}:archive-uncompressed-size")
                return
            scan(f"{label}:archive-comment", archive.comment or b"")
            canonical_names = set()
            for index, info in enumerate(all_infos):
                member_label = f"{label}/member-{index}"
                name_text = str(info.filename or "")
                name_bytes = name_text.encode("utf-8", errors="replace")
                scan(f"{member_label}:name", name_bytes)
                scan(f"{member_label}:comment", info.comment or b"")
                scan(f"{member_label}:extra", info.extra or b"")
                normalized_name = name_text.replace("\\", "/")
                comparable_name = normalized_name[:-1] if info.is_dir() and normalized_name.endswith("/") else normalized_name
                name_parts = comparable_name.split("/") if comparable_name else []
                unsafe_name = bool(
                    not comparable_name
                    or name_text.startswith(("/", "\\"))
                    or re.match(r"(?i)^[a-z]:[\\/]", name_text)
                    or "\x00" in name_text
                    or any(part in {"", ".", ".."} for part in name_parts)
                )
                canonical_name = "/".join(name_parts).casefold()
                if unsafe_name:
                    unscanned_findings.append(f"{member_label}:unsafe-name")
                elif canonical_name in canonical_names:
                    unscanned_findings.append(f"{member_label}:duplicate-name")
                else:
                    canonical_names.add(canonical_name)
                if info.is_dir():
                    continue
                if info.file_size > max_scan_bytes:
                    unscanned_findings.append(member_label)
                    continue
                try:
                    data = archive.read(info)
                except Exception:
                    unscanned_findings.append(f"{member_label}:archive-read")
                    continue
                scan(member_label, data)
                nested = io.BytesIO(data)
                if zipfile.is_zipfile(nested):
                    nested.seek(0)
                    scan_archive(member_label, nested, depth + 1)
    except Exception:
        unscanned_findings.append(f"{label}:invalid-archive")

def scan_file(label, path):
    try:
        size = path.stat().st_size
        if size > max_scan_bytes:
            unscanned_findings.append(label)
            return
        if zipfile.is_zipfile(path):
            scan_archive(label, path)
            return
        scan(label, path.read_bytes())
    except Exception:
        unscanned_findings.append(f"{label}:file-read")

for raw in spec:
    path = Path(raw)
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_symlink():
                unscanned_findings.append(f"{path.name}/{item.relative_to(path).as_posix()}:symlink")
                continue
            if not item.is_file():
                continue
            label = f"{path.name}/{item.relative_to(path).as_posix()}"
            scan_file(label, item)
    elif path.is_file():
        scan_file(path.name, path)
    else:
        unscanned_findings.append(f"{path.name or str(path)}:missing")

print(json.dumps({
    "privateKeyFindings": sorted(set(private_findings)),
    "ephemeralKeyPathFindings": sorted(set(ephemeral_key_path_findings)),
    "tokenFindings": sorted(set(token_findings)),
    "sourcePathFindings": sorted(set(source_findings)),
    "paidPayloadFindings": sorted(set(paid_findings)),
    "unscannedFindings": sorted(set(unscanned_findings)),
}, separators=(",", ":")))
`;
  try {
    return await runPythonJson(code, [specPath], {
      env: {
        ...process.env,
        VRCFORGE_PROBE_EXACT_SECRETS: JSON.stringify([
          appSessionToken,
          agentGatewayToken,
          agentApprovalToken,
          negativeTestSecret,
          privateUrlSentinel,
          ephemeralSigningKeyPath,
        ]),
        VRCFORGE_PROBE_PRIVATE_ROOTS: JSON.stringify([
          repoRoot,
          evidenceRoot,
          projectRoot,
          userDataRoot,
          packageFixtureRoot,
          pathToSkillRoot,
        ]),
        VRCFORGE_PROBE_PAID_MARKER: paidPayloadSentinel,
        VRCFORGE_PROBE_EPHEMERAL_KEY_PATH: ephemeralSigningKeyPath,
      },
    });
  } finally {
    await unlink(specPath).catch(() => {});
  }
}

function privacyUnscannedCategoryCounts(findings) {
  const counts = {
    missing: 0,
    fileRead: 0,
    invalidArchive: 0,
    archiveLimit: 0,
    unsafeArchiveName: 0,
    duplicateArchiveName: 0,
    symlink: 0,
    oversized: 0,
    other: 0,
  };
  for (const raw of findings || []) {
    const value = String(raw || "");
    if (value.endsWith(":missing")) counts.missing += 1;
    else if (value.endsWith(":file-read") || value.endsWith(":archive-read")) counts.fileRead += 1;
    else if (value.endsWith(":invalid-archive")) counts.invalidArchive += 1;
    else if (
      value.endsWith(":archive-depth")
      || value.endsWith(":archive-entry-count")
      || value.endsWith(":archive-uncompressed-size")
    ) counts.archiveLimit += 1;
    else if (value.endsWith(":unsafe-name")) counts.unsafeArchiveName += 1;
    else if (value.endsWith(":duplicate-name")) counts.duplicateArchiveName += 1;
    else if (value.endsWith(":symlink")) counts.symlink += 1;
    else if (!value.includes(":") || /\/member-\d+$/.test(value)) counts.oversized += 1;
    else counts.other += 1;
  }
  return counts;
}

function runtimeStateSourcePathFindingsAreClassified(scan) {
  const expectedStateFiles = new Set([
    "agent_gateway/approvals.jsonl",
    "agent_gateway/runtime-runs.jsonl",
  ]);
  return (scan?.sourcePathFindings || []).every((label) => expectedStateFiles.has(String(label)));
}

function applyPostShutdownPrivacyScans(
  report,
  signed,
  artifactScan,
  diagnosticLogScan,
  runtimeStateScan,
  persistentRuntimeScan,
  controlledKeyPathAbsent,
) {
  report.privacy.privateKeyAbsent = signed.privateKeyDeleted === true
    && signed.builderStoreDeleted === true
    && artifactScan.privateKeyFindings.length === 0
    && artifactScan.ephemeralKeyPathFindings.length === 0
    && persistentRuntimeScan.privateKeyFindings.length === 0
    && persistentRuntimeScan.ephemeralKeyPathFindings.length === 0;
  report.privacy.tokensAbsent = artifactScan.tokenFindings.length === 0;
  report.privacy.sourcePathsAbsent = artifactScan.sourcePathFindings.length === 0;
  report.privacy.paidPayloadAbsent = artifactScan.paidPayloadFindings.length === 0;
  report.privacy.artifactsFullyScanned = artifactScan.unscannedFindings.length === 0;
  report.privacy.rawDiagnosticSecretsAbsent = diagnosticLogScan.privateKeyFindings.length === 0
    && diagnosticLogScan.ephemeralKeyPathFindings.length === 0
    && diagnosticLogScan.tokenFindings.length === 0
    && diagnosticLogScan.paidPayloadFindings.length === 0;
  report.privacy.rawDiagnosticSourcePathsAbsent = diagnosticLogScan.sourcePathFindings.length === 0;
  report.privacy.rawDiagnosticsFullyScanned = diagnosticLogScan.unscannedFindings.length === 0;
  report.privacy.runtimeStateSecretsAbsent = runtimeStateScan.privateKeyFindings.length === 0
    && runtimeStateScan.ephemeralKeyPathFindings.length === 0
    && runtimeStateScan.tokenFindings.length === 0
    && runtimeStateScan.paidPayloadFindings.length === 0;
  report.privacy.runtimeStateFullyScanned = runtimeStateScan.unscannedFindings.length === 0;
  report.privacy.runtimeStateSourcePathsClassified = runtimeStateSourcePathFindingsAreClassified(runtimeStateScan);
  if (report.apkSemanticMatrix?.privateKeyBoundary) {
    report.apkSemanticMatrix.privateKeyBoundary.controlledKeyPathAbsent = signed.privateKeyDeleted === true
      && controlledKeyPathAbsent === true;
    report.apkSemanticMatrix.privateKeyBoundary.persistentRuntimeScanClear =
      persistentRuntimeScan.privateKeyFindings.length === 0
      && persistentRuntimeScan.ephemeralKeyPathFindings.length === 0
      && persistentRuntimeScan.unscannedFindings.length === 0;
    report.apkSemanticMatrix.privateKeyBoundary.supportBundleScanClear = report.privacy.supportBundleClean === true;
    report.apkSemanticMatrix.privateKeyBoundary.reportScanClear = !JSON.stringify(report).includes("-----BEGIN PRIVATE KEY-----")
      && !JSON.stringify(report).includes(ephemeralSigningKeyPath);
  }
  report.privacy.scan = {
    privateKeyFindingCount: artifactScan.privateKeyFindings.length,
    ephemeralKeyPathFindingCount: artifactScan.ephemeralKeyPathFindings.length,
    tokenFindingCount: artifactScan.tokenFindings.length,
    sourcePathFindingCount: artifactScan.sourcePathFindings.length,
    paidPayloadFindingCount: artifactScan.paidPayloadFindings.length,
    unscannedFindingCount: artifactScan.unscannedFindings.length,
    diagnosticLogPrivateKeyFindingCount: diagnosticLogScan.privateKeyFindings.length,
    diagnosticLogEphemeralKeyPathFindingCount: diagnosticLogScan.ephemeralKeyPathFindings.length,
    diagnosticLogTokenFindingCount: diagnosticLogScan.tokenFindings.length,
    diagnosticLogSourcePathFindingCount: diagnosticLogScan.sourcePathFindings.length,
    diagnosticLogPaidPayloadFindingCount: diagnosticLogScan.paidPayloadFindings.length,
    diagnosticLogUnscannedFindingCount: diagnosticLogScan.unscannedFindings.length,
    runtimeStatePrivateKeyFindingCount: runtimeStateScan.privateKeyFindings.length,
    runtimeStateEphemeralKeyPathFindingCount: runtimeStateScan.ephemeralKeyPathFindings.length,
    runtimeStateTokenFindingCount: runtimeStateScan.tokenFindings.length,
    runtimeStateSourcePathFindingCount: runtimeStateScan.sourcePathFindings.length,
    runtimeStatePaidPayloadFindingCount: runtimeStateScan.paidPayloadFindings.length,
    runtimeStateUnscannedFindingCount: runtimeStateScan.unscannedFindings.length,
    persistentPrivateKeyFindingCount: persistentRuntimeScan.privateKeyFindings.length,
    persistentEphemeralKeyPathFindingCount: persistentRuntimeScan.ephemeralKeyPathFindings.length,
    persistentPrivateKeyUnscannedFindingCount: persistentRuntimeScan.unscannedFindings.length,
  };
  report.diagnostics.privacy = {
    scanPhase: "post-graceful-shutdown",
    artifactUnscannedCategories: privacyUnscannedCategoryCounts(artifactScan.unscannedFindings),
    diagnosticLogUnscannedCategories: privacyUnscannedCategoryCounts(diagnosticLogScan.unscannedFindings),
    runtimeStateUnscannedCategories: privacyUnscannedCategoryCounts(runtimeStateScan.unscannedFindings),
    persistentRuntimeUnscannedCategories: privacyUnscannedCategoryCounts(
      persistentRuntimeScan.unscannedFindings,
    ),
    runtimeStateSourcePathsClassified: report.privacy.runtimeStateSourcePathsClassified,
  };
}

async function stagingDirectoriesClear() {
  const candidates = [
    resolve(userDataRoot, "skill-packages", ".staging"),
    resolve(userDataRoot, "skill-packages", ".uninstall-staging"),
    resolve(userDataRoot, "skills", ".package-projection-staging"),
  ];
  for (const candidate of candidates) {
    try {
      const entries = await readdir(candidate);
      if (entries.length > 0) return false;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  return true;
}

async function uninstallPackages(report, cdp, records) {
  for (let index = 0; index < records.length; index += 1) {
    const record = records[index];
    const result = index % 2 === 0
      ? await tauriInvoke(cdp, "uninstall_skill_package", {
        request: { id: record.id, body: { removeProjectedSkill: true }, timeoutMs: 120000 },
      })
      : await appApi(`/api/app/skill-packages/${encodeURIComponent(record.id)}`, {
        method: "DELETE",
        body: { removeProjectedSkill: true },
      });
    record.cleanupUninstalled = result?.ok === true && Boolean(result?.uninstalled);
    if (!record.cleanupUninstalled) addAssertion(report, `package uninstall failed for ${record.id}`);
  }
  const [restList, tauriList, skills] = await Promise.all([
    appApi("/api/app/skill-packages"),
    tauriInvoke(cdp, "fetch_skill_packages", {}),
    agentApi("/api/agent/skills"),
  ]);
  const remainingRest = new Set((restList?.installed || []).map((item) => String(item.id || item.manifest?.id || "")));
  const remainingTauri = new Set((tauriList?.installed || []).map((item) => String(item.id || item.manifest?.id || "")));
  const projected = new Set((skills?.skills || []).map((item) => String(item.name || "")));
  const registrySkills = restList?.registry?.skills && typeof restList.registry.skills === "object"
    ? restList.registry.skills
    : {};
  report.cleanup.installedPackagesClear = records.every((record) =>
    !remainingRest.has(record.id) && !remainingTauri.has(record.id));
  report.cleanup.projectedSkillsClear = records.every((record) => !projected.has(record.skillName));
  report.cleanup.registryEntriesClear = records.every((record) => !Object.hasOwn(registrySkills, record.id));
  report.cleanup.packageFilesClear = (await Promise.all(records.map((record) =>
    pathExists(resolve(userDataRoot, "skill-packages", record.id))))).every((exists) => !exists);
  report.cleanup.projectedFilesClear = (await Promise.all(records.map((record) =>
    pathExists(resolve(userDataRoot, "skills", record.skillName))))).every((exists) => !exists);
  report.cleanup.stagingClear = await stagingDirectoriesClear();
  const cleanupFilesystem = await packageFilesystemState();
  const filesystemResiduals = cleanupFilesystemResiduals(packageFilesystemBaseline, cleanupFilesystem);
  report.cleanup.filesystemResidueClear = filesystemResiduals.length === 0;
  if (filesystemResiduals.length > 0) {
    addAssertion(report, `package cleanup left unexpected filesystem residue: ${filesystemResiduals.join(", ")}`);
  }
  report.cleanup.rejectedOutputsClear = [
    "existingSourceRejected",
    "existingPackageRejected",
    "parentTraversalRejected",
    "secretRejectedNoOutput",
    "paidPayloadRejectedNoOutput",
  ].every((key) => report.pathToSkill[key] === true);
  report.governance.uninstallRemovedState = report.cleanup.installedPackagesClear
    && report.cleanup.projectedSkillsClear
    && report.cleanup.registryEntriesClear
    && report.cleanup.packageFilesClear
    && report.cleanup.projectedFilesClear
    && report.cleanup.filesystemResidueClear
    && records.every((record) => record.cleanupUninstalled);
}

function applyProcessBoundaryReport(report, snapshot) {
  const trackedLive = (snapshot?.trackedProcesses || []).length;
  const packagedLive = (snapshot?.packagedProcesses || []).length;
  const trackedGenerationCountEver = trackedProcessIdentities.size;
  const trackedUniquePidCountEver = new Set(
    [...trackedProcessIdentities.values()].map((identity) => identity.pid),
  ).size;
  const descendantGenerationCountEver = Math.max(
    0,
    trackedGenerationCountEver - (trackedRootObserved ? 1 : 0),
  );
  report.cleanup.processesClear = trackedLive === 0 && packagedLive === 0;
  report.cleanup.trackedTreeClear = trackedLive === 0;
  report.cleanup.portsClear = snapshot?.portQuerySucceeded === true
    && (snapshot?.ports || []).length === 0;
  report.cleanup.processTrackingComplete = trackedRootObserved
    && trackedGenerationCountEver > 1
    && trackedUniquePidCountEver > 1
    && descendantGenerationCountEver > 0
    && processStartWatcherVerified
    && processStartWatcherSettleVerified
    && processStartWatcherSettleMs >= 5000
    && snapshot?.portQuerySucceeded === true
    && processTrackingErrorCount === 0;
  report.processBoundary = {
    identityBinding: "pid+path+dmtf-creation+start-event-generation",
    creationDateFormat: "dmtf",
    rootTracked: trackedRootObserved,
    trackedProcessCountEver: trackedGenerationCountEver,
    descendantProcessCountEver: descendantGenerationCountEver,
    trackedGenerationCountEver,
    trackedUniquePidCountEver,
    descendantGenerationCountEver,
    processNamesEver: [...trackedProcessNamesEver].sort(),
    samplingErrorCount: processTrackingErrorCount,
    startEventWatcherVerified: processStartWatcherVerified,
    startEventWatcherMode: processStartWatcherMode,
    watcherSettleVerified: processStartWatcherSettleVerified,
    watcherSettleMs: processStartWatcherSettleMs,
    portQuerySucceeded: snapshot?.portQuerySucceeded === true,
    trackedTreeClear: trackedLive === 0,
  };
}

async function attemptFailureApiCleanup(report, cdp) {
  const cleanup = report.failureCleanup;
  cleanup.attempted = true;
  try {
    const cleanupPackageIds = [...requiredPackageIds, apkSemanticPackageId];
    const before = await appApi("/api/app/skill-packages", { timeoutMs: 15000 });
    cleanup.apiReachable = true;
    const requiredInstalled = (before?.installed || []).filter((item) =>
      cleanupPackageIds.includes(String(item.id || item.manifest?.id || "")));
    const installedIds = new Set(requiredInstalled
      .map((item) => String(item.id || item.manifest?.id || "")));
    const targetSkillNames = new Set(requiredPackageSkillNames.values());
    targetSkillNames.add(apkSemanticSkillName);
    for (const item of requiredInstalled) {
      const skillName = String(item.skillName || item.manifest?.skill_name || item.manifest?.skillName || "");
      if (skillName) targetSkillNames.add(skillName);
    }
    for (const packageId of installedIds) {
      const skillName = requiredPackageSkillNames.get(packageId);
      if (skillName) targetSkillNames.add(skillName);
    }
    cleanup.requiredPackagesFound = installedIds.size;
    for (const packageId of installedIds) {
      const response = await appApiRaw(`/api/app/skill-packages/${encodeURIComponent(packageId)}`, {
        method: "DELETE",
        body: { removeProjectedSkill: true },
        timeoutMs: 30000,
      });
      if (response.ok && response.payload?.ok === true) cleanup.apiUninstallCount += 1;
      else cleanup.apiErrorCount += 1;
    }
    const [restAfter, skillsAfter] = await Promise.all([
      appApi("/api/app/skill-packages", { timeoutMs: 15000 }),
      agentApi("/api/agent/skills", { timeoutMs: 15000 }),
    ]);
    const remainingRest = new Set((restAfter?.installed || [])
      .map((item) => String(item.id || item.manifest?.id || "")));
    const remainingSkills = new Set((skillsAfter?.skills || [])
      .map((item) => String(item.name || "")));
    const remainingRegistry = restAfter?.registry?.skills && typeof restAfter.registry.skills === "object"
      ? restAfter.registry.skills
      : {};
    cleanup.restClear = cleanupPackageIds.every((id) => !remainingRest.has(id));
    cleanup.projectedClear = [...targetSkillNames].every((name) => !remainingSkills.has(name));
    cleanup.registryClear = cleanupPackageIds.every((id) => !Object.hasOwn(remainingRegistry, id));
    cleanup.packageFilesClear = (await Promise.all(cleanupPackageIds.map((id) =>
      pathExists(resolve(userDataRoot, "skill-packages", id))))).every((exists) => !exists);
    cleanup.projectedFilesClear = (await Promise.all([...requiredPackageSkillNames.values(), apkSemanticSkillName].map((name) =>
      pathExists(resolve(userDataRoot, "skills", name))))).every((exists) => !exists);
    cleanup.tauriClear = false;
    if (cdp) {
      const tauriAfter = await tauriInvoke(cdp, "fetch_skill_packages", {});
      const remainingTauri = new Set((tauriAfter?.installed || [])
        .map((item) => String(item.id || item.manifest?.id || "")));
      cleanup.tauriClear = cleanupPackageIds.every((id) => !remainingTauri.has(id));
    }
    cleanup.stagingClear = await stagingDirectoriesClear();
    cleanup.apiComplete = cleanup.restClear
      && cleanup.projectedClear
      && cleanup.registryClear
      && cleanup.packageFilesClear
      && cleanup.projectedFilesClear
      && cleanup.tauriClear
      && cleanup.stagingClear
      && cleanup.apiErrorCount === 0;
  } catch (error) {
    cleanup.apiErrorCount += 1;
    cleanup.lastError = sanitizeReportText(String(error?.message || error));
  }
}

function isolatedFailureCleanupTargets() {
  const expected = [
    resolve(userDataRoot, "skill-packages"),
    resolve(userDataRoot, "skills"),
  ];
  for (const target of expected) {
    const normalizedBoundary = normalizedPath(userDataRoot);
    const normalizedTarget = normalizedPath(target);
    if (
      normalizedTarget === normalizedBoundary
      || !normalizedTarget.startsWith(`${normalizedBoundary}/`)
    ) {
      throw new Error("Failure cleanup target escaped its isolated probe boundary.");
    }
  }
  return expected;
}

function isolatedPathToSkillCleanupTarget() {
  const normalizedBoundary = normalizedPath(evidenceRoot);
  const normalizedTarget = normalizedPath(pathToSkillRoot);
  if (
    normalizedTarget === normalizedBoundary
    || !normalizedTarget.startsWith(`${normalizedBoundary}/`)
  ) {
    throw new Error("Path-to-Skill cleanup target escaped the isolated evidence root.");
  }
  return pathToSkillRoot;
}

async function finalizeFailureCleanup(report, finalSnapshot) {
  const cleanup = report.failureCleanup;
  if (!cleanup.attempted) cleanup.attempted = true;
  const sensitiveTarget = isolatedPathToSkillCleanupTarget();
  try {
    await rm(sensitiveTarget, { recursive: true, force: true });
  } finally {
    cleanup.pathToSkillClear = !(await pathExists(sensitiveTarget).catch(() => true));
  }
  const processBoundaryClear = Boolean(finalSnapshot) && snapshotIsClear(finalSnapshot);
  if (processBoundaryClear) {
    const targets = isolatedFailureCleanupTargets();
    for (const target of targets) await rm(target, { recursive: true, force: true });
    cleanup.isolatedResidueRemoved = true;
    cleanup.filesystemClear = cleanup.pathToSkillClear
      && (await Promise.all(targets.map(async (target) => !(await pathExists(target))))).every(Boolean);
  } else {
    cleanup.filesystemClear = false;
  }
  cleanup.verifiedClear = processBoundaryClear
    && cleanup.isolatedResidueRemoved === true
    && cleanup.filesystemClear === true;
}

function publicPackageReport(records) {
  return records.map((record) => ({
    id: record.id,
    signatureVerified: record.signatureVerified === true,
    trusted: record.trusted === true,
    imported: record.imported === true,
    projected: record.projected === true,
    runtimeStatus: record.runtimeStatus,
    runtimeResultVerified: record.runtimeResultVerified === true,
    supportFilesVerified: record.supportFilesVerified === true,
    entrypointVerified: record.entrypointVerified === true,
    runtimeAuditVerified: record.runtimeAuditVerified === true,
    preflightSignatureVerified: record.preflightSignatureVerified === true,
    preflightSignerUntrusted: record.preflightSignerUntrusted === true,
    preflightUntrustedDefaultDisabled: record.preflightUntrustedDefaultDisabled === true,
    preflightDryRunNoWrite: record.preflightDryRunNoWrite === true,
    preflightStateUnchanged: record.preflightStateUnchanged === true,
    cleanupUninstalled: record.cleanupUninstalled === true,
    ...(record.slug === "material-preset-pack"
      ? {
        requestOnly: record.requestOnly === true,
        directTargetCalls: Number(record.directTargetCalls || 0),
      }
      : {}),
  }));
}

function sanitizeReportText(value) {
  let text = String(value || "");
  const replacements = [
    [appSessionToken, "<redacted-app-token>"],
    [agentGatewayToken, "<redacted-agent-token>"],
    [agentApprovalToken, "<redacted-approval-token>"],
    [paidPayloadSentinel, "<redacted-paid-payload-sentinel>"],
    [negativeTestSecret, "<redacted-negative-test-secret>"],
    [privateUrlSentinel, "<redacted-private-url-sentinel>"],
    [ephemeralSigningKeyPath, "<redacted-ephemeral-signing-key-path>"],
    [repoRoot, "<repo>"],
    [repoRoot.replaceAll("\\", "/"), "<repo>"],
    [evidenceRoot, "<evidence>"],
    [evidenceRoot.replaceAll("\\", "/"), "<evidence>"],
  ];
  for (const [needle, replacement] of replacements) {
    if (needle) text = text.split(needle).join(replacement);
  }
  return text
    .replace(/-----BEGIN(?: OPENSSH)? PRIVATE KEY-----[\s\S]*?-----END(?: OPENSSH)? PRIVATE KEY-----/g, "<redacted-private-key>")
    .replace(/file:\/\/\/[A-Za-z]:\/[^\s)\]}]+/gi, "<absolute-file-url>")
    .replace(/\b[A-Za-z]:[\\/][^\r\n\"']+/g, "<absolute-path>");
}

function safeError(error) {
  return sanitizeReportText(String(error?.stack || error));
}

function validateFinalContract(report) {
  if (
    report.payloadMatchesManifest !== true
    || report.launchSource !== "manifest-directory-portable-zip-extracted-to-isolated-evidence-root"
  ) {
    addAssertion(report, "tested packaged process was not bound to the manifest-directory portable ZIP");
  }
  if (
    report.releaseBinding?.portableManifestEntryUnique !== true
    || report.releaseBinding?.portableManifestPathSafe !== true
    || report.releaseBinding?.portableDigestVerified !== true
    || report.releaseBinding?.extractedExecutableVerified !== true
    || report.releaseBinding?.extractedBackendVerified !== true
    || report.releaseBinding?.executableLaunchLockVerified !== true
    || report.releaseBinding?.completionExecutableVerified !== true
    || report.releaseBinding?.executableLaunchLockWatcherMode !== "wmi-instance-creation-poll-100ms"
  ) {
    addAssertion(report, "manifest portable ZIP selection or exact executable/backend digest binding failed");
  }
  for (const key of [
    "authenticatedHealth",
    "appAuthMissingTokenRejected",
    "appAuthWrongTokenRejected",
    "portableMode",
    "versionMatches",
    "programDirMatchesExtraction",
    "isolatedDataPathsVerified",
    "listenerUnique",
    "listenerExecutableExact",
    "backendDigestVerified",
  ]) {
    if (report.runtimeBinding?.[key] !== true) addAssertion(report, `runtime binding gate failed: ${key}`);
  }
  if (
    report.runtimeBinding?.executableName !== "vrcforge_backend.exe"
    || !/^[0-9a-f]{64}$/.test(String(report.runtimeBinding?.backendSha256 || ""))
  ) {
    addAssertion(report, "runtime listener executable identity/digest evidence was incomplete");
  }
  if (
    report.fixtures?.sourceDigestVerified !== true
    || report.fixtures?.sourcePackageBytesVerified !== true
    || !/^[0-9a-f]{64}$/.test(String(report.fixtures?.sourceDigest || ""))
    || report.fixtures?.privateKeyPersisted !== false
    || report.fixtures?.builderStorePersisted !== false
  ) {
    addAssertion(report, "fixture source digest or ephemeral signing cleanup evidence was incomplete");
  }
  const strictPolicy = report.releaseBinding?.buildPolicy;
  if (!allowUnpushed && (
    report.strictReleaseBinding !== true
    || report.releaseBinding?.strict !== true
    || report.releaseBinding?.strictBuildPolicy !== true
    || report.releaseBinding?.worktreeClean !== true
    || report.releaseBinding?.worktreeCleanAfterFixtureBuild !== true
    || report.releaseBinding?.executableLaunchLockVerified !== true
    || report.releaseBinding?.completionExecutableVerified !== true
    || report.releaseBinding?.worktreeCleanAtCompletion !== true
    || report.releaseBinding?.completionHeadMatches !== true
    || report.releaseBinding?.completionOriginMainMatches !== true
    || report.releaseBinding?.completionVersionMatches !== true
    || report.releaseBinding?.headEqualsOriginMain !== true
    || report.releaseBinding?.manifestEqualsHead !== true
    || report.releaseBinding?.extractedExecutableVerified !== true
    || report.releaseBinding?.extractedBackendVerified !== true
    || report.fixtures?.sourceMode !== "immutable-git-object-snapshot"
    || report.fixtures?.sourceCommit !== report.manifestCommit
    || strictPolicy?.mode !== "strict"
    || strictPolicy?.releaseEligible !== true
    || strictPolicy?.allowDirty !== false
    || strictPolicy?.allowUnpushed !== false
    || strictPolicy?.allowVersionMismatch !== false
  )) {
    addAssertion(report, "strict manifest commit/build-policy/extracted-payload binding was incomplete");
  }
  if (allowUnpushed && (report.strictReleaseBinding !== false || report.releaseBinding?.strict !== false)) {
    addAssertion(report, "allow-unpushed preacceptance was incorrectly marked as strict release evidence");
  }
  const requiredPackageSet = new Set(report.packages.map((item) => item.id));
  for (const packageId of requiredPackageIds) {
    if (!requiredPackageSet.has(packageId)) addAssertion(report, `report omitted required package ${packageId}`);
  }
  for (const item of report.packages) {
    for (const key of [
      "signatureVerified",
      "trusted",
      "imported",
      "projected",
      "runtimeResultVerified",
      "supportFilesVerified",
      "entrypointVerified",
      "runtimeAuditVerified",
      "preflightSignatureVerified",
      "preflightSignerUntrusted",
      "preflightUntrustedDefaultDisabled",
      "preflightDryRunNoWrite",
      "preflightStateUnchanged",
      "cleanupUninstalled",
    ]) {
      if (item[key] !== true) addAssertion(report, `${item.id} package gate failed: ${key}`);
    }
    if (item.id === "community.examples.material-preset-pack") {
      if (
        item.runtimeStatus !== "loaded"
        || item.requestOnly !== true
        || item.supportFilesVerified !== true
        || item.directTargetCalls !== 0
      ) {
        addAssertion(report, "material package final request-only contract failed");
      }
    } else if (item.runtimeStatus !== "executed") {
      addAssertion(report, `${item.id} final runtime status was not executed`);
    }
  }
  const matrix = report.apkSemanticMatrix || {};
  const matrixRequiredTruePaths = [
    "packagedExport.userSkillCreatedViaTauri",
    "packagedExport.userSkillDeletedViaTauri",
    "packagedExport.exportedViaTauri",
    "packagedExport.releaseExportRequested",
    "packagedExport.responseSignatureStatusSigned",
    "packagedExport.responseSignerFingerprintMatches",
    "packagedExport.responseLockDigestMatches",
    "packagedExport.responseManifestIdentityMatches",
    "packagedExport.outputCreated",
    "packagedExport.archiveIndependentlyVerified",
    "packagedExport.archivePublicKeyMatchesGenerated",
    "packagedExport.controlledTemporaryKeyUsed",
    "packagedExport.privateKeyDeletedImmediatelyAfterExport",
    "packagedExport.privateKeyInputExcludedFromResponse",
    "fixtureBuilder.generatedUpdate",
    "fixtureBuilder.didNotSupplyInstalledBase",
    "fixtureBuilder.signerFingerprintMatchesPackagedExport",
    "authorIdentity.publicKeyMatchesFingerprint",
    "archive.signedPayloadIdentityImmutable",
    "archive.signatureCoversCanonicalLock",
    "archive.lockCoversCanonicalManifest",
    "archive.lockCoversAllPayloadDigests",
    "archive.canonicalManifestVerified",
    "archive.canonicalLockVerified",
    "archive.privateKeyMaterialAbsent",
    "preInstallVerification.accepted",
    "preInstallVerification.packageIdVerified",
    "preInstallVerification.authorIdVerified",
    "preInstallVerification.semverVerified",
    "preInstallVerification.signatureVerified",
    "preInstallVerification.archiveDigestVerified",
    "preInstallVerification.payloadDigestVerified",
    "preInstallVerification.updateActionNew",
    "preInstallVerification.dryRunNoWrite",
    "preInstallVerification.stateUnchanged",
    "preInstallVerification.temporaryExtractionClear",
    "preInstallVerification.pathSafetyVerified",
    "preInstallVerification.duplicatePathRejected",
    "preInstallVerification.invalidPackageIdRejected",
    "installProvenance.installed",
    "installProvenance.projected",
    "installProvenance.installActionNew",
    "installProvenance.verified",
    "versionMonotonicity.semverMonotonic",
    "versionMonotonicity.preflightRecognizedMonotonicUpdate",
    "signedUpdate.samePackageId",
    "signedUpdate.sameAuthorId",
    "signedUpdate.samePublicKeyFingerprint",
    "signedUpdate.signatureVerified",
    "signedUpdate.preflightNoWrite",
    "signedUpdate.imported",
    "signedUpdate.importActionUpdate",
    "signedUpdate.priorVersionRetainedUntilUninstall",
    "signedUpdate.verified",
    "packageIdContinuity.updateLookupUsesExactManifestId",
    "packageIdContinuity.differentValidIdTreatedAsDistinctPackage",
    "packageIdContinuity.differentValidIdNotClaimedAsUpdate",
    "packageIdContinuity.differentValidIdNotImported",
    "packageIdContinuity.installedPackagePreserved",
    "failedUpdateAtomicity.allRejected",
    "failedUpdateAtomicity.allErrorCategoriesVerified",
    "failedUpdateAtomicity.oldInstallPreservedAfterEveryFailure",
    "failedUpdateAtomicity.temporaryExtractionClearAfterEveryFailure",
    "failedUpdateAtomicity.stagingClearAfterEveryFailure",
    "failedUpdateAtomicity.zipSlipNeverEscaped",
    "uninstall.accepted",
    "uninstall.bothVersionsRemoved",
    "uninstall.restClear",
    "uninstall.tauriClear",
    "uninstall.registryClear",
    "uninstall.installedMetadataClear",
    "uninstall.projectionClear",
    "uninstall.packageFilesystemClear",
    "uninstall.projectedFilesystemClear",
    "uninstall.stagingClear",
    "privateKeyBoundary.generatedEphemerally",
    "privateKeyBoundary.controlledTemporaryKeyUsed",
    "privateKeyBoundary.privateKeyDeletedImmediatelyAfterExport",
    "privateKeyBoundary.controlledKeyPathAbsent",
    "privateKeyBoundary.vskArchivesExcludePrivateKeyMaterial",
    "privateKeyBoundary.fixtureArchivesDeleted",
    "privateKeyBoundary.persistentRuntimeScanClear",
    "privateKeyBoundary.supportBundleScanClear",
    "privateKeyBoundary.reportScanClear",
  ];
  for (const path of matrixRequiredTruePaths) {
    const value = path.split(".").reduce((current, key) => current?.[key], matrix);
    if (value !== true) addAssertion(report, `apkSemanticMatrix final gate failed: ${path}`);
  }
  if (
    matrix.semanticModel !== "Android APK-style immutable signed update semantics mapped to VRCForge .vsk"
    || matrix.packageExtension !== ".vsk"
    || matrix.packageId !== apkSemanticPackageId
    || matrix.authorIdentity?.authorId !== apkSemanticAuthorId
    || matrix.authorIdentity?.authorIdSource !== "manifest.author"
    || matrix.archive?.signatureCoverage !== "canonical skill.lock.json, not raw ZIP container bytes"
    || matrix.archive?.apkV2WholeArchiveEquivalence !== "not claimed; APK v2+ also invalidates ZIP metadata changes"
    || matrix.versionMonotonicity?.androidVersionCodeEquivalent
      !== "VSK SemVer monotonic comparison (no versionCode field exists)"
    || matrix.fixtureBuilder?.role !== "same-signer update plus adversarial negative fixtures only"
    || Number(matrix.fixtureBuilder?.generatedNegativeCount || 0) !== 12
    || matrix.privateKeyBoundary?.storageMode
      !== "controlled temporary PKCS8 PEM path used only by packaged Tauri release export"
    || matrix.privateKeyBoundary?.privateKeyPersistedAfterExport !== false
    || matrix.privateKeyBoundary?.privateKeyMaterialReturnedToProbe !== false
    || matrix.boundaries?.apkCertificateRotationLineage !== "not implemented and not claimed for 1.3.0"
    || Number(matrix.failedUpdateAtomicity?.rejectionCount || 0) !== 12
    || Object.keys(matrix.negativeRejections || {}).length !== 12
    || Object.values(matrix.negativeRejections || {}).some((item) =>
      !apkSemanticNegativeEvidenceComplete(item))
  ) {
    addAssertion(report, "APK-semantic VSK identity/signature/update boundary evidence was incomplete");
  }
  for (const [groupName, group] of Object.entries({
    pathToSkill: Object.fromEntries(
      Object.entries(report.pathToSkill).filter(([key]) => key !== "recipes"),
    ),
    recipes: report.pathToSkill.recipes,
    governance: report.governance,
    ui: Object.fromEntries(Object.entries(report.ui).filter(([key]) => key !== "auditRows")),
    privacy: Object.fromEntries(Object.entries(report.privacy).filter(([key]) => key !== "scan")),
    cleanup: report.cleanup,
  })) {
    for (const [key, value] of Object.entries(group)) {
      if (value !== true) addAssertion(report, `${groupName} final gate failed: ${key}`);
    }
  }
  const requiredTransports = new Set([
    "packaged-webview-dom",
    "packaged-webview-tauri-ipc",
    "authenticated-loopback-rest",
  ]);
  const transports = new Set(report.transports);
  for (const transport of requiredTransports) {
    if (!transports.has(transport)) addAssertion(report, `required transport was not recorded: ${transport}`);
  }
  if (
    report.processBoundary?.identityBinding !== "pid+path+dmtf-creation+start-event-generation"
    || report.processBoundary?.creationDateFormat !== "dmtf"
    || report.processBoundary?.rootTracked !== true
    || Number(report.processBoundary?.trackedProcessCountEver || 0) < 2
    || Number(report.processBoundary?.descendantProcessCountEver || 0) < 1
    || Number(report.processBoundary?.trackedGenerationCountEver || 0) < 2
    || Number(report.processBoundary?.trackedUniquePidCountEver || 0) < 2
    || Number(report.processBoundary?.descendantGenerationCountEver || 0) < 1
    || Number(report.processBoundary?.trackedGenerationCountEver || 0)
      < Number(report.processBoundary?.trackedUniquePidCountEver || 0)
    || report.processBoundary?.startEventWatcherVerified !== true
    || report.processBoundary?.startEventWatcherMode !== "wmi-instance-creation-poll-100ms"
    || report.processBoundary?.watcherSettleVerified !== true
    || !Number.isInteger(report.processBoundary?.watcherSettleMs)
    || report.processBoundary.watcherSettleMs < 5000
    || report.processBoundary?.portQuerySucceeded !== true
    || report.processBoundary?.trackedTreeClear !== true
    || Number(report.processBoundary?.samplingErrorCount || 0) !== 0
  ) {
    addAssertion(report, "packaged root and transitive descendant process-tree evidence was incomplete");
  }
}

async function writeSanitizedReport(report) {
  const serialized = sanitizeReportText(`${JSON.stringify(report, null, 2)}\n`);
  await writeFile(reportPath, serialized, "utf8");
}

function runSelfTest() {
  const commit = "a".repeat(40);
  const strictPolicy = {
    buildPolicy: {
      mode: "strict",
      releaseEligible: true,
      allowDirty: false,
      allowUnpushed: false,
      allowVersionMismatch: false,
    },
  };
  const localPolicy = {
    buildPolicy: {
      mode: "local-acceptance",
      releaseEligible: false,
      allowDirty: true,
      allowUnpushed: true,
      allowVersionMismatch: false,
    },
  };
  const portableName = "VRCForge_Windows_x64_1.3.0.zip";
  const artifact = { name: portableName, sha256: "c".repeat(64) };
  const rejectsArtifact = (manifest) => {
    try {
      selectManifestPortableArtifact(manifest, portableName);
      return false;
    } catch {
      return true;
    }
  };
  const rejectsPayloadEntries = (names) => {
    try {
      selectPortablePayloadEntries(names);
      return false;
    } catch {
      return true;
    }
  };
  const processIdentityChecks = (() => {
    const dmtf = (sequence) => `20260717000000.${String(sequence).padStart(6, "0")}+540`;
    trackedProcessIdentities.clear();
    observedProcessStartEvents.clear();
    observedProcessStartEventsBySequence.clear();
    trackedRootObserved = false;
    trackedRootIdentityKey = "";
    const parent = {
      pid: 100,
      parentPid: 1,
      name: "vrcforge_backend.exe",
      path: "C:/probe/backend.exe",
      creationDate: dmtf(1),
      startEventSequence: 10,
    };
    const parentStart = { pid: 100, parentPid: 1, name: parent.name, creationDate: parent.creationDate, sequence: 10 };
    const child = {
      pid: 101,
      parentPid: 100,
      name: "child.exe",
      path: "C:/other/child.exe",
      creationDate: dmtf(2),
      startEventSequence: 0,
    };
    const childStart = { pid: 101, parentPid: 100, name: child.name, creationDate: child.creationDate, sequence: 11 };
    const reusedParent = { pid: 100, parentPid: 2, name: "unrelated.exe", creationDate: dmtf(3), sequence: 12 };
    storeTrackedProcessIdentity(parent);
    observedProcessStartEvents.set(child.pid, childStart);
    observedProcessStartEvents.set(parent.pid, reusedParent);
    for (const event of [parentStart, childStart, reusedParent]) {
      observedProcessStartEventsBySequence.set(event.sequence, event);
    }
    const historicalParentGenerationPreserved = observedStartChainReachesTracked(child, new Map()) === true;

    trackedProcessIdentities.clear();
    observedProcessStartEvents.clear();
    observedProcessStartEventsBySequence.clear();
    const oldTrackedParent = {
      ...parent,
      pid: 150,
      creationDate: dmtf(4),
      startEventSequence: 10,
    };
    const unrelatedReusedParent = {
      pid: oldTrackedParent.pid,
      parentPid: 2,
      name: "unrelated.exe",
      creationDate: dmtf(5),
      sequence: 12,
    };
    const unrelatedChild = {
      ...child,
      pid: 151,
      parentPid: oldTrackedParent.pid,
      creationDate: dmtf(6),
    };
    const oldTrackedStart = {
      pid: oldTrackedParent.pid,
      parentPid: oldTrackedParent.parentPid,
      name: oldTrackedParent.name,
      creationDate: oldTrackedParent.creationDate,
      sequence: oldTrackedParent.startEventSequence,
    };
    const unrelatedChildStart = {
      pid: unrelatedChild.pid,
      parentPid: unrelatedChild.parentPid,
      name: unrelatedChild.name,
      creationDate: unrelatedChild.creationDate,
      sequence: 13,
    };
    storeTrackedProcessIdentity(oldTrackedParent);
    for (const event of [oldTrackedStart, unrelatedReusedParent, unrelatedChildStart]) {
      observedProcessStartEvents.set(event.pid, event);
      observedProcessStartEventsBySequence.set(event.sequence, event);
    }
    const unrelatedReusedParentGenerationRejected = !observedStartChainReachesTracked(
      unrelatedChild,
      new Map(),
    );

    trackedProcessIdentities.clear();
    observedProcessStartEvents.clear();
    observedProcessStartEventsBySequence.clear();
    const liveParent = { ...parent, pid: 200, creationDate: dmtf(7), startEventSequence: 20 };
    const staleChild = { ...child, pid: 201, parentPid: 200, creationDate: dmtf(8) };
    const liveParentStart = { pid: 200, parentPid: 1, name: liveParent.name, creationDate: liveParent.creationDate, sequence: 20 };
    const staleChildStart = { pid: 201, parentPid: 200, name: staleChild.name, creationDate: staleChild.creationDate, sequence: 19 };
    storeTrackedProcessIdentity(liveParent);
    observedProcessStartEvents.set(liveParent.pid, liveParentStart);
    observedProcessStartEvents.set(staleChild.pid, staleChildStart);
    observedProcessStartEventsBySequence.set(liveParentStart.sequence, liveParentStart);
    observedProcessStartEventsBySequence.set(staleChildStart.sequence, staleChildStart);
    const liveParents = new Map([[liveParent.pid, liveParent]]);
    const reusedLiveParentDoesNotClaimOlderChild = !observedStartDirectlyFollowsTracked(staleChild, liveParents)
      && !observedStartChainReachesTracked(staleChild, liveParents);

    trackedProcessIdentities.clear();
    observedProcessStartEvents.clear();
    observedProcessStartEventsBySequence.clear();
    trackedRootObserved = false;
    trackedRootIdentityKey = "";
    trackedRootPid = 300;
    const root = {
      pid: trackedRootPid,
      parentPid: 1,
      name: "VRCForge.exe",
      path: exe,
      creationDate: dmtf(9),
      startEventSequence: 30,
    };
    const oldDescendant = {
      pid: 301,
      parentPid: trackedRootPid,
      name: "worker.exe",
      path: "C:/probe/worker.exe",
      creationDate: dmtf(10),
      startEventSequence: 31,
    };
    const reusedDescendant = {
      ...oldDescendant,
      creationDate: dmtf(11),
      startEventSequence: 32,
    };
    const rootStart = { pid: root.pid, parentPid: root.parentPid, name: root.name, creationDate: root.creationDate, sequence: 30 };
    const oldStart = { pid: oldDescendant.pid, parentPid: root.pid, name: oldDescendant.name, creationDate: oldDescendant.creationDate, sequence: 31 };
    const reusedStart = { pid: reusedDescendant.pid, parentPid: root.pid, name: reusedDescendant.name, creationDate: reusedDescendant.creationDate, sequence: 32 };
    storeTrackedProcessIdentity(root, { isRoot: true });
    storeTrackedProcessIdentity(oldDescendant);
    for (const event of [rootStart, oldStart]) {
      observedProcessStartEvents.set(event.pid, event);
      observedProcessStartEventsBySequence.set(event.sequence, event);
    }
    updateTrackedProcessTree([root, oldDescendant]);
    updateTrackedProcessTree([root]);
    observedProcessStartEvents.set(reusedStart.pid, reusedStart);
    observedProcessStartEventsBySequence.set(reusedStart.sequence, reusedStart);
    const reusedLive = updateTrackedProcessTree([root, reusedDescendant]);
    const reusedDescendantPidTracksNewGeneration = trackedIdentitiesForPid(reusedDescendant.pid).length === 2
      && reusedLive.some((item) => processIdentityMatches(item, reusedDescendant));
    const reusedDescendantPidBlocksClear = snapshotIsClear({
      portQuerySucceeded: true,
      packagedProcesses: [],
      trackedProcesses: reusedLive,
      ports: [],
    }) === false;
    const cleanupCandidates = buildTrackedCleanupCandidates(trackedRootPid);
    const cleanupUsesReusedDescendantGeneration = cleanupCandidates.some((item) =>
      item.pid === reusedDescendant.pid
      && item.creationDate === reusedDescendant.creationDate
      && item.startEventSequence === reusedDescendant.startEventSequence
      && item.isRoot === false);
    const missingExecutablePathIdentityRejected = !processIdentityMatches(
      { ...reusedDescendant, path: "" },
      reusedDescendant,
    );
    const nonDmtfGenerationRejected = !processIdentityMatches(
      { ...reusedDescendant, creationDate: "2026-07-17T00:00:00Z" },
      reusedDescendant,
    );
    return {
      historicalParentGenerationPreserved,
      unrelatedReusedParentGenerationRejected,
      reusedLiveParentDoesNotClaimOlderChild,
      reusedDescendantPidTracksNewGeneration,
      reusedDescendantPidBlocksClear,
      cleanupUsesReusedDescendantGeneration,
      missingExecutablePathIdentityRejected,
      nonDmtfGenerationRejected,
    };
  })();
  const lockDigest = "d".repeat(64);
  const nonElevatedWatcherReady = {
    ok: true,
    sha256: lockDigest,
    processStartWatcher: true,
    processStartWatcherMode: "wmi-instance-creation-poll-100ms",
    settleWindowMs: 5000,
  };
  const completeNegativeEvidence = {
    rejected: true,
    httpStatus: 400,
    errorCategoryVerified: true,
    installedStatePreserved: true,
    temporaryExtractionClear: true,
    stagingClear: true,
    zipSlipOutsideAbsent: true,
  };
  const semanticAuditRows = expectedImportedAuditGovernanceRows({
    audit: [
      {
        event: "skill_package_imported",
        skill_id: "community.example.first",
        version: "1.0.0",
        signature_status: "attested-value",
        risk_level: "custom-risk-value",
        signer_fingerprint: "a".repeat(64),
      },
      { event: "skill_package_enabled", skill_id: "community.example.ignored" },
      {
        event: "skill_package_imported",
        skillId: "community.example.second",
        packageVersion: "2.0.0",
        signatureStatus: "verified-value",
        riskLevel: "policy-tier-value",
        signerFingerprint: "b".repeat(64),
      },
    ],
  });
  const checks = {
    strictPolicyAccepted: strictBuildPolicyFromManifest(strictPolicy).strict === true,
    missingPolicyRejected: strictBuildPolicyFromManifest({}).strict === false,
    localPolicyRejectedAfterTemporalDrift: strictBuildPolicyFromManifest(localPolicy).strict === false
      && releaseBindingIsStrict({
        localMode: false,
        strictBuildPolicy: strictBuildPolicyFromManifest(localPolicy).strict,
        headCommit: commit,
        originMainCommit: commit,
        manifestCommit: commit,
        worktreeClean: true,
      }) === false,
    strictCommitBindingAccepted: releaseBindingIsStrict({
      localMode: false,
      strictBuildPolicy: true,
      headCommit: commit,
      originMainCommit: commit,
      manifestCommit: commit,
      worktreeClean: true,
    }) === true,
    dirtyWorktreeRejectedForStrictBinding: releaseBindingIsStrict({
      localMode: false,
      strictBuildPolicy: true,
      headCommit: commit,
      originMainCommit: commit,
      manifestCommit: commit,
      worktreeClean: false,
    }) === false,
    unpushedModeNeverStrict: releaseBindingIsStrict({
      localMode: true,
      strictBuildPolicy: true,
      headCommit: commit,
      originMainCommit: commit,
      manifestCommit: commit,
      worktreeClean: true,
    }) === false,
    localDirtyModeNeverStrict: releaseBindingIsStrict({
      localMode: true,
      strictBuildPolicy: true,
      headCommit: commit,
      originMainCommit: commit,
      manifestCommit: commit,
      worktreeClean: false,
    }) === false,
    commitDriftRejected: releaseBindingIsStrict({
      localMode: false,
      strictBuildPolicy: true,
      headCommit: commit,
      originMainCommit: "b".repeat(40),
      manifestCommit: commit,
      worktreeClean: true,
    }) === false,
    uniquePortableArtifactAccepted:
      selectManifestPortableArtifact({ artifacts: [artifact] }, portableName) === artifact,
    duplicatePortableArtifactRejected: rejectsArtifact({ artifacts: [artifact, { ...artifact }] }),
    traversalPortablePathRejected: rejectsArtifact({
      artifacts: [{ ...artifact, path: `../${portableName}` }],
    }),
    absolutePortablePathRejected: rejectsArtifact({
      artifacts: [{ ...artifact, path: `C:\\release\\${portableName}` }],
    }),
    exactPortablePayloadEntriesAccepted: (() => {
      const selected = selectPortablePayloadEntries([
        "VRCForge.exe",
        "backend/vrcforge_backend.exe",
        "VERSION",
      ]);
      return selected["VRCForge.exe"] === "VRCForge.exe"
        && selected["backend/vrcforge_backend.exe"] === "backend/vrcforge_backend.exe";
    })(),
    missingBackendPayloadRejected: rejectsPayloadEntries(["VRCForge.exe"]),
    duplicateBackendPayloadRejected: rejectsPayloadEntries([
      "VRCForge.exe",
      "backend/vrcforge_backend.exe",
      "backend/vrcforge_backend.exe",
    ]),
    duplicateRootPayloadRejected: rejectsPayloadEntries([
      "VRCForge.exe",
      "VRCForge.exe",
      "backend/vrcforge_backend.exe",
    ]),
    miscasedPayloadRejected: rejectsPayloadEntries([
      "vrcforge.exe",
      "backend/vrcforge_backend.exe",
    ]),
    completeResponseSecretRejected: serializedValueExcludes(
      {
        workflow: { sourceSummary: { packagePath: "{{packagePath}}" } },
        sourceFiles: {},
        diagnostic: privateUrlSentinel,
      },
      [privateUrlSentinel],
    ) === false,
    auditGovernanceDerivedFromSemanticPayload: exactJsonValue(semanticAuditRows, [
      {
        identityValues: ["community.example.second"],
        version: "2.0.0",
        signatureStatus: "verified-value",
        riskLevel: "policy-tier-value",
        signerFingerprint: "b".repeat(64),
      },
      {
        identityValues: ["community.example.first"],
        version: "1.0.0",
        signatureStatus: "attested-value",
        riskLevel: "custom-risk-value",
        signerFingerprint: "a".repeat(64),
      },
    ]),
    privacyUnscannedCategoriesRemainSummaryOnly: exactJsonValue(
      privacyUnscannedCategoryCounts([
        "logs/current.log:file-read",
        "agent_gateway/backend-owner.lock:file-read",
        "archive/member-1:unsafe-name",
        "missing-root:missing",
      ]),
      {
        missing: 1,
        fileRead: 2,
        invalidArchive: 0,
        archiveLimit: 0,
        unsafeArchiveName: 1,
        duplicateArchiveName: 0,
        symlink: 0,
        oversized: 0,
        other: 0,
      },
    ),
    expectedRuntimeStatePathLabelsClassified: runtimeStateSourcePathFindingsAreClassified({
      sourcePathFindings: [
        "agent_gateway/approvals.jsonl",
        "agent_gateway/runtime-runs.jsonl",
      ],
    }) === true,
    unexpectedRuntimeStatePathLabelRejected: runtimeStateSourcePathFindingsAreClassified({
      sourcePathFindings: ["agent_gateway/desktop-bridges.jsonl"],
    }) === false,
    historicalParentGenerationPreserved: processIdentityChecks.historicalParentGenerationPreserved,
    unrelatedReusedParentGenerationRejected: processIdentityChecks.unrelatedReusedParentGenerationRejected,
    reusedLiveParentDoesNotClaimOlderChild: processIdentityChecks.reusedLiveParentDoesNotClaimOlderChild,
    reusedDescendantPidTracksNewGeneration: processIdentityChecks.reusedDescendantPidTracksNewGeneration,
    reusedDescendantPidBlocksClear: processIdentityChecks.reusedDescendantPidBlocksClear,
    cleanupUsesReusedDescendantGeneration: processIdentityChecks.cleanupUsesReusedDescendantGeneration,
    missingExecutablePathIdentityRejected: processIdentityChecks.missingExecutablePathIdentityRejected,
    nonDmtfGenerationRejected: processIdentityChecks.nonDmtfGenerationRejected,
    nonElevatedProcessWatcherAccepted:
      executableLaunchLockReadiness(nonElevatedWatcherReady, lockDigest, null).ok === true,
    executableDigestMismatchRejected:
      executableLaunchLockReadiness(nonElevatedWatcherReady, "e".repeat(64), null).reason === "digest-mismatch",
    elevatedTraceWatcherModeNotAccepted:
      executableLaunchLockReadiness({
        ...nonElevatedWatcherReady,
        processStartWatcherMode: "win32-process-start-trace",
      }, lockDigest, null).reason === "watcher-not-ready",
    watcherAccessFailureNotMisclassifiedAsDigestMismatch:
      executableLaunchLockReadiness({ ok: false, errorType: "ManagementException" }, lockDigest, null).reason
        === "watcher-failed",
    completeApkSemanticNegativeEvidenceAccepted:
      apkSemanticNegativeEvidenceComplete(completeNegativeEvidence) === true,
    incompleteApkSemanticNegativeEvidenceRejected:
      apkSemanticNegativeEvidenceComplete({
        ...completeNegativeEvidence,
        installedStatePreserved: false,
      }) === false,
  };
  const failed = Object.entries(checks).filter(([, passed]) => !passed).map(([name]) => name);
  console.log(JSON.stringify({
    schema: "vrcforge.packaged_skill_ecosystem_probe.self_test.v1",
    ok: failed.length === 0,
    checks,
    failed,
  }, null, 2));
  if (failed.length > 0) process.exitCode = 1;
}

async function main() {
  await mkdir(evidenceRoot, { recursive: true });
  const report = {
    schema: "vrcforge.packaged_skill_ecosystem_probe.v1",
    marker,
    generatedAt: new Date().toISOString(),
    version: "",
    strictReleaseBinding: false,
    releaseBindingMode: allowUnpushed ? "allow-unpushed-local-preacceptance" : "strict-release",
    launchSource: "",
    payloadMatchesManifest: false,
    manifestCommit: "",
    payloadZipSha256: "",
    runtimeBinding: {},
    transports: [
      "packaged-webview-dom",
      "packaged-webview-tauri-ipc",
      "authenticated-loopback-rest",
    ],
    fixtures: {
      signedExamples: "ephemeral real Ed25519 release packages",
      unityTransport: "isolated static fake CLI; packaged transport fixture only; no live Unity claim",
      directUnityWrites: false,
      privateKeyPersisted: false,
      builderStorePersisted: false,
      sourceDigest: "",
      sourceDigestVerified: false,
      sourcePackageBytesVerified: false,
    },
    packages: [],
    apkSemanticMatrix: {},
    diagnostics: {
      pathToSkill: {
        recipePreviews: {},
        writtenSource: {},
        privateUrl: {},
        contextualPrefill: {},
        confirmationInvalidation: {},
      },
      ui: {
        firstRunLanguageGate: {},
        auditGovernance: {},
      },
      privacy: {},
    },
    pathToSkill: {
      previewViaTauri: false,
      writeViaRest: false,
      writtenRecipeContract: false,
      writtenSourceMatchesResponse: false,
      exportedVsk: false,
      exportedVskPreflight: false,
      exportedVskContentMatches: false,
      positiveTemporaryResidueAbsent: false,
      genericEntrypointPreserved: false,
      negativeTemporaryResidueAbsent: false,
      negativeSensitiveResidueAbsent: false,
      secretRejected: false,
      secretRejectedNoOutput: false,
      existingSourceRejected: false,
      existingPackageRejected: false,
      parentTraversalRejected: false,
      privateUrlRedacted: false,
      confirmationInvalidated: false,
      paidPayloadRejected: false,
      paidPayloadRejectedNoOutput: false,
      recipes: {
        tttMaterialGroup: false,
        boothImportPreflight: false,
        parameterCompression: false,
        pcQuestUploadPass: false,
      },
    },
    governance: {
      disableBlockedExecution: false,
      reenableWorked: false,
      safeModeBlockedRiskyEnable: false,
      safeModeTargetsRestored: false,
      revokeBlockedExecution: false,
      revokedSignerRetrustRejected: false,
      blockDisabledProjection: false,
      uninstallRemovedState: false,
    },
    ui: {
      firstRunLanguageGateVisible: false,
      firstRunLanguageDefaultLegal: false,
      firstRunLanguageContinueApplied: false,
      skillsWorkspaceVisible: false,
      pathToSkillVisible: false,
      contextualPathToSkillPrefill: false,
      auditSearchVisible: false,
      auditSignerVisible: false,
      auditFilterExercised: false,
      auditGovernanceFieldsVisible: false,
      auditPaginationExercised: false,
      auditAriaLive: false,
    },
    privacy: {
      privateKeyAbsent: false,
      tokensAbsent: false,
      sourcePathsAbsent: false,
      paidPayloadAbsent: false,
      artifactsFullyScanned: false,
      rawDiagnosticSecretsAbsent: false,
      rawDiagnosticSourcePathsAbsent: false,
      rawDiagnosticsFullyScanned: false,
      runtimeStateSecretsAbsent: false,
      runtimeStateFullyScanned: false,
      runtimeStateSourcePathsClassified: false,
      supportBundleClean: false,
    },
    cleanup: {
      installedPackagesClear: false,
      projectedSkillsClear: false,
      registryEntriesClear: false,
      packageFilesClear: false,
      projectedFilesClear: false,
      processesClear: false,
      trackedTreeClear: false,
      portsClear: false,
      processTrackingComplete: false,
      stagingClear: false,
      filesystemResidueClear: false,
      rejectedOutputsClear: false,
      ephemeralSigningKeyClear: false,
      builderStoreClear: false,
      apkSemanticFixtureArchivesClear: false,
    },
    failureCleanup: {
      attempted: false,
      apiReachable: false,
      requiredPackagesFound: 0,
      apiUninstallCount: 0,
      apiErrorCount: 0,
      restClear: false,
      tauriClear: false,
      projectedClear: false,
      registryClear: false,
      packageFilesClear: false,
      projectedFilesClear: false,
      stagingClear: false,
      apiComplete: false,
      isolatedResidueRemoved: false,
      pathToSkillClear: false,
      filesystemClear: false,
      verifiedClear: false,
    },
    processBoundary: {
      identityBinding: "pid+path+dmtf-creation+start-event-generation",
      creationDateFormat: "dmtf",
      rootTracked: false,
      trackedProcessCountEver: 0,
      descendantProcessCountEver: 0,
      processNamesEver: [],
      samplingErrorCount: 0,
      trackedTreeClear: false,
    },
    assertions: [],
    phases: {},
    closure: {},
  };
  let app;
  let records = [];
  let signed;
  let packagedExport;
  let supportBundlePath = "";
  let artifactPrivacyScan;
  let failureDetected = false;
  let finalSnapshot;
  try {
    if (!Number.isInteger(cdpPort) || cdpPort < 1024 || cdpPort > 65535 || cdpPort === 8757) {
      throw new Error("VRCFORGE_SKILL_PROBE_CDP_PORT must be an unused non-8757 user port.");
    }
    const sourceVersion = (await readFile(resolve(repoRoot, "VERSION"), "utf8")).trim();
    report.version = sourceVersion;
    const releaseBinding = await prepareManifestBoundPackage(sourceVersion);
    report.strictReleaseBinding = releaseBinding.strictReleaseBinding;
    report.launchSource = "manifest-directory-portable-zip-extracted-to-isolated-evidence-root";
    report.payloadMatchesManifest = releaseBinding.innerExeSha256 === releaseBinding.exeSha256
      && releaseBinding.innerBackendSha256 === releaseBinding.extractedBackendSha256;
    report.manifestCommit = releaseBinding.manifestCommit;
    report.payloadZipSha256 = releaseBinding.portableSha256;
    report.releaseBinding = {
      strict: releaseBinding.strictReleaseBinding,
      manifestReleaseEligible: releaseBinding.manifestReleaseEligible,
      strictBuildPolicy: releaseBinding.strictBuildPolicy,
      buildPolicy: releaseBinding.buildPolicy,
      worktreeClean: releaseBinding.worktreeClean,
      headEqualsOriginMain: releaseBinding.headCommit === releaseBinding.originMainCommit,
      manifestEqualsHead: releaseBinding.manifestCommit === releaseBinding.headCommit,
      portableDigestVerified: true,
      extractedExecutableVerified: releaseBinding.innerExeSha256 === releaseBinding.exeSha256,
      extractedBackendVerified: releaseBinding.innerBackendSha256 === releaseBinding.extractedBackendSha256,
      portableManifestEntryUnique: releaseBinding.portableManifestEntryUnique,
      portableManifestPathSafe: releaseBinding.portableManifestPathSafe,
      embeddedVersion: releaseBinding.embeddedVersion,
      worktreeCleanAfterFixtureBuild: false,
      executableLaunchLockVerified: false,
      executableLaunchLockWatcherMode: "",
      completionExecutableVerified: false,
      worktreeCleanAtCompletion: false,
      completionHeadMatches: false,
      completionOriginMainMatches: false,
      completionVersionMatches: false,
    };
    if (!allowUnpushed && !releaseBinding.strictReleaseBinding) {
      throw new Error("Strict release binding was not satisfied.");
    }

    const fixtureSource = await prepareFixtureSource(releaseBinding.headCommit);
    report.fixtures.sourceMode = fixtureSource.mode;
    report.fixtures.sourceCommit = fixtureSource.commit;
    await writeIsolatedFixtures();
    signed = await buildSignedExamplePackages(sourceVersion, fixtureSource);
    report.releaseBinding.worktreeCleanAfterFixtureBuild = await gitWorktreeIsClean();
    if (!allowUnpushed && !report.releaseBinding.worktreeCleanAfterFixtureBuild) {
      throw new Error("Strict packaged probe worktree changed while committed fixture packages were built.");
    }
    report.fixtures.privateKeyPersisted = !signed.privateKeyDeleted;
    report.fixtures.builderStorePersisted = !signed.builderStoreDeleted;
    report.fixtures.sourceDigest = fixtureSource.digest;
    report.fixtures.sourceDigestVerified = signed.sourceDigestVerified === true;
    report.fixtures.sourcePackageBytesVerified = signed.sourcePackageBytesVerified === true;
    report.fixtures.signerFingerprintPrefix = signed.fingerprint.slice(0, 16);
    report.beforeLaunch = summarizeSnapshot(await processSnapshot());
    if (report.beforeLaunch.processCount || report.beforeLaunch.portCount) {
      throw new Error("Preflight found an existing packaged instance or occupied probe port; nothing was terminated.");
    }

    app = await launchPackagedApp(releaseBinding);
    report.releaseBinding.executableLaunchLockVerified = app.executableLock?.verified === true
      && app.executableLock?.sha256 === releaseBinding.innerExeSha256;
    report.releaseBinding.executableLaunchLockWatcherMode = String(
      app.executableLock?.processStartWatcherMode || "",
    );
    report.runtimeBinding = await assertRuntimeBinding(sourceVersion, releaseBinding);
    report.phases.launch = {
      rendererReady: app.renderer?.ok === true,
      healthVersion: String(app.health?.version || ""),
      isolatedRuntime: Object.values(report.runtimeBinding)
        .filter((value) => typeof value === "boolean")
        .every(Boolean),
    };
    await exerciseFirstRunLanguageGate(report, app.cdp);
    packagedExport = await createPackagedExportBase(app.cdp, signed);
    report.apkSemanticMatrix = { packagedExport };
    report.fixtures.privateKeyPersisted = !signed.privateKeyDeleted;
    records = await runPackageLifecycle(report, app.cdp, signed);
    await runApkSemanticLifecycle(report, app.cdp, signed, packagedExport);
    report.fixtures.privateKeyPersisted = !signed.privateKeyDeleted;
    await runGovernanceLifecycle(report, app.cdp, records, signed.fingerprint);
    await runPathToSkillLifecycle(report, app.cdp);
    await runUiAcceptance(report, app.cdp, signed.fingerprint, records);
    supportBundlePath = await createAndValidateSupportBundle(report, sourceVersion);
    await uninstallPackages(report, app.cdp, records);
    report.packages = publicPackageReport(records);

    const privacyTargets = [
      packageFixtureRoot,
      ...signed.packages.map((item) => item.path),
      pathToSkillRoot,
      supportBundlePath,
    ];
    artifactPrivacyScan = await scanSharedArtifactPrivacy(privacyTargets);
    delete report.internalPaths;

    if (report.assertions.length > 0) {
      failureDetected = true;
      await attemptFailureApiCleanup(report, app.cdp);
    }

    app.cdp.close();
    report.closure.normal = await closePackagedApp(app);
    assertGracefulClosure(report, report.closure.normal, "after Skill Ecosystem acceptance");
    report.releaseBinding.completionExecutableVerified = (await sha256File(exe)) === releaseBinding.innerExeSha256;
    await releaseExecutableLaunchLock(app.executableLock);
    app.executableLock = null;
    const settledSnapshot = await processSnapshot();
    if (!snapshotIsClear(settledSnapshot)) {
      addAssertion(report, "late packaged descendant appeared during the process-start watcher settling window");
      await forceCloseLaunch(app);
    }
    app = undefined;
    finalSnapshot = await processSnapshot();
    applyProcessBoundaryReport(report, finalSnapshot);
    report.finalCleanup = summarizeSnapshot(finalSnapshot);
    await unlink(ephemeralSigningKeyPath).catch(() => {});
    report.cleanup.ephemeralSigningKeyClear = !(await pathExists(ephemeralSigningKeyPath));
    report.cleanup.builderStoreClear = !(await pathExists(resolve(packageFixtureRoot, ".builder-store")));
    const diagnosticLogScan = await scanSharedArtifactPrivacy([resolve(userDataRoot, "logs")]);
    const runtimeStateScan = await scanSharedArtifactPrivacy([
      resolve(userDataRoot, "artifacts", "dashboard", "agent_gateway"),
    ]);
    const persistentRuntimeScan = await scanSharedArtifactPrivacy([userDataRoot]);
    applyPostShutdownPrivacyScans(
      report,
      signed,
      artifactPrivacyScan,
      diagnosticLogScan,
      runtimeStateScan,
      persistentRuntimeScan,
      report.cleanup.ephemeralSigningKeyClear,
    );
    const completionBinding = await currentGitBindingSnapshot();
    report.releaseBinding.worktreeCleanAtCompletion = completionBinding.worktreeClean;
    report.releaseBinding.completionHeadMatches = completionBinding.head === releaseBinding.headCommit;
    report.releaseBinding.completionOriginMainMatches = completionBinding.originMain === releaseBinding.originMainCommit;
    report.releaseBinding.completionVersionMatches = completionBinding.version === sourceVersion;
    if (!allowUnpushed && (
      !report.releaseBinding.worktreeCleanAtCompletion
      || !report.releaseBinding.completionHeadMatches
      || !report.releaseBinding.completionOriginMainMatches
      || !report.releaseBinding.completionVersionMatches
    )) {
      addAssertion(report, "strict packaged probe Git/version binding changed before evidence completion");
    }
    validateFinalContract(report);
    if (report.assertions.length > 0) failureDetected = true;
  } catch (error) {
    failureDetected = true;
    report.error = safeError(error);
    addAssertion(report, `probe aborted: ${sanitizeReportText(String(error?.message || error))}`);
    if (app) await attemptFailureApiCleanup(report, app.cdp).catch(() => {});
  } finally {
    if (app?.cdp) {
      try { app.cdp.close(); } catch { /* Best effort. */ }
    }
    try {
      if (app) {
        const closure = await closePackagedApp(app);
        report.closure.finally = closure;
        assertGracefulClosure(report, closure, "during finally cleanup");
      }
    } catch (cleanupError) {
      report.cleanupError = safeError(cleanupError);
      addAssertion(report, `final cleanup failed: ${sanitizeReportText(String(cleanupError?.message || cleanupError))}`);
      if (app) {
        await forceCloseLaunch(app).catch((forceError) => {
          addAssertion(report, `final scoped cleanup failed: ${sanitizeReportText(String(forceError?.message || forceError))}`);
        });
      }
    }
    if (app?.executableLock) {
      try {
        await releaseExecutableLaunchLock(app.executableLock);
        app.executableLock = null;
        const settledSnapshot = await processSnapshot();
        if (!snapshotIsClear(settledSnapshot)) {
          addAssertion(report, "late packaged descendant appeared during final process-start watcher settling");
          await forceCloseLaunch(app);
        }
      } catch (lockCleanupError) {
        addAssertion(report, `executable launch lock cleanup failed: ${sanitizeReportText(String(lockCleanupError?.message || lockCleanupError))}`);
      }
    }
    stopProcessTracking();
    try {
      finalSnapshot = await processSnapshot();
      applyProcessBoundaryReport(report, finalSnapshot);
      report.finalCleanup = summarizeSnapshot(finalSnapshot);
      if (!report.cleanup.processesClear || !report.cleanup.trackedTreeClear || !report.cleanup.portsClear) {
        addAssertion(report, "packaged process tree or probe ports remained after final cleanup");
      }
    } catch (snapshotError) {
      report.cleanupError = safeError(snapshotError);
      addAssertion(report, "final process-boundary snapshot failed");
    }
    await unlink(ephemeralSigningKeyPath).catch(() => {});
    report.cleanup.ephemeralSigningKeyClear = !(await pathExists(ephemeralSigningKeyPath).catch(() => true));
    report.fixtures.privateKeyPersisted = !report.cleanup.ephemeralSigningKeyClear;
    if (signed) {
      report.cleanup.apkSemanticFixtureArchivesClear = await cleanupApkSemanticFixtureArchives(signed)
        .catch(() => false);
    }
    report.cleanup.builderStoreClear = !(await pathExists(resolve(packageFixtureRoot, ".builder-store")).catch(() => true));
    if (report.assertions.length > 0) failureDetected = true;
    if (failureDetected) {
      await finalizeFailureCleanup(report, finalSnapshot).catch((failureCleanupError) => {
        report.failureCleanup.apiErrorCount += 1;
        report.failureCleanup.lastError = sanitizeReportText(String(failureCleanupError?.message || failureCleanupError));
      });
    }
    report.ok = report.assertions.length === 0;
    await writeSanitizedReport(report);
  }
  console.log(reportPath);
  if (!report.ok) {
    console.error(`Packaged Skill Ecosystem probe failed: ${report.assertions.join("; ")}`);
    process.exitCode = 1;
  }
}

if (selfTest) {
  runSelfTest();
} else {
  main().catch(async (error) => {
    await mkdir(dirname(reportPath), { recursive: true });
    const fallback = {
      schema: "vrcforge.packaged_skill_ecosystem_probe.v1",
      marker,
      ok: false,
      strictReleaseBinding: false,
      assertions: [`unhandled probe failure: ${sanitizeReportText(String(error?.message || error))}`],
      error: safeError(error),
    };
    await writeSanitizedReport(fallback).catch(() => {});
    console.error(error);
    process.exit(1);
  });
}
