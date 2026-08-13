import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const chatTypes = read("src/lib/chat-types.ts");
const runtimeApi = read("src/lib/api/agent-runtime.ts");
const runController = read("src/hooks/use-chat-run-controller.ts");
const projectPicker = read("src/components/project/project-picker-modal.tsx");
const projectManagement = read("src/hooks/use-project-management.ts");
const app = read("src/App.tsx");
const runtimeSidebar = read("src/components/runtime/runtime-sidebar.tsx");
const projectSections = read("src/components/runtime/project-workbench-sections.tsx");
const dashboard = read("dashboard_server.py");

// Failure-first contract: project identity is explicit and closed to the two
// supported modes. It must survive persistence, queueing, and runtime dispatch.
assert.match(chatTypes, /projectType\??\s*:\s*['"]general['"]\s*\|\s*['"]unity['"]/);
assert.match(chatTypes, /queueEnvelope[\s\S]*projectType/);
assert.match(runtimeApi, /projectType\??\s*:\s*['"]general['"]\s*\|\s*['"]unity['"]/);
assert.match(runController, /projectType/);
assert.match(dashboard, /project_type\s*:\s*Literal\[\s*["']general["']\s*,\s*["']unity["']/);

// General projects bind to any existing absolute directory. Unity projects
// alone require Unity-root detection/selection; an active Unity process must
// never silently convert a temporary/general chat into a Unity chat.
assert.match(projectManagement, /projectType/);
assert.match(projectManagement, /isAbsoluteLocalPath/);
assert.match(dashboard, /project_type.*unity[\s\S]{0,500}(is_unity_project_path|Unity project root)/i);
assert.match(dashboard, /(temporary|general)[\s\S]{0,500}(unity|project_type)/i);
assert.match(runController, /(temporary|general)[\s\S]{0,500}(unity|projectType)/i);

// New-project UI must make the choice explicit (exactly two options), rather
// than inferring type from a path or from a running Unity instance.
assert.match(projectPicker, /projectType/);
assert.match(projectPicker, /general/);
assert.match(projectPicker, /unity/);
assert.match(projectPicker, /(radio|role="radiogroup"|type="radio")/i);
assert.match(projectPicker, /\[\s*["']general["']\s*,\s*["']unity["']\s*\]/i);

// A General project is a first-class workspace, not a renamed Unity selection.
// It must appear immediately from durable custom-project prefs, use a generic
// picker title, and omit Unity-only health rows from its right rail.
assert.match(projectPicker, /t\(["']project\.selectProjectTitle["']\)/);
assert.match(projectManagement, /projectPrefs\.customProjects/);
assert.match(projectManagement, /projectType:\s*customProject\.projectType/);
assert.match(app, /workspaceProjectType/);
assert.match(app, /workspaceProjectType\s*===\s*["']general["']/);
assert.match(runtimeSidebar, /projectType=\{workspaceProjectType\}/);
assert.match(projectSections, /projectType\s*===\s*["']unity["']/);
assert.match(projectSections, /\{isUnityProject\s*\?\s*\(/);

// Minimal executable semantics used by the contract itself.
function validateBinding(projectType, projectPath, isUnityRoot) {
  assert.ok(projectType === "general" || projectType === "unity");
  assert.ok(path.isAbsolute(projectPath));
  if (projectType === "unity") assert.equal(isUnityRoot, true);
  return { projectType, projectPath };
}

assert.deepEqual(validateBinding("general", path.resolve("existing-folder"), false).projectType, "general");
assert.deepEqual(validateBinding("unity", path.resolve("UnityProject"), true).projectType, "unity");
assert.throws(() => validateBinding("unity", path.resolve("ordinary-folder"), false));

console.log("project type contract: passed");
