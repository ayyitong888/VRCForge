const PROVIDER_PRESETS = {
  gemini: {
    providerLabel: "Gemini",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai/",
    model: "gemini-2.5-flash",
    authHeader: "Authorization: Bearer",
    usesBaseUrl: true,
  },
  deepseek: {
    providerLabel: "DeepSeek",
    base_url: "https://api.deepseek.com",
    model: "deepseek-chat",
    authHeader: "Authorization: Bearer",
    usesBaseUrl: true,
  },
  openai: {
    providerLabel: "OpenAI",
    base_url: "https://api.openai.com/v1",
    model: "gpt-4.1-mini",
    authHeader: "Authorization: Bearer",
    usesBaseUrl: true,
  },
  openrouter: {
    providerLabel: "OpenRouter",
    base_url: "https://openrouter.ai/api/v1",
    model: "openai/gpt-4.1-mini",
    authHeader: "Authorization: Bearer",
    usesBaseUrl: true,
  },
  anthropic: {
    providerLabel: "Anthropic",
    base_url: "",
    model: "claude-opus-4-6",
    authHeader: "x-api-key",
    usesBaseUrl: false,
  },
  custom: {
    providerLabel: "自定义",
    base_url: "",
    model: "",
    authHeader: "Authorization: Bearer",
    usesBaseUrl: true,
  },
};

const state = {
  socket: null,
  projects: [],
  selectedProjectPath: "",
  unityStatus: null,
  sceneAvatars: [],
  selectedAvatarPath: "",
  selectedAvatarName: "",
  recentLogs: [],
  apiConfig: null,
  providerDrafts: {},
  apiConfigDirty: false,
  blendshapes: [],
  blendshapeWorking: {},
  blendshapeBaseline: {},
  undoDepth: 0,
  clothes: [],
  latestScreenshotUrl: "",
};

const refs = {};

document.addEventListener("DOMContentLoaded", () => {
  cacheRefs();
  bindEvents();
  connectSocket();
  updateModeVisibility();
  syncMockModeText();
});

function cacheRefs() {
  const ids = [
    "socket-status",
    "unity-status-light",
    "unity-status-label",
    "status-provider",
    "status-model",
    "status-avatar",
    "project-count",
    "project-select",
    "refresh-projects-btn",
    "open-project-btn",
    "scene-avatar-select",
    "refresh-avatars-btn",
    "load-blendshapes-btn",
    "unity-host",
    "unity-port",
    "unity-instance",
    "sync-state-btn",
    "unity-status-btn",
    "unity-tools-btn",
    "unity-status-output",
    "config-path-tag",
    "provider-select",
    "api-model-input",
    "api-model-label",
    "api-key-input",
    "api-key-label",
    "api-key-note",
    "api-base-url-field",
    "api-base-url",
    "api-base-url-note",
    "config-save-status",
    "config-output",
    "save-config-btn",
    "reset-provider-btn",
    "source-mode",
    "source-mode-badge",
    "export-json-field",
    "export-json",
    "plan-json",
    "min-confidence",
    "mock-execute",
    "allow-low-confidence",
    "save-artifacts",
    "summary-output",
    "instruction-input",
    "ai-run-btn",
    "manual-apply-btn",
    "manual-undo-btn",
    "blendshape-count-chip",
    "pending-count",
    "blendshape-search",
    "avatar-path-display",
    "blendshape-list",
    "scan-clothes-btn",
    "generate-fx-btn",
    "apply-fx-btn",
    "fx-apply-panel",
    "fx-apply-count",
    "fx-dry-run",
    "fx-csharp-preview",
    "clothes-count-chip",
    "clothes-list",
    "fx-output",
    "scan-params-btn",
    "optimize-params-btn",
    "apply-params-btn",
    "param-diff-panel",
    "param-diff-count",
    "param-dry-run",
    "param-diff-list",
    "param-csharp-preview",
    "bool-count",
    "int-count",
    "float-count",
    "param-suggestions",
    "param-output",
    "vision-angle-tabs",
    "vision-multi-thumbs",
    "capture-screenshot-btn",
    "capture-multi-btn",
    "audit-vision-btn",
    "audit-multi-btn",
    "vision-status-chip",
    "vision-image",
    "vision-placeholder",
    "vision-result",
    "log-stream",
    "clear-logs-btn",
  ];

  for (const id of ids) {
    refs[id] = document.getElementById(id);
  }
}

