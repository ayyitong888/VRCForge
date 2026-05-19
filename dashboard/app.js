const PROVIDER_PRESETS = {
  gemini: {
    providerLabel: "Google AI Studio",
    base_url: "",
    model: "gemini-2.5-flash",
    authHeader: "API key",
    usesBaseUrl: false,
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
  ollama: {
    providerLabel: "Ollama",
    base_url: "http://127.0.0.1:11434/v1",
    model: "llama3.2",
    authHeader: "optional",
    usesBaseUrl: true,
  },
  vertexai: {
    providerLabel: "Google Vertex AI",
    base_url: "",
    model: "gemini-2.5-flash",
    authHeader: "ADC",
    usesBaseUrl: true,
  },
  custom: {
    providerLabel: "自定义",
    base_url: "",
    model: "",
    authHeader: "Authorization: Bearer",
    usesBaseUrl: true,
  },
};

const MANUAL_MODEL_VALUE = "__manual_model__";
const VISION_PLAY_MODE_GUIDANCE = "建议进入 Play Mode 并启动 Gesture Manager 后再截图；当前将使用 Scene View 截图 / Play Mode with Gesture Manager is recommended; current capture will use Scene View.";
const VISION_GESTURE_GUIDANCE = "建议安装 Gesture Manager 以获得准确效果 / Gesture Manager recommended for accurate preview";

const SHADER_CATEGORY_LABELS = {
  skin: "皮肤",
  eyes: "眼睛",
  hair: "头发",
  clothes: "服装",
  accessory: "配饰",
  unknown: "未分类",
};

const SHADER_SEMANTIC_LABELS = {
  base_color: "基础色",
  shade_color: "阴影色",
  emission_color: "自发光颜色",
  emission_strength: "自发光强度",
  smoothness: "光滑度",
  metallic: "金属度",
  rim_strength: "边缘光强度",
  outline_width: "描边宽度",
  outline_color: "描边颜色",
  alpha: "透明度",
};

const state = {
  socket: null,
  projects: [],
  selectedProjectPath: "",
  unityStatus: null,
  sceneAvatars: [],
  selectedAvatarPath: "",
  selectedAvatarName: "",
  apiConfig: null,
  currentProvider: "",
  providerDrafts: {},
  modelOptionsByProvider: {},
  apiConfigDirty: false,
  connectionNotices: {},
  blendshapes: [],
  blendshapeWorking: {},
  blendshapeBaseline: {},
  undoDepth: 0,
  clothes: [],
  sourceReferenceImages: [],
  targetReferenceImages: [],
  lastAiChanges: [],
  lastAiProof: null,
  currentHistoryRecord: null,
  tuningHistory: [],
  tuningPresets: [],
  lockedBlendshapes: [],
  presetLimit: 10,
  shaderInventory: null,
  shaderMaterials: [],
  shaderCategoryOverrides: {},
  shaderPlan: null,
  shaderPlanChanges: [],
  shaderHistoryRecord: null,
  shaderHistory: [],
  shaderPresets: [],
  lockedShaderMaterials: [],
  lockedShaderProperties: [],
  shaderReviewBeforePaths: [],
  shaderReviewAfterPaths: [],
  latestParameterSnapshotPath: "",
  latestScreenshotUrl: "",
  multiScreenshots: [],
  visionAuditsByImageUrl: {},
};

const refs = {};

const REFERENCE_GROUP_CONFIG = {
  source: {
    stateKey: "sourceReferenceImages",
    fileId: "source-reference-image-file",
    pathInputId: "source-reference-image-path",
    chooseBtnId: "source-reference-choose-btn",
    dropzoneId: "source-reference-dropzone",
    previewId: "source-reference-preview",
    countId: "source-reference-count",
    statusId: "source-reference-status",
    useLatestId: "source-use-latest-screenshot-ref-btn",
    captureId: "source-capture-screenshot-ref-btn",
    clearId: "source-clear-reference-image-btn",
    label: "原图",
    emptyText: "可选：上传当前脸/原图，或用 Unity 截图作为修改前参考。",
  },
  target: {
    stateKey: "targetReferenceImages",
    fileId: "target-reference-image-file",
    pathInputId: "target-reference-image-path",
    chooseBtnId: "target-reference-choose-btn",
    dropzoneId: "target-reference-dropzone",
    previewId: "target-reference-preview",
    countId: "target-reference-count",
    statusId: "target-reference-status",
    useLatestId: "target-use-latest-screenshot-ref-btn",
    captureId: "target-capture-screenshot-ref-btn",
    clearId: "target-clear-reference-image-btn",
    label: "目标图",
    emptyText: "可选：上传想要的脸/表情参考图，或用 Unity 截图作为目标参考。",
  },
};

document.addEventListener("DOMContentLoaded", () => {
  cacheRefs();
  bindEvents();
  loadPresetLimit();
  connectSocket();
  updateModeVisibility();
  syncMockModeText();
  updateAllReferenceImageStatus();
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
    "api-model-select",
    "api-model-input",
    "api-model-label",
    "api-model-note",
    "api-model-manual-field",
    "api-key-input",
    "api-key-label",
    "api-key-note",
    "api-base-url-field",
    "api-base-url-label",
    "api-base-url",
    "api-base-url-note",
    "config-save-status",
    "config-output",
    "load-models-btn",
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
    "source-reference-image-file",
    "source-reference-image-path",
    "source-reference-choose-btn",
    "source-reference-dropzone",
    "source-reference-preview",
    "source-reference-count",
    "source-use-latest-screenshot-ref-btn",
    "source-capture-screenshot-ref-btn",
    "source-clear-reference-image-btn",
    "source-reference-status",
    "target-reference-image-file",
    "target-reference-image-path",
    "target-reference-choose-btn",
    "target-reference-dropzone",
    "target-reference-preview",
    "target-reference-count",
    "target-use-latest-screenshot-ref-btn",
    "target-capture-screenshot-ref-btn",
    "target-clear-reference-image-btn",
    "target-reference-status",
    "reference-image-status",
    "ai-run-btn",
    "ai-apply-plan-btn",
    "save-preset-btn",
    "manual-apply-btn",
    "manual-undo-btn",
    "open-history-btn",
    "open-presets-btn",
    "lock-visible-btn",
    "unlock-visible-btn",
    "preset-limit-input",
    "ai-lock-instruction-input",
    "ai-lock-btn",
    "ai-unlock-btn",
    "blendshape-count-chip",
    "pending-count",
    "llm-change-panel",
    "llm-change-count",
    "llm-change-list",
    "tuning-history-panel",
    "tuning-history-count",
    "tuning-history-list",
    "tuning-preset-panel",
    "tuning-preset-count",
    "tuning-preset-list",
    "blendshape-search",
    "avatar-path-display",
    "blendshape-list",
    "scan-clothes-btn",
    "generate-fx-btn",
    "apply-fx-btn",
    "fx-apply-panel",
    "fx-apply-count",
    "fx-dry-run",
    "fx-payload-preview",
    "clothes-count-chip",
    "clothes-list",
    "fx-output",
    "scan-params-btn",
    "optimize-params-btn",
    "apply-params-btn",
    "rollback-params-btn",
    "param-diff-panel",
    "param-diff-count",
    "param-dry-run",
    "param-diff-list",
    "param-payload-preview",
    "bool-count",
    "int-count",
    "float-count",
    "param-suggestions",
    "param-output",
    "scan-shader-materials-btn",
    "generate-shader-plan-btn",
    "apply-shader-plan-btn",
    "restore-shader-plan-btn",
    "save-shader-preset-btn",
    "open-shader-history-btn",
    "open-shader-presets-btn",
    "capture-shader-before-btn",
    "capture-shader-after-btn",
    "review-shader-vision-btn",
    "shader-instruction-input",
    "shader-material-count-chip",
    "shader-materials-table",
    "shader-plan-list",
    "shader-history-panel",
    "shader-history-count",
    "shader-history-list",
    "shader-preset-panel",
    "shader-preset-count",
    "shader-preset-list",
    "shader-output",
    "vision-angle-tabs",
    "vision-multi-thumbs",
    "capture-screenshot-btn",
    "capture-multi-btn",
    "audit-vision-btn",
    "audit-multi-btn",
    "vision-status-chip",
    "vision-image",
    "vision-annotations",
    "vision-placeholder",
    "vision-result",
    "log-stream",
    "clear-logs-btn",
    "reference-lightbox",
    "reference-lightbox-image",
    "reference-lightbox-title",
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
  refs["api-model-select"].addEventListener("change", onModelSelectChanged);
  refs["load-models-btn"].addEventListener("click", () => runButtonTask("load-models-btn", "读取中...", loadProviderModels));
  refs["save-config-btn"].addEventListener("click", () => runButtonTask("save-config-btn", "保存中...", () => saveApiConfig(true)));
  refs["reset-provider-btn"].addEventListener("click", resetProviderDefaults);
  bindReferenceGroupEvents("source");
  bindReferenceGroupEvents("target");
  refs["ai-run-btn"].addEventListener("click", () => runButtonTask("ai-run-btn", "生成中...", runAiPipeline));
  refs["ai-apply-plan-btn"].addEventListener("click", () => runButtonTask("ai-apply-plan-btn", "应用中...", applyCurrentAiPlan));
  refs["save-preset-btn"].addEventListener("click", () => runButtonTask("save-preset-btn", "保存中...", saveCurrentPlanAsPreset));
  refs["manual-apply-btn"].addEventListener("click", () => runButtonTask("manual-apply-btn", "应用中...", applyManualBlendshapes));
  refs["manual-undo-btn"].addEventListener("click", () => runButtonTask("manual-undo-btn", "撤销中...", undoManualBlendshapes));
  refs["open-history-btn"].addEventListener("click", () => toggleTuningPanel("history"));
  refs["open-presets-btn"].addEventListener("click", () => toggleTuningPanel("presets"));
  refs["lock-visible-btn"].addEventListener("click", () => runButtonTask("lock-visible-btn", "锁定中...", () => setVisibleBlendshapeLocks(true)));
  refs["unlock-visible-btn"].addEventListener("click", () => runButtonTask("unlock-visible-btn", "解锁中...", () => setVisibleBlendshapeLocks(false)));
  refs["ai-lock-btn"].addEventListener("click", () => runButtonTask("ai-lock-btn", "AI 判断中...", () => setAiSelectedBlendshapeLocks(true)));
  refs["ai-unlock-btn"].addEventListener("click", () => runButtonTask("ai-unlock-btn", "AI 判断中...", () => setAiSelectedBlendshapeLocks(false)));
  refs["preset-limit-input"].addEventListener("change", persistPresetLimit);
  refs["scan-clothes-btn"].addEventListener("click", () => runButtonTask("scan-clothes-btn", "扫描中...", scanClothes));
  refs["generate-fx-btn"].addEventListener("click", () => runButtonTask("generate-fx-btn", "生成中...", generateFxBlueprint));
  refs["apply-fx-btn"].addEventListener("click", () => runButtonTask("apply-fx-btn", "写入中...", applyClothesFx));
  refs["scan-params-btn"].addEventListener("click", () => runButtonTask("scan-params-btn", "扫描中...", scanParameters));
  refs["optimize-params-btn"].addEventListener("click", () => runButtonTask("optimize-params-btn", "分析中...", optimizeParameters));
  refs["apply-params-btn"].addEventListener("click", () => runButtonTask("apply-params-btn", "应用中...", applyParameterOptimization));
  refs["rollback-params-btn"].addEventListener("click", () => runButtonTask("rollback-params-btn", "回滚中...", rollbackParameterOptimization));
  refs["scan-shader-materials-btn"].addEventListener("click", () => runButtonTask("scan-shader-materials-btn", "扫描中...", scanShaderMaterials));
  refs["generate-shader-plan-btn"].addEventListener("click", () => runButtonTask("generate-shader-plan-btn", "生成中...", generateShaderPlan));
  refs["apply-shader-plan-btn"].addEventListener("click", () => runButtonTask("apply-shader-plan-btn", "应用中...", applyShaderPlan));
  refs["restore-shader-plan-btn"].addEventListener("click", () => runButtonTask("restore-shader-plan-btn", "恢复中...", restoreShaderPlan));
  refs["save-shader-preset-btn"].addEventListener("click", () => runButtonTask("save-shader-preset-btn", "保存中...", saveCurrentShaderPlanAsPreset));
  refs["open-shader-history-btn"].addEventListener("click", () => toggleShaderPanel("history"));
  refs["open-shader-presets-btn"].addEventListener("click", () => toggleShaderPanel("presets"));
  refs["capture-shader-before-btn"].addEventListener("click", () => runButtonTask("capture-shader-before-btn", "截图中...", () => captureShaderReviewImage("before")));
  refs["capture-shader-after-btn"].addEventListener("click", () => runButtonTask("capture-shader-after-btn", "截图中...", () => captureShaderReviewImage("after")));
  refs["review-shader-vision-btn"].addEventListener("click", () => runButtonTask("review-shader-vision-btn", "复核中...", runShaderVisionReview));
  refs["shader-materials-table"].addEventListener("change", onShaderMaterialCategoryChanged);
  refs["capture-screenshot-btn"].addEventListener("click", () => runButtonTask("capture-screenshot-btn", "截图中...", captureScreenshot));
  refs["capture-multi-btn"].addEventListener("click", () => runButtonTask("capture-multi-btn", "多视角截图中...", captureMultiScreenshot));
  refs["audit-vision-btn"].addEventListener("click", () => runButtonTask("audit-vision-btn", "分析中...", auditVision));
  refs["audit-multi-btn"].addEventListener("click", () => runButtonTask("audit-multi-btn", "聚合分析中...", auditMultiVision));
  refs["clear-logs-btn"].addEventListener("click", clearLogView);
  refs["reference-lightbox"].addEventListener("click", closeReferenceLightbox);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeReferenceLightbox();
    }
  });
  refs["blendshape-search"].addEventListener("input", onBlendshapeSearchChanged);
  
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
  setSocketStatus("Connecting", false, "正在连接 dashboard websocket");

  socket.addEventListener("open", () => setSocketStatus("Live", true, ""));
  socket.addEventListener("close", (event) => {
    const reason = event.reason || `WebSocket closed with code ${event.code}`;
    setSocketStatus("Offline", false, reason);
    setTimeout(connectSocket, 1500);
  });
  socket.addEventListener("error", () => setSocketStatus("Error", false, "WebSocket 连接失败，请确认 dashboard 服务仍在运行"));
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

  updateModeVisibility();
  syncMockModeText();
  loadShaderTuningData().catch((error) => {
    refs["shader-output"].textContent = `材质数据加载失败：${error.message}`;
  });
  loadTuningData().catch((error) => {
    refs["summary-output"].textContent = `历史/预设读取失败：${error.message}`;
  });
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
  refs["unity-status-output"].textContent = formatConnectionResult("Unity MCP", connected, getConnectionFailureReason(payload));
  renderConnectionNotice("Unity MCP", connected, getConnectionFailureReason(payload));
}

