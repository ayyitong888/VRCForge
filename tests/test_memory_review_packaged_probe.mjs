import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const source = await readFile(resolve(root, "scripts", "diagnose_packaged_memory_restart.mjs"), "utf8");

assert.match(source, /vrcforge\.packaged_memory_restart_probe\.v2/);
assert.match(source, /prepareManifestBoundPackage/);
assert.match(source, /createMemoryReviewProvider/);
assert.match(source, /seedReviewChats/);
assert.match(source, /fetchReviewPair/);
assert.match(source, /fetch_agent_memory_review/);
assert.match(source, /update_agent_memory_review/);
assert.match(source, /run_agent_memory_review/);
assert.match(source, /mutate_agent_memory_review_candidate/);
assert.match(source, /\/api\/app\/agent\/memory\/review\/config/);
assert.match(source, /\/api\/app\/agent\/memory\/review\/run/);
for (const action of ["accept", "reject", "defer", "undo", "erase"]) {
  assert.ok(
    source.includes(`action: "${action}"`) || source.includes(`/${action}`),
    `packaged probe must exercise ${action}`,
  );
}
assert.match(source, /promotion crash after Memory write restart/);
assert.match(source, /concurrentRevisionResults/);
assert.match(source, /seedPromotionCrashState/);
assert.match(source, /before_memory_write/);
assert.match(source, /after_memory_write/);
assert.match(source, /sourceInvalidation/);
assert.match(source, /failNextRequests\(3\)/);
assert.match(source, /provider\.requests\.slice\(providerFailureRequestStart\)/);
assert.match(source, /providerFailureRequests\.length !== 3/);
assert.match(source, /allRequestsForcedFailure/);
assert.match(source, /timeoutMs: 300000/);
assert.doesNotMatch(source, /findTextInTree\(root, needle, maxFiles/);
assert.match(source, /createReadStream\(path, \{ highWaterMark: 1024 \* 1024 \}\)/);
assert.match(source, /needleBuffer\.length - 1/);
assert.match(source, /findTextInRoots\(\[userDataRoot, webviewDataRoot\], crashPromotionText\)/);
assert.match(source, /findTextInRoots\(\[userDataRoot, webviewDataRoot\], editedReviewText\)/);
assert.match(source, /scan\.unreadable\.length > 0/);
assert.doesNotMatch(source, /> 8 \* 1024 \* 1024/);
assert.match(source, /staleAcceptSnapshot\.candidates\?\.\[0\]\?\.state !== "expired"/);
assert.doesNotMatch(source, /expectAppApiFailure\([\s\S]{0,300}stale.*400/i);
assert.match(source, /provider\.requests\.some\(\(request\) => request\.hasTools\)/);
assert.match(source, /Memory Review provider request exposed an exact local project path/);
assert.match(source, /redactionSentinels/);
assert.match(source, /Memory Review provider request exposed a redaction sentinel/);
assert.match(source, /Memory Review WebView projection exposed a redaction sentinel/);
assert.match(source, /Memory Review persisted a redaction sentinel in app-owned storage/);
assert.match(source, /snapshotUnityProjectTree/);
assert.match(source, /\["Assets", "Packages", "ProjectSettings"\]/);
assert.match(source, /finalUnityTreeUnchanged/);
assert.match(source, /Memory Review changed an isolated Unity project file tree/);
assert.match(source, /providerDisclosure/);
assert.match(source, /configuredProjectMatches/);
assert.match(source, /eligibleCount/);
assert.match(source, /candidateCount/);
assert.match(source, /conflictExplanation/);
assert.match(source, /assertGracefulClosure/);
assert.match(source, /snapshotIsClear\(report\.finalCleanup\)/);

console.log("memory review packaged probe contract ok");