function bindEvents() {
  refs["source-mode"].addEventListener("change", updateModeVisibility);
  refs["mock-execute"].addEventListener("change", syncMockModeText);
  refs["project-select"].addEventListener("change", onProjectSelected);
  refs["scene-avatar-select"].addEventListener("change", onSceneAvatarSelected);
  refs["refresh-projects-btn"].addEventListener("click", () => runButtonTask("refresh-projects-btn", "刷新中...", refreshProjects));
  refs["open-project-btn"].addEventListener("click", () => runButtonTask("open-project-btn", "打开中...", openProject));
  refs["refresh-avatars-btn"].addEventListener("click", () => runButtonTask("refresh-avatars-btn", "扫描中...", refreshSceneAvatars));
  refs["load-blendshapes-btn"].addEventListener("click", () => runButtonTask("load-blendshapes-btn", "加载中...", loadBlendshapes));
  refs["sync-state-btn"].addEventListener("click", () => runButtonTask("sync-state-btn", "同步中...", syncDashboardState));
  refs["unity-status-btn"].addEventListener("click", () => runButtonTask("unity-status-btn", "检测中...", loadUnityStatus));
  refs["unity-tools-btn"].addEventListener("click", () => runButtonTask("unity-tools-btn", "读取中...", loadUnityTools));
  refs["provider-select"].addEventListener("change", onProviderChanged);
  refs["save-config-btn"].addEventListener("click", () => runButtonTask("save-config-btn", "保存中...", () => saveApiConfig(true)));
  refs["reset-provider-btn"].addEventListener("click", resetProviderDefaults);
  refs["ai-run-btn"].addEventListener("click", () => runButtonTask("ai-run-btn", "执行中...", runAiPipeline));
  refs["manual-apply-btn"].addEventListener("click", () => runButtonTask("manual-apply-btn", "应用中...", applyManualBlendshapes));
  refs["manual-undo-btn"].addEventListener("click", () => runButtonTask("manual-undo-btn", "撤销中...", undoManualBlendshapes));
  refs["scan-clothes-btn"].addEventListener("click", () => runButtonTask("scan-clothes-btn", "扫描中...", scanClothes));
  refs["generate-fx-btn"].addEventListener("click", () => runButtonTask("generate-fx-btn", "生成中...", generateFxBlueprint));
  refs["apply-fx-btn"].addEventListener("click", () => runButtonTask("apply-fx-btn", "写入中...", applyClothesFx));
  refs["scan-params-btn"].addEventListener("click", () => runButtonTask("scan-params-btn", "扫描中...", scanParameters));
  refs["optimize-params-btn"].addEventListener("click", () => runButtonTask("optimize-params-btn", "分析中...", optimizeParameters));
  refs["apply-params-btn"].addEventListener("click", () => runButtonTask("apply-params-btn", "应用中...", applyParameterOptimization));
  refs["capture-screenshot-btn"].addEventListener("click", () => runButtonTask("capture-screenshot-btn", "截图中...", captureScreenshot));
  refs["capture-multi-btn"].addEventListener("click", () => runButtonTask("capture-multi-btn", "多视角截图中...", captureMultiScreenshot));
  refs["audit-vision-btn"].addEventListener("click", () => runButtonTask("audit-vision-btn", "审核中...", auditVision));
  refs["audit-multi-btn"].addEventListener("click", () => runButtonTask("audit-multi-btn", "聚合审核中...", auditMultiVision));
  refs["clear-logs-btn"].addEventListener("click", clearLogView);
  refs["blendshape-search"].addEventListener("input", renderBlendshapeList);
  
  if (refs["vision-angle-tabs"]) {
    refs["vision-angle-tabs"].addEventListener("click", onVisionAngleTabClick);
  }

  ["api-model-input", "api-key-input", "api-base-url"].forEach((id) => {
    refs[id].addEventListener("input", markApiConfigDirty);
  });
}

function connectSocket() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
  state.socket = socket;
  setSocketStatus("Connecting", false);

  socket.addEventListener("open", () => setSocketStatus("Live", true));
  socket.addEventListener("close", () => {
    setSocketStatus("Offline", false);
    setTimeout(connectSocket, 1500);
  });
  socket.addEventListener("error", () => setSocketStatus("Error", false));
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    handleSocketEvent(message.type, message.payload);
  });
}

function handleSocketEvent(type, payload) {
  switch (type) {
    case "hello":
      applyBootstrap(payload);
      break;
    case "unity_status":
      applyUnityStatus(payload);
      break;
    case "projects":
      renderProjects(payload);
      break;
    case "state":
      applyDashboardState(payload);
      break;
    case "config":
      applyApiConfigPayload(payload, false);
      break;
    case "log":
      appendLog(payload, true);
      break;
    default:
      break;
  }
}

function applyBootstrap(payload) {
  if (payload.health?.defaults) {
    refs["source-mode"].value = payload.health.defaults.sourceMode || "unity_live_export";
    refs["export-json"].value = payload.health.defaults.exportJson || "";
    refs["plan-json"].value = payload.health.defaults.planJson || "";
    refs["min-confidence"].value = payload.health.defaults.minConfidence ?? 0.65;
    refs["mock-execute"].checked = Boolean(payload.health.defaults.mockExecute);
  }

  if (payload.state) {
    applyDashboardState(payload.state);
  }
  if (payload.config) {
    applyApiConfigPayload(payload.config, false);
  }
  if (payload.projects) {
    renderProjects(payload.projects);
  }
  if (payload.unityStatus) {
    applyUnityStatus(payload.unityStatus);
  }

  refs["log-stream"].innerHTML = "";
  for (const logEntry of payload.recentLogs || []) {
    appendLog(logEntry, false);
  }

  updateModeVisibility();
  syncMockModeText();
}

