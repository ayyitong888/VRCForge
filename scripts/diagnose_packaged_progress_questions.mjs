import { spawn } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const repoRoot = resolve(import.meta.dirname, "..");
const packagedRoot = resolve(repoRoot, "dist", "VRCForge_Windows_x64");
const packagedRootPowerShell = packagedRoot.replaceAll("'", "''");
const exe = resolve(packagedRoot, "VRCForge.exe");
const port = Number(process.env.VRCFORGE_CDP_PORT || "9342");
const marker = `PQ_PROBE_${Date.now()}`;
const outPath = resolve(repoRoot, "artifacts", "actual-app-progress", `progress-question-${marker}.json`);
const appOrigin = process.env.VRCFORGE_APP_ORIGIN || "http://127.0.0.1:8757";
const appRequestOrigin = "tauri://localhost";
let appSessionToken = "";

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
  const value = await runPowerShell(`
    $packagedRoot = '${packagedRootPowerShell}'
    $processes = Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Path -and $_.Path.StartsWith($packagedRoot, [StringComparison]::OrdinalIgnoreCase) } |
      Select-Object Id,ProcessName,Path
    $ports = Get-NetTCPConnection -LocalPort 8757 -ErrorAction SilentlyContinue |
      Select-Object LocalAddress,LocalPort,State,OwningProcess
    [pscustomobject]@{ processes = @($processes); ports = @($ports) } | ConvertTo-Json -Depth 4 -Compress
  `);
  return value ? JSON.parse(value) : { processes: [], ports: [] };
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
      const { resolve: resolvePending, reject } = pending.get(payload.id);
      pending.delete(payload.id);
      if (payload.error) {
        reject(new Error(payload.error.message || JSON.stringify(payload.error)));
      } else {
        resolvePending(payload.result);
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

function sanitizeProbeValue(value) {
  if (Array.isArray(value)) {
    return value.map(sanitizeProbeValue);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  const sanitized = {};
  for (const [key, raw] of Object.entries(value)) {
    if (/token|secret|authorization|apiKey|api_key|password/i.test(key)) {
      sanitized[key] = raw ? "<redacted>" : raw;
    } else {
      sanitized[key] = sanitizeProbeValue(raw);
    }
  }
  return sanitized;
}

async function appApi(path, options = {}) {
  if (!appSessionToken) {
    const tokenPath = resolve(
      process.env.VRCFORGE_CONFIG_DIR || resolve(process.env.LOCALAPPDATA || "", "VRCForge", "agentic-app", "config"),
      "app-session-token",
    );
    try {
      appSessionToken = (await readFile(tokenPath, "utf8")).trim();
    } catch {
      const sessionResponse = await fetch(`${appOrigin}/api/app/session`, { headers: { Origin: appRequestOrigin } });
      const sessionPayload = await sessionResponse.json();
      appSessionToken = sessionPayload.appSessionToken || sessionPayload.app_session_token || "";
    }
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs || 30000);
  try {
    const response = await fetch(`${appOrigin}${path}`, {
      method: options.method || "GET",
      headers: {
        Origin: appRequestOrigin,
        "Content-Type": "application/json",
        Authorization: `Bearer ${appSessionToken}`,
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal,
    });
    const text = await response.text();
    let payload = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = { text: text.slice(0, 1000) };
    }
    return { ok: response.ok, status: response.status, payload: sanitizeProbeValue(payload) };
  } finally {
    clearTimeout(timeout);
  }
}

async function resolveActiveChatScope(cdp, bootstrap) {
  const header = await evalValue(
    cdp,
    `(() => {
      const labels = Array.from(document.querySelectorAll("header > div:first-child > span"))
        .map((node) => (node.textContent || "").trim())
        .filter(Boolean);
      return { projectLabel: labels[0] || "", title: labels.at(-1) || "" };
    })()`,
  );
  const projectRows = bootstrap?.payload?.health?.projects?.projects || [];
  const projectPaths = Array.from(new Set(projectRows.map((item) => String(item?.path || "").trim()).filter(Boolean)));
  const query = projectPaths.map((path) => `projectPath=${encodeURIComponent(path)}`).join("&");
  const chatsResponse = await appApi(`/api/app/chats${query ? `?${query}` : ""}`);
  const chats = Array.isArray(chatsResponse?.payload?.chats) ? chatsResponse.payload.chats : [];
  const titleMatches = chats.filter(
    (chat) =>
      (String(chat?.sessionId || "").trim() || String(chat?.projectPath || "").trim()) &&
      String(chat?.title || "").trim() === String(header?.title || "").trim(),
  );
  const projectMatches = titleMatches.filter((chat) => {
    const projectRoot = String(chat?.projectPath || "").replace(/[\\/]+$/, "");
    const projectName = projectRoot.split(/[\\/]/).at(-1) || "";
    return projectName === String(header?.projectLabel || "").trim();
  });
  const candidates = projectMatches.length ? projectMatches : titleMatches;
  candidates.sort((left, right) => String(right?.updatedAt || "").localeCompare(String(left?.updatedAt || "")));
  const chat = candidates[0];
  return {
    ok: Boolean(chat?.sessionId || chat?.projectPath),
    projectLabel: String(header?.projectLabel || ""),
    title: String(header?.title || ""),
    chatId: String(chat?.id || ""),
    sessionId: String(chat?.sessionId || ""),
    projectRoot: String(chat?.projectPath || ""),
    candidateCount: candidates.length,
  };
}

async function waitForActiveChatScope(cdp, bootstrap, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let latest = null;
  while (Date.now() < deadline) {
    latest = await resolveActiveChatScope(cdp, bootstrap);
    if (latest.ok) {
      return latest;
    }
    await sleep(500);
  }
  return latest || { ok: false, projectLabel: "", title: "", chatId: "", sessionId: "", projectRoot: "", candidateCount: 0 };
}

async function main() {
  await mkdir(dirname(outPath), { recursive: true });
  await closeExistingVrcforgeProcesses();
  const beforeLaunch = await processSnapshot();
  const child = spawn(exe, [], {
    detached: false,
    stdio: "ignore",
    env: {
      ...process.env,
      WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${port} --remote-allow-origins=*`,
    },
  });

  const output = {
    schema: "vrcforge.packaged_progress_question_probe.v1",
    marker,
    beforeLaunch,
    assertions: [],
  };
  let cdp = null;
  let activeScope = null;
  let createdQuestionId = "";
  try {
    const page = await waitForCdpTarget();
    cdp = connectCdp(page.webSocketDebuggerUrl);
    await cdp.opened;
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    await waitForEval(cdp, "document.readyState === 'complete' || document.readyState === 'interactive'");
    output.ready = await waitForEval(
      cdp,
      `(() => {
        const textarea = document.querySelector("textarea");
        const submit = document.querySelector("button[type='submit']");
        return { ok: Boolean(textarea && submit && !textarea.disabled), bodyLength: document.body.innerText.length };
      })()`,
      30000,
    );
    output.bootstrap = await appApi("/api/app/bootstrap");
    activeScope = await waitForActiveChatScope(cdp, output.bootstrap);
    output.activeScope = activeScope;
    if (!activeScope.ok) {
      throw new Error(`Could not resolve the active chat session: ${JSON.stringify(activeScope)}`);
    }

    const progressTitle = `${marker} progress active`;
    const questionText = `${marker} choose acceptance path`;
    const optionLabel = `${marker} option one`;
    const secondOptionLabel = `${marker} option two`;
    const eighthOptionLabel = `${marker} option eight`;
    const optionDescription = `${marker} recommended explanation`;
    output.progressCreate = await appApi("/api/app/agent/progress/replace", {
      method: "POST",
      body: {
        sessionId: activeScope.sessionId,
        projectRoot: activeScope.projectRoot,
        items: [
          { id: `${marker}-progress-1`, title: `${marker} read old todos`, status: "completed", order: 1, owner: "agent" },
          { id: `${marker}-progress-2`, title: progressTitle, status: "in_progress", order: 2, owner: "agent" },
          { id: `${marker}-progress-3`, title: `${marker} final check`, status: "pending", order: 3, owner: "agent" },
        ],
      },
    });
    output.questionCreate = await appApi("/api/app/agent/questions", {
      method: "POST",
      body: {
        sessionId: activeScope.sessionId,
        projectRoot: activeScope.projectRoot,
        header: "Acceptance",
        question: questionText,
        options: Array.from({ length: 8 }, (_, index) => ({
          id: `option-${index + 1}`,
          label: index === 0 ? optionLabel : index === 1 ? secondOptionLabel : index === 7 ? eighthOptionLabel : `${marker} option ${index + 1}`,
          value: `accept option ${index + 1}`,
          description: index === 0 ? optionDescription : `${marker} explanation ${index + 1}`,
        })),
      },
    });
    createdQuestionId = output.questionCreate?.payload?.question?.questionId || "";

    output.visible = await waitForEval(
      cdp,
      `(() => {
        const bodyText = document.body.innerText;
        const asides = Array.from(document.querySelectorAll("aside")).map((node) => node.innerText || "");
        const rightRailText = asides[asides.length - 1] || "";
        const questionInRightRail = rightRailText.includes(${JSON.stringify(questionText)});
        const progressInRightRail = rightRailText.includes(${JSON.stringify(progressTitle)});
        const questionVisible = bodyText.includes(${JSON.stringify(questionText)}) && bodyText.includes(${JSON.stringify(optionLabel)});
        const secondOptionVisible = bodyText.includes(${JSON.stringify(secondOptionLabel)});
        const eighthOptionVisible = bodyText.includes(${JSON.stringify(eighthOptionLabel)});
        const recommendedVisible = /Recommended|推荐|推薦|推奨/.test(bodyText);
        const explanationButton = Array.from(document.querySelectorAll("button")).find((button) =>
          (button.title || "").includes(${JSON.stringify(optionDescription)})
        );
        const explanationTitle = Boolean(explanationButton);
        const optionScroller = explanationButton?.parentElement;
        const optionsAreScrollable = Boolean(
          optionScroller && optionScroller.scrollHeight > optionScroller.clientHeight && optionScroller.clientHeight <= 270
        );
        const hasSomethingElse = /Something else|其他回答|別の回答|その他/.test(bodyText);
        const hasSkip = /Skip|跳过|跳過|スキップ/.test(bodyText);
        const hasAwaitingRail = /待回答|Questions/.test(rightRailText);
        return {
          ok: questionVisible && secondOptionVisible && eighthOptionVisible && recommendedVisible && explanationTitle && optionsAreScrollable && !questionInRightRail && progressInRightRail && hasSomethingElse && hasSkip && !hasAwaitingRail,
          questionVisible,
          secondOptionVisible,
          eighthOptionVisible,
          recommendedVisible,
          explanationTitle,
          optionsAreScrollable,
          optionScrollerClientHeight: optionScroller?.clientHeight || 0,
          optionScrollerScrollHeight: optionScroller?.scrollHeight || 0,
          questionInRightRail,
          progressInRightRail,
          hasSomethingElse,
          hasSkip,
          hasAwaitingRail,
          rightRailText: rightRailText.slice(0, 2000),
          bodyText: bodyText.slice(0, 3000),
        };
      })()`,
      30000,
    );

    output.clickOption = await evalValue(
      cdp,
      `(() => {
        const buttons = Array.from(document.querySelectorAll("button"));
        const target = buttons.find((button) => (button.innerText || "").includes(${JSON.stringify(optionLabel)}));
        if (!target) {
          return { ok: false, reason: "option button not found", buttonTexts: buttons.map((button) => button.innerText).slice(-20) };
        }
        target.click();
        return { ok: true };
      })()`,
    );
    output.answered = await waitForEval(
      cdp,
      `(() => {
        const bodyText = document.body.innerText;
        return { ok: !bodyText.includes(${JSON.stringify(questionText)}), bodyText: bodyText.slice(0, 2500) };
      })()`,
      15000,
    );
    const scopeQuery = new URLSearchParams({
      includeAnswered: "true",
      limit: "20",
      sessionId: activeScope.sessionId,
      projectRoot: activeScope.projectRoot,
    });
    output.answerApi = createdQuestionId
      ? await appApi(`/api/app/agent/questions?${scopeQuery}`)
      : { ok: false, status: 0, payload: { error: "questionId missing" } };

    if (!output.visible?.ok) {
      output.assertions.push("question card/progress rail layout did not match expected visible state");
    }
    if (!output.clickOption?.ok) {
      output.assertions.push("question option button could not be clicked");
    }
    if (!output.answered?.ok) {
      output.assertions.push("answered question did not leave the composer-adjacent card");
    }
  } catch (error) {
    output.error = String(error && error.stack ? error.stack : error);
    output.assertions.push("probe threw before completion");
  } finally {
    if (activeScope?.ok) {
      const cleanupQuery = new URLSearchParams({ sessionId: activeScope.sessionId, projectRoot: activeScope.projectRoot });
      output.progressCleanup = [];
      for (const progressId of [`${marker}-progress-1`, `${marker}-progress-2`, `${marker}-progress-3`]) {
        output.progressCleanup.push(
          await appApi(`/api/app/agent/progress/${encodeURIComponent(progressId)}?${cleanupQuery}`, { method: "DELETE" }).catch((error) => ({
            ok: false,
            error: String(error),
          })),
        );
      }
      if (createdQuestionId && !output.answered?.ok) {
        output.questionCleanup = await appApi(`/api/app/agent/questions/${encodeURIComponent(createdQuestionId)}/answer`, {
          method: "POST",
          body: {
            answer: "Release probe cleanup",
            value: "Release probe cleanup",
            sessionId: activeScope.sessionId,
            projectRoot: activeScope.projectRoot,
          },
        }).catch((error) => ({ ok: false, error: String(error) }));
      }
    }
    if (cdp) {
      cdp.close();
    }
    try {
      child.kill();
    } catch {}
    await closeExistingVrcforgeProcesses().catch((error) => {
      output.cleanupError = String(error);
    });
    output.afterCleanup = await processSnapshot().catch((error) => ({ error: String(error) }));
    await writeFile(outPath, JSON.stringify(output, null, 2), "utf8");
    console.log(outPath);
    if (output.assertions.length) {
      console.error(output.assertions.join("\n"));
      process.exitCode = 1;
    }
  }
}

main();
