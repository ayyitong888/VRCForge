import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const source = await readFile(
  resolve(import.meta.dirname, "..", "src", "components", "settings", "checkpoint-storage-panel.tsx"),
  "utf8",
);

const zhCn = JSON.parse(await readFile(resolve(import.meta.dirname, "..", "src", "locales", "zh-CN.json"), "utf8"));
const zhTw = JSON.parse(await readFile(resolve(import.meta.dirname, "..", "src", "locales", "zh-TW.json"), "utf8"));
const enUs = JSON.parse(await readFile(resolve(import.meta.dirname, "..", "src", "locales", "en-US.json"), "utf8"));

assert.match(source, /isActiveRecoveryProtection/);
assert.match(source, /isAutoCleanupProtected/);
assert.ok(source.includes("selectableIds") && source.includes("isActiveRecoveryProtection"));
assert.ok(!source.includes("selectableIds.filter((item) => !item.protected"));
assert.ok(source.includes("const sameIds = ids.length === pendingDeleteIds.length && ids.every((id) => pendingDeleteIds.includes(id));"));
assert.ok(source.includes("setPendingDeleteIds(ids);"));
assert.ok(source.includes("onDeleteSelected(ids);"));
assert.ok(source.includes("hasPendingDelete") && source.includes("checkpointArchiveDeleteConfirm"));
assert.ok(source.includes("setPendingDeleteIds([])"));
assert.match(source, /t\("common\.cancel"\)/);
assert.match(source, /checkpointArchiveDeletePendingWarning/);
assert.ok(!source.includes("as ArchiveEntry"));
assert.doesNotMatch(source, /window\\.confirm/);

assert.equal(zhCn.settings.checkpointArchiveDeleteConfirm, "确认删除已选 {{count}} 个归档并永久失去其恢复能力");
assert.equal(zhTw.settings.checkpointArchiveDeleteConfirm, "確認刪除已選 {{count}} 個封存並永久失去其復原能力");
assert.equal(typeof enUs.settings.checkpointArchiveDeleteConfirm, "string");
assert.equal(typeof enUs.settings.checkpointArchiveDeletePendingWarning, "string");
assert.ok(zhCn.settings.checkpointArchiveRecentProtectedHint.includes("手动删除"));
assert.ok(zhTw.settings.checkpointArchiveRecentProtectedHint.includes("手動刪除"));
assert.ok(enUs.settings.checkpointArchiveRecentProtectedHint.includes("manual"));

console.log("checkpoint storage panel UI contract: ok");
