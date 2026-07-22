import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import {
  containsTextValue,
  encodedJsonValueOccurrenceProfile,
  findTextInRoots,
  inspectBoundJsonSource,
  matchesBoundSourceReceipt,
  partitionScanMatches,
  serializedJsonContainsText,
  stringContainsEncodedText,
} from "../scripts/lib/persistence_redaction_scan.mjs";

const root = await mkdtemp(join(tmpdir(), "vrcforge-redaction-scan-"));
try {
  const sourcePath = resolve(root, "source.json");
  const derivedRoot = resolve(root, "derived");
  const derivedPath = resolve(derivedRoot, "candidate.json");
  const unicodePath = resolve(derivedRoot, "unicode.json");
  const windowsNeedle = ["D:", "\\", "private", "\\", "sentinel.txt"].join("");
  await mkdir(derivedRoot, { recursive: true });
  await writeFile(sourcePath, JSON.stringify({ text: windowsNeedle }), "utf8");

  const inspectSource = () => inspectBoundJsonSource({
    filePath: sourcePath,
    expectedText: windowsNeedle,
    needles: [windowsNeedle],
    locateSource: (payload) => ({ container: payload, key: "text" }),
  });
  const exactSource = await inspectSource();
  assert.equal(exactSource.allowSourceMatch, true);

  const sourceOnly = await findTextInRoots([root], windowsNeedle);
  assert.deepEqual(sourceOnly.matches, [sourcePath]);
  assert.equal(sourceOnly.matchDetails[0].sha256, exactSource.fileSha256);
  assert.ok(sourceOnly.matchDetails[0].variantOccurrences.some((count) => count > 0));
  assert.deepEqual(partitionScanMatches(sourceOnly, [sourcePath]).unexpectedMatches, []);
  assert.equal(containsTextValue({ nested: [windowsNeedle] }, windowsNeedle), true);
  assert.equal(serializedJsonContainsText(JSON.stringify({ text: windowsNeedle }), windowsNeedle), true);
  const nestedProviderBody = JSON.stringify({
    messages: [{ content: JSON.stringify({ source: windowsNeedle }) }],
  });
  assert.equal(serializedJsonContainsText(nestedProviderBody, windowsNeedle), true);
  const unicodeEscapedNeedle = windowsNeedle
    .replace("D", "\\u0044")
    .replaceAll("\\", "\\\\");
  assert.equal(stringContainsEncodedText(`prefix:${unicodeEscapedNeedle}`, windowsNeedle), true);
  assert.equal(
    serializedJsonContainsText(JSON.stringify({ error: `prefix:${unicodeEscapedNeedle}` }), windowsNeedle),
    true,
  );
  await writeFile(unicodePath, JSON.stringify({ error: unicodeEscapedNeedle }), "utf8");
  const unicodeDiskLeak = await findTextInRoots([derivedRoot], windowsNeedle);
  assert.deepEqual(unicodeDiskLeak.matches, [unicodePath]);
  assert.equal(unicodeDiskLeak.matchDetails[0].unexpectedEncodedMatch, true);
  await rm(unicodePath);

  const expectedProfile = encodedJsonValueOccurrenceProfile(windowsNeedle, windowsNeedle);
  assert.equal(
    matchesBoundSourceReceipt(sourceOnly.matchDetails[0], exactSource.fileSha256, expectedProfile),
    true,
  );

  await writeFile(
    sourcePath,
    JSON.stringify({ text: windowsNeedle, debug: JSON.stringify({ source: windowsNeedle }) }),
    "utf8",
  );
  assert.equal((await inspectSource()).allowSourceMatch, false);
  await writeFile(
    sourcePath,
    JSON.stringify({
      text: windowsNeedle,
      debug: `prefix:${JSON.stringify({ source: windowsNeedle })}`,
    }),
    "utf8",
  );
  assert.equal((await inspectSource()).allowSourceMatch, false);
  await writeFile(sourcePath, JSON.stringify({ text: windowsNeedle, [windowsNeedle]: false }), "utf8");
  assert.equal((await inspectSource()).allowSourceMatch, false);

  const escapedNeedle = JSON.stringify(windowsNeedle);
  await writeFile(
    sourcePath,
    `{\"hidden\":${escapedNeedle},\"hidden\":\"safe\",\"text\":${escapedNeedle}}`,
    "utf8",
  );
  const duplicateKeySource = await inspectSource();
  assert.equal(duplicateKeySource.allowSourceMatch, true);
  const duplicateKeyScan = await findTextInRoots([root], windowsNeedle);
  assert.equal(
    matchesBoundSourceReceipt(
      duplicateKeyScan.matchDetails[0],
      duplicateKeySource.fileSha256,
      duplicateKeySource.expectedOccurrenceProfiles[0],
    ),
    false,
  );
  await writeFile(sourcePath, JSON.stringify({ text: windowsNeedle }), "utf8");

  await writeFile(
    derivedPath,
    JSON.stringify({ candidate: JSON.stringify({ path: windowsNeedle }) }),
    "utf8",
  );
  const withDerivedLeak = await findTextInRoots([root], windowsNeedle);
  const partition = partitionScanMatches(withDerivedLeak, [sourcePath]);
  assert.deepEqual(partition.allowedMatches, [sourcePath]);
  assert.deepEqual(partition.unexpectedMatches, [derivedPath]);
  assert.equal(withDerivedLeak.matchDetails.length, 2);
  assert.ok(withDerivedLeak.matchDetails.every((detail) => /^[0-9a-f]{64}$/.test(detail.sha256)));
} finally {
  await rm(root, { recursive: true, force: true });
}

console.log("persistence redaction scan contract ok");
