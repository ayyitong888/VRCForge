import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const packagedRoot = resolve(repoRoot, "dist", "VRCForge_Windows_x64");
const packagedRootPowerShell = packagedRoot.replaceAll("'", "''");
const exe = resolve(packagedRoot, "VRCForge.exe");
const port = Number(process.env.VRCFORGE_CDP_PORT || "9340");
const marker = `LATENCY_PROBE_${Date.now()}`;
const outPath = resolve(repoRoot, "artifacts", "latency", `packaged-latency-${marker}.json`);
const maxWaitMs = Number(process.env.VRCFORGE_PROBE_WAIT_MS || "180000");
const closeOnComplete = process.env.VRCFORGE_PROBE_CLOSE_ON_COMPLETE === "1";

function sleep(ms) {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms));
}

function runPowerShell(script) {
  return new Promise((resolveRun, rejectRun) => {
    const child = spawn(
      "powershell",
      ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
      { windowsHide: true },
    );
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

async function closeExistingVrcforgeProcesses() {
  await runPowerShell(`
    $packagedRoot = '${packagedRootPowerShell}'
    Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Path -and $_.Path.StartsWith($packagedRoot, [StringComparison]::OrdinalIgnoreCase) } |
      Stop-Process -Force -ErrorAction SilentlyContinue
  `);
  await waitForPortReleased(15000);
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

async function processSnapshot() {
  return runPowerShell(`
    $packagedRoot = '${packagedRootPowerShell}'
    $processes = Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Path -and $_.Path.StartsWith($packagedRoot, [StringComparison]::OrdinalIgnoreCase) } |
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
      entry.wallTime = params.wallTime;
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

async function main() {
  await mkdir(dirname(outPath), { recursive: true });
  await closeExistingVrcforgeProcesses();
  const beforeLaunch = await processSnapshot();
  const launchedAt = Date.now();
  const child = spawn(exe, [], {
    detached: !closeOnComplete,
    stdio: "ignore",
    env: {
      ...process.env,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --remote-allow-origins=*`,
    },
  });
  if (!closeOnComplete) {
    child.unref();
  }
  const page = await waitForCdpTarget();
  const cdp = connectCdp(page.webSocketDebuggerUrl);
  await cdp.opened;
  await cdp.send("Runtime.enable");
  await cdp.send("Page.enable");
  await cdp.send("Network.enable");
  await cdp.send("Performance.enable");

  const attachedAt = Date.now();
  await waitForEval(cdp, "document.readyState === 'complete' || document.readyState === 'interactive'");
  await evalValue(
    cdp,
    `(() => {
      const probe = window.__vrcLatencyProbe = {
        installedAt: performance.now(),
        fetches: [],
        longTasks: [],
        marks: [],
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
          if (url.includes("send_agent_message")) {
            row.responsePreview = await response
              .clone()
              .text()
              .then((text) => text.slice(0, 2000))
              .catch((error) => "response preview failed: " + String(error && error.message || error));
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

  const readyProbe = await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      return { ok: Boolean(textarea && submit), readyState: document.readyState, bodyLength: document.body.innerText.length };
    })()`,
    30000,
  );

  const startupMetrics = await evalValue(
    cdp,
    `(() => ({
      readyState: document.readyState,
      bodyLength: document.body.innerText.length,
      textTail: document.body.innerText.slice(-500),
      perfNow: performance.now(),
      navigation: performance.getEntriesByType("navigation").map((entry) => ({
        startTime: entry.startTime,
        domInteractive: entry.domInteractive,
        domContentLoadedEventEnd: entry.domContentLoadedEventEnd,
        loadEventEnd: entry.loadEventEnd,
        duration: entry.duration,
      })),
    }))()`,
  );

  const inputText = `Do not use tools. Reply in one short sentence and include this exact token: ${marker}`;
  const composerReady = await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      return {
        ok: Boolean(textarea && submit && !textarea.disabled),
        textareaDisabled: Boolean(textarea && textarea.disabled),
        submitDisabled: Boolean(submit && submit.disabled),
        bodyLength: document.body.innerText.length,
        tail: document.body.innerText.slice(-500),
      };
    })()`,
    30000,
  );
  const inputResult = await evalValue(
    cdp,
    `(async () => {
      const textarea = document.querySelector("textarea");
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      const start = performance.now();
      textarea.focus();
      setter.call(textarea, ${JSON.stringify(inputText)});
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((resolve) => requestAnimationFrame(resolve));
      return {
        duration: performance.now() - start,
        valueLength: textarea.value.length,
        disabled: textarea.disabled,
        activeTag: document.activeElement && document.activeElement.tagName,
      };
    })()`,
  );
  const submitReady = await waitForEval(
    cdp,
    `(() => {
      const textarea = document.querySelector("textarea");
      const submit = document.querySelector("button[type='submit']");
      return {
        ok: Boolean(textarea && submit && !textarea.disabled && !submit.disabled && textarea.value.includes(${JSON.stringify(marker)})),
        textareaDisabled: Boolean(textarea && textarea.disabled),
        submitDisabled: Boolean(submit && submit.disabled),
        valueLength: textarea ? textarea.value.length : null,
      };
    })()`,
    5000,
  );

  const clickResult = await evalValue(
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
      };
    })()`,
  );

  const samples = [];
  const sampleStartedAt = Date.now();
  const sampleDeadline = sampleStartedAt + maxWaitMs;
  let completed = false;
  let stableCompleteSamples = 0;
  while (Date.now() < sampleDeadline) {
    await sleep(250);
    samples.push(
      await evalValue(
        cdp,
        `(() => {
          const start = performance.now();
          const textarea = document.querySelector("textarea");
          const submit = document.querySelector("button[type='submit']");
          const body = document.body.innerText;
          return {
            at: performance.now(),
            evalCost: performance.now() - start,
            bodyLength: body.length,
            markerCount: (body.match(new RegExp(${JSON.stringify(marker)}, "g")) || []).length,
            runningText: /执行中|等待模型响应|思考中|running|thinking/i.test(body),
            textareaDisabled: Boolean(textarea && textarea.disabled),
            submitDisabled: Boolean(submit && submit.disabled),
            textareaValueLength: textarea ? textarea.value.length : null,
            runningText: /\u6267\u884c\u4e2d|\u7b49\u5f85\u6a21\u578b\u54cd\u5e94|\u601d\u8003\u4e2d|\u8fd0\u884c\u4e2d|running|thinking/i.test(body),
          };
        })()`,
      ),
    );
    const latest = samples.at(-1);
    if (latest.markerCount > 1 && !latest.runningText) {
      stableCompleteSamples += 1;
    } else {
      stableCompleteSamples = 0;
    }
    if (stableCompleteSamples >= 4) {
      completed = true;
      break;
    }
  }

  const finalProbe = await evalValue(
    cdp,
    `(() => ({
      probe: window.__vrcLatencyProbe,
      bodyLength: document.body.innerText.length,
      markerCount: (document.body.innerText.match(new RegExp(${JSON.stringify(marker)}, "g")) || []).length,
      markerSnippets: (() => {
        const body = document.body.innerText;
        const snippets = [];
        let index = -1;
        while ((index = body.indexOf(${JSON.stringify(marker)}, index + 1)) >= 0) {
          snippets.push(body.slice(Math.max(0, index - 180), Math.min(body.length, index + 240)));
        }
        return snippets;
      })(),
      tail: document.body.innerText.slice(-1200),
    }))()`,
  );
  const perfMetrics = await cdp.send("Performance.getMetrics");
  const network = summarizeNetwork(cdp.events).filter((entry) =>
    /ipc\.localhost|tauri\.localhost|127\.0\.0\.1|localhost/.test(entry.url || ""),
  );
  const output = {
    schema: "vrcforge.packaged_latency_probe.v1",
    marker,
    exe,
    port,
    beforeLaunch,
    launchedAt,
    attachedAt,
    attachMs: attachedAt - launchedAt,
    readyProbe,
    startupMetrics,
    composerReady,
    inputResult,
    submitReady,
    clickResult,
    completed,
    timedOut: !completed,
    waitMs: Date.now() - sampleStartedAt,
    maxWaitMs,
    samples,
    finalProbe,
    network,
    performanceMetrics: perfMetrics.metrics,
    childPid: child.pid,
    closeOnComplete,
  };
  await writeFile(outPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
  cdp.close();
  if (completed && closeOnComplete) {
    child.kill();
    await sleep(500);
    await closeExistingVrcforgeProcesses();
  }
  console.log(outPath);
}

main().catch(async (error) => {
  await closeExistingVrcforgeProcesses().catch(() => {});
  await mkdir(dirname(outPath), { recursive: true });
  await writeFile(outPath, `${JSON.stringify({ ok: false, error: String(error && error.stack || error), marker }, null, 2)}\n`, "utf8").catch(() => {});
  console.error(error);
  process.exit(1);
});
