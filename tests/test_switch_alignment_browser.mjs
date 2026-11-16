import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";
import loadTailwindConfig from "tailwindcss/loadConfig.js";

const root = resolve(import.meta.dirname, "..");
const baseline = process.env.VRCFORGE_SWITCH_BASELINE_REF;
const components = [
  ["src/components/settings/memory-review-settings.tsx", "ToggleRow"],
  ["src/components/skills/skill-package-manager-panel.tsx", "SkillSwitch"],
];
const sources = new Map(await Promise.all(components.map(async ([path, name]) => [
  resolve(root, path), {
    name,
    text: baseline
      ? execFileSync("git", ["show", `${baseline}:${path}`], { cwd: root, encoding: "utf8" })
      : await readFile(resolve(root, path), "utf8"),
  },
])));
const config = loadTailwindConfig(resolve(root, "tailwind.config.ts"));
const css = (await postcss([tailwindcss({
  ...config,
  content: [
    ...config.content.map(path => resolve(root, path)),
    // A baseline must retain its original utilities, including removed ones.
    ...[...sources.values()].map(({ text }) => ({ raw: text, extension: "tsx" })),
  ],
})]).process(
  "@tailwind base; @tailwind components; @tailwind utilities;\n" + await readFile(resolve(root, "src/theme.css"), "utf8"),
  { from: undefined },
)).css;
const bundle = await build({
  stdin: { resolveDir: root, loader: "tsx", contents: `
    import React, { useState } from "react";
    import { createRoot } from "react-dom/client";
    import { ToggleRow } from "./src/components/settings/memory-review-settings";
    import { SkillSwitch } from "./src/components/skills/skill-package-manager-panel";
    function Sample({ kind, initial, mode }) {
      const [checked, setChecked] = useState(initial);
      const id = kind + "-" + initial + "-" + mode;
      const props = { checked, label: id, busy: mode === "busy" };
      return <div data-case={id} className="flex items-center rounded-xl border border-border bg-card p-4">
        {kind === "memory"
          ? <ToggleRow {...props} disabled={mode === "disabled"} testId={id} description="Memory preference" onChange={setChecked} />
          : <SkillSwitch {...props} onChange={() => setChecked(value => !value)} />}
      </div>;
    }
    createRoot(document.getElementById("root")).render(<main className="grid grid-cols-2 gap-4 p-8">
      {["memory", "skill"].flatMap(kind => (kind === "memory" ? ["normal", "disabled", "busy"] : ["normal", "busy"])
        .flatMap(mode => [false, true].map(initial => <Sample key={kind + mode + initial} {...{ kind, mode, initial }} />)))}
    </main>);
  ` },
  bundle: true, write: false, jsx: "automatic", format: "iife", platform: "browser",
  plugins: [{
    name: "expose-real-switches-for-test",
    setup(plugin) {
      plugin.onLoad({ filter: /(?:memory-review-settings|skill-package-manager-panel)\.tsx$/ }, ({ path }) => {
        const { text, name } = sources.get(resolve(path));
        return { loader: "tsx", contents: `${text}\nexport { ${name} };` };
      });
    },
  }],
});

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const browser = await chromium.launch({ channel: "msedge", headless: true });
const failures = [], errors = [];
let states = 0;
const measure = nodes => nodes.map(button => {
  const track = button.getBoundingClientRect(), thumb = button.querySelector("span").getBoundingClientRect();
  return {
    id: button.closest("[data-case]").dataset.case, checked: button.getAttribute("aria-checked") === "true",
    width: thumb.width, height: thumb.height,
    left: thumb.left - track.left, right: track.right - thumb.right,
    top: thumb.top - track.top, bottom: track.bottom - thumb.bottom,
  };
});
const check = (geometry, context) => {
  for (const item of geometry) {
    states += 1;
    if (Math.min(item.left, item.right, item.top, item.bottom) < 0
      || item.width <= 0 || Math.abs(item.width - item.height) > 0.2
      || Math.abs(item.top - item.bottom) > 0.2
      || Math.abs((item.checked ? item.right : item.left) - item.top) > 0.2) {
      failures.push({ ...context, ...item });
    }
  }
};
const settle = page => page.evaluate(() => document.getAnimations().forEach(animation => animation.finish()));
try {
  for (const dpr of [1, 1.25, 1.5, 2]) {
    const page = await browser.newPage({ viewport: { width: 1100, height: 900 }, deviceScaleFactor: dpr });
    page.on("pageerror", error => errors.push(error.message));
    await page.setContent('<div id="root"></div>');
    await page.addStyleTag({ content: css });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.getByRole("switch").first().waitFor();
    for (const theme of ["light", "dark"]) {
      await page.evaluate(theme => {
        document.documentElement.dataset.theme = theme;
        document.documentElement.classList.toggle("dark", theme === "dark");
      }, theme);
      await settle(page);
      check(await page.getByRole("switch").evaluateAll(measure), { dpr, theme });
    }
    for (const kind of ["memory", "skill"]) {
      const control = page.locator(`[data-case="${kind}-false-normal"]`).getByRole("switch");
      for (const checked of [true, false]) {
        await control.click();
        assert.equal(await control.getAttribute("aria-checked"), String(checked), `${kind} click toggles its state`);
        await settle(page);
        check(await control.evaluateAll(measure), { dpr, theme: "dark", clicked: true });
      }
    }
    await page.close();
  }
} finally {
  await browser.close();
}
assert.deepEqual(errors, []);
assert.equal(failures.length, 0, JSON.stringify({
  failures: failures.length,
  memory: failures.find(item => item.id.startsWith("memory")),
  skill: failures.find(item => item.id.startsWith("skill")),
}, null, 2));
console.log(`PASS: ${states} switch states across two implementations, four DPRs, both themes, and real on/off clicks.`);
