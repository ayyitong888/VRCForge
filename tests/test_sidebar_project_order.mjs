import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectPathSource = await readFile(path.join(root, "src/lib/project-path.ts"), "utf8");
const projectPathModule = ts.transpileModule(projectPathSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
}).outputText;
const projectPathModuleUrl = `data:text/javascript;base64,${Buffer.from(projectPathModule).toString("base64")}`;
const sourcePath = path.join(root, "src/lib/sidebar-project-order.ts");
const source = await readFile(sourcePath, "utf8");
const moduleSource = ts
  .transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
    fileName: sourcePath,
  })
  .outputText.replace('from "./project-path";', `from "${projectPathModuleUrl}";`);
const projectOrder = await import(`data:text/javascript;base64,${Buffer.from(moduleSource).toString("base64")}`);

const project = (path) => ({ path, name: path.split("\\").at(-1) });
const chat = (projectPath, timestamps = {}) => ({
  id: `${projectPath}-${timestamps.updatedAt || timestamps.createdAt || "none"}`,
  sessionId: "session",
  title: "chat",
  projectPath,
  items: [],
  ...timestamps,
});

test("pinned project group stays first and each group sorts by latest chat time", () => {
  const projects = [project("C:\\NoTime"), project("C:\\PinnedOld"), project("C:\\Recent"), project("C:\\PinnedRecent"), project("C:\\CreatedOnly")];
  const chats = [
    chat("c:\\pinnedold", { updatedAt: "2026-08-01T10:00:00.000Z" }),
    chat("c:\\recent", { updatedAt: "2026-08-03T10:00:00.000Z" }),
    chat("C:\\Recent", { updatedAt: "2026-08-04T10:00:00.000Z" }),
    chat("C:\\PinnedRecent", { updatedAt: "2026-08-05T10:00:00.000Z" }),
    chat("C:\\CreatedOnly", { createdAt: "2026-08-02T10:00:00.000Z" }),
  ];

  const ordered = projectOrder.sortSidebarProjects(projects, chats, new Set(["c:/pinnedold", "C:\\PINNEDRECENT"]));

  assert.deepEqual(ordered.map((item) => item.path), [
    "C:\\PinnedRecent",
    "C:\\PinnedOld",
    "C:\\Recent",
    "C:\\CreatedOnly",
    "C:\\NoTime",
  ]);
  assert.deepEqual(projects.map((item) => item.path), ["C:\\NoTime", "C:\\PinnedOld", "C:\\Recent", "C:\\PinnedRecent", "C:\\CreatedOnly"]);
});

test("invalid updatedAt falls back to createdAt while ties and missing times stay stable", () => {
  const projects = [project("C:\\First"), project("C:\\Second"), project("C:\\Third"), project("C:\\Fourth")];
  const chats = [
    chat("C:\\First", { updatedAt: "not-a-date", createdAt: "2026-08-01T10:00:00.000Z" }),
    chat("C:\\Second", { updatedAt: "2026-08-03T10:00:00.000Z", createdAt: "2026-08-01T10:00:00.000Z" }),
    chat("C:\\Third", { updatedAt: "2026-08-03T10:00:00.000Z" }),
  ];

  const ordered = projectOrder.sortSidebarProjects(projects, chats, new Set());

  assert.deepEqual(ordered.map((item) => item.path), ["C:\\Second", "C:\\Third", "C:\\First", "C:\\Fourth"]);
});

test("projects are separated into General and Unity groups while legacy entries stay Unity", () => {
  const projects = [
    project("C:\\General"),
    { ...project("C:\\Unity"), projectType: "unity" },
    { ...project("C:\\GeneralTwo"), projectType: "general" },
    { ...project("C:\\Legacy"), projectType: undefined },
  ];

  const groups = projectOrder.groupSidebarProjects(projects);

  assert.deepEqual(groups.general.map((item) => item.path), ["C:\\GeneralTwo"]);
  assert.deepEqual(groups.unity.map((item) => item.path), ["C:\\General", "C:\\Unity", "C:\\Legacy"]);
});

test("opening a project never overrides the latest conversation order", () => {
  const projects = [
    { ...project("C:\\Alpha"), lastOpenedAt: "2026-10-20T10:00:00Z" },
    { ...project("C:\\Zulu"), lastOpenedAt: "2026-10-21T10:00:00Z" },
    { ...project("C:\\Middle"), lastOpenedAt: "2026-10-19T10:00:00Z" },
    { ...project("C:\\Unknown"), lastOpenedAt: "invalid" },
  ];
  const chats = [chat("C:\\Middle", { updatedAt: "2026-10-22T10:00:00Z" })];
  const ordered = projectOrder.sortSidebarProjects(projects, chats, new Set());
  assert.deepEqual(ordered.map((item) => item.name), ["Middle", "Alpha", "Zulu", "Unknown"]);
  assert.deepEqual(projects.map((item) => item.name), ["Alpha", "Zulu", "Middle", "Unknown"]);
});

test("manual order persists within pinned groups and resetting restores conversation order", () => {
  const projects = [project("C:\\Alpha"), project("C:\\Zulu"), project("C:\\Middle"), project("C:\\Pinned")];
  const chats = [chat("C:\\Middle", { updatedAt: "2026-10-22T10:00:00Z" })];
  const pinned = new Set(["c:/pinned"]);
  const manual = ["c:/zulu", "c:/alpha", "c:/middle", "c:/pinned"];
  assert.deepEqual(projectOrder.sortSidebarProjects(projects, chats, pinned, manual).map(p => p.name),
    ["Pinned", "Zulu", "Alpha", "Middle"]);
  assert.deepEqual(projectOrder.sortSidebarProjects(projects, chats, pinned, []).map(p => p.name),
    ["Pinned", "Middle", "Alpha", "Zulu"]);
  assert.deepEqual(projects.map(p => p.name), ["Alpha", "Zulu", "Middle", "Pinned"]);
});
