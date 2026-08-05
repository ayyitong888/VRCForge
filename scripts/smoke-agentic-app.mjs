import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const smokeRoot = path.join(root, "artifacts", "agentic-app-smoke");
const configDir = path.join(smokeRoot, "config");
const logsDir = path.join(smokeRoot, "logs");
const artifactsDir = path.join(smokeRoot, "artifacts");
const port = Number(process.env.VRCFORGE_SMOKE_PORT || 8769);
const endpoint = `http://127.0.0.1:${port}`;
const appSessionToken = "vrcforge-smoke-session-token";
const shellProjectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "vrcforge-agentic-shell-"));
const frontendDir = process.env.VRCFORGE_SMOKE_FRONTEND_DIR || path.join(root, "dist");

fs.rmSync(smokeRoot, { recursive: true, force: true });
for (const dir of [configDir, logsDir, artifactsDir]) {
  fs.mkdirSync(dir, { recursive: true });
}

const settingsPath = path.join(configDir, "settings.json");
if (!fs.existsSync(settingsPath)) {
  fs.writeFileSync(
    settingsPath,
    JSON.stringify(
      {
        gemini: {
          api_key_env: "GEMINI_API_KEY",
          model: "gemini-2.5-flash",
          thinking_level: "",
        },
        unity_mcp: {
          project_path: "",
          retries: 1,
          retry_backoff_seconds: 0.1,
          timeout_seconds: 2,
          export_tool_name: "vrc_export_blendshapes",
          execute_tool_name: "vrc_apply_blendshapes",
        },
        paths: {
          blendshape_export: "Assets/VRCForge/blendshapes_export.json",
        },
        planning: {
          min_confidence: 0.65,
        },
        dashboard: {
          project_roots: [],
          unity_editor_path: "",
          status_push_interval_seconds: 2.5,
        },
      },
      null,
      2,
    ),
    "utf8",
  );
}

assertFile(path.join(frontendDir, "index.html"), "Frontend build output is missing. Run the frontend build first.");
assertFile(path.join(root, "src-tauri", "tauri.conf.json"), "Tauri config is missing.");
assertSourceContract(
  path.join(root, "src", "components", "approvals", "scoped-pending-approval-card.tsx"),
  ["allowFutureEligible", "allowFutureCategory", "data-scoped-pending-approval", "data-approval-composer-replacement"],
  "Scoped approval UI must expose the guarded once/future/reject actions.",
);
assertSourceContract(
  path.join(root, "src", "components", "chat", "chat-workspace.tsx"),
  [
    "data-empty-chat-content",
    "data-chat-history-scroll",
    "data-chat-composer-dock",
    "!approvalComposer ? composer(false) : null",
    "approvalComposer || composer(true)",
    "scopedPendingApprovals",
    "ScopedPendingApprovalCard",
  ],
  "Pending approvals must replace the bottom composer dock while conversation history stays visible.",
);
assertSourceContract(
  path.join(root, "src", "App.tsx"),
  ["scopedPendingApprovals={pendingApprovalItems}"],
  "Pending approvals must remain visible after switching project or temporary chat scope.",
);
assertSourceContract(
  path.join(root, "src", "App.tsx"),
  ["showApprovalNotification", "vrcforge-approval-notification-action", "scopedPendingApprovals", "presentApproval"],
  "Desktop notification and chat approval wiring must remain connected.",
);
assertSourceContract(
  path.join(root, "src", "lib", "approval-presentation.ts"),
  [
    "createObjectTitle",
    "restoreTitle",
    "rollbackAvailable",
    "technicalDetails",
    "agentReason",
    "riskLevel",
  ],
  "Approval requests must have a user-facing summary while technical values stay in details.",
);
assertSourceContract(
  path.join(root, "src", "components", "approvals", "scoped-pending-approval-card.tsx"),
  ["presentApproval", "visibleApprovals", "approval.presentation.project", "approval.presentation.rollback"],
  "The composer approval card must hide in-flight approvals and use the localized user presentation.",
);
assertSourceContract(
  path.join(root, "src", "components", "approvals", "pending-approvals-strip.tsx"),
  ["presentApproval", "visibleApprovals", "allowFutureEligible", "technicalDetails"],
  "The non-chat approval surface must match the scoped user-facing approval contract.",
);
assertSourceContract(
  path.join(root, "src", "components", "chat", "conversation-card.tsx"),
  ["presentApproval", "approvalAction !== \"approve\"", "approvalAction !== \"reject\"", "<InlineApprovalCard approval={approval}"],
  "Conversation history must show only a read-only approval hint and hide it once a decision starts.",
);
for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP"]) {
  const localeValue = JSON.parse(fs.readFileSync(path.join(root, "src", "locales", `${locale}.json`), "utf8"));
  for (const key of [
    "project",
    "rollback",
    "createObjectTitle",
    "restoreTitle",
    "genericTitle",
    "rollbackAvailable",
    "restoreEffect",
  ]) {
    assert(localeValue?.approval?.presentation?.[key], `${locale} is missing approval.presentation.${key}.`);
  }
}
const scopedApprovalSource = fs.readFileSync(
  path.join(root, "src", "components", "approvals", "scoped-pending-approval-card.tsx"),
  "utf8",
);
assert(!scopedApprovalSource.includes("approval.riskLevel"), "The primary approval card must not expose the raw risk badge.");
const pendingApprovalSource = fs.readFileSync(
  path.join(root, "src", "components", "approvals", "pending-approvals-strip.tsx"),
  "utf8",
);
assert(!pendingApprovalSource.includes("approval.riskLevel"), "The secondary approval card must not expose the raw risk badge.");
const toastSource = fs.readFileSync(path.join(root, "src-tauri", "src", "approval_notification_windows.rs"), "utf8");
assert(toastSource.includes('APPROVAL_NOTIFICATION_DISPLAY_NAME: &str = "VRCForge"'), "Toast registration must use VRCForge display identity.");
assert(!toastSource.includes("POWERSHELL_APP_ID"), "Approval toasts must not fall back to the Windows PowerShell identity.");

