import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const source = await readFile(
  resolve(import.meta.dirname, "..", "src", "components", "settings", "external-agent-connectors-panel.tsx"),
  "utf8",
);
const genericRow = source.slice(source.indexOf("function GenericConnectorRow"), source.indexOf("function ConnectorToggle"));

assert.match(genericRow, /connector\.genericGuideTitle/);
assert.match(genericRow, /connector\.genericStepConfig/);
assert.match(genericRow, /connector\.genericStepTransport/);
assert.match(genericRow, /connector\.genericStepReload/);
assert.match(genericRow, /sm:grid-cols-2/);
assert.match(genericRow, /connector\.genericStdioHint/);
assert.match(genericRow, /connector\.genericHttpHint/);
assert.match(genericRow, /connector\.genericAutoTitle/);
assert.match(genericRow, /htmlFor="generic-mcp-config-path"/);
assert.match(genericRow, /id="generic-mcp-config-path"/);
assert.match(genericRow, /connector\.genericInstallStdio/);
assert.match(genericRow, /className="h-9 w-full min-w-0/);
assert.doesNotMatch(genericRow, /md:grid-cols-\[minmax\(0,1fr\)_auto\]/);
assert.ok(
  genericRow.indexOf("connector.copyStdio") < genericRow.indexOf("connector.genericAutoTitle"),
  "transport choices must appear before the optional automatic JSON installation path",
);

console.log("external Agent connector layout UI contract: ok");
