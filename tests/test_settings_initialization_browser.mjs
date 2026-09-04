// Isolated real-React settings initialization acceptance.
// Synthetic loopback responses only; never connects to the VRCForge backend or user data.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(
  process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright",
);

const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import React, { useState } from "react";
      import { createRoot } from "react-dom/client";
      import i18n from "i18next";
      import { initReactI18next } from "react-i18next";
      import en from "./src/locales/en-US.json";
      import { useSettingsWorkspaceController } from "./src/hooks/use-settings-workspace-controller";

      i18n.use(initReactI18next).init({
        lng: "en",
        resources: { en: { translation: en } },
        interpolation: { escapeValue: false },
      });

      function Fixture() {
        const [endpoint, setEndpoint] = useState(location.origin + "/hang");
        const [activeView, setActiveView] = useState("chat");
        const [error, setError] = useState("");
        const [doctorMessage, setDoctorMessage] = useState("");
        const controller = useSettingsWorkspaceController({
          endpoint,
          runtimeConnected: true,
          activeProjectPath: "/fixture/project",
          setActiveView,
          startRuntime: async () => endpoint,
          refresh: async () => {},
          setError,
          setDoctorMessage,
        });

        window.fixture = {
          open: () => void controller.openSettings(),
          openTwice: () => {
            void controller.openSettings();
            void controller.openSettings();
          },
          useFailureEndpoint: () => setEndpoint(location.origin + "/fail"),
        };

        return <main>
          <button data-testid="open" onClick={() => window.fixture.open()}>Open</button>
          <button data-testid="open-twice" onClick={() => window.fixture.openTwice()}>Open twice</button>
          <button data-testid="failure-context" onClick={() => window.fixture.useFailureEndpoint()}>Failure context</button>
          <div data-testid="active-view">{activeView}</div>
          <div data-testid="connector-data">{controller.connectorStatus?.gateway?.fixtureLabel || ""}</div>
          <div data-testid="diagnostics-data">{controller.diagnosticsStatus?.fixtureLabel || ""}</div>
          <div data-testid="connector-loading">{String(controller.loadingConnectors)}</div>
          <div data-testid="diagnostics-loading">{String(controller.loadingDiagnostics)}</div>
          <div data-testid="notes-loaded">{String(controller.agentNotesLoaded)}</div>
          <div data-testid="error">{error}</div>
          <div data-testid="doctor-message">{doctorMessage}</div>
        </main>;
      }

      createRoot(document.getElementById("root")).render(<Fixture />);
    `,
  },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});

const events = [];
const counts = { notes: 0, connectors: 0, diagnostics: 0 };
const pendingHangNotes = [];

function json(response, status, payload) {
  response.statusCode = status;
  response.setHeader("Content-Type", "application/json");
  response.end(JSON.stringify(payload));
}

const server = createServer((request, response) => {
  const url = request.url || "";
  if (url.endsWith("/api/app/agent-notes")) {
    counts.notes += 1;
    events.push("notes");
    if (url.startsWith("/hang/")) {
      pendingHangNotes.push(response);
      return;
    }
    json(response, 503, { error: "fixture notes failure" });
    return;
  }
  if (url.includes("/api/app/external-agent/connectors")) {
    counts.connectors += 1;
    events.push("connectors");
    json(response, 200, {
      gateway: {
        enabled: true,
        checkpointArchiveMaxSizeMb: 1,
        checkpointArchiveUsage: { ok: true, sizeBytes: 0, maxSizeMb: 1, directory: "/fixture" },
        fixtureLabel: url.startsWith("/hang/") ? "connector-hang-context" : "connector-failure-context",
      },
    });
    return;
  }
  if (url.endsWith("/api/app/diagnostics")) {
    counts.diagnostics += 1;
    events.push("diagnostics");
    json(response, 200, {
      logLevel: "info",
      debugLogging: false,
      fixtureLabel: url.startsWith("/hang/") ? "diagnostics-hang-context" : "diagnostics-failure-context",
    });
    return;
  }
  if (url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
    return;
  }
  if (url === "/") {
    response.setHeader("Content-Type", "text/html");
    response.end("<!doctype html><div id=\"root\"></div><script src=\"/fixture.js\"></script>");
    return;
  }
  response.statusCode = 404;
  response.end();
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));

let browser;
try {
  browser = await chromium.launch({
    headless: true,
    channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge",
  });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);

  const waitFor = async (predicate, message) => {
    for (let index = 0; index < 100; index += 1) {
      if (await predicate()) return;
      await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.fail(message);
  };

  await page.getByTestId("open").click();
  await waitFor(
    async () => (await page.getByTestId("connector-data").innerText()) === "connector-hang-context",
    "connector panel did not become visible while notes was hanging",
  );
  await waitFor(
    async () => (await page.getByTestId("diagnostics-data").innerText()) === "diagnostics-hang-context",
    "diagnostics panel did not become visible while notes was hanging",
  );
  assert.deepEqual(new Set(events.slice(0, 3)), new Set(["connectors", "diagnostics", "notes"]));
  assert.equal(await page.getByTestId("active-view").innerText(), "settings");

  await page.getByTestId("open-twice").click();
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.deepEqual(counts, { notes: 1, connectors: 1, diagnostics: 1 }, "same-context initialization was not deduplicated");

  for (const response of pendingHangNotes) {
    json(response, 200, { content: "fixture notes", path: "/fixture/AGENTS.md" });
  }
  await waitFor(
    async () => (await page.getByTestId("notes-loaded").innerText()) === "true",
    "hung notes request did not settle after fixture release",
  );

  await page.getByTestId("failure-context").click();
  await page.getByTestId("open").click();
  await waitFor(
    async () => (await page.getByTestId("connector-data").innerText()) === "connector-failure-context",
    "connector panel was blocked by failed notes request",
  );
  await waitFor(
    async () => (await page.getByTestId("diagnostics-data").innerText()) === "diagnostics-failure-context",
    "diagnostics panel was blocked by failed notes request",
  );
  await waitFor(
    async () => (await page.getByTestId("notes-loaded").innerText()) === "false",
    "notes failure was not surfaced as failed notes state",
  );
  assert.equal(counts.notes, 2);
  assert.equal(counts.connectors, 2);
  assert.equal(counts.diagnostics, 2);
  assert.deepEqual(errors, []);
  console.log("PASS browser fixture: independent panel-first initialization, same-context dedupe, and notes failure isolation");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
