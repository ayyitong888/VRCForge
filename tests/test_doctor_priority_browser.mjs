import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const repo = fileURLToPath(new URL("..", import.meta.url));
const bundle = await build({
  stdin: {
    resolveDir: repo,
    loader: "tsx",
    contents: `
      import { createRoot } from "react-dom/client";
      import { initializeI18n } from "./src/i18n";
      import { DoctorWorkspace } from "./src/components/doctor/doctor-workspace";
      const checks = Array.from({ length: 10 }, (_, index) => ({
        id: "warning." + index, title: "Warning " + index, status: "warning", howToFix: "Review warning " + index,
      }));
      checks.unshift({ id: "unknown", title: "Unknown check", status: "unknown", howToFix: "Review status" });
      checks.push({ id: "late-error", title: "Late error", status: "error", howToFix: "Review the error", fixable: true });
      void initializeI18n().then(() => createRoot(document.getElementById("root")).render(<DoctorWorkspace
        report={{ ok: false, checks }} loading={false} message="" messageTone="warn" fixingCheckId=""
        lastFixResult={null} chatRecoveries={[]} chatPersistenceBlocked={false} resolvingChatStorageConflict={false}
        exportingSupportBundle={false} onRefresh={() => {}} onFix={() => { window.repaired = true; }}
        onResolveChatConflict={() => {}} onOpenSettings={() => {}} onExportSupportBundle={() => {}}
        onCopy={() => {}} formatPayload={String} />));
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
  const suggestions = page.locator("section").filter({ has: page.getByRole("heading", { name: "Suggested fixes" }) });
  assert.match(await suggestions.innerText(), /Late error[\s\S]+Warning 0[\s\S]+Warning 1/);
  assert.doesNotMatch(await suggestions.innerText(), /Unknown check|Warning 7/);
  await suggestions.getByRole("button", { name: "Late error" }).click();
  const focused = page.locator(":focus");
  assert.match(await focused.innerText(), /Late error/);
  assert.equal(await page.evaluate(() => window.repaired), undefined, "Opening details must not run a repair");
  await page.getByRole("button", { name: "Safe fix" }).click();
  assert.equal(await page.evaluate(() => window.repaired), true, "The existing repair action remains available");
} finally {
  await browser.close();
}