function applyDashboardState(payload) {
  refs["unity-host"].value = payload.unityHost || refs["unity-host"].value;
  refs["unity-port"].value = payload.unityPort ?? refs["unity-port"].value;
  refs["unity-instance"].value = payload.unityInstance || "";
  state.selectedProjectPath = payload.selectedProjectPath || "";
  selectProjectOption(state.selectedProjectPath);
  if (payload.currentAvatarName) {
    setActiveAvatar(payload.currentAvatarName, payload.currentAvatarPath || state.selectedAvatarPath);
  }
  if (payload.latestScreenshotUrl) {
    renderScreenshot(payload.latestScreenshotUrl);
  }
}

function applyUnityStatus(payload) {
  state.unityStatus = payload;
  const connected = Boolean(payload.connected);
  refs["unity-status-label"].textContent = connected ? "已连接" : "未连接";
  refs["unity-status-light"].className = `light ${connected ? "light-on" : "light-off"}`;
  refs["unity-status-output"].textContent = prettyJson(payload.parsed || payload) || payload.error || "";
}

function renderProjects(payload) {
  state.projects = payload.projects || [];
  state.selectedProjectPath = payload.selectedProjectPath || state.selectedProjectPath || "";
  refs["project-count"].textContent = `${state.projects.length} 个工程`;
  refs["project-select"].innerHTML = state.projects.map((project) => {
    const badges = [];
    if (project.hasVrcAutoRig) badges.push("VRCAutoRig");
    if (project.hasUnityMcpPackage) badges.push("Unity MCP");
    const suffix = badges.length ? ` / ${badges.join(" / ")}` : "";
    return `<option value="${escapeHtml(project.path)}">${escapeHtml(project.name)} (${escapeHtml(project.editorVersion)})${escapeHtml(suffix)}</option>`;
  }).join("");
  selectProjectOption(state.selectedProjectPath);
}

function applyApiConfigPayload(payload, preserveDraft) {
  const apiConfig = payload.apiConfig || payload;
  const effective = payload.effective || apiConfig;
  const provider = apiConfig.provider || "gemini";
  const preset = PROVIDER_PRESETS[provider] || PROVIDER_PRESETS.gemini;
  state.apiConfig = {
    provider,
    api_key: apiConfig.api_key || "",
    base_url: apiConfig.base_url ?? preset.base_url,
    model: apiConfig.model || preset.model,
    authHeader: apiConfig.authHeader || preset.authHeader,
  };

  if (!preserveDraft) {
    state.providerDrafts[provider] = { ...state.apiConfig };
  }

  refs["config-path-tag"].textContent = payload.configPath || "config.json";
  refs["provider-select"].value = provider;
  refs["api-key-input"].value = state.apiConfig.api_key;
  refs["api-base-url"].value = state.apiConfig.base_url || "";
  refs["api-model-input"].value = state.apiConfig.model || "";
  applyProviderFieldVisibility(provider);
  refs["status-provider"].textContent = effective.providerLabel || preset.providerLabel;
  refs["status-model"].textContent = effective.model || state.apiConfig.model || "未设置";
  refs["config-output"].textContent = prettyJson({
    provider: state.apiConfig.provider,
    model: state.apiConfig.model,
    base_url: state.apiConfig.base_url,
    authHeader: state.apiConfig.authHeader,
  });
  setApiConfigDirty(false);
  refs["config-save-status"].textContent = "已生效";
}

function applyProviderFieldVisibility(provider) {
  const preset = PROVIDER_PRESETS[provider] || PROVIDER_PRESETS.gemini;
  const isAnthropic = provider === "anthropic";
  refs["api-key-label"].textContent = isAnthropic ? "API Key（x-api-key header）" : "API Key（Bearer token）";
  refs["api-key-note"].textContent = isAnthropic
    ? "Anthropic 直接走官方端点，不显示 Base URL。"
    : "配置会保存到本地 config.json，并立即热更新。";
  refs["api-base-url-field"].classList.toggle("hidden", isAnthropic);
  refs["api-base-url-note"].textContent = isAnthropic
    ? "Anthropic 走官方端点。"
    : "非 Anthropic provider 统一走 OpenAI 兼容接口。";
  refs["api-model-input"].placeholder = preset.model || "model-name";
  if (isAnthropic) {
    refs["api-base-url"].value = "";
  } else if (!refs["api-base-url"].value.trim()) {
    refs["api-base-url"].value = preset.base_url;
  }
}

function updateModeVisibility() {
  const sourceMode = refs["source-mode"].value;
  refs["source-mode-badge"].textContent = sourceMode;
  refs["export-json-field"].classList.toggle("hidden", sourceMode !== "custom_export");
}

function syncMockModeText() {
  const modeText = refs["mock-execute"].checked ? "Mock 模式已开启" : "当前走真实 Unity 执行";
  if (!refs["summary-output"].textContent.trim()) {
    refs["summary-output"].textContent = modeText;
  }
}

function buildConnectionPayload() {
  return {
    settings_path: ".gemini/settings.json",
    unity_host: refs["unity-host"].value.trim(),
    unity_port: Number(refs["unity-port"].value || 8080),
    unity_instance: refs["unity-instance"].value.trim(),
  };
}

