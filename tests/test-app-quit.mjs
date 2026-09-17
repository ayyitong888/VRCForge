import assert from "node:assert/strict";
import test from "node:test";
import { flushChatsBeforeQuit } from "../src/lib/app-quit.ts";

test("chat persistence completes before confirmed quit", async () => {
  const events = [];
  const result = await flushChatsBeforeQuit(
    async () => { events.push("persist-start"); await Promise.resolve(); events.push("persist-done"); },
    async () => { events.push("confirm"); },
  );
  assert.equal(result, "persisted");
  assert.deepEqual(events, ["persist-start", "persist-done", "confirm"]);
});

test("quit stays open after a persistence failure", async () => {
  const events = [];
  const result = await flushChatsBeforeQuit(
    async () => { events.push("persist"); throw new Error("storage unavailable"); },
    async () => { events.push("confirm"); },
  );
  assert.equal(result, "persistence_failed");
  assert.deepEqual(events, ["persist"]);
});