function renderProjects(payload) {
  state.projects = payload.projects || [];
  state.selectedProjectPath = payload.selectedProjectPath || state.selectedProjectPath || "";
  refs["project-count"].textContent = `${state.projects.length} 个工程`;
  refs["project-select"].innerHTML = state.projects.map((project) => {
    const badges = [];
    if (project.hasVrcForge) badges.push("VRCForge");
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
  state.currentProvider = provider;
  refs["api-key-input"].value = state.apiConfig.api_key;
  refs["api-base-url"].value = state.apiConfig.base_url || "";
  renderModelSelect(getCachedModelOptions(provider, state.apiConfig.model), state.apiConfig.model, !state.apiConfig.model);
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
  const isGemini = provider === "gemini";
  const isOllama = provider === "ollama";
  const isVertex = provider === "vertexai";
  const hideBaseField = isAnthropic || isGemini;

  refs["api-key-label"].textContent =
    isAnthropic ? "API Key（x-api-key header）"
      : isGemini ? "API Key（Google AI Studio）"
      : isOllama ? "API Key（可留空）"
      : isVertex ? "认证（ADC / gcloud）"
      : "API Key（Bearer token）";
  refs["api-key-note"].textContent =
    isVertex ? "Vertex AI 使用本机 Google Application Default Credentials；这里可留空。"
      : isOllama ? "本地 Ollama 默认不校验 API Key；如代理要求密钥再填写。"
      : "配置会保存到本地 config.json，并立即热更新。";
  refs["api-base-url-field"].classList.toggle("hidden", hideBaseField);
  refs["api-base-url-label"].textContent = isVertex ? "Project / Location" : "Base URL";
  refs["api-base-url"].placeholder = isVertex ? "project=my-gcp-project;location=us-central1" : "https://api.example.com/v1";
  refs["api-base-url-note"].textContent =
    isAnthropic ? "Anthropic 走官方端点。"
      : isGemini ? "Google AI Studio 走 google-genai 官方接口，不需要 Base URL。"
      : isVertex ? "Vertex AI 不使用 Base URL；这里填写 project/location，或使用 GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION。"
      : isOllama ? "Ollama 使用 OpenAI-compatible /v1 接口，默认 http://127.0.0.1:11434/v1。"
      : "该 provider 使用 OpenAI-compatible 接口。";
  refs["api-model-input"].placeholder = preset.model || "model-name";
  const modelCount = (state.modelOptionsByProvider[provider] || []).length;
  refs["api-model-note"].textContent = modelCount
    ? `已缓存 ${modelCount} 个模型，可重新读取刷新。`
    : (isOllama || isVertex ? "确认本地服务或 Vertex 认证后点击“读取模型列表”。" : "输入 API Key 后点击“读取模型列表”。");
  if (hideBaseField) {
    refs["api-base-url"].value = "";
  } else if (!refs["api-base-url"].value.trim()) {
    refs["api-base-url"].value = preset.base_url;
  }
}

function getCachedModelOptions(provider, selectedModel) {
  const cached = state.modelOptionsByProvider[provider] || [];
  if (cached.length) {
    return cached;
  }
  return selectedModel ? [{ id: selectedModel, label: selectedModel }] : [];
}

function sanitizeModelOptions(models) {
  const unique = new Map();
  for (const model of models || []) {
    const id = String(model.id || model.name || "").trim();
    if (!id || unique.has(id)) {
      continue;
    }
    unique.set(id, { id, label: String(model.label || id) });
  }
  return Array.from(unique.values()).sort((left, right) => left.id.localeCompare(right.id));
}

function renderModelSelect(models, selectedModel = "", manualMode = false) {
  const options = sanitizeModelOptions(models);
  const selected = String(selectedModel || "").trim();
  const hasSelected = selected && options.some((model) => model.id === selected);
  if (selected && !hasSelected) {
    options.unshift({ id: selected, label: `${selected}（当前）` });
  }

  const optionHtml = options.map((model) => {
    return `<option value="${escapeHtml(model.id)}">${escapeHtml(model.label)}</option>`;
  }).join("");
  refs["api-model-select"].innerHTML = `${optionHtml}<option value="${MANUAL_MODEL_VALUE}">手动输入...</option>`;

  const useManual = manualMode || !options.length;
  refs["api-model-select"].value = useManual ? MANUAL_MODEL_VALUE : (hasSelected || selected ? selected : options[0].id);
  refs["api-model-input"].value = useManual ? selected : refs["api-model-select"].value;
  refs["api-model-manual-field"].classList.toggle("hidden", !useManual);
}

function readSelectedModel() {
  const selected = refs["api-model-select"].value;
  if (selected === MANUAL_MODEL_VALUE) {
    return refs["api-model-input"].value.trim();
  }
  return selected || refs["api-model-input"].value.trim();
}

function onModelSelectChanged() {
  const manualMode = refs["api-model-select"].value === MANUAL_MODEL_VALUE;
  refs["api-model-manual-field"].classList.toggle("hidden", !manualMode);
  if (!manualMode) {
    refs["api-model-input"].value = refs["api-model-select"].value;
  }
  markApiConfigDirty();
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
  flushReferencePathInputs();
  const referencePayload = buildReferenceImagePayload();
  return {
    ...buildConnectionPayload(),
    avatar: state.selectedAvatarPath || null,
    instruction: refs["instruction-input"].value.trim() || null,
    model: readSelectedModel() || null,
    source_reference_image_paths: referencePayload.source.paths,
    source_reference_image_data_urls: referencePayload.source.dataUrls,
    target_reference_image_paths: referencePayload.target.paths,
    target_reference_image_data_urls: referencePayload.target.dataUrls,
    source_mode: refs["source-mode"].value,
    export_json: refs["export-json"].value.trim() || null,
    plan_json: refs["plan-json"].value.trim() || null,
    mock_execute: refs["mock-execute"].checked,
    min_confidence: Number(refs["min-confidence"].value || 0.65),
    allow_low_confidence: refs["allow-low-confidence"].checked,
    save_artifacts: refs["save-artifacts"].checked,
  };
}

function bindReferenceGroupEvents(group) {
  const config = REFERENCE_GROUP_CONFIG[group];
  refs[config.fileId].addEventListener("change", () => onReferenceImageFilesChanged(group));
  refs[config.chooseBtnId].addEventListener("click", () => refs[config.fileId].click());
  refs[config.pathInputId].addEventListener("paste", (event) => onReferenceImagePaste(group, event));
  refs[config.pathInputId].addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addReferencePathsFromInput(group);
    }
  });
  refs[config.pathInputId].addEventListener("blur", () => addReferencePathsFromInput(group));
  refs[config.dropzoneId].addEventListener("paste", (event) => onReferenceImagePaste(group, event));
  refs[config.dropzoneId].addEventListener("click", (event) => {
    if (event.target.closest("button") || event.target.closest("input")) {
      return;
    }
    refs[config.pathInputId].focus();
  });
  refs[config.dropzoneId].addEventListener("dragover", (event) => {
    event.preventDefault();
    refs[config.dropzoneId].classList.add("is-dragging");
  });
  refs[config.dropzoneId].addEventListener("dragleave", () => refs[config.dropzoneId].classList.remove("is-dragging"));
  refs[config.dropzoneId].addEventListener("drop", (event) => onReferenceImageDrop(group, event));
  refs[config.previewId].addEventListener("click", (event) => onReferencePreviewClick(group, event));
  refs[config.useLatestId].addEventListener("click", () => useLatestScreenshotAsReference(group));
  refs[config.captureId].addEventListener("click", () => runButtonTask(config.captureId, "截图中...", () => captureScreenshotAsReference(group)));
  refs[config.clearId].addEventListener("click", () => clearReferenceImages(group));
}

function buildReferenceImagePayload() {
  return {
    source: buildReferenceGroupPayload("source"),
    target: buildReferenceGroupPayload("target"),
  };
}

function buildReferenceGroupPayload(group) {
  const config = REFERENCE_GROUP_CONFIG[group];
  const storedImages = state[config.stateKey] || [];
  return {
    paths: storedImages.filter((item) => item.path).map((item) => item.path),
    dataUrls: storedImages.filter((item) => item.dataUrl).map((item) => item.dataUrl),
  };
}

async function onReferenceImageFilesChanged(group) {
  const config = REFERENCE_GROUP_CONFIG[group];
  const files = Array.from(refs[config.fileId].files || []);
  if (!files.length) {
    return;
  }

  const invalid = files.find((file) => !file.type.startsWith("image/"));
  if (invalid) {
    refs[config.statusId].textContent = `${config.label}包含非图片文件：${invalid.name}`;
    refs[config.fileId].value = "";
    return;
  }

  const tooLarge = files.find((file) => file.size > 8 * 1024 * 1024);
  if (tooLarge) {
    refs[config.statusId].textContent = `${tooLarge.name} 超过 8 MB，请换一张小一点的图。`;
    refs[config.fileId].value = "";
    return;
  }

  try {
    const images = await buildReferenceImageItemsFromFiles(files);
    state[config.stateKey].push(...images);
    refs[config.fileId].value = "";
    updateReferenceImageStatus(group);
  } catch (_error) {
    refs[config.statusId].textContent = `${config.label}读取失败。`;
  }
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result || "")));
    reader.addEventListener("error", reject);
    reader.readAsDataURL(file);
  });
}

async function onReferenceImagePaste(group, event) {
  event.stopPropagation();
  const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
  if (!files.length) {
    window.setTimeout(() => addReferencePathsFromInput(group), 0);
    return;
  }

  event.preventDefault();
  await addReferenceFiles(group, files);
}

async function onReferenceImageDrop(group, event) {
  event.preventDefault();
  const config = REFERENCE_GROUP_CONFIG[group];
  refs[config.dropzoneId].classList.remove("is-dragging");
  const files = Array.from(event.dataTransfer?.files || []).filter((file) => file.type.startsWith("image/"));
  if (!files.length) {
    refs[config.statusId].textContent = "拖入内容里没有图片文件。";
    return;
  }
  await addReferenceFiles(group, files);
}

