import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const read = (path) => readFile(resolve(import.meta.dirname, "..", path), "utf8");

const [protection, skills, contract] = await Promise.all([
  read("src/components/protection/protection-workspace.tsx"),
  read("src/components/skills/skills-workspace.tsx"),
  read("docs/PRODUCT_REGRESSION_CONTRACT.md"),
]);

// These workspaces live between two independently sized sidebars. Their
// layout must react to the actual center-column width, not the window width.
assert.match(protection, /data-vrcforge-protection-profiles/);
assert.match(protection, /PROTECTION_PROFILE_GRID/);
assert.match(protection, /PROTECTION_WORKSPACE_GRID/);
assert.match(protection, /PROTECTION_CONTROL_GRID/);
assert.match(protection, /function ProtectionProfileLine/);
assert.doesNotMatch(protection, /(?:sm|md|lg|xl):grid-cols-/);

assert.match(skills, /data-vrcforge-skills-layout/);
assert.match(skills, /SKILLS_WORKSPACE_GRID/);
assert.match(skills, /SKILL_FIELD_GRID/);
assert.match(skills, /SKILL_COMPACT_FIELD_GRID/);
assert.doesNotMatch(skills, /(?:sm|md|lg|xl):grid-cols-/);

assert.match(contract, /UX-016 — Center-width-responsive workspaces/);
assert.match(contract, /Optimization, Protection and Skills/);
assert.match(contract, /actual center-column width/);

console.log("center-width-responsive workspace layout contract: ok");
