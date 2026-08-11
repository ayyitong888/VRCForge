import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => readFile(path.join(root, relative), "utf8");

test("model settings persist a user context cap and feed it to runtime budgeting", async () => {
  const [settings, hook, app, api] = await Promise.all([
    read("src/components/settings/provider-settings.tsx"),
    read("src/hooks/use-provider-settings.ts"),
    read("src/App.tsx"),
    read("src/lib/api/app.ts"),
  ]);

  assert.match(settings, /provider\.contextWindow/);
  assert.match(settings, /contextWindow/);
  assert.match(settings, /type="range"/);
  assert.match(settings, /type="number"/);
  assert.equal(
    (settings.match(/onChange=\{\(event\) => onContextWindowChange\(event\.target\.value\)\}/g) || []).length,
    2,
  );
  assert.match(settings, />K<\/span>/);
  assert.match(settings, /onClick=\{\(\) => onContextWindowChange\(""\)\}/);
  assert.match(hook, /apiContextWindow/);
  assert.match(hook, /context_window:\s*normalizedContextWindow/);
  assert.match(api, /context_window:\s*number/);
  assert.match(app, /apiConfig\?\.contextWindow/);
  assert.match(app, /resolveContextLimit\([\s\S]*contextWindow/);
});

test("DeepSeek remains selectable in both main and dedicated vision lanes", async () => {
  const [settings, zh] = await Promise.all([
    read("src/components/settings/provider-settings.tsx"),
    read("src/locales/zh-CN.json"),
  ]);

  assert.equal((settings.match(/<option value="deepseek">DeepSeek<\/option>/g) || []).length, 2);
  assert.doesNotMatch(zh, /主模型无法识图（如 DeepSeek）/);
});
