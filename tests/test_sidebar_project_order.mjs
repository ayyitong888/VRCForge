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
