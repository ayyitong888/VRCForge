import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";

function textVariants(needle) {
  const variants = new Set();
  let encoded = String(needle ?? "");
  for (let depth = 0; depth < 5; depth += 1) {
    variants.add(encoded);
    encoded = JSON.stringify(encoded).slice(1, -1);
  }
  return [...variants];
}

function byteVariants(needle) {
  return textVariants(needle).map((value) => Buffer.from(value, "utf8"));
}

function countBufferOccurrences(buffer, needle) {
  let count = 0;
  let offset = 0;
  while (offset <= buffer.length - needle.length) {
    const foundAt = buffer.indexOf(needle, offset);
    if (foundAt < 0) break;
    count += 1;
    offset = foundAt + 1;
  }
  return count;
}

function decodeEscapesOnce(value) {
  const escapes = {
    "\\\"": "\"",
    "\\\\": "\\",
    "\\/": "/",
    "\\b": "\b",
    "\\f": "\f",
    "\\n": "\n",
    "\\r": "\r",
    "\\t": "\t",
  };
  return value
    .replace(/\\(?:["\\/bfnrt])/g, (match) => escapes[match] ?? match)
    .replace(/\\u\{([0-9a-f]{1,6})\}/gi, (match, hex) => {
      const codePoint = Number.parseInt(hex, 16);
      return codePoint <= 0x10ffff ? String.fromCodePoint(codePoint) : match;
    })
    .replace(/\\u([0-9a-f]{4})/gi, (_match, hex) => String.fromCharCode(Number.parseInt(hex, 16)))
    .replace(/\\x([0-9a-f]{2})/gi, (_match, hex) => String.fromCharCode(Number.parseInt(hex, 16)));
}

export function stringContainsEncodedText(value, needle) {
  const variants = textVariants(needle);
  let decoded = String(value ?? "");
  for (let depth = 0; depth < 8; depth += 1) {
    if (variants.some((variant) => decoded.includes(variant))) return true;
    const next = decodeEscapesOnce(decoded);
    if (next === decoded) break;
    decoded = next;
  }
  return false;
}

export function stringContainsUnexpectedEncodedText(value, needle) {
  let remainder = String(value ?? "");
  for (const variant of [...textVariants(needle)].sort((left, right) => right.length - left.length)) {
    remainder = remainder.split(variant).join("");
  }
  return stringContainsEncodedText(remainder, needle);
}

function containsTextValueAtDepth(value, needle, depth, seen) {
  if (typeof value === "string") {
    if (stringContainsEncodedText(value, needle)) return true;
    if (depth >= 8) return false;
    const trimmed = value.trim();
    if (
      trimmed.length === 0
      || trimmed.length > 1024 * 1024
      || !["{", "[", "\""].includes(trimmed[0])
    ) return false;
    try {
      return containsTextValueAtDepth(JSON.parse(trimmed), needle, depth + 1, seen);
    } catch {
      return false;
    }
  }
  if (Array.isArray(value)) {
    if (seen.has(value)) return false;
    seen.add(value);
    return value.some((item) => containsTextValueAtDepth(item, needle, depth, seen));
  }
  if (value && typeof value === "object") {
    if (seen.has(value)) return false;
    seen.add(value);
    return Object.entries(value).some(
      ([key, item]) => containsTextValueAtDepth(key, needle, depth, seen)
        || containsTextValueAtDepth(item, needle, depth, seen),
    );
  }
  return false;
}

export function containsTextValue(value, needle) {
  return containsTextValueAtDepth(value, String(needle ?? ""), 0, new WeakSet());
}

export function serializedJsonContainsText(raw, needle) {
  try {
    return containsTextValue(JSON.parse(String(raw || "")), needle);
  } catch {
    return byteVariants(needle).some((variant) => Buffer.from(String(raw || ""), "utf8").includes(variant));
  }
}

export function encodedJsonValueOccurrenceProfile(value, needle) {
  const serialized = Buffer.from(JSON.stringify(value), "utf8");
  return byteVariants(needle).map((variant) => countBufferOccurrences(serialized, variant));
}

export function matchesBoundSourceReceipt(receipt, fileSha256, expectedProfile) {
  return Boolean(receipt)
    && receipt.sha256 === fileSha256
    && receipt.unexpectedEncodedMatch === false
    && Array.isArray(receipt.variantOccurrences)
    && receipt.variantOccurrences.length === expectedProfile.length
    && receipt.variantOccurrences.every((count, index) => count === expectedProfile[index]);
}

export async function inspectBoundJsonSource({
  filePath,
  expectedText,
  needles,
  locateSource,
}) {
  const result = {
    fileSha256: "",
    sourceTextSha256: createHash("sha256").update(expectedText, "utf8").digest("hex"),
    targetExact: false,
    noAdditionalSentinels: false,
    additionalSentinelMatches: [],
    expectedOccurrenceProfiles: needles.map(
      (needle) => encodedJsonValueOccurrenceProfile(expectedText, needle),
    ),
    allowSourceMatch: false,
  };
  try {
    const raw = await readFile(filePath, "utf8");
    const parsed = JSON.parse(raw);
    result.fileSha256 = createHash("sha256").update(raw, "utf8").digest("hex");
    const binding = locateSource(parsed);
    if (
      binding?.container
      && typeof binding.container === "object"
      && Object.hasOwn(binding.container, binding.key)
      && binding.container[binding.key] === expectedText
    ) {
      result.targetExact = true;
      binding.container[binding.key] = "";
    }
    result.additionalSentinelMatches = needles.map(
      (needle) => containsTextValue(parsed, needle),
    );
    result.noAdditionalSentinels = result.additionalSentinelMatches.every((matched) => !matched);
    result.allowSourceMatch = result.targetExact && result.noAdditionalSentinels;
  } catch {
    // The caller treats unreadable, malformed, or unbound sources as non-allowlisted.
  }
  return result;
}

export async function findTextInTree(root, needle) {
  const pending = [root];
  let scannedFiles = 0;
  let scannedBytes = 0;
  const matches = [];
  const matchDetails = [];
  const unreadable = [];
  const variants = byteVariants(needle);
  const overlap = Math.max(
    8192,
    ...variants.map((variant) => variant.length - 1),
  );
  while (pending.length) {
    const current = pending.pop();
    let entries;
    try {
      entries = await readdir(current, { withFileTypes: true });
    } catch (error) {
      unreadable.push({ path: current, reason: String(error?.code || "read_failed") });
      continue;
    }
    for (const entry of entries) {
      const path = resolve(current, entry.name);
      if (entry.isDirectory()) {
        pending.push(path);
        continue;
      }
      if (!entry.isFile()) {
        unreadable.push({ path, reason: "unsupported_entry" });
        continue;
      }
      scannedFiles += 1;
      try {
        let tail = Buffer.alloc(0);
        const variantOccurrences = variants.map(() => 0);
        let unexpectedEncodedMatch = false;
        const digest = createHash("sha256");
        let fileBytes = 0;
        for await (const chunk of createReadStream(path, { highWaterMark: 1024 * 1024 })) {
          digest.update(chunk);
          fileBytes += chunk.length;
          scannedBytes += chunk.length;
          const combined = tail.length ? Buffer.concat([tail, chunk]) : chunk;
          if (stringContainsUnexpectedEncodedText(combined.toString("utf8"), needle)) {
            unexpectedEncodedMatch = true;
          }
          for (let index = 0; index < variants.length; index += 1) {
            const variant = variants[index];
            let offset = 0;
            while (offset <= combined.length - variant.length) {
              const foundAt = combined.indexOf(variant, offset);
              if (foundAt < 0) break;
              if (foundAt + variant.length > tail.length) variantOccurrences[index] += 1;
              offset = foundAt + 1;
            }
          }
          tail = overlap
            ? Buffer.from(combined.subarray(Math.max(0, combined.length - overlap)))
            : Buffer.alloc(0);
        }
        if (variantOccurrences.some((count) => count > 0) || unexpectedEncodedMatch) {
          matches.push(path);
          matchDetails.push({
            path,
            sha256: digest.digest("hex"),
            bytes: fileBytes,
            variantOccurrences,
            unexpectedEncodedMatch,
          });
        }
      } catch (error) {
        unreadable.push({ path, reason: String(error?.code || "read_failed") });
      }
    }
  }
  return { scannedFiles, scannedBytes, matches, matchDetails, unreadable };
}

export async function findTextInRoots(roots, needle) {
  const scans = await Promise.all(roots.map((root) => findTextInTree(root, needle)));
  return {
    scannedFiles: scans.reduce((total, scan) => total + scan.scannedFiles, 0),
    scannedBytes: scans.reduce((total, scan) => total + scan.scannedBytes, 0),
    matches: scans.flatMap((scan) => scan.matches),
    matchDetails: scans.flatMap((scan) => scan.matchDetails),
    unreadable: scans.flatMap((scan) => scan.unreadable),
  };
}

function pathKey(path) {
  const normalized = resolve(path);
  return process.platform === "win32" ? normalized.toLocaleLowerCase("en-US") : normalized;
}

export function partitionScanMatches(scan, allowedPaths = []) {
  const allowed = new Set(allowedPaths.map((path) => pathKey(path)));
  const allowedMatches = [];
  const unexpectedMatches = [];
  for (const path of scan.matches) {
    (allowed.has(pathKey(path)) ? allowedMatches : unexpectedMatches).push(path);
  }
  return { allowedMatches, unexpectedMatches };
}
