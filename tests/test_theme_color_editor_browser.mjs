import assert from "node:assert/strict";
import { build } from "esbuild";
import { chromium } from "playwright";

const { outputFiles } = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import { useState } from "react";
      import { createRoot } from "react-dom/client";
      import { initializeI18n, setLocale } from "./src/i18n";
      import { ThemeColorEditor } from "./src/components/settings/theme-color-editor";
      window.commits = [];
      window.EyeDropper = class {
        async open() { return { sRGBHex: "#456789" }; }
      };
      function Fixture() {
        const [value, setValue] = useState("#125678");
        return <ThemeColorEditor label="Accent" value={value} fallbackColor="#000000" recentColors={["#bbccdd"]}
          onCommit={color => { window.commits.push(color); setValue(color); }} />;
      }
      void initializeI18n().then(async () => {
        await setLocale("en-US");
        createRoot(document.querySelector("#root")).render(<Fixture />);
      });
    `,
  },
  bundle: true,
  write: false,
  jsx: "automatic",
});

const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
try {
  const page = await browser.newPage({ viewport: { width: 360, height: 600 } });
  await page.setContent('<div id="root"></div>');
  await page.addScriptTag({ content: outputFiles[0].text });
  await page.getByRole("textbox", { name: "Accent HEX" }).waitFor();
  assert.equal(await page.locator('input[type="color"]').count(), 0, "swatch must not open WebView's color picker");

  const trigger = page.getByRole("button", { name: /^Accent #/ });
  await trigger.click();
  assert.equal(await trigger.getAttribute("aria-expanded"), "true");
  const red = page.getByRole("slider", { name: "Accent RGB R" });
  await red.press("End");
  assert.equal(await red.inputValue(), "255");
  const green = page.getByRole("slider", { name: "Accent RGB G" });
  await green.press("Home");
  assert.equal(await green.inputValue(), "0");
  const blue = page.getByRole("slider", { name: "Accent RGB B" });
  await blue.press("ArrowRight");
  await blue.press("Escape");
  assert.equal(await trigger.getAttribute("aria-expanded"), "false");
  assert.ok(await trigger.evaluate((button) => button === document.activeElement));

  for (const [format, value] of [["HEX", "#fd0"], ["RGB", "rgb(18, 52, 86)"], ["HSL", "hsl(120 100% 50%)"]]) {
    const field = page.getByRole("textbox", { name: `Accent ${format}` });
    await field.fill(value);
    await field.press("Enter");
  }
  await page.getByRole("button", { name: "#BBCCDD" }).click();
  await page.getByRole("button", { name: "Pick a color from the screen" }).click();
  await page.getByRole("button", { name: "Accent #456789" }).waitFor();
  assert.deepEqual(await page.evaluate(() => window.commits), ["#ff5678", "#ff0078", "#ff0079", "#ffdd00", "#123456", "#00ff00", "#bbccdd", "#456789"]);
  console.log("theme color picker: RGB keyboard, HEX/RGB/HSL, recent color and eyedropper commits passed");
} finally {
  await browser.close();
}