function buildDashboardRequest() {
  return {
    ...buildConnectionPayload(),
    avatar: state.selectedAvatarPath || null,
    instruction: refs["instruction-input"].value.trim() || null,
    model: refs["api-model-input"].value.trim() || null,
    source_mode: refs["source-mode"].value,
    export_json: refs["export-json"].value.trim() || null,
    plan_json: refs["plan-json"].value.trim() || null,
    mock_execute: refs["mock-execute"].checked,
    min_confidence: Number(refs["min-confidence"].value || 0.65),
    allow_low_confidence: refs["allow-low-confidence"].checked,
    save_artifacts: refs["save-artifacts"].checked,
  };
}

async function runButtonTask(buttonId, loadingText, task) {
  const button = refs[buttonId];
  const original = button.dataset.originalText || button.textContent;
  button.dataset.originalText = original;
  button.disabled = true;
  button.classList.add("is-loading");
  button.textContent = loadingText;
  try {
    await task();
  } catch (error) {
    refs["summary-output"].textContent = error.message || String(error);
  } finally {
    button.disabled = false;
    button.classList.remove("is-loading");
    button.textContent = original;
  }
}

async function postJson(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || `Request failed: ${response.status}`);
  }
  return data;
}

async function refreshProjects() {
  const payload = await postJson("/api/projects/refresh");
  renderProjects(payload);
}

async function syncDashboardState() {
  const payload = await postJson("/api/state", {
    ...buildConnectionPayload(),
    project_path: refs["project-select"].value || null,
  });
  applyDashboardState(payload);
}

async function onProjectSelected() {
  const selectedProject = refs["project-select"].value;
  if (selectedProject) {
    refs["unity-instance"].value = projectNameFromPath(selectedProject);
  }
  await syncDashboardState();
}

async function openProject() {
  const payload = await postJson("/api/projects/open", {
    project_path: refs["project-select"].value || null,
  });
  refs["summary-output"].textContent = prettyJson(payload);
}

async function loadUnityStatus() {
  const payload = await postJson("/api/unity/status", buildConnectionPayload());
  refs["unity-status-output"].textContent = prettyJson(payload.parsed || payload);
}

async function loadUnityTools() {
  const payload = await postJson("/api/unity/tools", buildConnectionPayload());
  refs["unity-status-output"].textContent = prettyJson(payload.parsed || payload);
}

async function refreshSceneAvatars() {
  const payload = await postJson("/api/scene/avatars", buildConnectionPayload());
  state.sceneAvatars = payload.avatars || [];
  renderSceneAvatars();
  refs["summary-output"].textContent = `已扫描到 ${state.sceneAvatars.length} 个 Avatar`;
}

function renderSceneAvatars() {
  if (!state.sceneAvatars.length) {
    refs["scene-avatar-select"].innerHTML = '<option value="">没有扫描到 Avatar</option>';
    return;
  }

  refs["scene-avatar-select"].innerHTML = state.sceneAvatars.map((avatar) => {
    const selected = avatar.avatarPath === state.selectedAvatarPath ? "selected" : "";
    return `<option value="${escapeHtml(avatar.avatarPath)}" ${selected}>${escapeHtml(avatar.avatarName)} / ${escapeHtml(avatar.sceneName)}</option>`;
  }).join("");

  if (!state.selectedAvatarPath) {
    const first = state.sceneAvatars[0];
    refs["scene-avatar-select"].value = first.avatarPath;
    setActiveAvatar(first.avatarName, first.avatarPath);
  }
}

async function onSceneAvatarSelected() {
  const avatarPath = refs["scene-avatar-select"].value;
  const avatar = state.sceneAvatars.find((item) => item.avatarPath === avatarPath);
  if (avatar) {
    setActiveAvatar(avatar.avatarName, avatar.avatarPath);
  }
}

async function loadBlendshapes() {
  await ensureApiConfigSaved();
  if (!state.selectedAvatarPath && refs["scene-avatar-select"].value) {
    await onSceneAvatarSelected();
  }
  const payload = await postJson("/api/avatar/blendshapes", buildDashboardRequest());
  if (payload.selectedAvatar) {
    setActiveAvatar(payload.selectedAvatar.avatarName, payload.selectedAvatar.avatarPath);
  }
  state.blendshapes = payload.blendshapes || [];
  state.blendshapeBaseline = {};
  state.blendshapeWorking = {};
  for (const item of state.blendshapes) {
    const key = blendshapeKey(item.rendererPath, item.blendshapeName);
    state.blendshapeBaseline[key] = Number(item.currentWeight || 0);
    state.blendshapeWorking[key] = Number(item.currentWeight || 0);
  }
  renderBlendshapeList();
  refs["summary-output"].textContent = `已加载 ${state.blendshapes.length} 个 Blendshape`;
}