async function addReferenceFiles(group, files) {
  const config = REFERENCE_GROUP_CONFIG[group];
  const tooLarge = files.find((file) => file.size > 8 * 1024 * 1024);
  if (tooLarge) {
    refs[config.statusId].textContent = `${tooLarge.name} 超过 8 MB，请换一张小一点的图。`;
    return;
  }

  try {
    const images = await buildReferenceImageItemsFromFiles(files);
    state[config.stateKey].push(...images);
    updateReferenceImageStatus(group);
  } catch (_error) {
    refs[config.statusId].textContent = `${config.label}读取失败。`;
  }
}

function buildReferenceImageItemsFromFiles(files) {
  return Promise.all(files.map(async (file) => {
    const dataUrl = await readFileAsDataUrl(file);
    return {
      id: makeReferenceImageId(),
      name: file.name || "粘贴图片",
      dataUrl,
      previewUrl: dataUrl,
    };
  }));
}

function useLatestScreenshotAsReference(group) {
  const config = REFERENCE_GROUP_CONFIG[group];
  if (!state.latestScreenshotUrl) {
    refs[config.statusId].textContent = "还没有可用截图，请先在视觉质检模块捕获截图。";
    return;
  }
  addReferencePath(group, urlToArtifactPath(state.latestScreenshotUrl), "最近截图", state.latestScreenshotUrl);
}

async function captureScreenshotAsReference(group) {
  const config = REFERENCE_GROUP_CONFIG[group];
  const payload = await postJson("/api/vision/capture", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    width: 960,
    height: 960,
  });
  renderScreenshot(payload.imageUrl);
  refs["vision-result"].innerHTML = `<div class="info-card"><strong>截图已加入${escapeHtml(config.label)}</strong><span>${escapeHtml(payload.imagePath)}</span></div>`;
  refs["vision-status-chip"].textContent = "待分析";
  addReferencePath(group, payload.imagePath, "Unity 截图", payload.imageUrl ? `${payload.imageUrl}?t=${Date.now()}` : "");
}

function addReferencePath(group, path, name, previewUrl = "") {
  const config = REFERENCE_GROUP_CONFIG[group];
  state[config.stateKey].push({
    id: makeReferenceImageId(),
    name,
    path,
    previewUrl: previewUrl || pathToReferencePreviewUrl(path),
  });
  updateReferenceImageStatus(group);
}

function addReferencePathsFromInput(group) {
  const config = REFERENCE_GROUP_CONFIG[group];
  const input = refs[config.pathInputId];
  const paths = parseReferencePathInput(input.value);
  if (!paths.length) {
    return;
  }
  for (const path of paths) {
    addReferencePath(group, path, path.split(/[\\/]/).pop() || path);
  }
  input.value = "";
  updateReferenceImageStatus(group);
}

function parseReferencePathInput(value) {
  return String(value || "")
    .split(/\r?\n|;/)
    .map((item) => item.trim().replace(/^"|"$/g, ""))
    .filter(Boolean);
}

function pathToReferencePreviewUrl(path) {
  if (!path) {
    return "";
  }
  if (path.startsWith("/artifacts/")) {
    return path;
  }
  if (path.startsWith("artifacts/")) {
    return `/artifacts/${path.slice("artifacts/".length)}`;
  }
  return "";
}

function clearReferenceImages(group) {
  const config = REFERENCE_GROUP_CONFIG[group];
  state[config.stateKey] = [];
  refs[config.fileId].value = "";
  refs[config.pathInputId].value = "";
  updateReferenceImageStatus(group);
}

function removeReferenceImage(group, index) {
  const config = REFERENCE_GROUP_CONFIG[group];
  state[config.stateKey].splice(index, 1);
  updateReferenceImageStatus(group);
}

function onReferencePreviewClick(group, event) {
  const removeButton = event.target.closest("[data-reference-remove]");
  if (removeButton) {
    removeReferenceImage(group, Number(removeButton.dataset.referenceRemove));
    return;
  }

  const openButton = event.target.closest("[data-reference-open]");
  if (openButton) {
    openReferenceLightbox(group, Number(openButton.dataset.referenceOpen));
  }
}

function updateAllReferenceImageStatus() {
  updateReferenceImageStatus("source");
  updateReferenceImageStatus("target");
}

function updateReferenceImageStatus(group) {
  const config = REFERENCE_GROUP_CONFIG[group];
  const storedImages = state[config.stateKey] || [];
  const total = storedImages.length;
  refs[config.countId].textContent = `${total} 张`;
  refs[config.statusId].textContent = total
    ? `${config.label}已加入 ${total} 张：${summarizeReferenceImages(storedImages)}`
    : config.emptyText;
  renderReferencePreviewGrid(group);

  const sourceCount = (state.sourceReferenceImages || []).length;
  const targetCount = (state.targetReferenceImages || []).length;
  refs["reference-image-status"].textContent = sourceCount || targetCount
    ? `捏脸请求将同时发送：原图 ${sourceCount} 张，目标图 ${targetCount} 张。两组都可为空。`
    : "不传图时只按文字捏脸；传图时会把文字、原图和目标图同一轮发送给当前模型。";
}

function renderReferencePreviewGrid(group) {
  const config = REFERENCE_GROUP_CONFIG[group];
  const storedImages = state[config.stateKey] || [];
  refs[config.previewId].innerHTML = storedImages.map((item, index) => {
    const title = item.name || item.path || "参考图";
    const preview = item.previewUrl || item.dataUrl;
    const body = preview
      ? `<button class="reference-open" type="button" data-reference-open="${index}" aria-label="放大查看 ${escapeHtml(title)}"><img src="${escapeHtml(preview)}" alt="${escapeHtml(title)}"></button>`
      : `<div class="reference-path-preview"><strong>PATH</strong><span>${escapeHtml(title)}</span></div>`;
    return `
      <article class="reference-thumb">
        ${body}
        <button class="reference-remove" type="button" data-reference-remove="${index}" aria-label="移除 ${escapeHtml(title)}">×</button>
        <span>${escapeHtml(title)}</span>
      </article>
    `;
  }).join("");
}

function openReferenceLightbox(group, index) {
  const config = REFERENCE_GROUP_CONFIG[group];
  const item = state[config.stateKey]?.[index];
  const preview = item?.previewUrl || item?.dataUrl;
  if (!preview) {
    return;
  }

  const title = item.name || item.path || "参考图";
  refs["reference-lightbox-image"].src = preview;
  refs["reference-lightbox-image"].alt = title;
  refs["reference-lightbox-title"].textContent = title;
  refs["reference-lightbox"].classList.remove("hidden");
}

function closeReferenceLightbox() {
  if (!refs["reference-lightbox"] || refs["reference-lightbox"].classList.contains("hidden")) {
    return;
  }
  refs["reference-lightbox"].classList.add("hidden");
  refs["reference-lightbox-image"].removeAttribute("src");
  refs["reference-lightbox-title"].textContent = "";
}

function summarizeReferenceImages(storedImages) {
  const names = storedImages.map((item) => item.name || item.path || "上传图片");
  const shown = names.slice(0, 3).join("、");
  return names.length > 3 ? `${shown} 等` : shown;
}

function flushReferencePathInputs() {
  addReferencePathsFromInput("source");
  addReferencePathsFromInput("target");
}

function makeReferenceImageId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  return `ref-${Date.now()}-${Math.random().toString(16).slice(2)}`;
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
    const message = error.message || String(error);
    if (buttonId === "unity-status-btn" || buttonId === "unity-tools-btn") {
      refs["unity-status-output"].textContent = formatConnectionResult("Unity MCP", false, message);
      renderConnectionNotice("Unity MCP", false, message);
    } else {
      refs["summary-output"].textContent = message;
    }
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

async function getJson(path) {
  const response = await fetch(path);
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
  applyUnityStatus(payload);
}

async function loadUnityTools() {
  const payload = await postJson("/api/unity/tools", buildConnectionPayload());
  const connected = !payload.error && payload.connected !== false;
  const reason = getConnectionFailureReason(payload);
  refs["unity-status-output"].textContent = formatConnectionResult("Unity MCP tools", connected, reason);
  renderConnectionNotice("Unity MCP tools", connected, reason);
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
  refs["summary-output"].textContent = `已加载 ${state.blendshapes.length} 个脸部 Blendshape`;
}

function getFilteredBlendshapes() {
  const keyword = refs["blendshape-search"].value.trim();
  const searchTokens = tokenizeBlendshapeSearch(keyword);
  return state.blendshapes.filter((item) => {
    if (!searchTokens.length) {
      return true;
    }
    const haystack = buildBlendshapeSearchText(item);
    const compactHaystack = compactSearchText(haystack);
    return searchTokens.every((token) => haystack.includes(token) || compactHaystack.includes(compactSearchText(token)));
  });
}

