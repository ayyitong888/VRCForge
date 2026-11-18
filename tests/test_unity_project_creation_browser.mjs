import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const projectPath = "D:/fixture/projects/Avatar demo";
const fixture = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import { useState } from "react";
      import { createRoot } from "react-dom/client";
      import i18n from "i18next";
      import { initReactI18next } from "react-i18next";
      import en from "./src/locales/en-US.json";
      import { ProjectPickerModal } from "./src/components/project/project-picker-modal";
      import { createUnityProject } from "./src/lib/api/project";

      await i18n.use(initReactI18next).init({ lng: "en-US", resources: { "en-US": { translation: en } } });
      window.__TAURI_INTERNALS__ = {
        async invoke(command) {
          if (command === "select_folder") return "D:/fixture/projects";
          if (command === "create_unity_project") {
            return new Promise((resolve, reject) => { window.creationResponse = { resolve, reject }; });
          }
          throw new Error(command);
        },
      };
      const noop = () => {};
      function Fixture() {
        const [open, setOpen] = useState(true);
        const [selected, setSelected] = useState("");
        return <>
          <output aria-label="Selected project">{selected}</output>
          <ProjectPickerModal
            open={open} projects={[]} hiddenProjects={[]} customPathSet={new Set()} saving={false}
            newProjectPath="" error="" projectType="unity" onClose={() => setOpen(false)}
            onSelectProject={noop} onRemoveCustomProject={noop} onRestoreProject={noop}
            onNewProjectPathChange={noop} onClearError={noop} onAddProjectPath={noop} onBrowseProjectFolder={noop}
            creation={{
              options: { templates: [{name:"Avatar", path:"D:/fixture/template", unityVersion:"2022.3.22f1", source:"local"}], loading:false, error:"" },
              loadOptions: noop,
              create: createUnityProject,
              select: setSelected,
            }}
          />
        </>;
      }
      createRoot(document.getElementById("root")).render(<Fixture />);
    `,
  },
  bundle: true, write: false, format: "esm", jsx: "automatic",
});

const server = createServer((request, response) => {
  if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(fixture.outputFiles[0].text);
  } else {
    response.setHeader("Content-Type", "text/html");
    response.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  page.on("pageerror", (error) => console.error(error));
  page.setDefaultTimeout(5000);
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByRole("button", { name: "Create Unity Project" }).click();
  await page.getByRole("textbox", { name: "Project name" }).fill("Avatar demo");
  await page.getByRole("button", { name: "Choose Folder" }).click();
  const create = page.getByRole("button", { name: "Create Project", exact: true });
  await create.click();
  await page.waitForFunction(() => window.creationResponse);
  await page.evaluate(() => window.creationResponse.reject(new Error("Project folder already exists.")));
  await page.getByRole("alert").getByText("Project folder already exists.").waitFor();

  await create.click();
  await page.getByRole("button", { name: "Creating project…" }).waitFor();
  await page.evaluate((path) => window.creationResponse.resolve({
    ok: true, projectPath: path, catalogs: [
      { catalog: "unityHub", status: "registered" },
      { catalog: "vcc", status: "failed" },
    ],
  }), projectPath);
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  assert.equal(await page.getByLabel("Selected project").textContent(), projectPath);
} finally {
  await browser.close();
  server.close();
}
