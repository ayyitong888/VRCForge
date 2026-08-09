import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const source = await readFile(resolve(root, "scripts", "diagnose_packaged_memory_restart.mjs"), "utf8");

assert.match(source, /vrcforge\.packaged_memory_restart_probe\.v2/);
assert.match(source, /const allowUnpushed = process\.argv\.includes\("--allow-unpushed"\)/);
assert.match(source, /const selfTest = process\.argv\.includes\("--self-test"\)/);
assert.match(source, /const allowedOptions = new Set\(\["--allow-unpushed", "--self-test", "--help", "-h"\]\)/);
assert.match(source, /Unknown packaged Memory restart probe option/);
assert.match(source, /Usage: node scripts\/diagnose_packaged_memory_restart\.mjs \[--allow-unpushed\] \[--self-test\]/);
assert.match(source, /function normalizeBuildPolicy\(manifest\)/);
assert.match(source, /function isStrictBuildPolicy\(policy\)/);
assert.match(source, /function isLocalAcceptanceBuildPolicy\(policy\)/);
assert.match(source, /function runSelfTest\(\)/);
assert.match(source, /Memory restart probe self-test passed/);
assert.match(source, /Strict packaged probe requires clean HEAD=origin\/main and a strict release-eligible buildPolicy/);
assert.match(source, /--allow-unpushed requires a clean worktree plus buildPolicy\.mode=local-acceptance, releaseEligible=false, allowDirty=false, and allowUnpushed=true/);
assert.match(source, /mode: allowUnpushed \? "local-preacceptance" : "strict-release"/);
assert.match(source, /strictReleaseBinding: false/);
assert.match(source, /releaseEligible: false/);
assert.match(source, /worktreeClean: releaseBinding\.worktreeClean/);
assert.match(source, /buildPolicy: releaseBinding\.buildPolicy/);
assert.match(source, /portableSha256: releaseBinding\.portableSha256/);
assert.match(source, /extractedExeSha256: releaseBinding\.exeSha256/);
assert.match(source, /prepareManifestBoundPackage/);
assert.match(source, /waitForCdpPage/);
assert.match(source, /targets\.find\(\(target\) => target\?\.type === "page" && target\?\.webSocketDebuggerUrl\)/);
assert.match(source, /createMemoryReviewProvider/);
assert.match(source, /seedReviewChats/);
assert.match(source, /fetchReviewPair/);
assert.match(source, /fetch_agent_memory_review/);
assert.match(source, /runAutomaticMemoryPackagedProof/);
assert.match(source, /undoAndEraseAutomaticMemory/);
assert.match(source, /configureAutomaticReviewScope/);
assert.match(source, /onlyCandidateMatching/);
assert.match(source, /automatic Memory \$\{scope\} Review scope configuration did not bind the requested scope/);
assert.match(source, /automatic Memory scope selection requires the default-on, provider-free Review configuration/);
assert.match(source, /savedVia: "packaged-webview-tauri-ipc"/);
assert.match(source, /"save_chats"/);
assert.match(source, /planner: "packaged-memory-probe"/);
assert.match(source, /shellNeeded: false/);
assert.match(source, /automaticCapture\.eligibleCount/);
assert.match(source, /automaticCapture\.acceptedCount/);
assert.match(source, /automaticCapture\.conflictCount/);
assert.match(source, /historicalCreatedAt/);
assert.match(source, /I prefer \$\{marker\} historical accents/);
assert.match(source, /providerRequestBaseline/);
assert.match(source, /providerRequestCount/);
assert.match(source, /providerRequestDelta/);
assert.match(source, /networkRequests/);
assert.match(source, /automatic Memory provider baseline was not zero/);
assert.match(source, /automatic Memory chat capture made a provider request/);
assert.match(source, /automatic Memory probe did not retain the isolated loopback custom provider configuration/);
assert.doesNotMatch(source, /providerRequests: 0/);
assert.match(source, /I prefer secret/);
assert.match(source, /I prefer api-key/);
assert.match(source, /I prefer no approvals for future writes/);
assert.match(source, /I like to run this command now/);
assert.match(source, /automatic Memory admitted a historical, credential, permission, or action negative/);
assert.match(source, /automatic Memory restart/);
assert.match(source, /automatic Memory cleanup restart/);
assert.match(source, /action: "undo"/);
assert.match(source, /action: "erase"/);
assert.match(source, /automaticCaptureEnabled: current\.rest\.automaticCaptureEnabled/);
assert.match(source, /projectARoot/);
assert.match(source, /projectBRoot/);
assert.match(source, /"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"/);
assert.match(source, /OPENAI_API_KEY.*ANTHROPIC_API_KEY.*GOOGLE_API_KEY/s);
assert.match(source, /\^VRCFORGE_\.\*\(\?:API_KEY\|PROVIDER\|BASE_URL\|PROXY\|TOKEN\|SECRET\)/);
assert.ok(
  source.indexOf("provider = createMemoryReviewProvider()")
    < source.indexOf("const automaticMemory = await runAutomaticMemoryPackagedProof"),
  "automatic Memory proof must use the isolated loopback provider configured before capture",
);
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
assert.match(source, /persistence_redaction_scan\.mjs/);
assert.match(source, /inspectRedactionSource/);
assert.match(source, /stableSourceAllowance/);
assert.match(source, /receiptBound/);
assert.match(source, /matchesBoundSourceReceipt/);
assert.match(source, /expectedOccurrenceProfiles/);
assert.match(source, /sourcePositiveControlPassed/);
assert.match(source, /allowedMatches\.length === 1/);
assert.match(source, /redaction source positive control did not bind every sentinel scan/);
assert.match(source, /partitionScanMatches/);
assert.match(source, /unexpectedMatches\.length > 0/);
assert.match(source, /serializedJsonContainsText/);
assert.match(source, /stringContainsEncodedText/);
assert.match(source, /containsTextValue/);
assert.match(source, /observeReviewProjection/);
assert.match(source, /path\.startsWith\("\/api\/app\/agent\/memory\/review"\)/);
assert.match(source, /command\.includes\("agent_memory_review"\)/);
assert.match(source, /redactKnownProbeValues/);
assert.match(source, /reportContentDropped/);
assert.match(source, /redactionSourceInventory/);
assert.match(source, /unexpectedMatchFiles/);
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
assert.match(source, /requestPackagedAppQuit/);
assert.match(source, /quit\.accepted/);
assert.match(source, /function sameLaunchIdentity\(expected, observed\)/);
assert.match(source, /startedAtUtc/);
assert.match(source, /creationDateUtc/);
assert.match(source, /captureLaunchIdentity\(child\.pid\)/);
assert.match(source, /function cdpOwnershipAllowsAction\(ownership\)/);
assert.match(source, /function closeAuthorizationAction\(identityCheck, cdpOwnership\)/);
assert.match(source, /closeCalls\.quit !== 0 \|\| closeCalls\.force !== 1/);
assert.match(source, /waitForOwnedCdpListener\(launch\)/);
assert.match(source, /\$ids\.Contains\(\[int\]\$listeners\[0\]\.OwningProcess\)/);
assert.match(source, /captureLaunchIdentity\(processId, timeoutMs = 5000\)/);
assert.match(source, /await sleep\(50\)/);
assert.match(source, /Tracked packaged PID generation changed; no Quit or forced termination is authorized/);
const memoryClose = source.slice(
  source.indexOf("async function closePackagedApp(launch)"),
  source.indexOf("function assertGracefulClosure", source.indexOf("async function closePackagedApp(launch)")),
);
assert.ok(memoryClose.indexOf("validateLaunchIdentity(launch)") < memoryClose.indexOf("requestPackagedAppQuit(launch.cdp)"));
assert.ok(memoryClose.indexOf("inspectCdpListenerOwnership(launch)") < memoryClose.indexOf("requestPackagedAppQuit(launch.cdp)"));
assert.ok(memoryClose.indexOf('if (closeAction === "force-own-root")') < memoryClose.indexOf("requestPackagedAppQuit(launch.cdp)"));
assert.match(memoryClose, /forced: true,[\s\S]*evidenceFailure: true/);
assert.match(memoryClose, /forceCloseLaunch\(launch\)/);
const memoryForce = source.slice(
  source.indexOf("async function forceCloseLaunch(launch)"),
  source.indexOf("async function closePackagedApp(launch)"),
);
assert.equal((memoryForce.match(/Assert-TrackedRootIdentity/g) || []).length, 3);
assert.ok(memoryForce.lastIndexOf("Assert-TrackedRootIdentity") < memoryForce.indexOf("Stop-Process -Force"));
const memoryLaunch = source.slice(
  source.indexOf("async function launchPackagedApp()"),
  source.indexOf("async function readAppToken", source.indexOf("async function launchPackagedApp()")),
);
assert.ok(memoryLaunch.indexOf("waitForOwnedCdpListener(launch)") < memoryLaunch.indexOf("waitForCdpPage(45000)"));
assert.ok(memoryLaunch.indexOf("waitForCdpPage(45000)") < memoryLaunch.indexOf("const connectOwnership"));
assert.ok(memoryLaunch.indexOf("const connectOwnership") < memoryLaunch.indexOf("connectCdp(page.webSocketDebuggerUrl)"));
assert.doesNotMatch(source, /CloseMainWindow/);
assert.doesNotMatch(source, /requestManagedBackendShutdown/);
assert.match(source, /waitForPackagedClear\(30000\)/);
assert.match(source, /snapshotIsClear\(report\.finalCleanup\)/);

console.log("memory review packaged probe contract ok");