function renderBlendshapeList() {
  const keyword = refs["blendshape-search"].value.trim();
  const filtered = getFilteredBlendshapes();

  refs["blendshape-count-chip"].textContent = `${filtered.length} 个 Blendshape`;
  refs["pending-count"].textContent = `${collectPendingAdjustments().length} 项`;

  if (!state.blendshapes.length) {
    refs["blendshape-list"].innerHTML = '<div class="empty-state">还没有加载 Blendshape，先点“加载 Blendshape”。</div>';
    return;
  }

  if (!filtered.length) {
    refs["blendshape-list"].innerHTML = `<div class="empty-state">没有匹配到 Blendshape：${escapeHtml(keyword)}</div>`;
    return;
  }

  refs["blendshape-list"].innerHTML = filtered.map((item) => {
    const key = blendshapeKey(item.rendererPath, item.blendshapeName);
    const baseline = Number(state.blendshapeBaseline[key] ?? item.currentWeight ?? 0);
    const current = Number(state.blendshapeWorking[key] ?? baseline);
    const changed = Math.abs(current - baseline) > 0.001 ? "changed" : "";
    const locked = isBlendshapeLocked(item.rendererPath, item.blendshapeName);
    return `
      <article class="blendshape-row ${changed} ${locked ? "locked" : ""}">
        <div class="blendshape-head">
          <div>
            <strong>${escapeHtml(item.blendshapeName)}</strong>
            <span>${escapeHtml(item.rendererName)} / ${escapeHtml(item.meshName)}</span>
          </div>
          <div class="weight-badges">
            <label class="lock-toggle">
              <input data-renderer-path="${escapeHtml(item.rendererPath)}" data-blendshape-name="${escapeHtml(item.blendshapeName)}" class="blendshape-lock-toggle" type="checkbox" ${locked ? "checked" : ""}>
              <span>${locked ? "已锁定" : "可调整"}</span>
            </label>
            <span class="weight-tag js-base">当前 ${baseline.toFixed(1)}</span>
            <span class="weight-tag weight-live js-live">${current.toFixed(1)}</span>
          </div>
        </div>
        <div class="blendshape-subline">${escapeHtml(item.rendererPath)}</div>
        <div class="slider-row">
          <input data-renderer-path="${escapeHtml(item.rendererPath)}" data-blendshape-name="${escapeHtml(item.blendshapeName)}" class="blendshape-slider" type="range" min="0" max="100" step="0.1" value="${current.toFixed(1)}" ${locked ? "disabled" : ""}>
          <input data-renderer-path="${escapeHtml(item.rendererPath)}" data-blendshape-name="${escapeHtml(item.blendshapeName)}" class="blendshape-number" type="number" min="0" max="100" step="0.1" value="${current.toFixed(1)}" ${locked ? "disabled" : ""}>
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
  refs["blendshape-list"].querySelectorAll(".blendshape-lock-toggle").forEach((input) => {
    input.addEventListener("change", () => setBlendshapeLock(input.dataset.rendererPath, input.dataset.blendshapeName, input.checked));
  });
}

function onBlendshapeSearchChanged() {
  renderBlendshapeList();
  const keyword = refs["blendshape-search"].value.trim();
  if (keyword) {
    refs["summary-output"].textContent = `正在搜索 Blendshape：${keyword}`;
  }
}

function tokenizeBlendshapeSearch(keyword) {
  return normalizeSearchText(keyword)
    .split(/\s+/)
    .map((token) => token.trim())
    .filter(Boolean);
}

function buildBlendshapeSearchText(item) {
  const rawFields = [
    item.blendshapeName,
    item.rendererName,
    item.rendererPath,
    item.meshName,
  ].join(" ");
  return normalizeSearchText(`${rawFields} ${semanticBlendshapeAliases(rawFields)}`);
}

function normalizeSearchText(value) {
  return String(value || "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[_\-./\\:]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function compactSearchText(value) {
  return normalizeSearchText(value).replace(/\s+/g, "");
}

function semanticBlendshapeAliases(value) {
  const text = String(value || "").toLowerCase();
  const aliases = [];
  const add = (terms, words) => {
    if (terms.some((term) => text.includes(term))) {
      aliases.push(words);
    }
  };

  add(["eye", "pupil", "iris", "blink", "まばたき", "瞳", "目"], "眼 眼睛 眼球 瞳孔 眨眼 睁眼 闭眼 大眼 小眼");
  add(["brow", "eyebrow", "眉"], "眉 眉毛 眉形 眉头 眉尾");
  add(["mouth", "lip", "smile", "teeth", "口", "唇"], "嘴 嘴巴 口 唇 微笑 笑 开口 闭嘴");
  add(["jaw", "chin", "cheek", "face", "morph", "round", "narrow", "顎"], "脸 脸型 脸颊 下巴 下颚 圆脸 瘦脸 窄脸 捏脸");
  add(["nose", "鼻"], "鼻 鼻子 鼻梁");
  add(["ear", "耳"], "耳 耳朵");
  add(["tongue", "舌"], "舌 舌头");
  add(["hair", "髪", "髮"], "头发 刘海 发型");
  return aliases.join(" ");
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

  for (const item of payload.appliedAdjustments || []) {
    const key = blendshapeKey(item.rendererPath, item.blendshapeName);
    state.blendshapeBaseline[key] = Number(item.targetWeight);
    state.blendshapeWorking[key] = Number(item.targetWeight);
  }
  state.undoDepth = payload.undoDepth || 0;
  const appliedCount = (payload.appliedAdjustments || []).length;
  const skippedCount = (payload.skippedAdjustments || []).length;
  refs["summary-output"].textContent = `已应用 ${appliedCount} 项滑块改动${skippedCount ? `，跳过 ${skippedCount} 项锁定或无效目标` : ""}`;
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
  const payload = await postJson("/api/pipeline/plan", buildDashboardRequest());
  if (payload.selectedAvatar) {
    setActiveAvatar(payload.selectedAvatar.avatarName, payload.selectedAvatar.avatarPath);
  }
  state.currentHistoryRecord = payload.historyRecord || null;
  state.lastAiChanges = payload.changePreview || [];
  state.lastAiProof = payload.visualProof || null;
  renderAiChangePreview(state.lastAiChanges, payload.referenceImage, payload.verifiedChanges, state.lastAiProof);
  await loadTuningData();
  refs["summary-output"].textContent = state.currentHistoryRecord
    ? "AI 已生成可审阅 Plan。满意后点“应用 Plan”，喜欢结果再保存为预设。"
    : "AI 已生成可审阅 Plan。";
}

async function applyCurrentAiPlan() {
  const historyId = state.currentHistoryRecord?.id;
  if (!historyId) {
    refs["summary-output"].textContent = "还没有可应用的 AI Plan，先生成一轮。";
    return;
  }

  const payload = await reapplyHistoryRecord(historyId);
  const appliedCount = (payload.appliedAdjustments || []).length;
  const skippedCount = (payload.skippedAdjustments || []).length;
  refs["summary-output"].textContent = `已应用当前 Plan：${appliedCount} 项${skippedCount ? `，跳过 ${skippedCount} 项锁定或无效目标` : ""}`;
}

function applyDirectAdjustmentsToBlendshapeState(adjustments) {
  for (const adjustment of adjustments || []) {
    const key = blendshapeKey(
      adjustment.rendererPath || adjustment.renderer_path || "",
      adjustment.blendshapeName || adjustment.blendshape_name || "",
    );
    const target = Number(adjustment.targetWeight ?? adjustment.target_weight ?? 0);
    state.blendshapeBaseline[key] = target;
    state.blendshapeWorking[key] = target;
  }
  renderBlendshapeList();
}

function renderAiChangePreview(changes, referenceImage, verifiedChanges = [], visualProof = null) {
  const items = Array.isArray(changes) ? changes : [];
  refs["llm-change-count"].textContent = `${items.length} 项`;
  refs["llm-change-panel"].classList.remove("hidden");

  if (!items.length) {
    refs["llm-change-list"].innerHTML = '<div class="empty-state">LLM 没有返回可执行的 Blendshape 改动</div>';
    return;
  }

  const referenceLine = renderReferenceContextSummary(referenceImage);
  const verificationByKey = new Map((Array.isArray(verifiedChanges) ? verifiedChanges : []).map((item) => [
    blendshapeKey(item.rendererPath || "", item.blendshapeName || ""),
    item,
  ]));
  const proofHtml = renderAiProofStrip(visualProof);
  refs["llm-change-list"].innerHTML = `${referenceLine}${proofHtml}${items.map((item) => {
    const verified = verificationByKey.get(blendshapeKey(item.rendererPath || "", item.blendshapeName || ""));
    const previous = Number(item.previousWeight ?? 0);
    const target = Number(item.targetWeight ?? 0);
    const delta = Number(item.delta ?? target - previous);
    const direction = delta >= 0 ? "+" : "";
    const actual = verified?.actualWeight;
    const difference = verified?.difference;
    const hasActual = Number.isFinite(Number(actual));
    const verificationLabel = verified
      ? (verified.verified ? "Unity 已回读命中" : verified.verificationStatus === "unreadable" ? "Unity 回读失败" : "Unity 回读未吻合")
      : "等待 Unity 回读";
    return `
      <article class="change-row ${verified?.verified ? "change-verified" : verified ? "change-unverified" : ""}">
        <div class="change-head">
          <strong>${escapeHtml(item.blendshapeName || "")}</strong>
          <span>${previous.toFixed(1)} -> ${target.toFixed(1)} (${direction}${delta.toFixed(1)})</span>
        </div>
        <div class="blendshape-subline">${escapeHtml(item.rendererPath || "")}</div>
        <p>${escapeHtml(item.reason || "")}</p>
        <div class="change-meta">
          <span class="meta-chip">confidence ${Number(item.confidence ?? 0).toFixed(2)}</span>
          <span class="meta-chip verification-chip">${escapeHtml(verificationLabel)}</span>
          ${hasActual ? `<span class="meta-chip">Unity 实际 ${Number(actual).toFixed(1)}</span>` : ""}
          ${Number.isFinite(Number(difference)) ? `<span class="meta-chip">误差 ${Number(difference).toFixed(2)}</span>` : ""}
        </div>
      </article>
    `;
  }).join("")}`;
}

async function loadTuningData() {
  const query = state.selectedAvatarPath ? `?avatar_path=${encodeURIComponent(state.selectedAvatarPath)}` : "";
  const [historyPayload, presetPayload, lockPayload] = await Promise.all([
    getJson(`/api/tuning/history${query}`),
    getJson(`/api/tuning/presets${query}`),
    getJson(`/api/tuning/locks${query}`),
  ]);
  state.tuningHistory = historyPayload.records || [];
  state.tuningPresets = presetPayload.presets || [];
  state.lockedBlendshapes = lockPayload.lockedBlendshapes || [];
  renderTuningHistory();
  renderTuningPresets();
  if (state.blendshapes.length) {
    renderBlendshapeList();
  }
}

function toggleTuningPanel(kind) {
  const panelId = kind === "history" ? "tuning-history-panel" : "tuning-preset-panel";
  refs[panelId].classList.toggle("hidden");
  loadTuningData().catch((error) => {
    refs["summary-output"].textContent = `历史/预设读取失败：${error.message}`;
  });
}

function renderTuningHistory() {
  const records = [...(state.tuningHistory || [])].reverse();
  refs["tuning-history-count"].textContent = `${records.length} 条`;
  if (!records.length) {
    refs["tuning-history-list"].innerHTML = '<div class="empty-state">还没有 AI 捏脸历史。生成 Plan 后会自动保存。</div>';
    return;
  }

  refs["tuning-history-list"].innerHTML = records.map((record) => {
    const changes = Array.isArray(record.changes) ? record.changes : [];
    const locked = Array.isArray(record.locked_blendshapes) ? record.locked_blendshapes.length : 0;
    const prompt = record.user_prompt || "无文字指令";
    return `
      <article class="tuning-card">
        <div class="change-head">
          <strong>${escapeHtml(formatTimestamp(record.created_at) || record.id)}</strong>
          <span>${changes.length} 项 / ${record.applied ? "已应用" : "未应用"}</span>
        </div>
        <p>${escapeHtml(prompt)}</p>
        <div class="change-meta">
          <span class="meta-chip">${escapeHtml(record.provider || "")}</span>
          <span class="meta-chip">${escapeHtml(record.model || "")}</span>
          <span class="meta-chip">参考图 ${Number(record.reference_image_count || 0)}</span>
          ${locked ? `<span class="meta-chip">锁定 ${locked}</span>` : ""}
        </div>
        <div class="button-row compact-row">
          <button class="button button-ghost" data-history-action="reapply" data-history-id="${escapeHtml(record.id)}">重放</button>
          <button class="button button-ghost" data-history-action="preset" data-history-id="${escapeHtml(record.id)}">保存预设</button>
        </div>
      </article>
    `;
  }).join("");

  refs["tuning-history-list"].querySelectorAll("[data-history-action]").forEach((button) => {
    button.addEventListener("click", () => runInlineButtonTask(button, "处理中...", () => handleHistoryAction(button)));
  });
}

function renderTuningPresets() {
  const presets = [...(state.tuningPresets || [])].reverse();
  refs["tuning-preset-count"].textContent = `${presets.length} 个`;
  if (!presets.length) {
    refs["tuning-preset-list"].innerHTML = '<div class="empty-state">还没有保存的预设。应用满意的 Plan 后可以保存。</div>';
    return;
  }

  refs["tuning-preset-list"].innerHTML = presets.map((preset) => {
    const changes = Array.isArray(preset.changes) ? preset.changes : [];
    const tags = Array.isArray(preset.tags) ? preset.tags : [];
    return `
      <article class="tuning-card">
        <div class="change-head">
          <strong>${escapeHtml(preset.name || preset.id)}</strong>
          <span>${changes.length} 项</span>
        </div>
        <p>${escapeHtml(preset.description || preset.user_prompt || "保存的 Blendshape after 值预设")}</p>
        <div class="change-meta">
          <span class="meta-chip">after values</span>
          ${tags.map((tag) => `<span class="meta-chip">${escapeHtml(tag)}</span>`).join("")}
          ${preset.last_applied_at ? `<span class="meta-chip">最近应用 ${escapeHtml(formatTimestamp(preset.last_applied_at))}</span>` : ""}
        </div>
        <div class="button-row compact-row">
          <button class="button button-accent" data-preset-action="apply" data-preset-id="${escapeHtml(preset.id)}">应用</button>
          <button class="button button-ghost" data-preset-action="rename" data-preset-id="${escapeHtml(preset.id)}">重命名</button>
          <button class="button button-ghost" data-preset-action="duplicate" data-preset-id="${escapeHtml(preset.id)}">复制</button>
          <button class="button button-ghost danger-button" data-preset-action="delete" data-preset-id="${escapeHtml(preset.id)}">删除</button>
        </div>
      </article>
    `;
  }).join("");

  refs["tuning-preset-list"].querySelectorAll("[data-preset-action]").forEach((button) => {
    button.addEventListener("click", () => runInlineButtonTask(button, "处理中...", () => handlePresetAction(button)));
  });
}

async function runInlineButtonTask(button, loadingText, task) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = loadingText;
  try {
    await task();
  } catch (error) {
    refs["summary-output"].textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function handleHistoryAction(button) {
  const id = button.dataset.historyId;
  if (button.dataset.historyAction === "reapply") {
    const payload = await reapplyHistoryRecord(id);
    const appliedCount = (payload.appliedAdjustments || []).length;
    const skippedCount = (payload.skippedAdjustments || []).length;
    refs["summary-output"].textContent = `已重放历史：${appliedCount} 项${skippedCount ? `，跳过 ${skippedCount} 项` : ""}`;
    return;
  }
  if (button.dataset.historyAction === "preset") {
    await saveHistoryAsPreset(id);
  }
}

async function handlePresetAction(button) {
  const id = button.dataset.presetId;
  const action = button.dataset.presetAction;
  if (action === "apply") {
    const payload = await applyPreset(id);
    const appliedCount = (payload.appliedAdjustments || []).length;
    const skippedCount = (payload.skippedAdjustments || []).length;
    refs["summary-output"].textContent = `已应用预设：${appliedCount} 项${skippedCount ? `，跳过 ${skippedCount} 项` : ""}`;
  } else if (action === "rename") {
    await renamePreset(id);
  } else if (action === "duplicate") {
    await duplicatePreset(id);
  } else if (action === "delete") {
    await deletePreset(id);
  }
}

async function reapplyHistoryRecord(historyId) {
  const payload = await postJson(`/api/tuning/history/${encodeURIComponent(historyId)}/reapply`, buildDashboardRequest());
  state.currentHistoryRecord = payload.historyRecord || state.currentHistoryRecord;
  state.undoDepth = payload.undoDepth || state.undoDepth;
  state.lastAiChanges = payload.changePreview || state.lastAiChanges;
  applyDirectAdjustmentsToBlendshapeState(payload.appliedAdjustments || []);
  renderAiChangePreview(payload.changePreview || [], payload.referenceImage || null, payload.verifiedChanges || [], payload.visualProof || null);
  await loadTuningData();
  return payload;
}

async function applyPreset(presetId) {
  const payload = await postJson(`/api/tuning/presets/${encodeURIComponent(presetId)}/apply`, buildDashboardRequest());
  state.undoDepth = payload.undoDepth || state.undoDepth;
  state.lastAiChanges = payload.changePreview || state.lastAiChanges;
  applyDirectAdjustmentsToBlendshapeState(payload.appliedAdjustments || []);
  renderAiChangePreview(payload.changePreview || [], payload.referenceImage || null, payload.verifiedChanges || [], payload.visualProof || null);
  await loadTuningData();
  return payload;
}

async function saveCurrentPlanAsPreset() {
  const historyId = state.currentHistoryRecord?.id;
  if (!historyId) {
    refs["summary-output"].textContent = "还没有可保存的 AI Plan，先生成一轮。";
    return;
  }
  await saveHistoryAsPreset(historyId);
}

async function saveHistoryAsPreset(historyId) {
  const defaultName = suggestPresetName();
  const name = window.prompt("预设名称", defaultName);
  if (!name || !name.trim()) {
    return;
  }
  const tagsText = window.prompt("标签（可选，用逗号分隔）", "");
  const description = window.prompt("描述（可选）", "");
  const payload = await postJson("/api/tuning/presets", {
    history_id: historyId,
    name: name.trim(),
    tags: splitTags(tagsText),
    description: description || "",
    max_presets: readPresetLimit(),
  });
  state.tuningPresets = payload.presets || state.tuningPresets;
  refs["tuning-preset-panel"].classList.remove("hidden");
  renderTuningPresets();
  refs["summary-output"].textContent = `已保存预设：${payload.preset?.name || name.trim()}`;
}

async function renamePreset(presetId) {
  const preset = (state.tuningPresets || []).find((item) => item.id === presetId);
  const name = window.prompt("新的预设名称", preset?.name || "");
  if (!name || !name.trim()) {
    return;
  }
  const payload = await postJson(`/api/tuning/presets/${encodeURIComponent(presetId)}/rename`, { name: name.trim() });
  state.tuningPresets = payload.presets || state.tuningPresets;
  renderTuningPresets();
  refs["summary-output"].textContent = `已重命名预设：${payload.preset?.name || name.trim()}`;
}

async function duplicatePreset(presetId) {
  const preset = (state.tuningPresets || []).find((item) => item.id === presetId);
  const name = window.prompt("复制后的预设名称", `${preset?.name || "preset"}_copy`);
  const payload = await postJson(`/api/tuning/presets/${encodeURIComponent(presetId)}/duplicate`, {
    name: name || null,
    max_presets: readPresetLimit(),
  });
  state.tuningPresets = payload.presets || state.tuningPresets;
  renderTuningPresets();
  refs["summary-output"].textContent = `已复制预设：${payload.preset?.name || ""}`;
}

async function deletePreset(presetId) {
  const preset = (state.tuningPresets || []).find((item) => item.id === presetId);
  if (!window.confirm(`删除预设“${preset?.name || presetId}”？`)) {
    return;
  }
  const payload = await postJson(`/api/tuning/presets/${encodeURIComponent(presetId)}/delete`);
  state.tuningPresets = payload.presets || [];
  renderTuningPresets();
  refs["summary-output"].textContent = "预设已删除";
}

function suggestPresetName() {
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
  ].join("");
  return `face_tuning_${stamp}`;
}

function splitTags(value) {
  return String(value || "")
    .split(/[,\s，、]+/)
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function loadPresetLimit() {
  const stored = Number(window.localStorage.getItem("vrcforgePresetLimit") || 10);
  state.presetLimit = Number.isFinite(stored) ? Math.min(Math.max(Math.round(stored), 1), 100) : 10;
  if (refs["preset-limit-input"]) {
    refs["preset-limit-input"].value = String(state.presetLimit);
  }
}

function readPresetLimit() {
  const value = Number(refs["preset-limit-input"]?.value || state.presetLimit || 10);
  const limit = Number.isFinite(value) ? Math.min(Math.max(Math.round(value), 1), 100) : 10;
  state.presetLimit = limit;
  if (refs["preset-limit-input"]) {
    refs["preset-limit-input"].value = String(limit);
  }
  window.localStorage.setItem("vrcforgePresetLimit", String(limit));
  return limit;
}

function persistPresetLimit() {
  const limit = readPresetLimit();
  refs["summary-output"].textContent = `预设上限已设置为 ${limit} 组。新保存或复制预设时会自动保留最新 ${limit} 组。`;
}

function lockedBlendshapeKey(rendererPath, blendshapeName) {
  return `${rendererPath || ""}::${blendshapeName || ""}`;
}

function normalizeLockedBlendshape(item) {
  const rendererPath = String(item?.rendererPath || item?.renderer_path || "");
  const blendshapeName = String(item?.blendshapeName || item?.blendshape_name || item?.blendshape || "");
  if (!blendshapeName) {
    return null;
  }
  return { rendererPath, blendshapeName };
}

function isBlendshapeLocked(rendererPath, blendshapeName) {
  return (state.lockedBlendshapes || []).some((item) => {
    const normalized = normalizeLockedBlendshape(item);
    if (!normalized) {
      return false;
    }
    return (normalized.rendererPath === rendererPath || !normalized.rendererPath) && normalized.blendshapeName === blendshapeName;
  });
}

async function setBlendshapeLock(rendererPath, blendshapeName, locked) {
  const map = new Map();
  for (const item of state.lockedBlendshapes || []) {
    const normalized = normalizeLockedBlendshape(item);
    if (normalized) {
      map.set(lockedBlendshapeKey(normalized.rendererPath, normalized.blendshapeName), normalized);
    }
  }
  const key = lockedBlendshapeKey(rendererPath, blendshapeName);
  if (locked) {
    map.set(key, { rendererPath, blendshapeName });
    const valueKey = blendshapeKey(rendererPath, blendshapeName);
    state.blendshapeWorking[valueKey] = Number(state.blendshapeBaseline[valueKey] ?? state.blendshapeWorking[valueKey] ?? 0);
  } else {
    map.delete(key);
  }
  await saveLockedBlendshapes(Array.from(map.values()));
  refs["summary-output"].textContent = locked ? `已锁定：${blendshapeName}` : `已解锁：${blendshapeName}`;
}

async function setVisibleBlendshapeLocks(locked) {
  const visible = getFilteredBlendshapes();
  if (!visible.length) {
    refs["summary-output"].textContent = "当前列表没有可锁定的 Blendshape。";
    return;
  }

  const map = new Map();
  for (const item of state.lockedBlendshapes || []) {
    const normalized = normalizeLockedBlendshape(item);
    if (normalized) {
      map.set(lockedBlendshapeKey(normalized.rendererPath, normalized.blendshapeName), normalized);
    }
  }

  for (const item of visible) {
    const key = lockedBlendshapeKey(item.rendererPath, item.blendshapeName);
    if (locked) {
      map.set(key, { rendererPath: item.rendererPath, blendshapeName: item.blendshapeName });
      const valueKey = blendshapeKey(item.rendererPath, item.blendshapeName);
      state.blendshapeWorking[valueKey] = Number(state.blendshapeBaseline[valueKey] ?? state.blendshapeWorking[valueKey] ?? 0);
    } else {
      map.delete(key);
    }
  }

  await saveLockedBlendshapes(Array.from(map.values()));
  refs["summary-output"].textContent = `${locked ? "已锁定" : "已解锁"}当前列表 ${visible.length} 项。重新生成 Plan 时会重抽未锁定的部分。`;
}

async function setAiSelectedBlendshapeLocks(locked) {
  await ensureApiConfigSaved();
  if (!state.selectedAvatarPath) {
    refs["summary-output"].textContent = "请先选择 Avatar。";
    return;
  }
  if (!state.blendshapes.length) {
    await loadBlendshapes();
  }
  const instruction = refs["ai-lock-instruction-input"].value.trim()
    || refs["blendshape-search"].value.trim()
    || refs["instruction-input"].value.trim();
  if (!instruction) {
    refs["summary-output"].textContent = "请先描述要让 AI 判断的部位，例如“眼睛”“嘴角”“眉毛”。";
    return;
  }

  const payload = await postJson("/api/tuning/locks/ai-select", {
    ...buildDashboardRequest(),
    avatar_path: state.selectedAvatarPath,
    action: locked ? "lock" : "unlock",
    selection_instruction: instruction,
    candidate_blendshapes: state.blendshapes,
    current_locked_blendshapes: state.lockedBlendshapes || [],
  });
  const selected = (payload.selectedBlendshapes || [])
    .map((item) => normalizeLockedBlendshape(item))
    .filter(Boolean);
  if (!selected.length) {
    refs["summary-output"].textContent = `AI 没有找到适合${locked ? "锁定" : "解锁"}的 Blendshape。`;
    return;
  }

  const map = new Map();
  for (const item of state.lockedBlendshapes || []) {
    const normalized = normalizeLockedBlendshape(item);
    if (normalized) {
      map.set(lockedBlendshapeKey(normalized.rendererPath, normalized.blendshapeName), normalized);
    }
  }

  for (const item of selected) {
    const key = lockedBlendshapeKey(item.rendererPath, item.blendshapeName);
    if (locked) {
      map.set(key, item);
      const valueKey = blendshapeKey(item.rendererPath, item.blendshapeName);
      state.blendshapeWorking[valueKey] = Number(state.blendshapeBaseline[valueKey] ?? state.blendshapeWorking[valueKey] ?? 0);
    } else {
      map.delete(key);
    }
  }

  await saveLockedBlendshapes(Array.from(map.values()));
  refs["summary-output"].textContent = `AI 已根据“${instruction}”${locked ? "锁定" : "解锁"} ${selected.length} 个 Blendshape。`;
}

async function saveLockedBlendshapes(lockedBlendshapes) {
  if (!state.selectedAvatarPath) {
    refs["summary-output"].textContent = "请先选择 Avatar。";
    return;
  }
  const payload = await postJson("/api/tuning/locks", {
    avatar_path: state.selectedAvatarPath,
    locked_blendshapes: lockedBlendshapes,
  });
  state.lockedBlendshapes = payload.lockedBlendshapes || [];
  renderBlendshapeList();
}

function renderReferenceContextSummary(referenceImage) {
  if (!referenceImage) {
    return "";
  }
  const groups = Array.isArray(referenceImage.groups) ? referenceImage.groups : [];
  if (groups.length) {
    const summary = groups
      .map((group) => `${group.label || group.role || "参考图"} ${Array.isArray(group.images) ? group.images.length : 0} 张`)
      .join(" / ");
    return `<div class="change-reference">参考图：${escapeHtml(summary)}</div>`;
  }
  if (Array.isArray(referenceImage.images) && referenceImage.images.length) {
    return `<div class="change-reference">参考图：${referenceImage.images.length} 张</div>`;
  }
  if (referenceImage.imagePath) {
    return `<div class="change-reference">参考图：${escapeHtml(referenceImage.imagePath)}</div>`;
  }
  return "";
}

function renderAiProofStrip(visualProof) {
  const before = visualProof?.before;
  const after = visualProof?.after;
  const errors = Array.isArray(visualProof?.errors) ? visualProof.errors : [];
  if (!before?.imageUrl && !after?.imageUrl && !errors.length) {
    return "";
  }

  const imageCard = (label, item) => item?.imageUrl ? `
    <figure class="proof-card">
      <img src="${item.imageUrl}?t=${Date.now()}" alt="${escapeHtml(label)}">
      <figcaption>${escapeHtml(label)}</figcaption>
    </figure>
  ` : "";

  const errorHtml = errors.length
    ? `<div class="proof-error">${errors.map((item) => `${escapeHtml(item.stage || "proof")}: ${escapeHtml(item.error || "")}`).join("<br>")}</div>`
    : "";

  return `
    <div class="proof-block">
      <div class="proof-title">捏脸前后对比</div>
      <div class="proof-grid">
        ${imageCard("执行前", before)}
        ${imageCard("执行后", after)}
      </div>
      ${errorHtml}
    </div>
  `;
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
    <label class="switch-row ${item.canToggleSceneObject ? "" : "disabled"}">
      <div>
        <strong>${escapeHtml(item.displayName || item.name || item.parameterName || "Unnamed")}</strong>
        <span>${escapeHtml(item.menuPath || item.objectPath || item.parameterName || "")}</span>
        <div class="change-meta">
          <span class="meta-chip">${escapeHtml(sourceLabel(item.source))}</span>
          ${item.parameterName ? `<span class="meta-chip">${escapeHtml(item.parameterName)}</span>` : ""}
          ${item.valueType ? `<span class="meta-chip">${escapeHtml(item.valueType)}</span>` : ""}
        </div>
      </div>
      <input data-clothing-index="${index}" type="checkbox" ${item.active ? "checked" : ""} ${item.canToggleSceneObject ? "" : "disabled"}>
    </label>
  `).join("");

  refs["clothes-list"].querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
    checkbox.addEventListener("change", async () => {
      const index = Number(checkbox.dataset.clothingIndex);
      const item = state.clothes[index];
      if (!item.canToggleSceneObject || !item.objectPath) {
        return;
      }
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

function sourceLabel(source) {
  if (source === "menu_control") {
    return "Menu";
  }
  if (source === "parameter") {
    return "Parameter";
  }
  if (source === "scene_object") {
    return "Scene";
  }
  return source || "Unknown";
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
  refs["fx-apply-count"].textContent = `${payload.result?.createdCount ?? payload.createdCount ?? state.clothes.length} 件`;
  refs["fx-payload-preview"].textContent = payload.applyPayload || "";
  
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
  refs["param-payload-preview"].textContent = payload.applyPayload || "";

  if (isDryRun) {
    refs["param-output"].textContent = "(Dry run) Diff 与 payload 预览如上，未执行实际回写。";
  } else {
    state.latestParameterSnapshotPath = payload.snapshotPath || state.latestParameterSnapshotPath;
    refs["param-output"].textContent = prettyJson(payload.result || payload);
  }
}

async function rollbackParameterOptimization() {
  const payload = await postJson("/api/parameters/rollback", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    snapshot_path: state.latestParameterSnapshotPath || null,
  });

  state.latestParameterSnapshotPath = payload.snapshotPath || state.latestParameterSnapshotPath;
  refs["param-output"].textContent = prettyJson(payload.result || payload);
}

async function scanShaderMaterials() {
  const payload = await postJson("/api/shader/materials/scan", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    category_overrides: state.shaderCategoryOverrides || {},
  });
  state.shaderInventory = payload.inventory || null;
  state.shaderMaterials = payload.materials || [];
  renderShaderMaterialInventory(payload.summary || {});
  refs["shader-output"].textContent = prettyJson({
    summary: payload.summary || {},
    jsonPath: payload.jsonPath || "",
  });
}

function renderShaderMaterialInventory(summary = {}) {
  const materials = state.shaderMaterials || [];
  refs["shader-material-count-chip"].textContent = `${materials.length} 个材质`;
  if (!materials.length) {
    refs["shader-materials-table"].innerHTML = '<div class="empty-state">还没有材质清单。点击“扫描材质”。</div>';
    return;
  }

  const rows = materials.map((item) => {
    const id = item.material_id || "";
    const category = state.shaderCategoryOverrides[id] || item.category || "unknown";
    const locked = (state.lockedShaderMaterials || []).includes(id);
    return `
      <tr>
        <td><code>${escapeHtml(item.item_path || item.renderer_path || "")}</code></td>
        <td>${escapeHtml(item.renderer_name || "")}</td>
        <td>${escapeHtml(item.mesh_name || "")}</td>
        <td>${Number(item.slot_index ?? 0)}</td>
        <td>${escapeHtml(item.material_name || "")}</td>
        <td>${escapeHtml(item.shader_family || "不支持")}</td>
        <td>
          <select class="shader-category-select" data-material-id="${escapeHtml(id)}">
            ${["skin", "eyes", "hair", "clothes", "accessory", "unknown"].map((option) => (
              `<option value="${option}" ${option === category ? "selected" : ""}>${SHADER_CATEGORY_LABELS[option] || option}</option>`
            )).join("")}
          </select>
        </td>
        <td>
          <label class="lock-toggle">
            <input class="shader-material-lock-toggle" data-material-id="${escapeHtml(id)}" type="checkbox" ${locked ? "checked" : ""}>
            <span>${locked ? "已锁定" : "可调整"}</span>
          </label>
        </td>
      </tr>
    `;
  }).join("");

  refs["shader-materials-table"].innerHTML = `
    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>对象路径</th>
            <th>渲染器</th>
            <th>网格</th>
            <th>槽位</th>
            <th>材质</th>
            <th>Shader 类型</th>
            <th>分类</th>
            <th>锁定</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="field-note">lilToon：${Number(summary.lilToonCount || 0)}，Poiyomi：${Number(summary.poiyomiCount || 0)}，不支持：${Number(summary.unsupportedCount || 0)}</div>
  `;
}

function onShaderMaterialCategoryChanged(event) {
  const lockToggle = event.target.closest(".shader-material-lock-toggle");
  if (lockToggle) {
    setShaderMaterialLock(lockToggle.dataset.materialId || "", lockToggle.checked).catch((error) => {
      refs["shader-output"].textContent = `材质锁定状态更新失败：${error.message}`;
    });
    return;
  }

  const select = event.target.closest(".shader-category-select");
  if (!select) {
    return;
  }
  const materialId = select.dataset.materialId || "";
  if (!materialId) {
    return;
  }
  state.shaderCategoryOverrides[materialId] = select.value;
  const material = (state.shaderMaterials || []).find((item) => item.material_id === materialId);
  if (material) {
    material.category = select.value;
  }
}

async function setShaderMaterialLock(materialId, locked) {
  if (!materialId) {
    return;
  }
  const set = new Set(state.lockedShaderMaterials || []);
  if (locked) {
    set.add(materialId);
  } else {
    set.delete(materialId);
  }
  const payload = await postJson("/api/shader/locks", {
    avatar_path: state.selectedAvatarPath || "",
    locked_materials: Array.from(set),
    locked_properties: state.lockedShaderProperties || [],
  });
  state.lockedShaderMaterials = payload.lockedMaterials || [];
  state.lockedShaderProperties = payload.lockedProperties || [];
  renderShaderMaterialInventory(state.shaderInventory?.summary || {});
}

function buildShaderTuningRequest() {
  flushReferencePathInputs();
  const referencePayload = buildReferenceImagePayload();
  return {
    ...buildConnectionPayload(),
    avatar: state.selectedAvatarPath || null,
    avatar_path: state.selectedAvatarPath || null,
    instruction: refs["shader-instruction-input"].value.trim() || refs["instruction-input"].value.trim() || null,
    model: readSelectedModel() || null,
    inventory: state.shaderInventory,
    category_overrides: state.shaderCategoryOverrides || {},
    locked_materials: state.lockedShaderMaterials || [],
    locked_properties: state.lockedShaderProperties || [],
    source_reference_image_paths: referencePayload.source.paths,
    source_reference_image_data_urls: referencePayload.source.dataUrls,
    target_reference_image_paths: referencePayload.target.paths,
    target_reference_image_data_urls: referencePayload.target.dataUrls,
    source_mode: refs["source-mode"].value,
    export_json: refs["export-json"].value.trim() || null,
    plan_json: refs["plan-json"].value.trim() || null,
    mock_execute: refs["mock-execute"].checked,
    save_artifacts: refs["save-artifacts"].checked,
  };
}

async function generateShaderPlan() {
  await ensureApiConfigSaved();
  if (!state.shaderInventory) {
    await scanShaderMaterials();
  }
  const payload = await postJson("/api/shader/plan", buildShaderTuningRequest());
  state.shaderInventory = payload.inventory || state.shaderInventory;
  state.shaderPlan = payload.plan || null;
  state.shaderPlanChanges = payload.changePreview || [];
  state.shaderHistoryRecord = payload.historyRecord || null;
  state.lockedShaderMaterials = payload.lockedMaterials || state.lockedShaderMaterials || [];
  state.lockedShaderProperties = payload.lockedProperties || state.lockedShaderProperties || [];
  renderShaderPlanPreview(payload);
  await loadShaderTuningData();
  refs["shader-output"].textContent = prettyJson({
    warnings: payload.warnings || [],
    skipped: payload.skippedChanges || [],
  });
}

async function applyShaderPlan() {
  const changes = state.shaderPlanChanges || state.shaderPlan?.changes || [];
  if (!changes.length) {
    refs["shader-output"].textContent = "没有可应用的有效材质计划。";
    return;
  }

  const payload = await postJson("/api/shader/apply", {
    ...buildShaderTuningRequest(),
    changes,
    history_id: state.shaderHistoryRecord?.id || null,
  });
  refs["shader-output"].textContent = prettyJson({
    applied: payload.appliedChanges || [],
    skipped: payload.skippedChanges || [],
    undoDepth: payload.undoDepth || 0,
  });
  await loadShaderTuningData();
}

async function restoreShaderPlan() {
  const payload = await postJson("/api/shader/restore", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
  });
  refs["shader-output"].textContent = prettyJson({
    restored: payload.restoredChanges || [],
    skipped: payload.skippedChanges || [],
    undoDepth: payload.undoDepth || 0,
  });
}

async function captureShaderReviewImage(kind) {
  const payload = await postJson("/api/vision/capture", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    width: 960,
    height: 960,
  });
  renderScreenshot(payload.imageUrl);
  const path = payload.imagePath || urlToArtifactPath(payload.imageUrl || "");
  if (kind === "before") {
    state.shaderReviewBeforePaths = [path];
  } else {
    state.shaderReviewAfterPaths = [path];
  }
  refs["shader-output"].textContent = `${kind === "before" ? "调整前" : "调整后"}材质复核截图已保存：${path}`;
}

