import assert from "node:assert/strict";
import { build } from "esbuild";
import { chromium } from "playwright";

const fixture = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import { useState } from "react";
    import { createRoot } from "react-dom/client";
    import i18n from "i18next";
    import { initReactI18next } from "react-i18next";
    import en from "./src/locales/en-US.json";
    import { SidebarMenus } from "./src/components/sidebar/sidebar-menus";
    i18n.use(initReactI18next).init({ lng: "en-US", resources: { "en-US": { translation: en } } });

    function Fixture() {
      const [menu, setMenu] = useState(null);
      const [deleteTarget, setDeleteTarget] = useState("");
      return <>
        <div data-vrcforge-chat-id="fixture">
          <button onContextMenu={event => {
            event.preventDefault();
            setMenu({ chatId: "fixture", x: 10, y: 20 });
          }}>Open fixture chat</button>
        </div>
        <button data-background>Background action</button>
        <SidebarMenus projectMenu={null} chatMenu={menu} selectionMenu={null} deleteTargetId={deleteTarget}
          chats={[{ id: "fixture", title: "Fixture archive" }]}
          onDeleteChat={setDeleteTarget} onCloseChatMenu={() => setMenu(null)}
          onCancelDeleteChat={() => setDeleteTarget("")}
          onConfirmDeleteChat={() => { window.confirmed = true; setDeleteTarget(""); }} />
      </>;
    }
    createRoot(document.querySelector("#root")).render(<Fixture />);
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});

const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  const page = await browser.newPage();
  await page.setContent('<div id="root"></div>');
  await page.addScriptTag({ content: fixture.outputFiles[0].text });
  const row = page.getByRole("button", { name: "Open fixture chat" });
  await row.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Delete Permanently" }).click();
  const dialog = page.getByRole("dialog", { name: "Delete Chat Permanently" });
  assert.equal(await page.locator("dialog:modal").count(), 1, "deletion makes the background inert");
  await page.locator("[data-background]").evaluate(el => el.focus());
  assert.ok(await dialog.evaluate(el => el.contains(document.activeElement)));
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  assert.ok(await row.evaluate(el => el === document.activeElement), "focus returns to the chat after the menu has unmounted");
  assert.equal(await page.evaluate(() => window.confirmed), undefined);
  console.log("Chat deletion modal keeps focus, cancels on Escape and returns to the originating chat.");
} finally {
  await browser.close();
}
