import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const exe = resolve(repoRoot, "dist", "VRCForge_Windows_x64", "VRCForge.exe");
const port = Number(process.env.VRCFORGE_CDP_PORT || "9341");
const marker = `WORKFLOW_PROBE_${Date.now()}`;
const outPath = resolve(repoRoot, "artifacts", "latency", `packaged-workflows-${marker}.json`);
const maxWaitMs = Number(process.env.VRCFORGE_WORKFLOW_WAIT_MS || "240000");

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function runPowerShell(script) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], { windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", rejectRun);
    child.on("close", (code) => {
      if (code === 0) {
        resolveRun(stdout.trim());
      } else {
        rejectRun(new Error(stderr.trim() || stdout.trim() || `PowerShell exited ${code}`));
      }
    });
  });
}

async function waitForPortReleased(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    last = await runPowerShell(`
      $rows = Get-NetTCPConnection -LocalPort 8757 -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq 'Listen' } |
        Select-Object -First 5 LocalAddress,LocalPort,State,OwningProcess
      if ($rows) { $rows | ConvertTo-Json -Compress } else { '' }
    `);
    if (!last) {
      return;
    }
    await sleep(250);
  }
  throw new Error(`Port 8757 still has a listener before launch: ${last}`);
}

async function closeExistingVrcforgeProcesses() {
  await runPowerShell(`
    Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.ProcessName -eq 'VRCForge' -or $_.ProcessName -eq 'vrcforge_backend' -or $_.ProcessName -eq 'vrcforge-agentic-app' } |
      Stop-Process -Force -ErrorAction SilentlyContinue
  `);
  await waitForPortReleased(15000);
}

async function processSnapshot() {
  return runPowerShell(`
    $processes = Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.ProcessName -eq 'VRCForge' -or $_.ProcessName -eq 'vrcforge_backend' -or $_.ProcessName -eq 'vrcforge-agentic-app' } |
      Select-Object Id,ProcessName,Path
    $ports = Get-NetTCPConnection -LocalPort 8757 -ErrorAction SilentlyContinue |
      Select-Object LocalAddress,LocalPort,State,OwningProcess
    [pscustomobject]@{ processes = @($processes); ports = @($ports) } | ConvertTo-Json -Depth 4 -Compress
  `).then((value) => (value ? JSON.parse(value) : { processes: [], ports: [] }));
}

async function jsonFetch(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} for ${url}`);
  }
  return response.json();
}

async function waitForCdpTarget() {
  const deadline = Date.now() + 30000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const targets = await jsonFetch(`http://127.0.0.1:${port}/json/list`);
      const page = targets.find((target) => target.type === "page" && target.webSocketDebuggerUrl);
      if (page) {
        return page;
      }
    } catch (error) {
      lastError = error;
    }
    await sleep(100);
  }
  throw lastError || new Error("Timed out waiting for WebView2 CDP target.");
}

function connectCdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let nextId = 1;
  const pending = new Map();
  const events = [];
  ws.addEventListener("message", (message) => {
    const payload = JSON.parse(String(message.data));
    if (payload.id && pending.has(payload.id)) {
      const { resolve, reject } = pending.get(payload.id);
      pending.delete(payload.id);
      if (payload.error) {
        reject(new Error(payload.error.message || JSON.stringify(payload.error)));
      } else {
        resolve(payload.result);
      }
      return;
    }
    if (payload.method) {
      events.push({ t: Date.now(), method: payload.method, params: payload.params });
    }
  });
  const opened = new Promise((resolveOpen, rejectOpen) => {
    ws.addEventListener("open", resolveOpen, { once: true });
    ws.addEventListener("error", rejectOpen, { once: true });
  });
  return {
    events,
    opened,
    close: () => ws.close(),
    send(method, params = {}) {
      const id = nextId++;
      ws.send(JSON.stringify({ id, method, params }));
      return new Promise((resolveSend, rejectSend) => {
        pending.set(id, { resolve: resolveSend, reject: rejectSend });
      });
    },
  };
}

async function evalValue(cdp, expression, timeout = 15000) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
    timeout,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "Runtime.evaluate failed");
  }
  return result.result?.value;
}