async function checkVisionCaptureEnvironment() {
  const status = await postJson("/api/vision/capture-status", {
    ...buildConnectionPayload(),
    require_play_mode: false,
  });
  const notices = [];
  if (!status.isPlayMode) {
    notices.push(VISION_PLAY_MODE_GUIDANCE);
  } else if (!status.gestureManagerDetected) {
    notices.push(VISION_GESTURE_GUIDANCE);
  }
  const toolWarnings = Array.isArray(status.warnings) ? status.warnings.filter(Boolean) : [];
  for (const warning of toolWarnings) {
    if (!notices.includes(warning)) {
      notices.push(warning);
    }
  }
  if (notices.length) {
    const message = notices.join("\n");
    window.alert(message);
    refs["vision-result"].innerHTML = renderVisionNoticeHtml("截图环境提醒", notices);
    refs["vision-status-chip"].textContent = "建议检查";
  }
  return status;
}

function collectCaptureWarnings(capture) {
  const warnings = Array.isArray(capture?.warnings) ? capture.warnings.filter(Boolean) : [];
  return [...new Set(warnings)];
}

function renderVisionNoticeHtml(title, notices) {
  const items = Array.isArray(notices) ? notices.filter(Boolean) : [];
  return `
    <div class="info-card result-warn">
      <strong>${escapeHtml(title)}</strong>
      ${items.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
    </div>
  `;
}

