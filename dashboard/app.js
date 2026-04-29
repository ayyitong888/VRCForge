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
  recentLogs: [],
  apiConfig: null,
  providerDrafts: {},
  apiConfigDirty: false,
};

const refs = {};

document.addEventListener("DOMContentLoaded", () => {
  cacheRefs();
  bindEvents();
  connectSocket();
  updateModeVisibility();
  syncMetricMode();
});

function cacheRefs() {
  const ids = [
    "socket-status",
    "unity-status-pill",
    "active-project-name",
    "active-provider-name",
    "active-model-name",
    "active-execution-mode",
    "project-count",
    "project-select",
    "refresh-projects-btn",
    "open-project-btn",
    "install-project-btn",
    "unity-host",
    "unity-port",
    "unity-instance",
    "sync-state-btn",
    "unity-status-btn",
    "unity-instances-btn",
    "unity-tools-btn",
    "unity-status-text",
    "unity-status-output",
    "unity-instances-output",
    "unity-tools-output",
    "config-path-tag",
    "provider-select",
    "api-model-input",
    "api-key-input",
    "api-key-label",
    "api-key-note",
    "api-base-url-field",
    "api-base-url",
    "api-base-url-note",
    "effective-provider-name",
    "effective-model-name",
    "effective-auth-header",
    "config-save-status",
    "config-output",
    "save-config-btn",
    "reset-provider-btn",
    "source-mode",
    "source-mode-badge",
    "export-json-field",
    "export-json",
    "plan-json",
    "avatar-select",
    "min-confidence",
    "mock-execute",
    "allow-low-confidence",
    "save-artifacts",
    "instruction-input",
    "refresh-avatars-btn",
    "generate-plan-btn",
    "run-pipeline-btn",
    "summary-tag",
    "summary-output",
    "avatars-list",
    "avatar-source-tag",
    "preview-output",
    "plan-output",
    "plan-count-tag",
    "csharp-output",
    "result-output",
    "artifact-paths",
    "artifact-tag",
    "log-stream",
    "clear-logs-btn",
  ];

  for (const id of ids) {
    refs[id] = document.getElementById(id);
  }
}

function bindEvents() {
  refs["source-mode"].addEventListener("change", updateModeVisibility);
  refs["mock-execute"].addEventListener("change", syncMetricMode);
  refs["project-select"].addEventListener("change", onProjectSelected);
  refs["refresh-projects-btn"].addEventListener("click", () => postJson("/api/projects/refresh"));
  refs["sync-state-btn"].addEventListener("click", syncDashboardState);
  refs["unity-status-btn"].addEventListener("click", loadUnityStatus);
  refs["unity-instances-btn"].addEventListener("click", loadUnityInstances);
  refs["unity-tools-btn"].addEventListener("click", loadUnityTools);
  refs["refresh-avatars-btn"].addEventListener("click", loadAvatars);
  refs["generate-plan-btn"].addEventListener("click", generatePlan);
  refs["run-pipeline-btn"].addEventListener("click", runPipeline);
  refs["open-project-btn"].addEventListener("click", openProject);
  refs["install-project-btn"].addEventListener("click", installProject);
  refs["clear-logs-btn"].addEventListener("click", clearLogView);
  refs["provider-select"].addEventListener("change", onProviderChanged);
  refs["save-config-btn"].addEventListener("click", () => saveApiConfig(false));
  refs["reset-provider-btn"].addEventListener("click", resetProviderDefaults);

  for (const id of ["api-model-input", "api-key-input", "api-base-url"]) {
    refs[id].addEventListener("input", markApiConfigDirty);
  }
}