function renderBlendshapeList() {
  const keyword = refs["blendshape-search"].value.trim().toLowerCase();
  const filtered = state.blendshapes.filter((item) => {
    if (!keyword) {
      return true;
    }
    return [
      item.blendshapeName,
      item.rendererName,
      item.rendererPath,
      item.meshName,
    ].some((value) => String(value || "").toLowerCase().includes(keyword));
  });

  refs["blendshape-count-chip"].textContent = `${filtered.length} 个 Blendshape`;
  refs["pending-count"].textContent = `${collectPendingAdjustments().length} 项`;

  if (!filtered.length) {
    refs["blendshape-list"].innerHTML = '<div class="empty-state">没有匹配到 Blendshape</div>';
    return;
  }

  refs["blendshape-list"].innerHTML = filtered.map((item) => {
    const key = blendshapeKey(item.rendererPath, item.blendshapeName);
    const baseline = Number(state.blendshapeBaseline[key] ?? item.currentWeight ?? 0);
    const current = Number(state.blendshapeWorking[key] ?? baseline);
    const changed = Math.abs(current - baseline) > 0.001 ? "changed" : "";
    return `
      <article class="blendshape-row ${changed}">
        <div class="blendshape-head">
          <div>
            <strong>${escapeHtml(item.blendshapeName)}</strong>
            <span>${escapeHtml(item.rendererName)} / ${escapeHtml(item.meshName)}</span>
          </div>
          <div class="weight-badges">
            <span class="weight-tag js-base">当前 ${baseline.toFixed(1)}</span>
            <span class="weight-tag weight-live js-live">${current.toFixed(1)}</span>
          </div>
        </div>
        <div class="blendshape-subline">${escapeHtml(item.rendererPath)}</div>
        <div class="slider-row">
          <input data-renderer-path="${escapeHtml(item.rendererPath)}" data-blendshape-name="${escapeHtml(item.blendshapeName)}" class="blendshape-slider" type="range" min="0" max="100" step="0.1" value="${current.toFixed(1)}">
          <input data-renderer-path="${escapeHtml(item.rendererPath)}" data-blendshape-name="${escapeHtml(item.blendshapeName)}" class="blendshape-number" type="number" min="0" max="100" step="0.1" value="${current.toFixed(1)}">
        </div>
      </article>
    `;
  }).join("");

  refs["blendshape-list"].querySelectorAll(".blendshape-slider").forEach((slider) => {
    slider.addEventListener("input", () => updateBlendshapeValue(slider.dataset.rendererPath, slider.dataset.blendshapeName, Number(slider.value), slider));
  });
  refs["blendshape-list"].querySelectorAll(".blendshape-number").forEach((input) => {
    input.addEventListener("input", () => updateBlendshapeValue(input.dataset.rendererPath, input.dataset.blendshapeName, Number(input.value), input));
  });
}

function updateBlendshapeValue(rendererPath, blendshapeName, value, sourceElement) {
  const key = blendshapeKey(rendererPath, blendshapeName);
  const safeValue = Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0;
  state.blendshapeWorking[key] = safeValue;
  const row = sourceElement.closest(".blendshape-row");
  if (row) {
    row.querySelector(".js-live").textContent = safeValue.toFixed(1);
    row.classList.toggle("changed", Math.abs(safeValue - Number(state.blendshapeBaseline[key] ?? 0)) > 0.001);
    const slider = row.querySelector(".blendshape-slider");
    const number = row.querySelector(".blendshape-number");
    if (slider !== sourceElement) {
      slider.value = safeValue.toFixed(1);
    }
    if (number !== sourceElement) {
      number.value = safeValue.toFixed(1);
    }
  }
  refs["pending-count"].textContent = `${collectPendingAdjustments().length} 项`;
  refs["summary-output"].textContent = `实时预览已更新：${blendshapeName} -> ${safeValue.toFixed(1)}`;
}

function collectPendingAdjustments() {
  return state.blendshapes
    .map((item) => {
      const key = blendshapeKey(item.rendererPath, item.blendshapeName);
      const current = Number(state.blendshapeWorking[key] ?? item.currentWeight ?? 0);
      const previous = Number(state.blendshapeBaseline[key] ?? item.currentWeight ?? 0);
      return {
        renderer_path: item.rendererPath,
        blendshape_name: item.blendshapeName,
        target_weight: current,
        previous_weight: previous,
      };
    })
    .filter((item) => Math.abs(item.target_weight - item.previous_weight) > 0.001);
}

async function applyManualBlendshapes() {
  await ensureApiConfigSaved();
  const adjustments = collectPendingAdjustments();
  if (!adjustments.length) {
    refs["summary-output"].textContent = "没有待应用的滑块改动。";
    return;
  }

  const payload = await postJson("/api/blendshapes/apply", {
    ...buildDashboardRequest(),
    adjustments,
  });

  for (const item of adjustments) {
    const key = blendshapeKey(item.renderer_path, item.blendshape_name);
    state.blendshapeBaseline[key] = item.target_weight;
  }
  state.undoDepth = payload.undoDepth || 0;
  refs["summary-output"].textContent = `已应用 ${adjustments.length} 项滑块改动`;
  refs["pending-count"].textContent = "0 项";
  renderBlendshapeList();
}

async function undoManualBlendshapes() {
  const payload = await postJson("/api/blendshapes/undo", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath,
  });
  for (const item of payload.restoredAdjustments || []) {
    const key = blendshapeKey(item.rendererPath, item.blendshapeName);
    state.blendshapeBaseline[key] = item.targetWeight;
    state.blendshapeWorking[key] = item.targetWeight;
  }
  state.undoDepth = payload.undoDepth || 0;
  refs["summary-output"].textContent = `已撤销 ${payload.restoredAdjustments?.length || 0} 项改动`;
  renderBlendshapeList();
}

