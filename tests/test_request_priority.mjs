import assert from "node:assert/strict";

import { LatestForegroundRequestGate } from "../src/lib/request-priority.ts";

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

async function appliesOnlyWhileCurrent(gate, token, pending, apply) {
  const value = await pending;
  if (gate.isCurrent(token)) {
    apply(value);
  }
}

{
  const gate = new LatestForegroundRequestGate();
  const oldRead = deferred();
  const foregroundRead = deferred();
  let state = "initial";

  const oldToken = gate.beginBackground();
  assert.notEqual(oldToken, null);
  const oldTask = appliesOnlyWhileCurrent(gate, oldToken, oldRead.promise, (value) => {
    state = value;
  });

  const foregroundToken = gate.beginForeground();
  const foregroundTask = appliesOnlyWhileCurrent(gate, foregroundToken, foregroundRead.promise, (value) => {
    state = value;
  });
  assert.equal(gate.beginBackground(), null, "background refresh must not start during foreground work");

  foregroundRead.resolve("foreground-new");
  await foregroundTask;
  gate.endForeground(foregroundToken);
  oldRead.resolve("background-old");
  await oldTask;
  assert.equal(state, "foreground-new", "late background data must not overwrite foreground state");
}

{
  const gate = new LatestForegroundRequestGate();
  const firstRead = deferred();
  const secondRead = deferred();
  let state = "initial";

  const firstToken = gate.beginForeground();
  const firstTask = appliesOnlyWhileCurrent(gate, firstToken, firstRead.promise, (value) => {
    state = value;
  });
  const secondToken = gate.beginForeground();
  const secondTask = appliesOnlyWhileCurrent(gate, secondToken, secondRead.promise, (value) => {
    state = value;
  });

  secondRead.resolve("second-new");
  await secondTask;
  gate.endForeground(secondToken);
  firstRead.resolve("first-old");
  await firstTask;
  gate.endForeground(firstToken);
  assert.equal(state, "second-new", "a late earlier foreground response must be stale");
  assert.notEqual(gate.beginBackground(), null, "background retry must resume after foreground completion");
}

{
  const gate = new LatestForegroundRequestGate();
  const writeResponse = deferred();
  const staleRead = deferred();
  let state = "initial";

  const writeToken = gate.beginAuthoritative();
  const writeTask = (async () => {
    const value = await writeResponse.promise;
    if (gate.commitAuthoritative(writeToken) !== null) {
      state = value;
    }
  })();
  const readToken = gate.beginForeground();
  const readTask = appliesOnlyWhileCurrent(gate, readToken, staleRead.promise, (value) => {
    state = value;
  });

  staleRead.resolve("stale-before-write");
  await readTask;
  assert.equal(state, "stale-before-write");
  writeResponse.resolve("authoritative-after-write");
  await writeTask;
  assert.equal(state, "authoritative-after-write", "a successful write must invalidate stale reads");
  gate.endForeground(readToken);
  gate.endForeground(writeToken);
}

console.log("request priority contract: ok");
