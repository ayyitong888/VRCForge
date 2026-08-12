import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const api = readFileSync(new URL("../src/lib/api/optimization.ts", import.meta.url), "utf8");
const controller = readFileSync(
  new URL("../src/hooks/use-optimization-workspace-controller.ts", import.meta.url),
  "utf8",
);

test("package install result exposes compatibility-first message", () => {
  assert.match(api, /export type PackageInstallRequestResult = \{[\s\S]*message\?: string;/);
  assert.match(api, /approvalCreated\?: boolean;/);
});

test("installed package message is shown instead of claiming an install was queued", () => {
  assert.match(
    controller,
    /payload\.message \|\| payload\.error \|\| "Install request was not queued\."/,
  );
  assert.doesNotMatch(controller, /payload\.error \|\| "Install request queued\."/);
});
