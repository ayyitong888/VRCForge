import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/lib/api/optimization.ts", import.meta.url), "utf8");

test("package install result exposes compatibility-first message", () => {
  assert.match(api, /export type PackageInstallRequestResult = \{[\s\S]*message\?: string;/);
  assert.match(api, /approvalCreated\?: boolean;/);
});