function renderVisionCaptureResult(title, imagePath, capture) {
  const warnings = collectCaptureWarnings(capture);
  const modeLabel = capture?.captureMode === "game_view" ? "Play Mode / Game View" : "Static / Scene View";
  refs["vision-result"].innerHTML = `
    <div class="info-card ${warnings.length ? "result-warn" : ""}">
      <strong>${escapeHtml(title)}</strong>
      <span>${escapeHtml(imagePath || "")}</span>
      <p>${escapeHtml(modeLabel)}</p>
      ${warnings.map((item) => `<p>${escapeHtml(item)}</p>`).join("")}
    </div>
  `;
}

async function runShaderVisionReview() {
  const goal = refs["shader-instruction-input"].value.trim() || refs["instruction-input"].value.trim();
  const payload = await postJson("/api/shader/vision-review", {
    ...buildDashboardRequest(),
    avatar_path: state.selectedAvatarPath || null,
    goal,
    before_image_paths: state.shaderReviewBeforePaths || [],
    after_image_paths: state.shaderReviewAfterPaths || [],
  });
  const review = payload.review || {};
  refs["shader-output"].textContent = prettyJson(review);
}

function renderShaderPlanPreview(payload) {
  const changes = payload.changePreview || [];
  const skipped = payload.skippedChanges || [];
  if (!changes.length && !skipped.length) {
    refs["shader-plan-list"].classList.add("empty-state");
    refs["shader-plan-list"].innerHTML = "没有生成有效的材质改动。";
    return;
  }

  refs["shader-plan-list"].classList.remove("empty-state");
  refs["shader-plan-list"].innerHTML = `
    ${changes.map((item) => `
      <article class="change-row change-verified">
        <div class="change-head">
          <strong>${escapeHtml(item.material_name || item.material_id || "")}</strong>
          <span>${escapeHtml(SHADER_SEMANTIC_LABELS[item.semantic_property] || item.semantic_property || "")}</span>
        </div>
        <div class="blendshape-subline">${escapeHtml(SHADER_CATEGORY_LABELS[item.category] || item.category || "未分类")} / ${escapeHtml(item.shader_family || "")}</div>
        <p>${escapeHtml(String(item.before ?? ""))} -> ${escapeHtml(String(item.after ?? ""))}</p>
        <p>${escapeHtml(item.reason || "")}</p>
        <div class="change-meta">
          <span class="meta-chip">有效</span>
          <span class="meta-chip">置信度 ${Number(item.confidence ?? 0).toFixed(2)}</span>
        </div>
      </article>
    `).join("")}
    ${skipped.map((item) => `
      <article class="change-row change-unverified">
        <div class="change-head">
          <strong>${escapeHtml(item.material_name || item.material_id || "已跳过")}</strong>
          <span>${escapeHtml(SHADER_SEMANTIC_LABELS[item.semantic_property] || item.semantic_property || "")}</span>
        </div>
        <p>${escapeHtml(item.warning || "校验已跳过")}</p>
      </article>
    `).join("")}
  `;
}

