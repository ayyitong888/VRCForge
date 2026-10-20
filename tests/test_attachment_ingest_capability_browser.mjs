import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const fixture = `
  import React from "react";
  import { createRoot } from "react-dom/client";
  import i18n from "i18next";
  import { initReactI18next } from "react-i18next";
  import en from "./src/locales/en-US.json";
  import { readChatAttachment, appendAttachmentSummary, serializeChatAttachments } from "./src/lib/conversation-utils";
  import { AttachmentStrip } from "./src/components/chat/composer";
  await i18n.use(initReactI18next).init({ lng: "en-US", resources: { "en-US": { translation: en } } });
  const root = createRoot(document.getElementById("root"));
  window.replay = async ({ name, type, bytes }) => {
    const file = new File([new Uint8Array(bytes)], name, { type });
    const attachment = await readChatAttachment(file, i18n.t.bind(i18n));
    root.render(<><section data-card="normal"><AttachmentStrip attachments={[attachment]} /></section>
      <section data-card="compact"><AttachmentStrip attachments={[attachment]} compact /></section></>);
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return { attachment, summary: appendAttachmentSummary("Read the attachment", [attachment], i18n.t.bind(i18n)),
      serialized: serializeChatAttachments([attachment])[0],
      normal: document.querySelector('[data-card="normal"]').textContent,
      compact: document.querySelector('[data-card="compact"]').textContent };
  };
  window.legacySummary = () => appendAttachmentSummary("", [{id:"legacy",name:"old.pdf",type:"application/pdf",size:10,payloadKind:"data_url",dataUrl:"data:application/pdf;base64,JVBERg=="}], i18n.t.bind(i18n));
`;
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: fixture }, bundle: true,
  write: false, format: "esm", jsx: "automatic", define: { "process.env.NODE_ENV": '"test"' } });
const server = createServer((req, res) => {
  if (req.url === "/fixture.js") { res.setHeader("Content-Type", "text/javascript"); res.end(bundle.outputFiles[0].text); }
  else if (req.url === "/") { res.setHeader("Content-Type", "text/html"); res.end('<div id="root"></div><script type="module" src="/fixture.js"></script>'); }
  else { res.statusCode = 404; res.end(); }
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const pdf = process.env.VRCFORGE_ATTACHMENT_PDF ? await readFile(process.env.VRCFORGE_ATTACHMENT_PDF) : Buffer.from("%PDF-1.7\nfixture: no document parser is exercised");
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j9t0AAAAASUVORK5CYII=", "base64");
const validZip = Buffer.from("UEsDBBQAAAAAAF0yVF2dKsMdEAAAABAAAAAIAAAAbm90ZS50eHRyZWFkYWJsZSBmaXh0dXJlUEsBAhQAFAAAAAAAXTJUXZ0qwx0QAAAAEAAAAAgAAAAAAAAAAAAAAIABAAAAAG5vdGUudHh0UEsFBgAAAAABAAEANgAAADYAAAAAAA==", "base64");
const validWav = Buffer.from("UklGRigAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQQAAAAAAAEA", "base64");
const cases = [
  ["sample.json", "application/json", Buffer.from('{"body":"JSON_SENTINEL"}'), "text"],
  ["structured.json", "application/ld+json", Buffer.from('{"body":"JSON_SENTINEL"}'), "text"],
  ["small.txt", "text/plain", Buffer.from("plain text body"), "text"],
  ["chinese.txt", "text/plain", Buffer.from("中文正文\n第二行，保留原值。"), "text"],
  ["chinese.json", "application/json", Buffer.from('{"说明":"中文原值"}'), "text"],
  ["zip-mislabeled.txt", "text/plain", validZip, "metadata"],
  ["wav-mislabeled.txt", "text/plain", validWav, "metadata"],
  ["invalid.json", "application/json", Buffer.from([0x7b, 0x22, 0x78, 0x22, 0x3a, 0x22, 0xff, 0x22, 0x7d]), "metadata"],
  ["nul.json", "application/json", Buffer.from('{"x":"a\u0000b"}'), "metadata"],
  ["at-limit.txt", "text/plain", Buffer.alloc(512 * 1024, 65), "text"],
  ["large.txt", "text/plain", Buffer.alloc(512 * 1024 + 1, 65), "metadata"],
  ["author-license.pdf", "application/pdf", pdf, "metadata"],
  ["report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", Buffer.from("PK\x03\x04fixture"), "metadata"],
  ["voice.mp3", "audio/mpeg", Buffer.from("ID3fixture"), "metadata"],
  ["unknown.bin", "", Buffer.from("unknown bytes"), "metadata"],
  ["pixel.png", "image/png", png, "data_url"],
  ["oversized.png", "image/png", Buffer.alloc(4 * 1024 * 1024 + 1), "metadata"],
];
let browser;
const rows = [], failures = [];
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(() => typeof window.replay === "function");
  for (const [name, type, bytes, expected] of cases) {
    const result = await page.evaluate(args => window.replay(args), {name, type, bytes: [...bytes]});
    const { attachment, summary, serialized, normal, compact } = result;
    const errors = [];
    if (attachment.payloadKind !== expected) errors.push(`payloadKind=${attachment.payloadKind}, expected ${expected}`);
    if (expected === "text" && (serialized.text !== bytes.toString("utf8") || attachment.dataUrl)) errors.push("text body missing or sent as data URL");
    if (expected === "metadata") {
      if (attachment.text !== undefined || attachment.dataUrl !== undefined) errors.push("unsupported body retained as inline payload");
      if (!attachment.error) errors.push("missing honest capability/limit reason");
      if (!normal.includes(attachment.error || "__missing__") || !compact.includes(attachment.error || "__missing__")) errors.push("reason absent from real AttachmentStrip normal/compact cards");
      if (!/not (parsed|provided)|metadata|file information/i.test(summary)) errors.push("summary claims body capability");
    }
    if (expected === "data_url" && !serialized.dataUrl?.startsWith("data:image/png;base64,")) errors.push("image pixel payload lost");
    if (errors.length) failures.push({name, errors});
    rows.push({name, type, bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"),
      expected, payloadKind: attachment.payloadKind, textBytes: attachment.text === undefined ? null : Buffer.byteLength(attachment.text),
      hasDataUrl: Boolean(attachment.dataUrl), error: attachment.error || null, normal, compact, summary, errors});
  }
  const legacySummary = await page.evaluate(() => window.legacySummary());
  if (/payload attached/i.test(legacySummary)) failures.push({name:"legacy non-image data URL",errors:["summary still claims payload attached"]});
  const report = {method:"Real browser File/Blob/FileReader and actual AttachmentStrip; no model or backend request.", actualPdf: Boolean(process.env.VRCFORGE_ATTACHMENT_PDF), rows, legacySummary, failures};
  if (process.env.VRCFORGE_ATTACHMENT_REPLAY_OUTPUT) await writeFile(process.env.VRCFORGE_ATTACHMENT_REPLAY_OUTPUT, JSON.stringify(report, null, 2));
  assert.deepEqual(failures, []);
  console.log(`PASS ${rows.length} actual browser attachment cases, legacy summary, normal/compact cards`);
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