function connectSocket() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
  state.socket = socket;

  setSocketStatus("Connecting", "metric-warn");

  socket.addEventListener("open", () => {
    setSocketStatus("Live", "metric-success");
  });

  socket.addEventListener("close", () => {
    setSocketStatus("Offline", "metric-danger");
    setTimeout(connectSocket, 1500);
  });

  socket.addEventListener("error", () => {
    setSocketStatus("Error", "metric-danger");
  });

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
      applyDashboardState(payload, false);
      break;
    case "config":
      applyApiConfigPayload(payload, { fromSocket: true });
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
    refs["source-mode"].value = payload.health.defaults.sourceMode || "mvp_sample";
    refs["export-json"].value = payload.health.defaults.exportJson || "";
    refs["plan-json"].value = payload.health.defaults.planJson || "";
    refs["min-confidence"].value = payload.health.defaults.minConfidence ?? 0.65;
    refs["mock-execute"].checked = Boolean(payload.health.defaults.mockExecute);
  }

  if (payload.state) {
    applyDashboardState(payload.state, true);
  }

  if (payload.config) {
    applyApiConfigPayload(payload.config, { fromSocket: false });
  } else if (payload.health?.apiConfig) {
    applyApiConfigPayload({ apiConfig: payload.health.apiConfig, effective: payload.health.apiConfig }, { fromSocket: false });
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
  syncMetricMode();
}

function applyDashboardState(payload, preferCurrentProject) {
  refs["unity-host"].value = payload.unityHost || refs["unity-host"].value;
  refs["unity-port"].value = payload.unityPort ?? refs["unity-port"].value;
  refs["unity-instance"].value = payload.unityInstance || "";
  state.selectedProjectPath = payload.selectedProjectPath || "";

  if (!preferCurrentProject) {
    selectProjectOption(state.selectedProjectPath);
  }

  refs["active-project-name"].textContent = state.selectedProjectPath ? projectNameFromPath(state.selectedProjectPath) : "None";
}

function applyApiConfigPayload(payload, options = {}) {
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
    usesBaseUrl: apiConfig.usesBaseUrl ?? preset.usesBaseUrl,
  };
  state.providerDrafts[provider] = { ...state.apiConfig };

  refs["config-path-tag"].textContent = payload.configPath || "config.json";
  refs["provider-select"].value = provider;
  refs["api-key-input"].value = state.apiConfig.api_key;
  refs["api-base-url"].value = state.apiConfig.base_url || "";
  refs["api-model-input"].value = state.apiConfig.model || "";
  applyProviderFieldVisibility(provider);
  updateEffectiveSummary(effective);

  refs["config-output"].textContent = prettyJson({
    configPath: payload.configPath || "config.json",
    apiConfig: state.apiConfig,
    effective,
  });
  setApiConfigDirty(false);
  refs["config-save-status"].textContent = options.fromSocket ? "Live update received" : "Saved and active";
}

function updateEffectiveSummary(effective) {
  const provider = effective.provider || state.apiConfig?.provider || "gemini";
  const providerLabel = effective.providerLabel || PROVIDER_PRESETS[provider]?.providerLabel || provider;
  const model = effective.model || state.apiConfig?.model || "";
  const authHeader = effective.authHeader || PROVIDER_PRESETS[provider]?.authHeader || "Authorization: Bearer";

  refs["effective-provider-name"].textContent = providerLabel;
  refs["effective-model-name"].textContent = model || "Not set";
  refs["effective-auth-header"].textContent = authHeader;
  refs["active-provider-name"].textContent = providerLabel;
  refs["active-model-name"].textContent = model || "Not set";
}