async function runAiPipeline() {
  await ensureApiConfigSaved();
  const payload = await postJson("/api/pipeline/run", buildDashboardRequest());
  if (payload.selectedAvatar) {
    setActiveAvatar(payload.selectedAvatar.avatarName, payload.selectedAvatar.avatarPath);
  }
  applyPlanToBlendshapeState(payload.plan);
  refs["summary-output"].textContent = payload.summary || payload.preview || "AI 执行完成";
}

function applyPlanToBlendshapeState(plan) {
  for (const adjustment of plan?.adjustments || []) {
    const key = blendshapeKey(adjustment.renderer_path, adjustment.blendshape_name);
    state.blendshapeBaseline[key] = Number(adjustment.target_weight);
    state.blendshapeWorking[key] = Number(adjustment.target_weight);
  }
  renderBlendshapeList();
}

async function scanClothes() {
  const payload = await postJson("/api/clothes/scan", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
  });
  state.clothes = payload.clothes || [];
  renderClothes();
}

function renderClothes() {
  refs["clothes-count-chip"].textContent = `${state.clothes.length} 件`;
  if (!state.clothes.length) {
    refs["clothes-list"].innerHTML = "没有扫描到衣服对象";
    refs["clothes-list"].classList.add("empty-state");
    return;
  }

  refs["clothes-list"].classList.remove("empty-state");
  refs["clothes-list"].innerHTML = state.clothes.map((item, index) => `
    <label class="switch-row">
      <div>
        <strong>${escapeHtml(item.name)}</strong>
        <span>${escapeHtml(item.objectPath)}</span>
      </div>
      <input data-clothing-index="${index}" type="checkbox" ${item.active ? "checked" : ""}>
    </label>
  `).join("");

  refs["clothes-list"].querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
    checkbox.addEventListener("change", async () => {
      const index = Number(checkbox.dataset.clothingIndex);
      const item = state.clothes[index];
      const nextValue = checkbox.checked;
      checkbox.disabled = true;
      try {
        await postJson("/api/clothes/toggle", {
          ...buildConnectionPayload(),
          object_path: item.objectPath,
          active: nextValue,
        });
        item.active = nextValue;
      } catch (error) {
        checkbox.checked = !nextValue;
        refs["summary-output"].textContent = error.message || String(error);
      } finally {
        checkbox.disabled = false;
      }
    });
  });
}

async function generateFxBlueprint() {
  const payload = await postJson("/api/clothes/generate-fx", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
  });
  refs["fx-output"].textContent = prettyJson(payload.fxBlueprint || payload);
}

async function applyClothesFx() {
  if (!state.clothes.length) {
    refs["fx-output"].textContent = "没有可写入的衣物对象";
    return;
  }
  
  const isDryRun = refs["fx-dry-run"].checked;
  const payload = await postJson("/api/clothes/apply-fx", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    items: state.clothes,
    dry_run: isDryRun,
  });
  
  refs["fx-apply-panel"].classList.remove("hidden");
  refs["fx-apply-count"].textContent = `${payload.createdCount ?? state.clothes.length} 件`;
  refs["fx-csharp-preview"].textContent = payload.generatedCsharp || "";
  
  if (isDryRun) {
    refs["fx-output"].textContent = "(Dry run) 预览如上所示，不会对 Unity 写入任何资产。";
  } else {
    refs["fx-output"].textContent = prettyJson(payload.result || payload);
  }
}

async function scanParameters() {
  const payload = await postJson("/api/parameters/scan", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
  });
  const stats = payload.stats || {};
  refs["bool-count"].textContent = stats.boolCount ?? 0;
  refs["int-count"].textContent = stats.intCount ?? 0;
  refs["float-count"].textContent = stats.floatCount ?? 0;
  refs["param-output"].textContent = prettyJson(stats);
}

async function optimizeParameters() {
  const payload = await postJson("/api/parameters/optimize", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
  });
  const suggestions = payload.optimization?.suggestions || [];
  if (!suggestions.length) {
    refs["param-suggestions"].innerHTML = "当前没有明确的 Int → Bool 降级建议";
    refs["param-suggestions"].classList.add("empty-state");
  } else {
    refs["param-suggestions"].classList.remove("empty-state");
    refs["param-suggestions"].innerHTML = suggestions.map((item) => `
      <article class="info-card">
        <strong>${escapeHtml(item.name)}</strong>
        <span>${escapeHtml(item.currentType)} -> ${escapeHtml(item.suggestedType)}</span>
        <p>${escapeHtml(item.reason)}</p>
      </article>
    `).join("");
  }
  refs["param-output"].textContent = prettyJson(payload.optimization || payload);
  // 缓存 suggestions 供后续 apply 使用
  state.paramSuggestions = suggestions;
}

async function applyParameterOptimization() {
  const suggestions = state.paramSuggestions || [];
  if (!suggestions.length) {
    refs["param-output"].textContent = "没有可应用的参数建议，请先执行扫描与分析";
    return;
  }

  const isDryRun = refs["param-dry-run"].checked;
  const payload = await postJson("/api/parameters/apply-optimization", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    suggestions: suggestions,
    dry_run: isDryRun,
  });

  refs["param-diff-panel"].classList.remove("hidden");
  refs["param-diff-count"].textContent = `${payload.appliedCount ?? 0} 项`;
  refs["param-diff-list"].innerHTML = (payload.diff || []).map((item) => `
    <article class="info-card">
      <strong>${escapeHtml(item.name)}</strong>
      <span>${escapeHtml(item.from)} -> ${escapeHtml(item.to)}</span>
    </article>
  `).join("");
  refs["param-csharp-preview"].textContent = payload.generatedCsharp || "";

  if (isDryRun) {
    refs["param-output"].textContent = "(Dry run) Diff 与代码预览如上，未执行实际回写。";
  } else {
    refs["param-output"].textContent = prettyJson(payload.result || payload);
  }
}