async function loadShaderTuningData() {
  const query = state.selectedAvatarPath ? `?avatar_path=${encodeURIComponent(state.selectedAvatarPath)}` : "";
  const [historyPayload, presetPayload, lockPayload] = await Promise.all([
    getJson(`/api/shader/history${query}`),
    getJson(`/api/shader/presets${query}`),
    getJson(`/api/shader/locks${query}`),
  ]);
  state.shaderHistory = historyPayload.records || [];
  state.shaderPresets = presetPayload.presets || [];
  state.lockedShaderMaterials = lockPayload.lockedMaterials || [];
  state.lockedShaderProperties = lockPayload.lockedProperties || [];
  renderShaderHistory();
  renderShaderPresets();
}

function toggleShaderPanel(kind) {
  const panelId = kind === "history" ? "shader-history-panel" : "shader-preset-panel";
  refs[panelId].classList.toggle("hidden");
  loadShaderTuningData().catch((error) => {
    refs["shader-output"].textContent = `材质历史/预设加载失败：${error.message}`;
  });
}

function renderShaderHistory() {
  const records = [...(state.shaderHistory || [])].reverse();
  refs["shader-history-count"].textContent = `${records.length}`;
  if (!records.length) {
    refs["shader-history-list"].innerHTML = '<div class="empty-state">还没有材质调校历史。</div>';
    return;
  }

  refs["shader-history-list"].innerHTML = records.map((record) => {
    const changes = Array.isArray(record.changes) ? record.changes : [];
    return `
      <article class="tuning-card">
        <div class="change-head">
          <strong>${escapeHtml(formatTimestamp(record.created_at) || record.id)}</strong>
          <span>${changes.length} 项 / ${record.applied ? "已应用" : "待复核"}</span>
        </div>
        <p>${escapeHtml(record.user_instruction || "")}</p>
        <div class="change-meta">
          <span class="meta-chip">${escapeHtml(record.provider || "")}</span>
          <span class="meta-chip">${escapeHtml(record.model || "")}</span>
        </div>
        <div class="button-row compact-row">
          <button class="button button-ghost" data-shader-history-action="reapply" data-history-id="${escapeHtml(record.id)}">重放</button>
          <button class="button button-ghost" data-shader-history-action="preset" data-history-id="${escapeHtml(record.id)}">保存预设</button>
        </div>
      </article>
    `;
  }).join("");

  refs["shader-history-list"].querySelectorAll("[data-shader-history-action]").forEach((button) => {
    button.addEventListener("click", () => runInlineButtonTask(button, "处理中...", () => handleShaderHistoryAction(button)));
  });
}

function renderShaderPresets() {
  const presets = [...(state.shaderPresets || [])].reverse();
  refs["shader-preset-count"].textContent = `${presets.length}`;
  if (!presets.length) {
    refs["shader-preset-list"].innerHTML = '<div class="empty-state">还没有保存的材质预设。</div>';
    return;
  }

  refs["shader-preset-list"].innerHTML = presets.map((preset) => {
    const changes = Array.isArray(preset.changes) ? preset.changes : [];
    return `
      <article class="tuning-card">
        <div class="change-head">
          <strong>${escapeHtml(preset.name || preset.id)}</strong>
          <span>${changes.length} 项</span>
        </div>
        <p>${escapeHtml(preset.description || preset.user_instruction || "保存的材质 after 值预设")}</p>
        <div class="button-row compact-row">
          <button class="button button-accent" data-shader-preset-action="apply" data-preset-id="${escapeHtml(preset.id)}">应用</button>
          <button class="button button-ghost" data-shader-preset-action="rename" data-preset-id="${escapeHtml(preset.id)}">重命名</button>
          <button class="button button-ghost" data-shader-preset-action="duplicate" data-preset-id="${escapeHtml(preset.id)}">复制</button>
          <button class="button button-ghost danger-button" data-shader-preset-action="delete" data-preset-id="${escapeHtml(preset.id)}">删除</button>
        </div>
      </article>
    `;
  }).join("");

  refs["shader-preset-list"].querySelectorAll("[data-shader-preset-action]").forEach((button) => {
    button.addEventListener("click", () => runInlineButtonTask(button, "处理中...", () => handleShaderPresetAction(button)));
  });
}

async function handleShaderHistoryAction(button) {
  const id = button.dataset.historyId;
  if (button.dataset.shaderHistoryAction === "reapply") {
    const payload = await postJson(`/api/shader/history/${encodeURIComponent(id)}/reapply`, buildShaderTuningRequest());
    refs["shader-output"].textContent = prettyJson({
      applied: payload.appliedChanges || [],
      skipped: payload.skippedChanges || [],
    });
    await loadShaderTuningData();
    return;
  }
  if (button.dataset.shaderHistoryAction === "preset") {
    await saveShaderHistoryAsPreset(id);
  }
}

async function handleShaderPresetAction(button) {
  const id = button.dataset.presetId;
  const action = button.dataset.shaderPresetAction;
  if (action === "apply") {
    const payload = await postJson(`/api/shader/presets/${encodeURIComponent(id)}/apply`, buildShaderTuningRequest());
    refs["shader-output"].textContent = prettyJson({
      applied: payload.appliedChanges || [],
      skipped: payload.skippedChanges || [],
    });
  } else if (action === "rename") {
    const preset = (state.shaderPresets || []).find((item) => item.id === id);
    const name = window.prompt("材质预设名称", preset?.name || "");
    if (name && name.trim()) {
      await postJson(`/api/shader/presets/${encodeURIComponent(id)}/rename`, { name: name.trim() });
    }
  } else if (action === "duplicate") {
    const preset = (state.shaderPresets || []).find((item) => item.id === id);
    const name = window.prompt("复制后的材质预设名称", `${preset?.name || "shader_preset"}_copy`);
    await postJson(`/api/shader/presets/${encodeURIComponent(id)}/duplicate`, {
      name: name || null,
      max_presets: readPresetLimit(),
    });
  } else if (action === "delete") {
    if (window.confirm("删除这个材质预设？")) {
      await postJson(`/api/shader/presets/${encodeURIComponent(id)}/delete`);
    }
  }
  await loadShaderTuningData();
}

async function saveCurrentShaderPlanAsPreset() {
  const historyId = state.shaderHistoryRecord?.id;
  if (!historyId) {
    refs["shader-output"].textContent = "还没有可保存的材质历史。请先生成材质计划。";
    return;
  }
  await saveShaderHistoryAsPreset(historyId);
}

async function saveShaderHistoryAsPreset(historyId) {
  const name = window.prompt("材质预设名称", suggestShaderPresetName());
  if (!name || !name.trim()) {
    return;
  }
  const description = window.prompt("描述（可选）", "");
  const payload = await postJson("/api/shader/presets", {
    history_id: historyId,
    name: name.trim(),
    tags: [],
    description: description || "",
    max_presets: readPresetLimit(),
  });
  state.shaderPresets = payload.presets || state.shaderPresets;
  refs["shader-preset-panel"].classList.remove("hidden");
  renderShaderPresets();
  refs["shader-output"].textContent = `已保存材质预设：${payload.preset?.name || name.trim()}`;
}

function suggestShaderPresetName() {
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0"),
  ].join("");
  return `shader_tuning_${stamp}`;
}

async function captureScreenshot() {
  await checkVisionCaptureEnvironment();
  const payload = await postJson("/api/vision/capture", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    width: 960,
    height: 960,
  });
  renderScreenshot(payload.imageUrl);
  renderVisionCaptureResult("截图已更新", payload.imagePath, payload.capture);
  refs["vision-status-chip"].textContent = "待分析";
}

function renderScreenshot(imageUrl) {
  if (!imageUrl) {
    refs["vision-image"].classList.add("hidden");
    refs["vision-placeholder"].classList.remove("hidden");
    renderVisionAnnotations([]);
    return;
  }
  state.latestScreenshotUrl = imageUrl;
  refs["vision-image"].src = `${imageUrl}?t=${Date.now()}`;
  refs["vision-image"].classList.remove("hidden");
  refs["vision-placeholder"].classList.add("hidden");
  const audit = state.visionAuditsByImageUrl[imageUrl] || state.visionAuditsByImageUrl[urlToArtifactPath(imageUrl)];
  renderVisionAnnotations(audit?.annotations || []);
}

function renderVisionAnnotations(annotations) {
  const container = refs["vision-annotations"];
  const items = Array.isArray(annotations) ? annotations.filter(item => item && item.box) : [];
  if (!items.length) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }

  container.classList.remove("hidden");
  container.innerHTML = items.map((item) => {
    const box = item.box || {};
    const x = clampPercent(box.x);
    const y = clampPercent(box.y);
    const width = clampPercent(box.width);
    const height = clampPercent(box.height);
    const severity = normalizeSeverity(item.severity);
    return `
      <div class="vision-box severity-${severity}" style="left:${x}%;top:${y}%;width:${width}%;height:${height}%;">
        <span class="vision-box-label">${escapeHtml(item.label || "风险区域")}</span>
      </div>
    `;
  }).join("");
}

function normalizeSeverity(value) {
  const severity = String(value || "medium").toLowerCase();
  return ["low", "medium", "high"].includes(severity) ? severity : "medium";
}

function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return 0;
  }
  return Math.max(0, Math.min(100, number * 100));
}

async function auditVision() {
  const payload = await postJson("/api/vision/audit", {
    ...buildConnectionPayload(),
    image_path: state.latestScreenshotUrl ? urlToArtifactPath(state.latestScreenshotUrl) : null,
  });
  const audit = payload.audit || {};
  state.visionAuditsByImageUrl[payload.imageUrl || state.latestScreenshotUrl] = audit;
  state.visionAuditsByImageUrl[urlToArtifactPath(payload.imageUrl || state.latestScreenshotUrl)] = audit;
  renderVisionAnnotations(audit.annotations || []);
  refs["vision-status-chip"].textContent = audit.status === "pass" ? "通过" : "穿模";
  refs["vision-result"].innerHTML = `
    <article class="info-card ${audit.status === "pass" ? "result-pass" : "result-fail"}">
      <strong>${escapeHtml(audit.status === "pass" ? "通过" : "检测到穿模风险")}</strong>
      <span>${escapeHtml(audit.summary || "无结论")}</span>
      <p>${escapeHtml((audit.issues || []).join(" / ") || "无额外问题")}</p>
      ${audit.annotations?.length ? `<p>${audit.annotations.length} 个可定位区域已标注在截图上</p>` : ""}
    </article>
  `;
}

