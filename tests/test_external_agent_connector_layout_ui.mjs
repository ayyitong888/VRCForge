import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const source = await readFile(
  resolve(import.meta.dirname, "..", "src", "components", "settings", "external-agent-connectors-panel.tsx"),
  "utf8",
);
const genericRow = source.slice(source.indexOf("function GenericConnectorRow"), source.indexOf("function ConnectorToggle"));

assert.match(genericRow, /flex min-w-0 flex-wrap items-start justify-between gap-3/);
assert.match(genericRow, /mt-3 grid gap-2 text-xs text-muted-foreground/);
assert.match(genericRow, /className="h-9 w-full min-w-0/);
assert.doesNotMatch(genericRow, /md:grid-cols-\[minmax\(0,1fr\)_auto\]/);
assert.ok(
  genericRow.indexOf("connector.copyStdio") < genericRow.indexOf("connector.genericHint"),
  "generic connector actions belong in the header before the full-width description and path input",
);

console.log("external Agent connector layout UI contract: ok");
