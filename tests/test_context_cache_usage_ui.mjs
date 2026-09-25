import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import { build } from "esbuild";

const bundle = await build({ entryPoints: ["src/lib/conversation-utils.ts"], bundle: true, write: false, format: "esm", platform: "node" });
const { buildContextUsageFromRuntime } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`);
const translations = JSON.parse(readFileSync("src/locales/en-US.json", "utf8"));
const t = (key, values = {}) => {
  const text = key.split(".").reduce((value, part) => value?.[part], translations) || key;
  return text.replace(/\{\{(\w+)\}\}/g, (_match, name) => String(values[name] ?? name));
};
const compiled = ts.transpileModule(readFileSync("src/components/chat/composer.tsx", "utf8"), {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const jsx = (type, props) => ({ type, props });
const exports = {};
new Function("exports", "require", compiled)(exports, (name) => {
  if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
  if (name.endsWith("/i18n")) return { t };
  if (name.endsWith("/utils")) return { cn: (...values) => values.filter(Boolean).join(" "), formatCount: String };
  return {};
});
const base = { exact: true, inputTokens: 1000, totalTokens: 1100, peakInputTokens: 600,
  requestCount: 2, cacheUsageRequestCount: 2, cacheUsageComplete: true, cacheReadTokens: 250 };
function render(changes = {}) {
  const usage = buildContextUsageFromRuntime({ ...base, ...changes }, "fixture", "fixture", undefined, t, 2000);
  return { usage, meter: exports.ContextUsageMeter({ usage }) };
}
function nodes(node) {
  if (Array.isArray(node)) return node.flatMap(nodes);
  return node && typeof node === "object" ? [node, ...nodes(node.props?.children)] : [];
}

test("complete cache uses cumulative input and preserves peak context occupancy", () => {
  const { usage, meter } = render();
  assert.equal(usage.cacheHitRatio, 0.25);
  assert.equal(usage.used, 600);
  assert.equal(meter.props["data-context-percent"], "30");
  assert.match(meter.props.title, /Cache hit rate \(all requests\): 25%/);
  assert.equal(meter.props["aria-label"], meter.props.title);
  assert.match(nodes(meter).find((node) => "data-context-cache-hit-rate" in node.props).props.children, /25%/);
});

test("explicit zero is a valid rate", () => {
  const { usage, meter } = render({ cacheReadTokens: 0 });
  assert.equal(usage.cacheHitRatio, 0);
  assert.match(meter.props.title, /Cache hit rate \(all requests\): 0%/);
});

test("cache rate preserves up to two decimal places", () => {
  for (const [cacheReadTokens, percent] of [[448, "4.48"], [9448, "94.48"], [450, "4.5"]]) {
    const { meter } = render({ inputTokens: 10000, cacheReadTokens });
    assert.ok(meter.props.title.includes(`Cache hit rate (all requests): ${percent}%`));
  }
});

test("legacy missing cache information stays unknown", () => {
  const { usage, meter } = render({ cacheReadTokens: undefined, cacheUsageComplete: undefined, cacheUsageRequestCount: undefined });
  assert.equal(usage.cacheHitRatio, undefined);
  assert.match(meter.props.title, /Cache hit rate: unknown/);
});

test("partial, mismatched, malformed, and zero denominator data never show a rate", () => {
  for (const changes of [
    { cacheUsageComplete: false }, { cacheUsageComplete: undefined },
    { cacheUsageRequestCount: 1 }, { cacheUsageRequestCount: undefined },
    { requestCount: 0, cacheUsageRequestCount: 0 }, { requestCount: 1.5, cacheUsageRequestCount: 1.5 },
    { inputTokens: 0, cacheReadTokens: 0 }, { inputTokens: undefined },
    { inputTokens: NaN }, { inputTokens: Infinity }, { inputTokens: -1 },
    { cacheReadTokens: undefined }, { cacheReadTokens: NaN }, { cacheReadTokens: -1 },
    { cacheReadTokens: 1001 }, { exact: false },
  ]) {
    const { usage, meter } = render(changes);
    assert.equal(usage.cacheHitRatio, undefined, JSON.stringify(changes));
    assert.match(meter.props.title, /Cache hit rate: incomplete data/);
    assert.doesNotMatch(meter.props.title, /Cache hit rate \(all requests\): .*%/);
  }
});

test("component rejects invalid ratios and retains cached-session wording", () => {
  for (const ratio of [NaN, Infinity, -0.1, 1.1]) {
    const { usage } = render();
    const meter = exports.ContextUsageMeter({ usage: { ...usage, cached: true, cacheHitRatio: ratio } });
    assert.match(meter.props.title, /incomplete data/);
    assert.doesNotMatch(meter.props.title, /Cache hit rate \(all requests\): .*%/);
  }
});

test("all supported locales explain cache rate and unavailable states", () => {
  for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP"]) {
    const chat = JSON.parse(readFileSync(`src/locales/${locale}.json`, "utf8")).chat;
    assert.match(chat.contextCacheHitRate, /\{\{percent\}\}/);
    assert.ok(chat.contextCacheHitRateIncomplete);
    assert.ok(chat.contextCacheHitRateUnknown);
  }
});
