import assert from "node:assert/strict";
import { build } from "esbuild";
import { resolve } from "node:path";

const result = await build({
  entryPoints: [resolve("src/lib/message-time.ts")],
  bundle: true,
  format: "esm",
  platform: "node",
  write: false,
});
const moduleUrl = `data:text/javascript,${encodeURIComponent(result.outputFiles[0].text)}`;
const time = await import(moduleUrl);

assert.equal(time.parseMessageTimestamp("not-a-time"), undefined);
assert.equal(time.formatLocalMessageTime(Number.NaN, "en-US"), "", "invalid numeric timestamps remain unknown");
assert.equal(time.formatMessageDateSeparator(Number.POSITIVE_INFINITY, "en-US"), "", "infinite numeric timestamps remain unknown");
assert.equal(time.parseMessageTimestamp("2026-10-22T12:00:00.000Z"), Date.parse("2026-10-22T12:00:00.000Z"));
assert.equal(time.shouldShowMessageDateSeparator(undefined, Date.parse("2026-10-22T12:00:00Z")), true);
assert.equal(time.shouldShowMessageDateSeparator(0, time.MESSAGE_DATE_SEPARATOR_THRESHOLD_MS - 1), false);
assert.equal(time.shouldShowMessageDateSeparator(0, time.MESSAGE_DATE_SEPARATOR_THRESHOLD_MS), true);
assert.match(time.formatMessageDateSeparator("2020-10-22T12:00:00Z", "en-US"), /2020/);
const today = new Date();
const todayIso = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 12).toISOString();
assert.match(time.formatMessageDateSeparator(todayIso, "en-US"), /today/i);
const yesterday = new Date(today.getTime() - 86_400_000);
const yesterdayIso = new Date(yesterday.getFullYear(), yesterday.getMonth(), yesterday.getDate(), 12).toISOString();
assert.match(time.formatMessageDateSeparator(yesterdayIso, "en-US"), /yesterday/i);
assert.match(time.formatLocalMessageTime("2026-10-22T12:00:00Z", "en-US"), /\d/);
console.log("chat message time contracts: ok");
