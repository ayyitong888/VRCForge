import assert from "node:assert/strict";
import { build } from "esbuild";

const bundle = await build({ entryPoints: ["src/lib/chat-thread.ts"], bundle: true, write: false, format: "esm", platform: "node" });
const { filterPersistableChats } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`);
const steps = [{ kind: "skill", actionId: "a", result: { unknown: [1, "保留"] } }];
const response = { plan: { reply: "完成", steps: structuredClone(steps), unknown: "keep" }, steps };
const chat = { id: "chat", title: "Test", sessionId: "session", items: [{ id: "agent", type: "agent", response }] };
const before = JSON.stringify(chat);
const projected = filterPersistableChats([chat])[0];
assert.equal(Object.hasOwn(projected.items[0].response.plan, "steps"), false);
assert.deepEqual(projected.items[0].response.steps, steps);
assert.equal(projected.items[0].response.plan.unknown, "keep");
assert.equal(JSON.stringify(chat), before, "must not mutate the live response");
for (const variant of [
  { plan: { steps }, steps: [...steps, { kind: "assistant", reply: "new" }] },
  { plan: { steps } },
  { plan: { steps: [{ unknown: "different" }] }, steps },
]) {
  const result = filterPersistableChats([{ ...chat, items: [{ id: "agent", type: "agent", response: variant }] }])[0];
  assert.deepEqual(result.items[0].response, variant, "nonidentical history must remain intact");
}
assert.deepEqual(filterPersistableChats([projected]), [projected], "projection is idempotent");
console.log("chat persistence exact duplicate projection passed");

const stringify = JSON.stringify;
try {
  JSON.stringify = () => { throw new Error("durable projection must not serialize full tool results"); };
  const largeSteps = [{ output: "tool result ".repeat(600000) }];
  const large = { ...chat, items: [{ id: "large", type: "agent", response: { steps: largeSteps, plan: { steps: largeSteps.map(step => ({...step})) } } }] };
  const result = filterPersistableChats([large])[0];
  assert.equal(result.items[0].response.plan.steps, undefined);
  assert.equal(result.items[0].response.steps, largeSteps);
} finally { JSON.stringify = stringify; }
console.log("PASS duplicate tool-result projection without serialized copies");