function applyProviderFieldVisibility(provider) {
  const preset = PROVIDER_PRESETS[provider] || PROVIDER_PRESETS.gemini;
  const isAnthropic = provider === "anthropic";

  refs["api-key-label"].textContent = isAnthropic ? "API Key（x-api-key header）" : "API Key（Bearer token）";
  refs["api-key-note"].textContent = isAnthropic
    ? "Anthropic 走官方端点，只保存 API Key 与 Model。"
    : "保存到本地 config.json，保存后立即生效，不需要重启 dashboard。";
  refs["api-base-url-field"].classList.toggle("hidden", isAnthropic);
  refs["api-base-url-note"].textContent = isAnthropic
    ? "Anthropic 使用官方端点。"
    : "非 Anthropic provider 统一走 OpenAI 兼容接口。";
  refs["api-model-input"].placeholder = preset.model || "model-name";

  if (isAnthropic) {
    refs["api-base-url"].value = "";
  } else if (!refs["api-base-url"].value.trim()) {
    refs["api-base-url"].value = preset.base_url;
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
  updateEffectiveSummary({
    provider,
    providerLabel: PROVIDER_PRESETS[provider]?.providerLabel || provider,
    model: refs["api-model-input"].value.trim() || PROVIDER_PRESETS[provider]?.model || "",
    authHeader: PROVIDER_PRESETS[provider]?.authHeader || "Authorization: Bearer",
  });
  markApiConfigDirty();
}

function resetProviderDefaults() {
  const provider = refs["provider-select"].value;
  const draft = buildDefaultProviderDraft(provider);
  state.providerDrafts[provider] = { ...draft };
  refs["api-key-input"].value = "";
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
    usesBaseUrl: preset.usesBaseUrl,
  };
}

function rememberCurrentProviderDraft() {
  const provider = refs["provider-select"].value;
  if (!provider) {
    return;
  }

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
    authHeader: preset.authHeader,
    usesBaseUrl: preset.usesBaseUrl,
  };
}

function markApiConfigDirty() {
  rememberCurrentProviderDraft();
  setApiConfigDirty(true);
}

function setApiConfigDirty(isDirty) {
  state.apiConfigDirty = Boolean(isDirty);
  if (state.apiConfigDirty) {
    refs["config-save-status"].textContent = "Unsaved changes";
  }
}

async function saveApiConfig(silent) {
  const payload = readApiForm();
  const response = await postJson("/api/config", payload);
  applyApiConfigPayload(response, { fromSocket: false });
  if (!silent) {
    refs["summary-tag"].textContent = "Config saved";
    refs["summary-output"].textContent = prettyJson(response);
  }
  return response;
}

async function ensureApiConfigSaved() {
  if (!state.apiConfigDirty) {
    return;
  }
  await saveApiConfig(true);
}

function renderProjects(payload) {
  state.projects = payload.projects || [];
  state.selectedProjectPath = payload.selectedProjectPath || state.selectedProjectPath || "";
  refs["project-count"].textContent = `${state.projects.length} projects`;

  const options = state.projects.map((project) => {
    const status = [];
    if (project.hasVrcAutoRig) status.push("VRCAutoRig");
    if (project.hasUnityMcpPackage) status.push("Unity MCP");
    const suffix = status.length ? ` / ${status.join(" / ")}` : "";
    return `<option value="${escapeHtml(project.path)}">${escapeHtml(project.name)} (${escapeHtml(project.editorVersion)})${escapeHtml(suffix)}</option>`;
  });

  refs["project-select"].innerHTML = options.join("");
  selectProjectOption(state.selectedProjectPath);
  refs["active-project-name"].textContent = state.selectedProjectPath ? projectNameFromPath(state.selectedProjectPath) : "None";
}

function selectProjectOption(projectPath) {
  if (!projectPath) {
    return;
  }
  refs["project-select"].value = projectPath;
}

function applyUnityStatus(payload) {
  state.unityStatus = payload;
  const connected = Boolean(payload.connected);
  refs["unity-status-pill"].textContent = connected ? "Connected" : "Disconnected";
  refs["unity-status-pill"].className = `metric-value ${connected ? "metric-success" : "metric-danger"}`;
  refs["unity-status-text"].textContent = connected
    ? `Target: ${payload.instance || "default"} @ ${payload.host}:${payload.port}`
    : payload.error || "Unity MCP server is not ready yet.";
  refs["unity-status-output"].textContent = prettyJson(payload.parsed || payload) || payload.output || payload.error || "";
}

function syncMetricMode() {
  const executionMode = refs["mock-execute"].checked ? "Mock" : "Live Unity";
  refs["active-execution-mode"].textContent = executionMode;
}

function updateModeVisibility() {
  const sourceMode = refs["source-mode"].value;
  refs["source-mode-badge"].textContent = sourceMode;
  refs["export-json-field"].classList.toggle("hidden", sourceMode !== "custom_export");
  syncMetricMode();
}

