import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";
import postcss from "postcss";
import tailwind from "tailwindcss";
import { chromium } from "playwright";

const css = (await postcss([tailwind("tailwind.config.ts")]).process(
  "@tailwind base; @tailwind utilities;\n" + await readFile("src/theme.css", "utf8"),
  { from: undefined },
)).css;
const fixture = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import { createRoot } from "react-dom/client";
    import i18n from "i18next";
    import { initReactI18next } from "react-i18next";
    import en from "./src/locales/en-US.json";
    import zh from "./src/locales/zh-CN.json";
    import { RightRuntimeSidebar } from "./src/components/runtime/runtime-sidebar";
    import { PendingApprovalsStrip } from "./src/components/approvals/pending-approvals-strip";
    import { ScopedPendingApprovalCard } from "./src/components/approvals/scoped-pending-approval-card";
    i18n.use(initReactI18next).init({
      lng: "en-US",
      resources: { "en-US": { translation: en }, "zh-CN": { translation: zh },
        long: { translation: { ...en, approval: { ...en.approval, allowFuture:
          "Always allow future requests for this specific category when the same permission is required again" } } } },
    });
    window.setApprovalLocale = (language) => i18n.changeLanguage(language);
    window.approvals = [];
    const approval = { id: "fixture", status: "pending", allowFutureEligible: true };
    const approvalProps = {
      approvals: [approval], actions: {}, onApprove: (...args) => window.approvals.push(args), onReject: () => {},
    };
    createRoot(document.getElementById("root")).render(<>
      <div data-fixture-sidebar style={{ position: "absolute", right: 0, width: 260, height: "100%" }}>
        <RightRuntimeSidebar runtimeConnected workspaceProjectLabel="Project" workspaceProjectType="general"
          authorizationStatus={{}} agentProgress={[]} projectWorkspace={false} approvalsLoaded pendingApprovals={1}
          activeDesktopActions={[]} localizeHealthMessage={() => ""}
          approvalPanel={<PendingApprovalsStrip {...approvalProps} loading={false} onClose={() => {}}
            approvals={[approval, { ...approval, id: "second" }, { ...approval, id: "third" }]} />} />
      </div>
      <div data-fixture-chat style={{ position: "absolute", bottom: 0, left: 0, width: 450 }}>
        <ScopedPendingApprovalCard {...approvalProps} disabled={false} onModifyApproval={() => {}} />
      </div>
    </>);
  ` },
  bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
});

const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 720 } });
  await page.setContent('<div id="root"></div>');
  await page.addStyleTag({ content: css });
  await page.addScriptTag({ content: fixture.outputFiles[0].text });
  const trigger = (scope) => page.locator(`${scope} button[aria-haspopup="menu"]`).first();
  const open = (scope) => trigger(scope).click();
  const assertVisibleMenu = async (label) => {
    const item = page.getByRole("menuitem");
    await item.waitFor();
    const geometry = await item.evaluate(button => {
      const icon = button.querySelector("svg");
      const iconRect = icon.getBoundingClientRect();
      const menu = button.closest('[role="menu"]');
      const rect = menu.getBoundingClientRect();
      const hit = document.elementFromPoint(iconRect.x + 2, iconRect.y + iconRect.height / 2);
      return {
        iconReachable: icon.contains(hit),
        insideViewport: rect.x >= 0 && rect.y >= 0 && rect.right <= innerWidth && rect.bottom <= innerHeight,
        noHorizontalOverflow: button.scrollWidth <= menu.clientWidth,
      };
    });
    assert.ok(geometry.iconReachable, `${label}: leading icon is painted and hit-testable`);
    assert.ok(geometry.insideViewport, `${label}: menu fits inside the viewport`);
    assert.ok(geometry.noHorizontalOverflow, `${label}: label fits inside the menu`);
    return item;
  };

  const sidebar = "[data-fixture-sidebar]";
  await page.evaluate(() => window.setApprovalLocale("zh-CN"));
  for (const name of ["驳回", "仅允许一次"]) {
    const button = page.locator(sidebar).getByRole("button", { name, exact: true }).first();
    const geometry = await button.evaluate(el => {
      const range = document.createRange();
      range.selectNode(el.lastChild);
      return {
        lines: range.getClientRects().length,
        iconWidth: el.querySelector("svg").getBoundingClientRect().width,
        fits: el.clientWidth >= el.scrollWidth && el.clientHeight >= el.scrollHeight,
      };
    });
    assert.deepEqual(geometry, { lines: 1, iconWidth: 16, fits: true }, `${name} fits on one line with its full icon`);
  }
  await page.evaluate(() => window.setApprovalLocale("en-US"));
  await open(sidebar);
  await assertVisibleMenu("260px status sidebar");
  await page.mouse.click(560, 700);
  assert.equal(await page.getByRole("menu").count(), 0, "click outside dismisses the menu");
  assert.deepEqual(await page.evaluate(() => window.approvals), []);

  const strip = page.locator(sidebar).locator(".overflow-auto").first();
  await strip.evaluate(el => {
    const button = el.querySelector('button[aria-haspopup="menu"]');
    el.scrollTop += button.getBoundingClientRect().top - el.getBoundingClientRect().top - 8;
  });
  await open(sidebar);
  await assertVisibleMenu("status sidebar scrolled near the top of the approval list");
  await page.mouse.click(560, 700);

  await page.locator(sidebar).evaluate(el => el.style.width = "320px");
  await open(sidebar);
  await assertVisibleMenu("320px status sidebar");
  await page.mouse.click(560, 700);

  await page.evaluate(() => window.setApprovalLocale("zh-CN"));
  await open(sidebar);
  const chinese = await assertVisibleMenu("Chinese status sidebar");
  assert.equal(await chinese.innerText(), "以后允许此类操作");
  await page.mouse.click(560, 700);

  await page.evaluate(() => window.setApprovalLocale("long"));
  await open("[data-fixture-chat]");
  const future = await assertVisibleMenu("bottom of chat with a long label");
  await future.click();
  assert.deepEqual(await page.evaluate(() => window.approvals), [["fixture", true]]);
  assert.equal(await page.getByRole("menu").count(), 0);
  console.log("Approval menus fit both surfaces and send the intended approval callback.");
} finally {
  await browser.close();
}