async function waitForEval(cdp, expression, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let lastValue = null;
  while (Date.now() < deadline) {
    lastValue = await evalValue(cdp, expression, 5000).catch((error) => ({ error: String(error) }));
    if (lastValue === true || (lastValue && lastValue.ok)) {
      return lastValue;
    }
    await sleep(100);
  }
  throw new Error(`Timed out waiting for expression: ${expression}; last=${JSON.stringify(lastValue)}`);
}

function summarizeNetwork(events) {
  const requests = new Map();
  for (const event of events) {
    const params = event.params || {};
    const id = params.requestId;
    if (!id) {
      continue;
    }
    const entry = requests.get(id) || { id };
    if (event.method === "Network.requestWillBeSent") {
      entry.url = params.request?.url;
      entry.method = params.request?.method;
      entry.startTs = params.timestamp;
    } else if (event.method === "Network.responseReceived") {
      entry.status = params.response?.status;
      entry.responseTs = params.timestamp;
    } else if (event.method === "Network.loadingFinished") {
      entry.endTs = params.timestamp;
      entry.encodedDataLength = params.encodedDataLength;
    } else if (event.method === "Network.loadingFailed") {
      entry.failedTs = params.timestamp;
      entry.errorText = params.errorText;
    }
    requests.set(id, entry);
  }
  return [...requests.values()]
    .filter((entry) => entry.url)
    .map((entry) => ({
      url: entry.url,
      method: entry.method,
      status: entry.status,
      durationMs: entry.endTs && entry.startTs ? Math.round((entry.endTs - entry.startTs) * 1000) : null,
      responseMs: entry.responseTs && entry.startTs ? Math.round((entry.responseTs - entry.startTs) * 1000) : null,
      errorText: entry.errorText,
    }));
}

function localLongTaskCount(finalProbe) {
  return Array.isArray(finalProbe?.probe?.longTasks) ? finalProbe.probe.longTasks.length : 0;
}

async function installProbe(cdp) {
  await evalValue(
    cdp,
    `(() => {
      const sanitizeProbeValue = (value, depth = 0) => {
        const sensitiveKeyPattern = new RegExp(["api" + "[_-]?" + "key", "sec" + "ret", "tok" + "en", "author" + "ization"].join("|"), "i");
        if (depth > 6) {
          return "[max-depth]";
        }
        if (typeof value === "string") {
          if (value.startsWith("data:")) {
            return "[data-url:" + value.length + "]";
          }
          return value.length > 2000 ? value.slice(0, 2000) + "[truncated]" : value;
        }
        if (!value || typeof value !== "object") {
          return value;
        }
        if (Array.isArray(value)) {
          return value.slice(0, 24).map((item) => sanitizeProbeValue(item, depth + 1));
        }
        const out = {};
        for (const [key, item] of Object.entries(value)) {
          if (sensitiveKeyPattern.test(key)) {
            out[key] = "[redacted]";
          } else {
            out[key] = sanitizeProbeValue(item, depth + 1);
          }
        }
        return out;
      };
      const probe = window.__vrcWorkflowProbe = {
        installedAt: performance.now(),
        fetches: [],
        longTasks: [],
      };
      const originalFetch = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const input = args[0];
        const url = typeof input === "string" ? input : (input && input.url) || String(input);
        const method = (args[1] && args[1].method) || (input && input.method) || "GET";
        const start = performance.now();
        const row = { url, method, start };
        probe.fetches.push(row);
        try {
          const response = await originalFetch(...args);
          row.status = response.status;
          row.end = performance.now();
          row.duration = row.end - start;
          if (/send_agent_message|compact_agent_history|record_agent_run_queued|request_agent_run_cancel/i.test(url)) {
            try {
              const cloneText = await response.clone().text();
              row.responseLength = cloneText.length;
              if (cloneText.length <= 120000) {
                try {
                  row.responseJson = sanitizeProbeValue(JSON.parse(cloneText));
                } catch {
                  row.responseText = cloneText.slice(0, 2000);
                }
              }
            } catch (error) {
              row.responseCaptureError = String(error && error.message || error);
            }
          }
          return response;
        } catch (error) {
          row.error = String(error && error.message || error);
          row.end = performance.now();
          row.duration = row.end - start;
          throw error;
        }
      };
      if ("PerformanceObserver" in window) {
        try {
          const observer = new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
              probe.longTasks.push({ name: entry.name, start: entry.startTime, duration: entry.duration });
            }
          });
          observer.observe({ entryTypes: ["longtask"] });
          probe.longTaskObserver = true;
        } catch (error) {
          probe.longTaskObserverError = String(error && error.message || error);
        }
      }
      return true;
    })()`,
  );
}