async function captureMultiScreenshot() {
  await checkVisionCaptureEnvironment();
  const payload = await postJson("/api/vision/capture-multi", {
    ...buildConnectionPayload(),
    avatar_path: state.selectedAvatarPath || null,
    width: 960,
    height: 960,
    angles: ["front", "side_left", "side_right", "back"]
  });
  state.multiScreenshots = payload.captures || payload.results || [];
  
  if (state.multiScreenshots.length > 0) {
    renderScreenshot(state.multiScreenshots[0].imageUrl);
    renderMultiThumbs();
    const warnings = state.multiScreenshots.flatMap((item) => collectCaptureWarnings(item.capture));
    renderVisionCaptureResult(
      "已捕获多视角截图",
      `共 ${state.multiScreenshots.length} 张`,
      { captureMode: state.multiScreenshots[0]?.capture?.captureMode, warnings }
    );
  refs["vision-status-chip"].textContent = "待分析";
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
    <div class="vision-thumb-card" data-index="${i}">
      <img src="${item.imageUrl}?t=${Date.now()}" alt="thumb">
      <div class="thumb-label">
        <strong>${escapeHtml(formatAngleLabel(item.angle))}</strong>
        <span>yaw ${Number(item.rotation?.yaw ?? item.capture?.yaw ?? 0).toFixed(0)}°</span>
      </div>
    </div>
  `).join("");
  container.querySelectorAll(".vision-thumb-card").forEach((card) => {
    card.addEventListener("click", () => showMultiScreenshot(Number(card.dataset.index)));
  });
}

function formatAngleLabel(angle) {
  const labels = {
    front: "正面",
    side_left: "左侧",
    side_right: "右侧",
    back: "背面",
  };
  return labels[angle] || angle || "视角";
}

function showMultiScreenshot(index) {
  const item = state.multiScreenshots[index];
  if (!item) {
    return;
  }
  renderScreenshot(item.imageUrl);
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
  for (const res of (payload.results || [])) {
    if (res.imageUrl && res.audit) {
      state.visionAuditsByImageUrl[res.imageUrl] = res.audit;
      state.visionAuditsByImageUrl[urlToArtifactPath(res.imageUrl)] = res.audit;
    }
  }
  
  let html = `
    <article class="info-card ${isPass ? "result-pass" : "result-fail"}">
      <strong>聚合分析结论: ${isPass ? "通过" : "穿模风险"}</strong>
    </article>
  `;
  
  for (const res of (payload.results || [])) {
    const a = res.audit || {};
    html += `
      <article class="info-card ${a.status === "pass" ? "" : "result-fail"}" style="margin-top:0.5rem">
        <strong>${escapeHtml(res.imagePath.split("/").pop())}: ${a.status}</strong>
        <span>${escapeHtml(a.summary || "")}</span>
        ${a.issues && a.issues.length ? `<p>${escapeHtml(a.issues.join(", "))}</p>` : ""}
        ${a.annotations && a.annotations.length ? `<p>${a.annotations.length} 个标注区域</p>` : ""}
      </article>
    `;
  }
  refs["vision-result"].innerHTML = html;
  const firstAnnotated = (payload.results || []).find(res => res.imageUrl && res.audit?.annotations?.length);
  if (firstAnnotated) {
    renderScreenshot(firstAnnotated.imageUrl);
  }
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
  const provider = refs["provider-select"].value;
  const previousProvider = state.currentProvider || state.apiConfig?.provider || provider;
  if (previousProvider !== provider) {
    rememberCurrentProviderDraft(previousProvider);
  }
  state.currentProvider = provider;
  const draft = state.providerDrafts[provider] || buildDefaultProviderDraft(provider);
  refs["api-key-input"].value = draft.api_key || "";
  refs["api-base-url"].value = draft.base_url || "";
  renderModelSelect(getCachedModelOptions(provider, draft.model), draft.model, !draft.model);
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
  delete state.modelOptionsByProvider[provider];
  renderModelSelect(getCachedModelOptions(provider, draft.model), draft.model, !draft.model);
  applyProviderFieldVisibility(provider);
  markApiConfigDirty();
}

function rememberCurrentProviderDraft(providerOverride = refs["provider-select"].value) {
  state.providerDrafts[providerOverride] = readApiForm(providerOverride);
}

function readApiForm(providerOverride = refs["provider-select"].value) {
  const provider = providerOverride;
  const preset = PROVIDER_PRESETS[provider] || PROVIDER_PRESETS.gemini;
  return {
    provider,
    api_key: refs["api-key-input"].value.trim(),
    base_url: (provider === "anthropic" || provider === "gemini") ? "" : (refs["api-base-url"].value.trim() || preset.base_url),
    model: readSelectedModel() || preset.model,
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

async function loadProviderModels() {
  const draft = readApiForm();
  refs["api-model-note"].textContent = "正在从 provider API 读取模型列表...";

  try {
    const payload = await postJson("/api/models", draft);
    const provider = payload.provider || draft.provider;
    const models = sanitizeModelOptions(payload.models || []);
    state.modelOptionsByProvider[provider] = models;
    renderModelSelect(models, payload.selectedModel || draft.model || models[0]?.id || "", false);
    rememberCurrentProviderDraft();
    setApiConfigDirty(true);
    refs["api-model-note"].textContent = models.length
      ? `已读取 ${models.length} 个模型，选择后保存即可生效。`
      : "没有读取到模型，可切换为手动输入。";
    refs["config-output"].textContent = prettyJson({
      provider,
      modelCount: models.length,
      selectedModel: readSelectedModel(),
    });
  } catch (error) {
    renderModelSelect([], draft.model, true);
    refs["api-model-note"].textContent = "模型列表读取失败，已切换为手动填写。";
    throw error;
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

function setSocketStatus(text, connected, reason = "") {
  refs["socket-status"].textContent = text;
  refs["socket-status"].className = connected ? "status-live" : "status-dead";
  renderConnectionNotice("Dashboard socket", connected, reason);
}

function selectProjectOption(projectPath) {
  if (projectPath) {
    refs["project-select"].value = projectPath;
  }
}

function setActiveAvatar(avatarName, avatarPath) {
  const avatarChanged = avatarPath && avatarPath !== state.selectedAvatarPath;
  state.selectedAvatarName = avatarName || "";
  state.selectedAvatarPath = avatarPath || "";
  if (avatarChanged) {
    state.currentHistoryRecord = null;
    state.lastAiChanges = [];
  }
  refs["status-avatar"].textContent = avatarName || "未加载";
  refs["avatar-path-display"].textContent = avatarPath || "未选择";
  if (refs["scene-avatar-select"] && avatarPath) {
    refs["scene-avatar-select"].value = avatarPath;
  }
  if (avatarPath) {
    loadTuningData().catch((error) => {
      refs["summary-output"].textContent = `历史/预设读取失败：${error.message}`;
    });
  }
}

function renderConnectionNotice(scope, connected, reason = "") {
  const container = refs["log-stream"];
  if (!container) {
    return;
  }
  state.connectionNotices[scope] = {
    connected,
    reason,
    timestamp: new Date().toISOString(),
  };
  const notices = Object.entries(state.connectionNotices);
  container.classList.remove("empty-state");
  container.innerHTML = notices.map(([noticeScope, notice]) => {
    const message = notice.connected
      ? `${noticeScope} 连接成功`
      : `${noticeScope} 连接未成功${notice.reason ? `：${notice.reason}` : ""}`;
    return `
    <article class="log-entry ${notice.connected ? "log-success" : "log-error"}">
      <div class="log-entry-head">
        <span class="log-scope">${escapeHtml(noticeScope)}</span>
        <span>${escapeHtml(formatTimestamp(notice.timestamp))}</span>
      </div>
      <p class="log-message">${escapeHtml(message)}</p>
    </article>
  `;
  }).join("");
}

function formatConnectionResult(scope, connected, reason = "") {
  if (connected) {
    return `${scope}: 连接成功`;
  }
  return `${scope}: 连接未成功${reason ? `\n原因：${reason}` : ""}`;
}

function getConnectionFailureReason(payload) {
  if (!payload) {
    return "没有收到后端响应";
  }
  const parsed = payload.parsed || {};
  return payload.error
    || payload.reason
    || payload.message
    || parsed.error
    || parsed.reason
    || parsed.message
    || "";
}

function clearLogView() {
  state.connectionNotices = {};
  refs["log-stream"].classList.add("empty-state");
  refs["log-stream"].textContent = "等待连接结果";
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

function applyUnityStatus(payload) {
  state.unityStatus = payload;
  const connected = Boolean(payload.connected);
  const missingTools = payload.missingRequiredVrcForgeTools || payload.tools?.missingRequiredVrcForgeTools || [];
  const hasInstances = Boolean(payload.unityInstanceRegistered || payload.activeInstanceCount);
  refs["unity-status-label"].textContent = connected
    ? (missingTools.length ? "已连接 / 工具缺失" : "已连接")
    : "未连接";
  refs["unity-status-light"].className = `light ${connected ? "light-on" : "light-off"}`;
  if (!refs["unity-instance"].value && payload.activeInstance?.sessionId) {
    refs["unity-instance"].value = payload.activeInstance.sessionId;
  }
  const lines = [
    formatConnectionResult("Unity MCP", connected, getConnectionFailureReason(payload)),
    `MCP Server: ${payload.mcpServerReachable ? "reachable" : "not reachable"}`,
    `Unity Instance: ${hasInstances ? `${payload.activeInstance?.project || payload.activeInstance?.sessionId || "active"} (${payload.activeInstanceCount || 1})` : "none"}`,
    `Tools: total ${payload.tools?.totalTools ?? 0}, VRCForge ${payload.tools?.vrcForgeToolsCount ?? 0}`,
  ];
  if (missingTools.length) {
    lines.push(`Missing VRCForge tools: ${missingTools.join(", ")}`);
  }
  refs["unity-status-output"].textContent = lines.join("\n");
  renderConnectionNotice("Unity MCP", connected, missingTools.length ? "VRCForge Unity tools missing or incomplete" : getConnectionFailureReason(payload));
}

function renderProjects(payload) {
  state.projects = payload.projects || [];
  state.selectedProjectPath = payload.selectedProjectPath || state.selectedProjectPath || "";
  refs["project-count"].textContent = `${state.projects.length} 个工程`;
  refs["project-select"].innerHTML = state.projects.map((project) => {
    const badges = [];
    if (project.activeMcp) badges.push("Active MCP");
    if (project.hasVrcForge) badges.push("VRCForge");
    if (project.hasUnityMcpPackage) badges.push("Unity MCP");
    if (project.sources?.length) badges.push(project.sources.join("+"));
    const suffix = badges.length ? ` / ${badges.join(" / ")}` : "";
    const disabled = project.selectable === false ? "disabled" : "";
    return `<option value="${escapeHtml(project.path || "")}" data-session-id="${escapeHtml(project.sessionId || "")}" data-project-name="${escapeHtml(project.name || "")}" data-active-mcp="${project.activeMcp ? "1" : "0"}" ${disabled}>${escapeHtml(project.name)} (${escapeHtml(project.editorVersion)})${escapeHtml(suffix)}</option>`;
  }).join("");
  selectProjectOption(state.selectedProjectPath);
}

async function onProjectSelected() {
  const selectedProject = refs["project-select"].value;
  const selectedOption = refs["project-select"].selectedOptions?.[0];
  const sessionId = selectedOption?.dataset?.sessionId || "";
  if (sessionId) {
    refs["unity-instance"].value = sessionId;
  } else if (selectedProject) {
    refs["unity-instance"].value = selectedOption?.dataset?.projectName || projectNameFromPath(selectedProject);
  }
  await syncDashboardState();
}

async function loadUnityTools() {
  const payload = await postJson("/api/unity/tools", buildConnectionPayload());
  const connected = Boolean(payload.reachable || payload.connected || payload.ok);
  const reason = getConnectionFailureReason(payload);
  const lines = [
    formatConnectionResult("Unity MCP tools", connected, reason),
    `Total tools: ${payload.totalTools ?? 0}`,
    `Default/CoplayDev tools: ${payload.defaultToolsCount ?? 0}`,
    `VRCForge tools: ${payload.vrcForgeToolsCount ?? 0}`,
    `Active instance: ${payload.instance || refs["unity-instance"].value || "(auto)"}`,
  ];
  if (payload.missingRequiredVrcForgeTools?.length) {
    lines.push(`Missing required VRCForge tools: ${payload.missingRequiredVrcForgeTools.join(", ")}`);
  }
  if (payload.vrcForgeToolNames?.length) {
    lines.push(`VRCForge tool names: ${payload.vrcForgeToolNames.join(", ")}`);
  }
  refs["unity-status-output"].textContent = lines.join("\n");
  renderConnectionNotice("Unity MCP tools", connected && !payload.onlyDefaultTools, payload.onlyDefaultTools ? "Unity MCP connected, but VRCForge tools are missing" : reason);
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
