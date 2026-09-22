// Opt-in real App regression. Uses a caller-owned loopback CDP session;
// navigation only: never changes provider fields or submits model requests.
import assert from "node:assert/strict";

const endpoint = process.env.VRCFORGE_TEST_CDP_URL;
assert.ok(endpoint, "Set VRCFORGE_TEST_CDP_URL to the test App's loopback CDP endpoint");
const url = new URL(endpoint);
assert.ok(["127.0.0.1", "localhost"].includes(url.hostname));
const tabs = await (await fetch(new URL("/json", url))).json();
const targets = tabs.filter(tab => tab.type === "page" && tab.title === "VRCForge");
assert.equal(targets.length, 1, "Expected exactly one VRCForge WebView");
const socket = new WebSocket(targets[0].webSocketDebuggerUrl);
await new Promise(resolve => { socket.onopen = resolve; });
const pending = new Map();
let sequence = 0;
socket.onmessage = event => {
  const message = JSON.parse(event.data);
  pending.get(message.id)?.(message);
  pending.delete(message.id);
};
async function evaluate(expression) {
  const id = ++sequence;
  const response = await new Promise(resolve => {
    pending.set(id, resolve);
    socket.send(JSON.stringify({ id, method: "Runtime.evaluate", params: { expression, returnByValue: true, awaitPromise: true } }));
  });
  assert.ok(!response.error && !response.result?.exceptionDetails, JSON.stringify(response.error || response.result?.exceptionDetails));
  return response.result.result.value;
}
async function waitFor(expression) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.fail(`UI condition not reached: ${expression}`);
}
async function click(label) {
  const expression = `Array.from(document.querySelectorAll('button')).find(e => e.textContent.trim() === ${JSON.stringify(label)})`;
  await waitFor(`Boolean(${expression})`);
  await evaluate(`(${expression}).click()`);
}
try {
  const dialog = "Boolean(document.querySelector('[data-vrcforge-onboarding]'))";
  if (!await evaluate(dialog)) {
    await click("设置");
    await click("重新引导");
  }
  for (const entry of ["设置应用内 AI", "配置 MCP 客户端"]) {
    await click(entry);
    await waitFor("Boolean(document.querySelector('button[aria-label=\"返回应用\"]'))");
    // Exercise the real sidebar callback, including while a spotlight is active.
    await click("返回应用");
    await waitFor(dialog);
    assert.ok(await evaluate("document.querySelector('[data-vrcforge-onboarding]').innerText.includes('第 1 / 3 步')"));
    assert.notEqual(await evaluate("localStorage.getItem('vrcforge_onboarded')"), "true");
  }
  console.log("AI and external MCP settings return restores the current step without marking completion: ok");
} finally {
  socket.close();
}
