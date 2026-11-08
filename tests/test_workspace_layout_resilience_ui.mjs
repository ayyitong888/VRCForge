import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const read = (path) => readFile(resolve(import.meta.dirname, "..", path), "utf8");

const skills = await read("src/components/skills/skills-workspace.tsx");

// These workspaces live between two independently sized sidebars. Their
// layout must react to the actual center-column width, not the window width.
assert.match(skills, /data-vrcforge-skills-layout/);
assert.match(skills, /SKILLS_WORKSPACE_GRID/);
assert.match(skills, /SKILL_FIELD_GRID/);
assert.match(skills, /SKILL_COMPACT_FIELD_GRID/);
assert.doesNotMatch(skills, /(?:sm|md|lg|xl):grid-cols-/);

console.log("center-width-responsive workspace layout contract: ok");