async function attachProbeImage(cdp, name) {
  return evalValue(
    cdp,
    `(async () => {
      const input = document.querySelector("input[type='file']");
      if (!input) {
        return { ok: false, reason: "file input not found" };
      }
      const pngBase64 = "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFklEQVR4nGP8z8Dwn4GBgYGJgYGBgQEAJ7MBBO9JwmsAAAAASUVORK5CYII=";
      const binary = atob(pngBase64);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      const file = new File([bytes], ${JSON.stringify(name)}, { type: "image/png" });
      const transfer = new DataTransfer();
      transfer.items.add(file);
      const start = performance.now();
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 250));
      const body = document.body.innerText;
      return {
        ok: body.includes(${JSON.stringify(name)}),
        duration: performance.now() - start,
        fileCount: input.files ? input.files.length : null,
        bodyLength: body.length,
        tail: body.slice(-700),
      };
    })()`,
  );
}

async function typeComposer(cdp, text) {
  return evalValue(
    cdp,
    `(async () => {
      const textarea = document.querySelector("textarea");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      const start = performance.now();
      textarea.focus();
      setter.call(textarea, ${JSON.stringify(text)});
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolve) => requestAnimationFrame(resolve));
      return {
        duration: performance.now() - start,
        valueLength: textarea.value.length,
        disabled: textarea.disabled,
      };
    })()`,
  );
}

async function clickSubmit(cdp) {
  return evalValue(
    cdp,
    `(async () => {
      const submit = document.querySelector("button[type='submit']");
      const start = performance.now();
      submit.click();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      return {
        duration: performance.now() - start,
        disabledAfterFrame: submit.disabled,
        bodyLength: document.body.innerText.length,
        buttonText: submit.innerText,
      };
    })()`,
  );
}

async function clickStop(cdp) {
  return evalValue(
    cdp,
    `(async () => {
      const buttons = Array.from(document.querySelectorAll("button"));
      const stop = buttons.find((button) => /stop|停止|中止/i.test(button.title || button.innerText || ""));
      if (!stop) {
        return { ok: false, reason: "stop button not found", buttonTexts: buttons.map((button) => ({ title: button.title, text: button.innerText })).slice(-12) };
      }
      const start = performance.now();
      stop.click();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      return { ok: true, duration: performance.now() - start, bodyLength: document.body.innerText.length };
    })()`,
  );
}

async function bodySnapshot(cdp, markerValue) {
  return evalValue(
    cdp,
    `(() => {
      const body = document.body.innerText;
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      return {
        bodyLength: body.length,
        markerCount: (body.match(new RegExp(${JSON.stringify(markerValue)}, "g")) || []).length,
        workflowMarkerCount: (body.match(new RegExp(${JSON.stringify(marker)}, "g")) || []).length,
        runningText: /\\u6267\\u884c\\u4e2d|\\u7b49\\u5f85\\u6a21\\u578b\\u54cd\\u5e94|\\u601d\\u8003\\u4e2d|\\u8fd0\\u884c\\u4e2d|running|thinking/i.test(body),
        queuedText: /\\u5df2\\u6392\\u961f|\\u961f\\u5217|queued|queue/i.test(body),
        compactText: /\\u538b\\u7f29|compacted|compact/i.test(body),
        textareaDisabled: Boolean(textarea && textarea.disabled),
        submitDisabled: Boolean(submit && submit.disabled),
        textareaValueLength: textarea ? textarea.value.length : null,
        tail: body.slice(-1200),
      };
    })()`,
  );
}