async function captureScreenshot() {
  const payload = await postJson("/api/vision/capture", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    width: 960,
    height: 960,
  });
  renderScreenshot(payload.imageUrl);
  refs["vision-result"].innerHTML = `<div class="info-card"><strong>截图已更新</strong><span>${escapeHtml(payload.imagePath)}</span></div>`;
  refs["vision-status-chip"].textContent = "待审核";
}

function renderScreenshot(imageUrl) {
  if (!imageUrl) {
    refs["vision-image"].classList.add("hidden");
    refs["vision-placeholder"].classList.remove("hidden");
    return;
  }
  state.latestScreenshotUrl = imageUrl;
  refs["vision-image"].src = `${imageUrl}?t=${Date.now()}`;
  refs["vision-image"].classList.remove("hidden");
  refs["vision-placeholder"].classList.add("hidden");
}

async function auditVision() {
  const payload = await postJson("/api/vision/audit", {
    ...buildConnectionPayload(),
    image_path: state.latestScreenshotUrl ? urlToArtifactPath(state.latestScreenshotUrl) : null,
  });
  const audit = payload.audit || {};
  refs["vision-status-chip"].textContent = audit.status === "pass" ? "通过" : "穿模";
  refs["vision-result"].innerHTML = `
    <article class="info-card ${audit.status === "pass" ? "result-pass" : "result-fail"}">
      <strong>${escapeHtml(audit.status === "pass" ? "通过" : "检测到穿模风险")}</strong>
      <span>${escapeHtml(audit.summary || "无结论")}</span>
      <p>${escapeHtml((audit.issues || []).join(" / ") || "无额外问题")}</p>
    </article>
  `;
}

async function captureMultiScreenshot() {
  const payload = await postJson("/api/vision/capture-multi", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    width: 960,
    height: 960,
    angles: ["front", "side_left", "side_right", "back"]
  });
  state.multiScreenshots = payload.results || [];
  
  if (state.multiScreenshots.length > 0) {
    renderScreenshot(state.multiScreenshots[0].imageUrl);
    renderMultiThumbs();
    refs["vision-result"].innerHTML = `<div class="info-card"><strong>已捕获多视角截图</strong><span>共 ${state.multiScreenshots.length} 张</span></div>`;
    refs["vision-status-chip"].textContent = "待审核";
  }
}

function renderMultiThumbs() {
  const container = refs["vision-multi-thumbs"];
  if (!state.multiScreenshots || !state.multiScreenshots.length) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");
  container.innerHTML = state.multiScreenshots.map((item, i) => `
    <div class="vision-thumb-card" style="cursor:pointer;" onclick="renderScreenshot('${item.imageUrl}')">
      <img src="${item.imageUrl}?t=${Date.now()}" alt="thumb">
      <div class="thumb-label">${escapeHtml(item.angle)}</div>
    </div>
  `).join("");
}

async function auditMultiVision() {
  if (!state.multiScreenshots || !state.multiScreenshots.length) {
    refs["vision-result"].innerHTML = "<div class='info-card result-fail'><strong>无多视角截图</strong><p>请先点击『多视角截图』</p></div>";
    return;
  }
  
  const payload = await postJson("/api/vision/audit-multi", {
    ...buildConnectionPayload(),
    image_paths: state.multiScreenshots.map(item => urlToArtifactPath(item.imageUrl))
  });
  
  const isPass = payload.overallStatus === "pass";
  refs["vision-status-chip"].textContent = isPass ? "全角度通过" : "多图穿模";
  
  let html = `
    <article class="info-card ${isPass ? "result-pass" : "result-fail"}">
      <strong>聚合审核结论: ${isPass ? "通过" : "穿模风险"}</strong>
    </article>
  `;
  
  for (const res of (payload.results || [])) {
    const a = res.audit || {};
    html += `
      <article class="info-card ${a.status === "pass" ? "" : "result-fail"}" style="margin-top:0.5rem">
        <strong>${escapeHtml(res.imagePath.split("/").pop())}: ${a.status}</strong>
        <span>${escapeHtml(a.summary || "")}</span>
        ${a.issues && a.issues.length ? `<p>${escapeHtml(a.issues.join(", "))}</p>` : ""}
      </article>
    `;
  }
  refs["vision-result"].innerHTML = html;
}

