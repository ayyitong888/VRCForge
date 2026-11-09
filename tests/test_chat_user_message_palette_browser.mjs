import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { build } from "esbuild";
import postcss from "postcss";
import tailwind from "tailwindcss";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import { createRoot } from "react-dom/client";
      import { ConversationCard } from "./src/components/chat/conversation-card";
      import { initializeI18n } from "./src/i18n";
      initializeI18n().then(() => createRoot(document.getElementById("root")).render(
        <main className="bg-workspace text-foreground"><ConversationCard item={{id:"message", type:"user", text:"A sent message."}}/></main>
      ));
    `,
  },
  bundle: true, write: false, format: "iife", platform: "browser", jsx: "automatic",
});
const theme = process.env.VRCFORGE_PALETTE_BASELINE
  ? execFileSync("git", ["show", `${process.env.VRCFORGE_PALETTE_BASELINE}:src/theme.css`], { encoding: "utf8" })
  : readFileSync("src/theme.css", "utf8");
const cssSource = readFileSync("src/styles.css", "utf8").replace('@import "./theme.css";', theme);
const css = (await postcss([tailwind("tailwind.config.ts")]).process(cssSource, { from: "src/styles.css" })).css;
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setContent('<div id="root"></div>');
  await page.addStyleTag({ content: css });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const bubble = page.locator("[data-user-message-bubble]");
  await bubble.waitFor();
  const fills = new Set();
  for (const scheme of ["light", "dark"]) {
    for (const palette of ["default", "forest", "custom"]) {
      await page.evaluate(([scheme, palette]) => {
        const root = document.documentElement;
        root.dataset.theme = scheme;
        root.dataset.vrcforgePalette = palette;
        root.style.setProperty("--vrcforge-custom-accent-h", "312");
        root.style.setProperty("--vrcforge-custom-accent-s", "74%");
        root.style.setProperty("--vrcforge-custom-accent-light-l", "48%");
        root.style.setProperty("--vrcforge-custom-accent-dark-l", "68%");
      }, [scheme, palette]);
      const colors = await bubble.evaluate((el) => {
        const style = getComputedStyle(el);
        const context = document.createElement("canvas").getContext("2d");
        const rgb = (color) => {
          context.fillStyle = color;
          context.fillRect(0, 0, 1, 1);
          return [...context.getImageData(0, 0, 1, 1).data].slice(0, 3);
        };
        return { background: rgb(style.backgroundColor), text: rgb(style.color) };
      });
      const [red, green, blue] = colors.background;
      const colored = palette === "forest" ? green > red + 12
        : palette === "custom" ? Math.min(red, blue) > green + 12
          : blue > red + 12;
      assert.ok(colored, `${scheme}/${palette} bubble uses its palette hue`);
      const luminance = (rgb) => rgb.reduce((total, value, index) => {
        const channel = value / 255;
        return total + (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][index];
      }, 0);
      const [low, high] = [luminance(colors.background), luminance(colors.text)].sort((a, b) => a - b);
      assert.ok((high + 0.05) / (low + 0.05) >= 4.5, `${scheme}/${palette} text remains readable`);
      fills.add(colors.background.join(","));
    }
  }
  assert.equal(fills.size, 6);
  assert.deepEqual(errors, []);
  console.log("PASS: user bubbles use the default, forest, and custom accent colors in light and dark themes");
} finally {
  await browser.close();
}
