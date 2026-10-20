// Regression for the production projectDisplayLabel hook behavior.
// Two projects deliberately share a display name; the fixture never supplies
// its own label formatter, so suffixes must come from useProjectManagement.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { dirname } from "node:path";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import React from "react";
      import { createRoot } from "react-dom/client";
      import i18n from "i18next";
      import { initReactI18next } from "react-i18next";
      import en from "./src/locales/en-US.json";
      import { useProjectManagement } from "./src/hooks/use-project-management";
      i18n.use(initReactI18next).init({lng:"en-US",resources:{"en-US":{translation:en}}});
      const projects = [
        {name:"VRCForge",path:"C:/fixture/left/VRCForge",projectType:"unity",sources:["fixture"]},
        {name:"VRCForge",path:"C:/fixture/right/VRCForge",projectType:"unity",sources:["fixture"]},
      ];
      const noop = () => {};
      function Fixture(){
        const management = useProjectManagement({
          endpoint: location.origin,
          runtimeConnected: false,
          activeProjectPath: projects[0].path,
          activeProjectType: "unity",
          projects,
          refresh: async () => {},
          refreshProjects: async () => {},
          startRuntime: async () => null,
          setError: noop,
          onProjectAdded: noop,
          onActiveProjectHidden: noop,
        });
        return <main>{management.projectItems.map((project) =>
          <div data-testid={project.path.includes("left") ? "left" : "right"} key={project.path}>
            {management.projectDisplayLabel(project)}
          </div>
        )}</main>;
      }
      createRoot(document.getElementById("root")).render(<Fixture/>);
    `,
  },
  plugins: process.env.VRCFORGE_PROJECT_HOOK_SOURCE ? [{
    name: "original-project-hook",
    setup(builder) {
      builder.onLoad({ filter: /use-project-management\.ts$/ }, args => ({
        contents: readFileSync(process.env.VRCFORGE_PROJECT_HOOK_SOURCE, "utf8"),
        loader: "ts", resolveDir: dirname(args.path),
      }));
    },
  }] : [],
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});

const server = createServer((request, response) => {
  if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
    return;
  }
  if (request.url === "/") {
    response.setHeader("Content-Type", "text/html");
    response.end('<div id="root"></div><script src="/fixture.js"></script>');
    return;
  }
  response.statusCode = 404;
  response.end();
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.getByTestId("left").waitFor({ timeout: 3000 });
  const left = await page.getByTestId("left").innerText();
  const right = await page.getByTestId("right").innerText();
  assert.equal(left, "VRCForge (left/VRCForge)");
  assert.equal(right, "VRCForge (right/VRCForge)");
  assert.notEqual(left, right, "same-name projects must remain distinguishable");
  console.log("production projectDisplayLabel same-name suffix regression: passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
