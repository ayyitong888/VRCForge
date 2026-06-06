import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";

const root = process.cwd();
const smokeRoot = path.join(root, "artifacts", "agentic-app-smoke");
const configDir = path.join(smokeRoot, "config");
const logsDir = path.join(smokeRoot, "logs");
const artifactsDir = path.join(smokeRoot, "artifacts");
const port = Number(process.env.VRCFORGE_SMOKE_PORT || 8769);
const endpoint = `http://127.0.0.1:${port}`;

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
          command: ["powershell", "-ExecutionPolicy", "Bypass", "-File", "tools/unity-mcp-cli.ps1"],
          host: "127.0.0.1",
          port: 8080,
          instance: "",
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

assertFile(path.join(root, "dist", "index.html"), "Frontend build output is missing. Run npm run build first.");
assertFile(path.join(root, "src-tauri", "tauri.conf.json"), "Tauri config is missing.");

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

  const blocked = await postJson(`${endpoint}/api/app/permission`, { execution_mode: "roslyn_full_auto" });
  assert(blocked.status === 409, "Roslyn full-auto should require the one-time warning acknowledgement.");

  const enabled = await postJson(`${endpoint}/api/app/permission`, {
    execution_mode: "roslyn_full_auto",
    acknowledge_roslyn_risk: true,
  });
  assert(enabled.status === 200, "Acknowledged Roslyn mode switch should succeed.");
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
    instructions: "Load this skill only for the smoke review phrase.",
  });
  assert(createdSkill.status === 200, "User skill creation should succeed.");
  assert(createdSkill.json.skill.name === "smoke-review", "Created skill should be normalized.");
  assert(fs.existsSync(path.join(smokeRoot, "skills", "smoke-review", "SKILL.md")), "User skill should be stored as SKILL.md.");

  const userSkillTurn = await postJson(`${endpoint}/api/app/agent/message`, {
    message: "smoke review",
  });
  assert(userSkillTurn.status === 200, "User skill runtime turn should return normally.");
  assert(userSkillTurn.json.skill.status === "loaded", "User skill should load instructions instead of executing hidden code.");
  assert(userSkillTurn.json.skill.result.name === "smoke-review", "Loaded user skill should match the request.");

  const deletedSkill = await requestJson(`${endpoint}/api/app/skills/smoke-review`, "DELETE");
  assert(deletedSkill.status === 200, "User skill deletion should succeed.");
  assert(!fs.existsSync(path.join(smokeRoot, "skills", "smoke-review", "SKILL.md")), "Deleted user skill should remove SKILL.md.");

  const highRiskTarget = path.join(smokeRoot, "approved-shell.txt");
  const highRiskTurn = await postJson(`${endpoint}/api/app/agent/message`, {
    message: "写入测试文件",
    shell_command: "Set-Content -Path approved-shell.txt -Value ok -Encoding utf8",
    workspace_root: smokeRoot,
    cwd: smokeRoot,
  });
  assert(highRiskTurn.status === 200, "High-risk shell turn should return normally.");
  assert(highRiskTurn.json.shell.status === "pending_approval", "High-risk shell command should require approval.");
  assert(!fs.existsSync(highRiskTarget), "High-risk shell command must not execute before approval.");
  const shellApproval = await postJson(
    `${endpoint}/api/app/agent/approvals/${highRiskTurn.json.shell.approval_id}/approve`,
    {},
  );
  assert(shellApproval.status === 200, "Desktop approval endpoint should approve shell execution.");
  assert(shellApproval.json.execution.status === "applied", "Approved shell payload should execute.");
  assert(fs.existsSync(highRiskTarget), "Approved high-risk shell command should create the target file.");

  console.log("agentic app smoke passed");
} finally {
  child.kill();
  setTimeout(() => child.kill("SIGKILL"), 500).unref?.();
}

function assertFile(filePath, message) {
  if (!fs.existsSync(filePath)) {
    throw new Error(message);
  }
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
      const response = await fetch(url);
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
    headers: { "Content-Type": "application/json" },
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
