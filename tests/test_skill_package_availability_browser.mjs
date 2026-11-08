import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: {
    resolveDir: fileURLToPath(new URL("..", import.meta.url)),
    loader: "tsx",
    contents: `
      import { createRoot } from "react-dom/client";
      import { initializeI18n } from "./src/i18n";
      import { SkillPackageManagerPanel } from "./src/components/skills/skill-package-manager-panel";
      const noop = async () => {};
      const packages = [
        { id: "available", title: "Available skill", enabled: true },
        { id: "paused", title: "Paused skill", enabled: false, signatureStatus: "signed", governance: { enableAllowed: true, signerTrustStatus: "trusted" } },
        { id: "unsigned", title: "Untrusted skill", enabled: false, signatureStatus: "signed", governance: { enableAllowed: true, signerTrustStatus: "untrusted" } },
        { id: "blocked", title: "Restricted skill", enabled: false, governance: { enableAllowed: false, blockingReasons: ["Permission is restricted"] } },
      ];
      void initializeI18n().then(() => createRoot(document.getElementById("root")).render(
        <SkillPackageManagerPanel packages={packages} endpoint="" packageStore="" loading={false}
          message="" error="" governance={{}} audit={[]} pathToSkillDraftSeed={null}
          onRefresh={noop} onPreflight={noop} onImport={noop} onExport={noop} onSetEnabled={noop}
          onUninstall={async (id) => { window.uninstalled = id; }}
          onSetSafeMode={noop} onTrustSigner={noop} onRevokeSigner={noop} onBlockPackage={noop}
          onPreviewPathToSkill={noop} onWritePathToSkill={noop} selectedUnityProjectPath="" onRefreshApprovals={noop} />
      ));
    `,
  },
  bundle: true,
  write: false,
  jsx: "automatic",
});

const browser = await chromium.launch({ headless: true, channel: "msedge" });
try {
  const page = await browser.newPage({ locale: "en-US" });
  await page.setContent('<div id="root"></div>');
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const card = (name) => page.locator("[data-vrcforge-installed-skill-card]").filter({ hasText: name });
  const paused = card("Paused skill");
  assert.match(await paused.innerText(), /Disabled/);
  assert.doesNotMatch(await paused.innerText(), /Blocked|Signed/);
  assert.match(await card("Available skill").innerText(), /Enabled/);
  assert.match(await card("Untrusted skill").innerText(), /Disabled[\s\S]*Untrusted signer/);
  assert.match(await card("Restricted skill").innerText(), /Blocked[\s\S]*Permission is restricted/);
  assert.equal(await paused.getByRole("button", { name: "Uninstall" }).count(), 0);
  await paused.getByText("Advanced actions", { exact: true }).click();
  await paused.getByRole("button", { name: "Uninstall" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  assert.equal(await page.evaluate(() => window.uninstalled), undefined);
  await paused.getByRole("button", { name: "Uninstall" }).click();
  await dialog.getByRole("button", { name: "Uninstall" }).click();
  assert.equal(await page.evaluate(() => window.uninstalled), "paused");
} finally {
  await browser.close();
}