async function waitForMarkerComplete(cdp, markerValue, timeoutMs = 90000) {
  const samples = [];
  const deadline = Date.now() + timeoutMs;
  let stable = 0;
  while (Date.now() < deadline) {
    await sleep(250);
    const sample = await bodySnapshot(cdp, markerValue);
    sample.at = Date.now();
    samples.push(sample);
    if (sample.markerCount > 1 && !sample.runningText) {
      stable += 1;
    } else {
      stable = 0;
    }
    if (stable >= 4) {
      return { ok: true, samples, final: sample };
    }
  }
  return { ok: false, samples, final: samples.at(-1) };
}

async function waitForText(cdp, pattern, timeoutMs = 20000) {
  const source = pattern.source;
  const flags = pattern.flags;
  const samples = [];
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await sleep(250);
    const sample = await evalValue(
      cdp,
      `(() => {
        const body = document.body.innerText;
        return {
          at: performance.now(),
          ok: new RegExp(${JSON.stringify(source)}, ${JSON.stringify(flags)}).test(body),
          bodyLength: body.length,
          tail: body.slice(-700),
        };
      })()`,
    );
    samples.push(sample);
    if (sample.ok) {
      return { ok: true, samples, final: sample };
    }
  }
  return { ok: false, samples, final: samples.at(-1) };
}

