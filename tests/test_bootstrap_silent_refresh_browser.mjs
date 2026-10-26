import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const source = await readFile('src/App.tsx', 'utf8');
const sourceFile = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function findFunction(name) {
  let found = null;
  const visit = (node) => {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node.getText(sourceFile);
    if (!found) ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  assert.ok(found, `App.tsx must define ${name}`);
  return ts.transpileModule(found, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function makeHarness() {
  const bootstrapRequestSequenceRef = { current: 0 };
  const bootstrapForegroundRequestRef = { current: 0 };
  const bootstrapSilentRefreshPromiseRef = { current: null };
  const bootstrapSilentPendingTargetRef = { current: null };
  const bootstrapSilentActiveTargetRef = { current: null };
  const calls = [];
  const applied = [];
  const waiters = [];
  let state = { old: true };
  const fetchBootstrap = (target) => {
    const item = deferred();
    calls.push({ target, item });
    return item.promise;
  };
  const setBootstrap = (value) => {
    state = typeof value === 'function' ? value(state) : value;
    applied.push(state);
  };
  const exports = new Function(
    'endpoint', 'bootstrapRequestSequenceRef', 'bootstrapForegroundRequestRef',
    'bootstrapSilentRefreshPromiseRef', 'bootstrapSilentPendingTargetRef', 'bootstrapSilentActiveTargetRef',
    'fetchBootstrap', 'setBootstrap', 'setCachedProjectSnapshot', 'cacheProjectSnapshot',
    'setStartupIssue', 'setError', 'isRuntimeSessionVerificationError',
    'handleRuntimeSessionFailure',
    `${findFunction('refresh')}\n${findFunction('refreshSilently')}\nreturn { refresh, refreshSilently };`,
  )(
    'endpoint-A', bootstrapRequestSequenceRef, bootstrapForegroundRequestRef,
    bootstrapSilentRefreshPromiseRef, bootstrapSilentPendingTargetRef, bootstrapSilentActiveTargetRef,
    fetchBootstrap, setBootstrap, () => {}, () => {}, () => {}, () => {},
    () => false, () => {},
  );
  return { ...exports, calls, applied, waiters, get state() { return state; }, bootstrapRequestSequenceRef, bootstrapForegroundRequestRef, bootstrapSilentRefreshPromiseRef, bootstrapSilentPendingTargetRef, bootstrapSilentActiveTargetRef };
}

const h = makeHarness();
const concurrent = Array.from({ length: 20 }, () => h.refreshSilently('endpoint-A'));
assert.equal(h.calls.length, 1, 'overlapping silent refreshes must use one active request');
h.calls[0].item.resolve({ id: 'first', health: { projects: { projects: [] } } });
await new Promise((resolve) => setImmediate(resolve));
assert.equal(h.calls.length, 2, 'overlapping refreshes must leave one latest follow-up');
h.calls[1].item.resolve({ id: 'latest', health: { projects: { projects: [] } } });
await Promise.all(concurrent);
assert.deepEqual(h.applied.map((item) => item.id), ['first', 'latest'], 'same-target coalescing must preserve progress and the latest follow-up');

const steadyPolling = makeHarness();
const firstPoll = steadyPolling.refreshSilently('endpoint-A');
const firstPollBurst = Array.from({ length: 8 }, () => steadyPolling.refreshSilently('endpoint-A'));
assert.equal(steadyPolling.calls.length, 1, 'polls during one fetch must coalesce');
steadyPolling.calls[0].item.resolve({ id: 'progress-1', health: { projects: { projects: [] } } });
await new Promise((resolve) => setImmediate(resolve));
assert.deepEqual(steadyPolling.applied.map((item) => item.id), ['progress-1'], 'a continuous same-target poll stream must not invalidate every response');
assert.equal(steadyPolling.calls.length, 2, 'one latest poll should follow the active fetch');
const secondPollBurst = Array.from({ length: 8 }, () => steadyPolling.refreshSilently('endpoint-A'));
steadyPolling.calls[1].item.resolve({ id: 'progress-2', health: { projects: { projects: [] } } });
await new Promise((resolve) => setImmediate(resolve));
assert.deepEqual(steadyPolling.applied.map((item) => item.id), ['progress-1', 'progress-2']);
assert.equal(steadyPolling.calls.length, 3);
const steadyLatest = steadyPolling.bootstrapSilentRefreshPromiseRef.current;
steadyPolling.calls[2].item.resolve({ id: 'latest', health: { projects: { projects: [] } } });
await Promise.all([firstPoll, steadyLatest, ...firstPollBurst, ...secondPollBurst]);
assert.equal(steadyPolling.applied.at(-1).id, 'latest');

const session = makeHarness();
const oldTarget = session.refreshSilently('endpoint-A');
const newTarget = session.refreshSilently('endpoint-B');
assert.equal(session.calls.length, 1);
session.calls[0].item.resolve({ id: 'old-session', health: { projects: { projects: [] } } });
await new Promise((resolve) => setImmediate(resolve));
assert.equal(session.calls.length, 2, 'a target change during an active fetch must queue the new endpoint');
assert.equal(session.calls[1].target, 'endpoint-B');
session.calls[1].item.resolve({ id: 'new-session', health: { projects: { projects: [] } } });
await Promise.all([oldTarget, newTarget]);
assert.deepEqual(session.applied.map((item) => item.id), ['new-session']);

const foregroundRace = makeHarness();
const firstSilent = foregroundRace.refreshSilently('endpoint-A');
const foreground = foregroundRace.refresh('endpoint-A');
const duringForeground = foregroundRace.refreshSilently('endpoint-A');
assert.equal(foregroundRace.calls.length, 2);
foregroundRace.calls[0].item.resolve({ id: 'stale-silent', health: { projects: { projects: [] } } });
await Promise.all([firstSilent, duringForeground]);
assert.deepEqual(foregroundRace.applied, [], 'silent result superseded by foreground refresh must not apply');
foregroundRace.calls[1].item.resolve({ id: 'foreground', health: { projects: { projects: [] } } });
await foreground;
await new Promise((resolve) => setImmediate(resolve));
assert.equal(foregroundRace.calls.length, 3, 'silent trigger during foreground request must run once afterward');
const latestSilent = foregroundRace.bootstrapSilentRefreshPromiseRef.current;
foregroundRace.calls[2].item.resolve({ id: 'latest-after-foreground', health: { projects: { projects: [] } } });
await latestSilent;
assert.deepEqual(foregroundRace.applied.map((item) => item.id), ['foreground', 'latest-after-foreground']);

console.log(JSON.stringify({ status: 'passed', coalescedCalls: h.calls.length, staleSessionSuppressed: true }));