const python = process.env.PYTHON || "python";
const child = spawn(python, ["dashboard_server.py", "--host", "127.0.0.1", "--port", String(port)], {
  cwd: root,
  env: {
    ...process.env,
    VRCFORGE_APP_DIR: root,
    VRCFORGE_USER_DATA_DIR: smokeRoot,
    VRCFORGE_CONFIG_DIR: configDir,
    VRCFORGE_LOG_DIR: logsDir,
    VRCFORGE_ARTIFACTS_DIR: artifactsDir,
    VRCFORGE_DASHBOARD_DIR: path.join(root, "dashboard"),
    VRCFORGE_SETTINGS_PATH: settingsPath,
    VRCFORGE_APP_SESSION_TOKEN: appSessionToken,
  },
  stdio: ["ignore", "pipe", "pipe"],
});

let stdout = "";
let stderr = "";
child.stdout.on("data", (chunk) => {
  stdout += chunk.toString();
});
child.stderr.on("data", (chunk) => {
  stderr += chunk.toString();
});

try {
  const bootstrap = await waitForJson(`${endpoint}/api/app/bootstrap`, 20000);
  assert(bootstrap.ok === true, "Bootstrap ok flag should be true.");
  assert(bootstrap.app.surface === "tauri-agentic-desktop", "Bootstrap should describe the desktop surface.");
  assert(bootstrap.app.browserRequired === false, "Desktop surface must not require a browser.");
  assert(bootstrap.agentManifest.toolCount >= 10, "Agent manifest should expose the VRCForge skills.");
  assert(bootstrap.permission.executionMode === "approval", "Default permission mode should be per-action approval.");

  const doctor = await requestJson(`${endpoint}/api/app/doctor`, "GET");
  assert(doctor.status === 200, "Doctor endpoint should be available.");
  assert(doctor.json.schema === "vrcforge.doctor.v1", "Doctor should return the vrcforge.doctor.v1 schema.");
  assert(Array.isArray(doctor.json.checks) && doctor.json.checks.length > 0, "Doctor should return checks.");
  assert(!JSON.stringify(doctor.json).toLowerCase().includes("approval_token"), "Doctor must not expose approval tokens.");

  const directFull = await postJson(`${endpoint}/api/app/permission`, { execution_mode: "roslyn_full_auto" });
  assert(directFull.status === 200, "Full permission mode should switch on without a one-time warning acknowledgement.");
  assert(directFull.json.permission.fullPermission === true, "Full permission flag should be recorded.");
  assert(!("unityAcknowledgement" in directFull.json), "Full permission switching must not wait for Unity acknowledgement.");

  const enabled = await postJson(`${endpoint}/api/app/permission`, {
    execution_mode: "roslyn_full_auto",
    acknowledge_roslyn_risk: true,
  });
  assert(enabled.status === 200, "Acknowledged full permission mode switch should still be accepted for compatibility.");
  assert(enabled.json.permission.roslynRiskAcknowledged === true, "Risk acknowledgement should persist true.");

  const approval = await postJson(`${endpoint}/api/app/permission`, { execution_mode: "approval" });
  assert(approval.status === 200, "Switching back to approval should succeed.");
  assert(approval.json.permission.roslynRiskAcknowledged === true, "Risk acknowledgement must not reset.");

  const lowRiskTurn = await postJson(`${endpoint}/api/app/agent/message`, {
    message: "列目录",
    workspace_root: smokeRoot,
    cwd: smokeRoot,
  });
  assert(lowRiskTurn.status === 200, "Agent runtime message should accept natural language input.");
  assert(lowRiskTurn.json.plan.planner === "deterministic-local", "Agent runtime should produce a plan.");
  assert(lowRiskTurn.json.shell.status === "executed", "Low-risk shell commands should execute directly.");
  assert(lowRiskTurn.json.shell.classification.risk === "low", "Directory listing should be low-risk.");

  const attachmentTurn = await postJson(`${endpoint}/api/app/agent/message`, {
    message: "read the attached smoke note",
    attachments: [
      {
        id: "smoke-att-1",
        name: "smoke-note.txt",
        type: "text/plain",
        size: 12,
        text: "hello smoke",
        payloadKind: "text",
      },
    ],
  });
  assert(attachmentTurn.status === 200, "Agent runtime should accept bounded file/image attachment payloads.");
  assert(attachmentTurn.json.attachments?.[0]?.payloadKind === "text", "Attachment payload kind should be preserved.");
  assert(attachmentTurn.json.attachments?.[0]?.text === "hello smoke", "Text attachment payload should be preserved.");

  const workspaceDiff = await requestJson(`${endpoint}/api/app/workspace/diff?root=${encodeURIComponent(root)}&includePatch=true`, "GET");
  assert(workspaceDiff.status === 200, "Workspace diff endpoint should be available.");
  assert(typeof workspaceDiff.json.patch === "string", "Workspace diff review payload should include a patch string.");

  const unityStatusTurn = await postJson(`${endpoint}/api/app/agent/message`, {
    message: "检查 Unity MCP 状态",
  });
  assert(unityStatusTurn.status === 200, "Unity status skill turn should return normally.");
  assert(unityStatusTurn.json.plan.skillTool === "vrcforge_unity_status", "Unity status intent should route to the Unity status skill.");
  assert(unityStatusTurn.json.skill.tool === "vrcforge_unity_status", "Runtime should execute the routed Unity status skill.");
  assert(["executed", "failed", "blocked"].includes(unityStatusTurn.json.skill.status), "Unity status skill should produce a bounded status.");

  const skillManifestTurn = await postJson(`${endpoint}/api/app/agent/message`, {
    message: "列一下 skills",
  });
  assert(skillManifestTurn.status === 200, "Skill manifest turn should return normally.");
  assert(skillManifestTurn.json.plan.skillTool === "vrcforge_skill_manifest", "Skill list intent should route to the manifest skill.");
  assert(skillManifestTurn.json.skill.result.toolCount >= 10, "Skill manifest should include the registered tools.");
  assert(!("token" in skillManifestTurn.json.skill.result), "Skill manifest must not leak the local gateway token.");

  const createdSkill = await requestJson(`${endpoint}/api/app/skills`, "POST", {
    name: "smoke-review",
    title: "Smoke Review",
    description: "Smoke skill for registry validation.",
    whenToUse: "smoke review",
    inputs: ["runtime state"],
    outputs: ["smoke notes"],
    allowedTools: ["vrcforge_health"],
    entrypointTool: "vrcforge_health",
    argumentHint: "target",
    instructions: "Load this skill only for the smoke review phrase. Args=$ARGUMENTS",
  });
  assert(createdSkill.status === 200, "User skill creation should succeed.");
  assert(createdSkill.json.skill.name === "smoke-review", "Created skill should be normalized.");
  assert(fs.existsSync(path.join(smokeRoot, "skills", "smoke-review", "SKILL.md")), "User skill should be stored as SKILL.md.");

  const userSkillTurn = await postJson(`${endpoint}/api/app/agent/message`, {
    message: "/smoke-review target-avatar",
  });
  assert(userSkillTurn.status === 200, "User skill runtime turn should return normally.");
  assert(userSkillTurn.json.skill.status === "executed", "User skill entrypoint should execute through an allowed read-only tool.");
  assert(userSkillTurn.json.skill.result.name === "smoke-review", "Loaded user skill should match the request.");
  assert(userSkillTurn.json.skill.result.arguments === "target-avatar", "Direct skill invocation should pass arguments.");
  assert(userSkillTurn.json.skill.entrypointTool === "vrcforge_health", "User skill should expose its read-only entrypoint.");

  const skillCheck = await requestJson(`${endpoint}/api/app/skills/check`, "GET");
  assert(skillCheck.status === 200, "Skill check should be available.");
  assert(skillCheck.json.count >= createdSkill.json.count, "Skill check should cover registered skills.");

  const deletedSkill = await requestJson(`${endpoint}/api/app/skills/smoke-review`, "DELETE");
  assert(deletedSkill.status === 200, "User skill deletion should succeed.");
  assert(!fs.existsSync(path.join(smokeRoot, "skills", "smoke-review", "SKILL.md")), "Deleted user skill should remove SKILL.md.");

  for (const dir of ["Assets", "Packages", "ProjectSettings"]) {
    fs.mkdirSync(path.join(shellProjectRoot, dir), { recursive: true });
  }
  const highRiskTarget = path.join(shellProjectRoot, "Assets", "approved-shell.txt");
  const highRiskTurn = await postJson(`${endpoint}/api/app/agent/message`, {
    message: "写入测试文件",
    shell_command: "Set-Content -Path Assets/approved-shell.txt -Value ok -Encoding utf8",
    workspace_root: shellProjectRoot,
    cwd: shellProjectRoot,
  });
  assert(highRiskTurn.status === 200, "High-risk shell turn should return normally.");
  assert(highRiskTurn.json.shell.status === "pending_approval", "High-risk shell command should require approval.");
  assert(!fs.existsSync(highRiskTarget), "High-risk shell command must not execute before approval.");
  const shellApproval = await postJson(
    `${endpoint}/api/app/agent/approvals/${highRiskTurn.json.shell.approval_id}/approve`,
    {},
  );
  assert(shellApproval.status === 200, "Desktop approval endpoint should approve shell execution.");
  assert(
    shellApproval.json.execution.status === "applied",
    `Approved shell payload should execute: ${JSON.stringify(shellApproval.json.execution)}`,
  );
  assert(fs.existsSync(highRiskTarget), "Approved high-risk shell command should create the target file.");

  console.log("agentic app smoke passed");
} finally {
  child.kill();
  setTimeout(() => child.kill("SIGKILL"), 500).unref?.();
  fs.rmSync(shellProjectRoot, { recursive: true, force: true });
}

function assertFile(filePath, message) {
  if (!fs.existsSync(filePath)) {
    throw new Error(message);
  }
}

function assertSourceContract(filePath, requiredTokens, message) {
  assertFile(filePath, message);
  const source = fs.readFileSync(filePath, "utf8");
  assert(requiredTokens.every((token) => source.includes(token)), message);
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

async function waitForJson(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { headers: appAuthHeaders() });
      if (response.ok) {
        return await response.json();
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError?.message || "unknown"}\nstdout:\n${stdout}\nstderr:\n${stderr}`);
}

async function postJson(url, body) {
  return requestJson(url, "POST", body);
}

async function requestJson(url, method, body) {
  const response = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", ...appAuthHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let json = {};
  try {
    json = await response.json();
  } catch {
    json = {};
  }
  return { status: response.status, json };
}

function appAuthHeaders() {
  return { Authorization: `Bearer ${appSessionToken}` };
}
