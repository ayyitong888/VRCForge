import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { Composer } from "./src/components/chat/composer";
    import { SettingsWorkspace } from "./src/components/settings/settings-workspace";
    import { loadRunningEnterAction, persistRunningEnterAction } from "./src/lib/app-preferences";
    import { DEFAULT_THEME_CUSTOMIZATION } from "./src/lib/theme-customization";
    import { initializeI18n } from "./src/i18n";
    window.calls = [];
    function Fixture() {
      const [input, setInput] = useState("message");
      const [sending, setSending] = useState(true);
      const [queueAllowed, setAllowed] = useState(true);
      const [runningEnterAction, setRunningEnterAction] = useState(loadRunningEnterAction);
      const [suggestion, setSuggestion] = useState();
      window.setSending = setSending;
      window.setAllowed = setAllowed;
      window.setSuggestion = setSuggestion;
      return <>
        <Composer input={input} setInput={setInput} sending={sending} queueAllowed={queueAllowed}
          inputSuggestion={suggestion} onDismissInputSuggestion={() => setSuggestion(undefined)}
          runningEnterAction={runningEnterAction} commands={[{name: "goal", title: "Goal"}]}
          onSubmit={e => { e?.preventDefault(); window.calls.push("send"); }}
          onQueue={() => window.calls.push("queue")} onSteerNow={() => window.calls.push("steer")}
          onSwitchMode={() => {}} />
        <SettingsWorkspace activeSection="chat" currentLanguage="en-US" visionProvider=""
          themeCustomization={DEFAULT_THEME_CUSTOMIZATION} runningEnterAction={runningEnterAction}
          onRunningEnterActionChange={value => {
            setRunningEnterAction(value);
            persistRunningEnterAction(value);
          }} />
      </>;
    }
    initializeI18n().then(() => createRoot(document.getElementById("root")).render(<Fixture />));
  ` },
  bundle: true, write: false, platform: "browser", format: "esm", jsx: "automatic",
  plugins: [{ name: "baseline", setup(plugin) {
    if (process.env.VRCFORGE_COMPOSER_BASELINE) {
      plugin.onLoad({ filter: /components[\\/]chat[\\/]composer\.tsx$/ }, async () => ({
        loader: "tsx", contents: await readFile(process.env.VRCFORGE_COMPOSER_BASELINE, "utf8"),
      }));
    }
  } }],
});
const server = createServer((_request, response) => {
  response.setHeader("Content-Type", "text/html;charset=utf-8");
  response.end('<div id="root"></div><script type="module">' + bundle.outputFiles[0].text.replaceAll("</script", "<\\/script") + "</script>");
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const browser = await chromium.launch({ headless: true, channel: "msedge" });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("http://127.0.0.1:" + server.address().port);
  const input = page.locator("textarea").first();
  const preference = page.locator("#settings-running-enter-action");
  const calls = () => page.evaluate(() => window.calls);
  assert.equal(await input.getAttribute("title"), null, "running input has no floating shortcut hint");
  assert.equal(await preference.inputValue(), "queue", "default preference queues ordinary Enter");
  await input.fill("正在执行时追加");
  await input.press("Enter");
  await input.press("Control+Enter");
  await input.press("Meta+Enter");
  assert.deepEqual(await calls(), ["queue", "steer", "steer"]);
  await input.press("Shift+Enter");
  assert.match(await input.inputValue(), /\n/);
  await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true });
  assert.equal((await calls()).length, 3, "Shift+Enter and IME confirmation do not send");
  await input.fill("/goal");
  await input.press("Enter");
  assert.equal(await input.inputValue(), "/goal ", "Enter still selects a slash palette item");
  assert.equal((await calls()).length, 3);
  await input.fill("follow-up");
  await page.evaluate(() => window.setAllowed(false));
  await page.locator("[data-composer-primary-action=stop]").waitFor();
  await input.press("Enter");
  await input.press("Control+Enter");
  assert.equal((await calls()).length, 3, "running input still honors its delivery gate");

  await page.evaluate(() => window.setAllowed(true));
  await preference.selectOption("steer");
  await input.press("Enter");
  await input.press("Control+Enter");
  assert.deepEqual((await calls()).slice(-2), ["steer", "queue"], "changing Settings swaps the live mapping");
  await page.reload();
  assert.equal(await preference.inputValue(), "steer", "the setting survives reload");
  await input.press("Enter");
  await input.press("Control+Enter");
  assert.deepEqual(await calls(), ["steer", "queue"], "the restored setting controls the composer");
  await page.evaluate(() => window.setSending(false));
  await page.locator("[data-composer-primary-action=send]").waitFor();
  await input.press("Enter");
  await input.press("Control+Enter");
  assert.deepEqual((await calls()).slice(-2), ["send", "send"], "idle keys still send a normal message");
  assert.equal(await input.getAttribute("title"), null, "idle input has no floating shortcut hint");

  await input.fill("");
  await page.evaluate(() => window.setSuggestion("继续检查输入预测"));
  const preview = page.locator("[data-composer-input-suggestion]");
  await preview.waitFor();
  await page.keyboard.insertText("继续");
  await preview.waitFor({ timeout: 2000 });
  assert.equal(await preview.innerText(), "检查输入预测");
  assert.equal(await input.inputValue(), "继续", "preview stays outside the textarea value");
  await input.press("Shift+ArrowLeft");
  await preview.waitFor({ state: "hidden", timeout: 2000 });
  await input.press("End");
  await preview.waitFor({ timeout: 2000 });
  const beforeTab = await calls();
  await input.press("Tab");
  assert.equal(await input.inputValue(), "继续检查输入预测");
  assert.deepEqual(await calls(), beforeTab, "Tab accepts the suffix without sending");
  assert.deepEqual(errors, []);
  console.log("PASS composer delivery keys, settings, slash palette, IME, gate and inline prediction");
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
