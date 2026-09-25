import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { build } from "esbuild";

// Execute the real Composer with inert hooks: no browser, providers, or IPC.
const composerSource = readFileSync("src/components/chat/composer.tsx", "utf8");
const compiled = ts.transpileModule(composerSource, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
let hookIndex = 0;
let menuOpen = true;
let selectedPaletteIndex = 0;
const t = (key) => key;
const jsx = (type, props) => ({ type, props });
const requireStub = (name) => {
  if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
  if (name === "react") return {
    useState: (value) => { const index = hookIndex++; return [index === 1 ? menuOpen : index === 2 ? selectedPaletteIndex : value, () => {}]; },
    useRef: () => ({ current: null }), useEffect: () => {},
  };
  if (name === "react-i18next") return { useTranslation: () => ({ t }) };
  if (name.endsWith("/i18n")) return { t };
  if (name.endsWith("/permission-ui")) return { permissionVisualState: () => ({}), executionModeLabel: (mode) => mode, EXECUTION_MODES: [] };
  if (name.endsWith("/utils")) return { cn: (...values) => values.filter(Boolean).join(" ") };
  return new Proxy({}, { get: (_target, property) => property });
};
const exports = {};
new Function("exports", "require", compiled)(exports, requireStub);
function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!node || typeof node !== "object") return [];
  return [node, ...elements(node.props?.children)];
}
let selected;
let permissionChanges = 0;
let inputChanges = 0;
const base = {
  input: "Keep this draft", setInput: () => inputChanges++, sending: false,
  onSubmit: () => {}, onSwitchMode: () => permissionChanges++, onPlanModeChange: (mode) => { selected = mode; },
};
function render(props = {}) { hookIndex = 0; return elements(exports.Composer({ ...base, ...props })); }
const initial = render();
const toggle = initial.find((node) => node.props?.["data-composer-action"] === "plan");
assert.ok(toggle, "plus menu exposes Plan mode");
assert.equal(toggle.props["aria-pressed"], false, "fresh composer defaults to execute");
toggle.props.onClick();
assert.equal(selected, true);
assert.equal(inputChanges, 0, "toggling does not erase the draft");
assert.equal(permissionChanges, 0, "planning is independent from approval policy");
const enabled = render({ planMode: true });
const badge = enabled.find((node) => "data-composer-plan-mode" in (node.props || {}));
assert.ok(badge, "enabled mode stays visible when the plus menu closes");
assert.equal(enabled.find((node) => node.type === "textarea").props.placeholder, "composerAction.planPlaceholder");
badge.props.onClick();
assert.equal(selected, false, "visible badge exits planning");
// Keyboard palette selects Plan after the default attachment action.
selectedPaletteIndex = 1;
render().find((node) => node.type === "textarea").props.onKeyDown({ key: "Enter", shiftKey: false, nativeEvent: {}, preventDefault() {} });
selectedPaletteIndex = 0;
assert.equal(selected, true);
assert.equal(inputChanges, 0);

// Test real request construction for both browser and Tauri routes.
const bundle = await build({ entryPoints: ["src/lib/api/agent-runtime.ts"], bundle: true, write: false, format: "esm", platform: "node" });
const api = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`);
globalThis.window = { setTimeout, clearTimeout };
const requests = [];
globalThis.fetch = async (_url, init) => { requests.push(JSON.parse(init.body)); return new Response('{"ok":true}'); };
await api.sendAgentMessage("http://fixture.invalid", "read", "s1");
await api.sendAgentMessage("http://fixture.invalid", "plan", "s2", [], undefined, { planMode: true });
assert.deepEqual(requests.map((request) => request.planMode), [false, true]);
window.__TAURI_INTERNALS__ = { invoke: async (command, args) => { requests.push({ command, ...args.request }); return { ok: true }; } };
await api.sendAgentMessage("http://fixture.invalid", "plan", "s3", [], undefined, { planMode: true });
assert.equal(requests.at(-1).command, "send_agent_message");
assert.equal(requests.at(-1).planMode, true);
await api.recordAgentRunQueued("http://fixture.invalid", { clientTurnId: "queued", planMode: true });
assert.equal(requests.at(-1).planMode, true);

// Execute App's real retry function against an unrelated active composer mode.
const app = readFileSync("src/App.tsx", "utf8");
const retrySource = app.slice(app.indexOf("  function retryConversationItem("), app.indexOf("  async function runExplicitWorkspaceAction("));
const retryJs = ts.transpileModule(retrySource, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
for (const originalMode of [true, false, undefined]) {
  const chat = { id: "a", planMode: !originalMode, items: [{ id: "u", type: "user", text: "original", planMode: originalMode }] };
  let sent;
  const retry = new Function("isChatRunActive", "compacting", "getChatById", "activeChatId", "latestConversationItemId", "isRetryableConversationItem", "resolveContextLimit", "providerSnapshot", "currentModelInfo", "apiConfig", "cloneChatAttachments", "runTurnNow", `${retryJs}; return retryConversationItem;`)(
    () => false, false, () => chat, "a", () => "u", () => true, () => ({ known: false }), {}, null, null, (items) => items,
    (_id, turn) => { sent = turn; },
  );
  retry("u");
  assert.equal(sent.planMode, originalMode === true, "retry keeps original mode, including legacy execute default");
}
const sessions = readFileSync("src/hooks/use-chat-sessions.ts", "utf8");
assert.match(sessions, /planMode: chat\.planMode === true/);
assert.match(app, /planMode=\{activeChat\?\.planMode === true\}/, "indicator is scoped to active chat");
assert.match(app, /updateChat\(chatId, \(chat\) => \(\{ \.\.\.chat, planMode \}\)\)/);
const controller = readFileSync("src/hooks/use-chat-run-controller.ts", "utf8");
assert.match(controller, /\(turn\.planMode === true\) === \(currentTurnRef\.current\?\.planMode === true\)/, "different mode must enqueue instead of steering current turn");
for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP"]) {
  const strings = JSON.parse(readFileSync(`src/locales/${locale}.json`, "utf8")).composerAction;
  for (const key of ["plan", "planDesc", "planEnabledDesc", "planDisable", "planPlaceholder"]) assert.ok(strings[key]);
}
console.log("explicit plan UI, mode isolation, retry, queue, and HTTP/IPC requests: passed");