function onVisionAngleTabClick(e) {
  if (!e.target.classList.contains("tab-btn")) return;
  
  document.querySelectorAll("#vision-angle-tabs .tab-btn").forEach(btn => btn.classList.remove("tab-active"));
  e.target.classList.add("tab-active");
  
  const angle = e.target.dataset.angle;
  if (angle === "") {
    // Single capture tab
    refs["capture-screenshot-btn"].classList.remove("hidden");
    refs["audit-vision-btn"].classList.remove("hidden");
    refs["capture-multi-btn"].classList.add("hidden");
    refs["audit-multi-btn"].classList.add("hidden");
    refs["vision-multi-thumbs"].classList.add("hidden");
  } else {
    // Multi capture tab (acts globally for multi)
    refs["capture-screenshot-btn"].classList.add("hidden");
    refs["audit-vision-btn"].classList.add("hidden");
    refs["capture-multi-btn"].classList.remove("hidden");
    refs["audit-multi-btn"].classList.remove("hidden");
    if (state.multiScreenshots && state.multiScreenshots.length) {
      refs["vision-multi-thumbs"].classList.remove("hidden");
    }
  }
}

function onProviderChanged() {
  rememberCurrentProviderDraft();
  const provider = refs["provider-select"].value;
  const draft = state.providerDrafts[provider] || buildDefaultProviderDraft(provider);
  refs["api-key-input"].value = draft.api_key || "";
  refs["api-base-url"].value = draft.base_url || "";
  refs["api-model-input"].value = draft.model || "";
  applyProviderFieldVisibility(provider);
  markApiConfigDirty();
}

function buildDefaultProviderDraft(provider) {
  const preset = PROVIDER_PRESETS[provider] || PROVIDER_PRESETS.gemini;
  return {
    provider,
    api_key: "",
    base_url: preset.base_url,
    model: preset.model,
    authHeader: preset.authHeader,
  };
}

function resetProviderDefaults() {
  const provider = refs["provider-select"].value;
  const draft = buildDefaultProviderDraft(provider);
  refs["api-key-input"].value = "";
  refs["api-base-url"].value = draft.base_url || "";
  refs["api-model-input"].value = draft.model || "";
  applyProviderFieldVisibility(provider);
  markApiConfigDirty();
}

function rememberCurrentProviderDraft() {
  const provider = refs["provider-select"].value;
  state.providerDrafts[provider] = readApiForm();
}

function readApiForm() {
  const provider = refs["provider-select"].value;
  const preset = PROVIDER_PRESETS[provider] || PROVIDER_PRESETS.gemini;
  return {
    provider,
    api_key: refs["api-key-input"].value.trim(),
    base_url: provider === "anthropic" ? "" : (refs["api-base-url"].value.trim() || preset.base_url),
    model: refs["api-model-input"].value.trim() || preset.model,
  };
}

function markApiConfigDirty() {
  rememberCurrentProviderDraft();
  setApiConfigDirty(true);
}

function setApiConfigDirty(isDirty) {
  state.apiConfigDirty = Boolean(isDirty);
  if (isDirty) {
    refs["config-save-status"].textContent = "有未保存改动";
  }
}

async function saveApiConfig(showOutput) {
  const payload = await postJson("/api/config", readApiForm());
  applyApiConfigPayload(payload, false);
  if (showOutput) {
    refs["summary-output"].textContent = "Provider 配置已保存并热更新";
  }
}

async function ensureApiConfigSaved() {
  if (!state.apiConfigDirty) {
    return;
  }
  await saveApiConfig(false);
}

function setSocketStatus(text, connected) {
  refs["socket-status"].textContent = text;
  refs["socket-status"].className = connected ? "status-live" : "status-dead";
}

function selectProjectOption(projectPath) {
  if (projectPath) {
    refs["project-select"].value = projectPath;
  }
}

function setActiveAvatar(avatarName, avatarPath) {
  state.selectedAvatarName = avatarName || "";
  state.selectedAvatarPath = avatarPath || "";
  refs["status-avatar"].textContent = avatarName || "未加载";
  refs["avatar-path-display"].textContent = avatarPath || "未选择";
  if (refs["scene-avatar-select"] && avatarPath) {
    refs["scene-avatar-select"].value = avatarPath;
  }
}

function appendLog(entry, autoScroll) {
  state.recentLogs.push(entry);
  const node = document.createElement("article");
  node.className = `log-entry log-${entry.level || "info"}`;
  node.innerHTML = `
    <div class="log-entry-head">
      <span class="log-scope">${escapeHtml(entry.scope || "system")}</span>
      <span>${escapeHtml(formatTimestamp(entry.timestamp))}</span>
    </div>
    <p class="log-message">${escapeHtml(entry.message || "")}</p>
    ${entry.data && Object.keys(entry.data).length ? `<pre class="log-data">${escapeHtml(prettyJson(entry.data))}</pre>` : ""}
  `;
  refs["log-stream"].appendChild(node);
  if (autoScroll) {
    refs["log-stream"].scrollTop = refs["log-stream"].scrollHeight;
  }
}

function clearLogView() {
  refs["log-stream"].innerHTML = "";
}

function urlToArtifactPath(url) {
  if (!url.startsWith("/artifacts/")) {
    return url;
  }
  return `artifacts/${url.slice("/artifacts/".length)}`;
}

function blendshapeKey(rendererPath, blendshapeName) {
  return `${rendererPath}::${blendshapeName}`;
}

function projectNameFromPath(projectPath) {
  if (!projectPath) {
    return "";
  }
  const parts = projectPath.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1];
}

function prettyJson(value) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function formatTimestamp(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#039;");
}
