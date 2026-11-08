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
      import { useState } from "react";
      import { createRoot } from "react-dom/client";
      import { initializeI18n } from "./src/i18n";
      import { CheckpointWorkspace } from "./src/components/checkpoints/checkpoint-workspace";
      const projectRoot = "E:/projects/Avatar Demo";
      const checkpoints = ["Face", "Body"].map((part) => ({
        id: "checkpoint-" + part,
        targetTool: "vrcforge_set_material_texture",
        projectRoot,
        createdAt: "2026-11-08T16:16:42.111Z",
        archiveFiles: ["Assets/Materials/" + part + ".mat", "Assets/Materials/" + part + ".mat.meta"],
        ok: true,
        status: "ready",
      }));
      const noop = () => {};
      function Fixture() {
        const [preview, setPreview] = useState(null);
        return <CheckpointWorkspace checkpoints={checkpoints} selectedProjectPath={projectRoot}
          interruptedRecoveries={[]} adjustmentCheckpoints={[]} preview={preview} recoveryPreview={null}
          adjustmentPreview={null} loading={false} restoringId="" recoveryBusyId="" adjustmentBusyId=""
          message="" recoveryMessage="" adjustmentMessage="" onRefresh={noop}
          onPreview={(id) => {
            const checkpoint = checkpoints.find((item) => item.id === id);
            setPreview({ ok: true, checkpoint, changedFiles: ["M\\t" + checkpoint.archiveFiles[0]], workingTreeStatus: ["?? Assets/Temporary.txt"] });
          }}
          onManageStorage={() => { window.managingStorage = true; }}
          onRestore={(id) => { window.restored = id; }} onPreviewRecovery={noop} onRestoreRecovery={noop}
          onExportRecoveryBundle={noop} onResolveRecovery={noop} onCreateAdjustment={noop} onPreviewAdjustment={noop}
          onSelectAdjustment={noop} onApplyAdjustment={noop} onOverwriteAdjustment={noop}
          onRenameAdjustment={noop} onDeleteAdjustment={noop} />;
      }
      void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture />));
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
  const list = page.locator("[data-vrcforge-checkpoint-list]");
  const detail = page.locator("[data-vrcforge-checkpoint-detail]");
  assert.match(await list.innerText(), /Face\.mat[\s\S]*Body\.mat/);
  assert.match(await detail.innerText(), /Restore Preview/);
  await list.getByRole("button", { name: /Body\.mat/ }).click();
  assert.equal(await list.getByRole("button", { name: /Body\.mat/ }).getAttribute("aria-pressed"), "true");
  const previewText = await detail.innerText();
  assert.match(previewText, /Body\.mat[\s\S]*Changed files \(1\)[\s\S]*Assets\/Materials\/Body\.mat/);
  assert.doesNotMatch(previewText, /Temporary\.txt|vrcforge_set_material_texture/);
  await detail.getByText("Working tree (1)", { exact: true }).click();
  assert.match(await detail.innerText(), /Temporary\.txt/);
  assert.equal(await page.evaluate(() => window.restored), undefined);
  await detail.getByRole("button", { name: "Restore", exact: true }).click();
  assert.equal(await page.evaluate(() => window.restored), "checkpoint-Body");
  await list.getByRole("button", { name: /Manage storage|checkpoint\.manageStorage/ }).click();
  assert.equal(await page.evaluate(() => window.managingStorage), true);
} finally {
  await browser.close();
}