function buildConnectionPayload() {
  return {
    settings_path: ".gemini/settings.json",
    unity_host: refs["unity-host"].value.trim(),
    unity_port: Number(refs["unity-port"].value || 8080),
    unity_instance: refs["unity-instance"].value.trim(),
  };
}

function buildPipelinePayload() {
  return {
    ...buildConnectionPayload(),
    instruction: refs["instruction-input"].value.trim(),
    avatar: refs["avatar-select"].value || null,
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

async function syncDashboardState() {
  const selectedProject = refs["project-select"].value;
  if (selectedProject && (!refs["unity-instance"].value || refs["unity-instance"].value === projectNameFromPath(state.selectedProjectPath || ""))) {
    refs["unity-instance"].value = projectNameFromPath(selectedProject);
  }

  const payload = await postJson("/api/state", {
    ...buildConnectionPayload(),
    project_path: selectedProject || null,
  });
  applyDashboardState(payload, false);
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
  refs["summary-tag"].textContent = "Project opened";
  refs["summary-output"].textContent = prettyJson(payload);
}

async function installProject() {
  const payload = await postJson("/api/projects/install", {
    project_path: refs["project-select"].value || null,
    launch_unity: false,
  });
  refs["summary-tag"].textContent = "Project installed";
  refs["summary-output"].textContent = payload.output || prettyJson(payload);
}

async function loadUnityStatus() {
  const payload = await postJson("/api/unity/status", buildConnectionPayload());
  refs["unity-status-output"].textContent = prettyJson(payload.parsed || payload);
}

async function loadUnityInstances() {
  const payload = await postJson("/api/unity/instances", buildConnectionPayload());
  refs["unity-instances-output"].textContent = prettyJson(payload.parsed || payload);
}

async function loadUnityTools() {
  const payload = await postJson("/api/unity/tools", buildConnectionPayload());
  refs["unity-tools-output"].textContent = prettyJson(payload.parsed || payload);
}

async function loadAvatars() {
  await ensureApiConfigSaved();
  const payload = await postJson("/api/avatars", buildPipelinePayload());
  renderAvatarPayload(payload);
  refs["summary-tag"].textContent = "Avatars loaded";
  refs["summary-output"].textContent = prettyJson(payload.summary || payload);
}

async function generatePlan() {
  await ensureApiConfigSaved();
  const payload = await postJson("/api/pipeline/plan", buildPipelinePayload());
  renderPipelinePayload(payload);
  refs["summary-tag"].textContent = "Plan ready";
  refs["summary-output"].textContent = payload.preview || "";
}

async function runPipeline() {
  await ensureApiConfigSaved();
  const payload = await postJson("/api/pipeline/run", buildPipelinePayload());
  renderPipelinePayload(payload);
  refs["summary-tag"].textContent = payload.executionMode === "mock" ? "Mock executed" : "Live executed";
  refs["summary-output"].textContent = payload.summary || "";
}

function renderAvatarPayload(payload) {
  refs["avatar-source-tag"].textContent = payload.exportSource || "Unknown source";
  const avatars = payload.avatars || [];
  renderAvatarCards(avatars, refs["avatar-select"].value || null);
  renderAvatarSelect(avatars);
}

function renderPipelinePayload(payload) {
  refs["avatar-source-tag"].textContent = payload.exportSource || "Unknown source";
  refs["plan-count-tag"].textContent = `${payload.plan?.adjustments?.length || 0} adjustments`;
  refs["preview-output"].textContent = payload.preview || "";
  refs["plan-output"].textContent = prettyJson(payload.plan || {});
  refs["csharp-output"].textContent = payload.csharp || "";
  refs["result-output"].textContent = prettyJson(payload.result || {}) || payload.summary || "";
  renderArtifacts(payload.artifacts);
  renderAvatarCards(payload.availableAvatars || [], payload.selectedAvatar?.avatarPath || null);
  renderAvatarSelect(payload.availableAvatars || [], payload.selectedAvatar?.avatarPath || null);
}

function renderAvatarSelect(avatars, selectedAvatarPath = null) {
  if (!avatars.length) {
    refs["avatar-select"].innerHTML = `<option value="">No avatars loaded</option>`;
    return;
  }

  const currentValue = selectedAvatarPath || refs["avatar-select"].value;
  const options = avatars.map((avatar) => {
    const value = avatar.avatarPath;
    const selected = currentValue === value ? "selected" : "";
    return `<option value="${escapeHtml(value)}" ${selected}>${escapeHtml(avatar.avatarName)} / ${escapeHtml(avatar.sceneName)}</option>`;
  });
  refs["avatar-select"].innerHTML = options.join("");
  if (selectedAvatarPath) {
    refs["avatar-select"].value = selectedAvatarPath;
  }
}

function renderAvatarCards(avatars, selectedAvatarPath) {
  refs["avatars-list"].innerHTML = avatars.map((avatar) => {
    const selectedClass = avatar.avatarPath === selectedAvatarPath ? "selected" : "";
    return `
      <article class="avatar-card ${selectedClass}" data-avatar-path="${escapeHtml(avatar.avatarPath)}">
        <h3>${escapeHtml(avatar.avatarName)}</h3>
        <div class="avatar-meta">
          <span>${escapeHtml(avatar.sceneName)}</span>
          <span>${avatar.rendererCount} renderers</span>
          <span>${avatar.blendshapeCount} blendshapes</span>
          <span>${avatar.isVrChatAvatar ? "VRChat Avatar" : "Animator Root"}</span>
        </div>
        <p class="hero-text">${escapeHtml(avatar.avatarPath)}</p>
      </article>
    `;
  }).join("");

  refs["avatars-list"].querySelectorAll(".avatar-card").forEach((card) => {
    card.addEventListener("click", () => {
      refs["avatar-select"].value = card.dataset.avatarPath;
      renderAvatarCards(avatars, card.dataset.avatarPath);
    });
  });
}

function renderArtifacts(artifacts) {
  if (!artifacts) {
    refs["artifact-tag"].textContent = "Artifacts idle";
    refs["artifact-paths"].innerHTML = "";
    return;
  }

  refs["artifact-tag"].textContent = "Artifacts saved";
  const files = artifacts.files || {};
  refs["artifact-paths"].innerHTML = Object.entries(files)
    .filter(([, value]) => value)
    .map(([key, value]) => `
      <div class="artifact-item">
        <strong>${escapeHtml(key)}</strong>
        <code>${escapeHtml(value)}</code>
      </div>
    `)
    .join("");
}

function appendLog(entry, autoScroll) {
  state.recentLogs.push(entry);
  const node = document.createElement("article");
  node.className = `log-entry log-${entry.level || "info"}`;
  node.innerHTML = `
    <div class="log-entry-head">
      <span class="log-entry-level">${escapeHtml(entry.scope || "system")} / ${escapeHtml(entry.level || "info")}</span>
      <span>${escapeHtml(formatTimestamp(entry.timestamp))}</span>
    </div>
    <p class="log-entry-body">${escapeHtml(entry.message || "")}</p>
    ${entry.data && Object.keys(entry.data).length ? `<div class="log-entry-data">${escapeHtml(prettyJson(entry.data))}</div>` : ""}
  `;
  refs["log-stream"].appendChild(node);
  if (autoScroll) {
    refs["log-stream"].scrollTop = refs["log-stream"].scrollHeight;
  }
}

function clearLogView() {
  refs["log-stream"].innerHTML = "";
}

async function postJson(path, payload = {}) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    refs["summary-tag"].textContent = "Error";
    refs["summary-output"].textContent = data.detail || prettyJson(data);
    throw new Error(data.detail || `Request failed: ${response.status}`);
  }

  return data;
}

function setSocketStatus(text, className) {
  refs["socket-status"].textContent = text;
  refs["socket-status"].className = `metric-value ${className}`;
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

function projectNameFromPath(projectPath) {
  if (!projectPath) {
    return "";
  }
  const parts = projectPath.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1];
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTimestamp(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}