async function runSend(cdp, name, text, markerValue, timeoutMs = 90000) {
  const input = await typeComposer(cdp, text);
  const submitReady = await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      return {
        ok: Boolean(textarea && submit && !textarea.disabled && !submit.disabled && textarea.value.includes(${JSON.stringify(markerValue)})),
        textareaDisabled: Boolean(textarea && textarea.disabled),
        submitDisabled: Boolean(submit && submit.disabled),
        valueLength: textarea ? textarea.value.length : null,
      };
    })()`,
    5000,
  );
  const click = await clickSubmit(cdp);
  const completion = await waitForMarkerComplete(cdp, markerValue, timeoutMs);
  return { name, marker: markerValue, input, submitReady, click, completion };
}

function summarizeVisionFetches(finalProbe, markerValue) {
  const fetches = finalProbe?.probe?.fetches;
  if (!Array.isArray(fetches)) {
    return [];
  }
  return fetches
    .filter((row) => String(row.url || "").includes("send_agent_message"))
    .filter((row) => JSON.stringify(row.responseJson || row.responseText || "").includes(markerValue))
    .map((row) => {
      const payload = row.responseJson || {};
      const vision = payload.vision || {};
      const steps = Array.isArray(payload.steps) ? payload.steps : [];
      return {
        status: row.status,
        duration: row.duration,
        responseLength: row.responseLength,
        visionStatus: vision.status || "",
        visionSource: vision.source || "",
        visionProvider: vision.provider || "",
        visionProviderLabel: vision.providerLabel || "",
        visionModel: vision.model || "",
        visionImageCount: vision.imageCount || 0,
        visionReason: vision.reason || "",
        firstStepKind: steps[0]?.kind || "",
        firstStepStatus: steps[0]?.status || "",
        firstStepImageCount: steps[0]?.imageCount || 0,
      };
    });
}

function fetchRows(finalProbe, urlPart) {
  const fetches = finalProbe?.probe?.fetches;
  if (!Array.isArray(fetches)) {
    return [];
  }
  return fetches.filter((row) => String(row.url || "").includes(urlPart));
}

function markerResponseRows(finalProbe, markerValue) {
  return fetchRows(finalProbe, "send_agent_message").filter((row) =>
    JSON.stringify(row.responseJson || row.responseText || "").includes(markerValue),
  );
}

function validateWorkflowOutput(output, markers) {
  const failures = [];
  const byName = new Map(output.scenarios.map((scenario) => [scenario.name, scenario]));
  const base = byName.get("baseline-send");
  if (!base?.completion?.ok) {
    failures.push("baseline send did not complete");
  }
  const queue = byName.get("queue");
  if (!queue?.queuedVisible?.ok || !queue?.firstCompletion?.ok || !queue?.secondCompletion?.ok) {
    failures.push("queue did not show and complete both queued turns");
  }
  const cancel = byName.get("cancel");
  if (!cancel?.running?.ok || !cancel?.stopClick?.ok || !cancel?.settled?.ok) {
    failures.push("cancel controls did not reach running/stop/settled states");
  }
  const cancelRows = markerResponseRows(output.finalProbe, markers.cancelMarker);
  const completedCancelRows = cancelRows.filter((row) => {
    const plan = row.responseJson?.plan || {};
    return row.status === 200 && plan.nextStep !== "cancelled";
  });
  if (completedCancelRows.length > 0) {
    failures.push("cancelled turn still completed with a non-cancelled response");
  }
  if (!fetchRows(output.finalProbe, "request_agent_run_cancel").some((row) => row.status === 200)) {
    failures.push("cancel request did not return HTTP 200");
  }
  const compact = byName.get("compact");
  if (!compact?.settled?.ok) {
    failures.push("compact did not settle");
  }
  const vision = byName.get("vision-attachment");
  if (!vision?.attachment?.ok || !vision?.completion?.ok) {
    failures.push("vision attachment was not attached and completed");
  }
  const visionRow = output.visionFetches[0];
  if (!visionRow || visionRow.status !== 200 || visionRow.visionImageCount !== 1 || visionRow.firstStepKind !== "vision") {
    failures.push("vision attachment did not reach the runtime vision step");
  }
  if (output.longTaskCount !== 0) {
    failures.push(`renderer long tasks recorded: ${output.longTaskCount}`);
  }
  return failures;
}

async function main() {
  await mkdir(dirname(outPath), { recursive: true });
  await closeExistingVrcforgeProcesses();
  const beforeLaunch = await processSnapshot();
  const launchedAt = Date.now();
  const child = spawn(exe, [], {
    detached: false,
    stdio: "ignore",
    env: {
      ...process.env,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --remote-allow-origins=*`,
    },
  });
  const page = await waitForCdpTarget();
  const cdp = connectCdp(page.webSocketDebuggerUrl);
  await cdp.opened;
  await cdp.send("Runtime.enable");
  await cdp.send("Page.enable");
  await cdp.send("Network.enable");
  await cdp.send("Performance.enable");
  const attachedAt = Date.now();
  await waitForEval(cdp, "document.readyState === 'complete' || document.readyState === 'interactive'");
  await installProbe(cdp);
  const ready = await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      return { ok: Boolean(textarea && submit && !textarea.disabled), bodyLength: document.body.innerText.length };
    })()`,
    30000,
  );

  const scenarios = [];
  const baseMarker = `${marker}_BASE`;
  scenarios.push(await runSend(cdp, "baseline-send", `Do not use tools. Reply in one short sentence and include this exact token: ${baseMarker}`, baseMarker));

  const queueFirst = `${marker}_QUEUE_A`;
  const queueSecond = `${marker}_QUEUE_B`;
  const queueInputA = await typeComposer(cdp, `Do not use tools. Reply in one short sentence and include this exact token: ${queueFirst}`);
  const queueClickA = await clickSubmit(cdp);
  await waitForText(cdp, /\u6267\u884c\u4e2d|\u7b49\u5f85\u6a21\u578b\u54cd\u5e94|\u601d\u8003\u4e2d|running|thinking/i, 10000);
  const queueInputB = await typeComposer(cdp, `Do not use tools. Reply in one short sentence and include this exact token: ${queueSecond}`);
  const queueClickB = await clickSubmit(cdp);
  const queuedVisible = await waitForText(cdp, /\u5df2\u6392\u961f|\u961f\u5217|queued|queue/i, 10000);
  const queueACompletion = await waitForMarkerComplete(cdp, queueFirst, 90000);
  const queueBCompletion = await waitForMarkerComplete(cdp, queueSecond, 90000);
  scenarios.push({
    name: "queue",
    markers: [queueFirst, queueSecond],
    inputA: queueInputA,
    clickA: queueClickA,
    inputB: queueInputB,
    clickB: queueClickB,
    queuedVisible,
    firstCompletion: queueACompletion,
    secondCompletion: queueBCompletion,
  });

  const cancelMarker = `${marker}_CANCEL`;
  const cancelInput = await typeComposer(
    cdp,
    `Do not use tools. Write a deliberately long answer in ten numbered short lines and include this exact token in every line: ${cancelMarker}`,
  );
  const cancelClick = await clickSubmit(cdp);
  const cancelRunning = await waitForText(cdp, /\u6267\u884c\u4e2d|\u7b49\u5f85\u6a21\u578b\u54cd\u5e94|\u601d\u8003\u4e2d|running|thinking/i, 10000);
  const stopClick = await clickStop(cdp);
  const cancelSettled = await waitForText(cdp, /cancel|cancelled|\u53d6\u6d88|\u505c\u6b62|\u6838\u5fc3\u5728\u7ebf|DeepSeek|Gemini/i, 30000);
  scenarios.push({ name: "cancel", marker: cancelMarker, input: cancelInput, click: cancelClick, running: cancelRunning, stopClick, settled: cancelSettled });

  const compactInput = await typeComposer(cdp, "/compact");
  const compactClick = await clickSubmit(cdp);
  const compactSettled = await waitForText(cdp, /\u538b\u7f29|compacted|compact|Nothing to compact|\u6ca1\u6709\u53ef\u538b\u7f29/i, 90000);
  scenarios.push({ name: "compact", input: compactInput, click: compactClick, settled: compactSettled });

  const visionMarker = `${marker}_VISION`;
  const visionAttachmentName = `${visionMarker}.png`;
  const visionAttach = await attachProbeImage(cdp, visionAttachmentName);
  const visionScenario = await runSend(
    cdp,
    "vision-attachment",
    `Do not use tools. Reply in one short sentence and include this exact token: ${visionMarker}. Describe whether the attached image was analyzed or whether vision is unavailable.`,
    visionMarker,
    120000,
  );
  scenarios.push({ ...visionScenario, attachment: visionAttach });

  const finalProbe = await evalValue(
    cdp,
    `(() => ({
      probe: window.__vrcWorkflowProbe,
      bodyLength: document.body.innerText.length,
      workflowMarkerCount: (document.body.innerText.match(new RegExp(${JSON.stringify(marker)}, "g")) || []).length,
      tail: document.body.innerText.slice(-1500),
    }))()`,
  );
  const perfMetrics = await cdp.send("Performance.getMetrics");
  const network = summarizeNetwork(cdp.events).filter((entry) =>
    /ipc\.localhost|tauri\.localhost|127\.0\.0\.1|localhost/.test(entry.url || ""),
  );
  const output = {
    schema: "vrcforge.packaged_workflow_probe.v1",
    marker,
    exe,
    port,
    beforeLaunch,
    launchedAt,
    attachedAt,
    attachMs: attachedAt - launchedAt,
    ready,
    scenarios,
    visionFetches: summarizeVisionFetches(finalProbe, visionMarker),
    finalProbe,
    longTaskCount: localLongTaskCount(finalProbe),
    network,
    performanceMetrics: perfMetrics.metrics,
    childPid: child.pid,
    maxWaitMs,
  };
  output.assertions = {
    failures: validateWorkflowOutput(output, { cancelMarker, visionMarker }),
  };
  await writeFile(outPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  cdp.close();
  child.kill();
  await sleep(500);
  await closeExistingVrcforgeProcesses();
  console.log(outPath);
  if (output.assertions.failures.length > 0) {
    console.error(`Packaged workflow probe failed: ${output.assertions.failures.join("; ")}`);
    process.exit(1);
  }
}

main().catch(async (error) => {
  await closeExistingVrcforgeProcesses().catch(() => {});
  await mkdir(dirname(outPath), { recursive: true });
  await writeFile(outPath, `${JSON.stringify({ ok: false, error: String(error && error.stack || error), marker }, null, 2)}\n`, "utf8").catch(() => {});
  console.error(error);
  process.exit(1);
});
