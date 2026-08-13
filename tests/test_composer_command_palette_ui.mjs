import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const source = await readFile(resolve(root, "src/components/chat/composer.tsx"), "utf8");

assert.match(source, /data-composer-command-palette/);
assert.match(source, /data-composer-palette-item/);
assert.match(source, /max-h-72[^\n]*overflow-y-auto/);
assert.match(source, /paletteCommands\.map\(\(command, index\)/);
assert.match(source, /composerActionIcon\(command\.action\.id\)/);
assert.match(source, /action\.disabledReason/);
assert.match(source, /disabled=\{Boolean\(command\.action\?\.disabled\)\}/, "mouse clicks must not bypass disabled actions");
assert.match(source, /onMouseEnter=\{\(\) => setPaletteIndex\(index\)\}/);
assert.match(source, /event\.key === "ArrowDown"/);
assert.match(source, /event\.key === "ArrowUp"/);
assert.match(source, /event\.key === "Escape"/);
assert.match(source, /event\.key === "Enter"/);
assert.match(source, /setPaletteDismissed\(true\)/);
assert.match(source, /setPaletteDismissed\(false\)/);
assert.match(source, /actions\.length \? actions : \[\{ id: "attach"/);
assert.match(source, /const paletteCommands[\s\S]*actionMenuOpen \? commandActions : slashMatches/);
assert.match(source, /onClick=\{\(\) => \{[\s\S]*setActionMenuOpen\(false\)/);
assert.match(source, /grid-cols-\[minmax\(0,auto\)_minmax\(0,1fr\)\]/);
assert.match(source, /useEffect\(\(\) => \{[\s\S]*setPaletteIndex\(\(current\) => Math\.min\(current/);
assert.match(source, /\[actionMenuOpen, paletteCommands\.length, slashQuery\]/);

const row = source.slice(source.indexOf("{paletteCommands.map"), source.indexOf("</div>", source.indexOf("{paletteCommands.map")));
assert.match(row, /data-composer-action=\{command\.action\?\.id\}/);
assert.match(row, /data-composer-slash-command=\{command\.name\}/);
assert.match(row, /command\.title/);

console.log("composer command palette contract: ok");
