import assert from "node:assert/strict";
import { build } from "esbuild";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import Module from "node:module";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const sourcePath = resolve(root, "src/components/workspace/workspace-header.tsx");
const entry = `
  import React from "react";
  import { renderToStaticMarkup } from "react-dom/server";
  import { WorkspaceHeader } from ${JSON.stringify(sourcePath)};
  const base = {
    activeProjectLabel: "Project",
    activeView: "chat",
    activeChatTitle: "QA",
    theme: "dark",
    hasStartupIssue: false,
    healthErrors: 0,
    healthWarnings: 0,
    startupIssue: "",
    loadingDoctor: false,
    loading: false,
    onToggleTheme: () => {},
    onOpenDoctor: () => {},
    onRetryStartupOrHealth: () => {},
    onDismissDoctorPrompt: () => {},
    onStartRuntime: () => {},
  };
  export function render(props) {
    return renderToStaticMarkup(React.createElement(WorkspaceHeader, {...base, ...props}));
  }
`;

async function compileAndLoad(sourceOverride) {
  const compiled = await build({
    stdin: { contents: entry, resolveDir: root, sourcefile: "workspace-header-conflict-entry.mjs" },
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    external: ["react", "react-dom/server", "react/jsx-runtime"],
    plugins: [{
      name: "workspace-header-test-ports",
      setup(api) {
        api.onResolve({ filter: /^react-i18next$/ }, () => ({ path: "translation", namespace: "header-test" }));
        api.onResolve({ filter: /^lucide-react$/ }, () => ({ path: "icons", namespace: "header-test" }));
        api.onResolve({ filter: /^\.\.\/ui\/button$/ }, () => ({ path: "button", namespace: "header-test" }));
        api.onLoad({ filter: /.*/, namespace: "header-test" }, (args) => {
          if (args.path === "translation") return { contents: 'export const useTranslation=()=>({t:(key)=>key});', loader: "js" };
          if (args.path === "icons") return { contents: 'export const AlertTriangle=()=>null,Moon=()=>null,Sun=()=>null;', loader: "js" };
          return { contents: 'import React from "react"; export const Button=({variant: _variant, ...props})=>React.createElement("button",props,props.children);', loader: "jsx" };
        });
        if (sourceOverride) {
          api.onLoad({ filter: /workspace-header\.tsx$/ }, () => ({ contents: sourceOverride, loader: "tsx" }));
        }
      },
    }],
  });
  const filename = resolve(root, "tests/workspace-header-conflict-fixture.cjs");
  const module = new Module(filename);
  module.paths = Module._nodeModulePaths(root);
  module._compile(compiled.outputFiles[0].text, filename);
  return module.exports;
}

const current = await compileAndLoad();
const oldSource = execFileSync("git", ["show", "HEAD:src/components/workspace/workspace-header.tsx"], { cwd: root, encoding: "utf8" });
const baseline = await compileAndLoad(oldSource);
const distinct = {
  showDoctorStartupPrompt: true,
  hasStartupIssue: true,
  startupIssue: "backend startup failed",
  error: "2 chats changed in another window",
};
const duplicate = {
  showDoctorStartupPrompt: true,
  hasStartupIssue: true,
  startupIssue: "backend startup failed",
  error: "backend startup failed",
};
const plain = { showDoctorStartupPrompt: false, error: "runtime disconnected" };

const baselineDistinct = baseline.render(distinct);
assert.equal(baselineDistinct.includes("data-workspace-error"), false, "preimage must reproduce the hidden-conflict RED");
const currentDistinct = current.render(distinct);
assert.match(currentDistinct, /data-workspace-error/);
assert.match(currentDistinct, /2 chats changed in another window/);
assert.match(currentDistinct, /backend startup failed/);
const currentDuplicate = current.render(duplicate);
assert.equal((currentDuplicate.match(/data-workspace-error/g) || []).length, 0, "same startup error must not duplicate the banner");
const currentPlain = current.render(plain);
assert.match(currentPlain, /data-workspace-error/);
assert.match(currentPlain, /runtime disconnected/);

console.log("workspace header conflict DOM: preimage RED / current GREEN");
