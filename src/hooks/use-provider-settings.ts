import { FormEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AppBootstrap,
  ProviderModelInfo,
  ProviderApiType,
  ProviderReasoningVariants,
  fetchProviderModels,
  fetchReasoningVariants,
  testProviderCapability,
  updateApiConfig,
  updateVisionConfig,
} from "../lib/api";
import { defaultBaseUrlForProvider, defaultModelForProvider, providerDisplayName, providerNeedsApiKey } from "../lib/provider-ui";
import { hasSavedProviderKey } from "../lib/provider-key-state";
import { supportedApiTypeOptions } from "../lib/provider-protocol-options";

type ApiConfig = AppBootstrap["apiConfig"];
type VisionConfig = AppBootstrap["visionConfig"];

export type ProviderSnapshot = {
  provider: string;
  providerLabel: string;
  model: string;
};

export type ModelOptionsScope = {
  provider: string;
  baseUrl: string;
};

type UseProviderSettingsParams = {
  endpoint: string;
  runtimeConnected: boolean;
  apiConfig?: ApiConfig;
  visionConfig?: VisionConfig;
  startRuntime: () => Promise<string | null>;
  refresh: (target?: string) => Promise<void>;
  setError: (message: string) => void;
};

export function useProviderSettings({
  endpoint,
  runtimeConnected,
  apiConfig,
  visionConfig,
  startRuntime,
  refresh,
  setError,
}: UseProviderSettingsParams) {
  const { t } = useTranslation();
  const [apiProvider, setApiProvider] = useState("gemini");
  const [apiKey, setApiKey] = useState("");
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [apiModel, setApiModel] = useState("gemini-2.5-flash");
  const [apiType, setApiType] = useState<ProviderApiType>("auto");
  const [apiContextWindow, setApiContextWindow] = useState("");
  // Provider default (send nothing) is distinct from an explicit `none` variant.
  const [apiThinkingLevel, setApiThinkingLevel] = useState("default");
  const [reasoningVariants, setReasoningVariants] = useState<ProviderReasoningVariants | null>(null);
  const [savingApiConfig, setSavingApiConfig] = useState(false);
  const [visionProvider, setVisionProvider] = useState("");
  const [visionApiKey, setVisionApiKey] = useState("");
  const [visionBaseUrl, setVisionBaseUrl] = useState("");
  const [visionModel, setVisionModel] = useState("");
  // Dedicated image routing is opt-in. When disabled, the current chat model
  // receives image attachments directly.
  const [visionEnabled, setVisionEnabled] = useState(false);
  const [savingVisionConfig, setSavingVisionConfig] = useState(false);
  const [modelOptions, setModelOptions] = useState<ProviderModelInfo[]>([]);
  const [modelOptionsScope, setModelOptionsScope] = useState<ModelOptionsScope | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [testingProvider, setTestingProvider] = useState("");
  const [providerTestMessage, setProviderTestMessage] = useState("");
  const [providerTestPassed, setProviderTestPassed] = useState(false);
  const [providerTestFingerprint, setProviderTestFingerprint] = useState("");
  const [providerTestCredentialFingerprint, setProviderTestCredentialFingerprint] = useState("");
  const [providerTestEndpoint, setProviderTestEndpoint] = useState("");
  const providerTestGeneration = useRef(0);
  const previousExplicitProtocol = useRef<{ provider: string; apiType: ProviderApiType } | null>(null);

  useEffect(() => {
    if (!apiConfig) {
      return;
    }
    // A probe must never outlive the persisted provider credentials it tested.
    // Invalidate its generation before a late response can repopulate green
    // state for a changed account or saved key.
    providerTestGeneration.current += 1;
    setTestingProvider("");
    setApiProvider(apiConfig.provider || "gemini");
    setApiBaseUrl(apiConfig.base_url || "");
    setApiModel(apiConfig.model || defaultModelForProvider(apiConfig.provider || "gemini"));
    setApiType(apiConfig.api_type || apiConfig.apiType || "auto");
    previousExplicitProtocol.current = null;
    setApiContextWindow(apiConfig.contextWindow ? String(Math.round(apiConfig.contextWindow / 1000)) : "");
    setApiThinkingLevel(apiConfig.thinking_level || "default");
    setModelOptions([]);
    setModelOptionsScope(null);
    setProviderTestPassed(false);
    setProviderTestFingerprint("");
    setProviderTestCredentialFingerprint("");
    setProviderTestEndpoint("");
  }, [apiConfig?.provider, apiConfig?.base_url, apiConfig?.model, apiConfig?.api_type, apiConfig?.apiType, apiConfig?.thinking_level, apiConfig?.contextWindow, apiConfig?.credentialFingerprint, apiConfig?.apiKeyPresent, apiConfig?.configured]);

  useEffect(() => {
    // A probe belongs to one runtime endpoint. Reconnecting or switching the
    // managed runtime invalidates its result before a late response can be
    // displayed as current.
    providerTestGeneration.current += 1;
    setTestingProvider("");
    setProviderTestPassed(false);
    setProviderTestFingerprint("");
    setProviderTestCredentialFingerprint("");
    setProviderTestEndpoint("");
  }, [endpoint, runtimeConnected]);

  useEffect(() => {
    if (!runtimeConnected || !apiConfig?.configured || apiProvider !== apiConfig.provider
        || apiModel !== apiConfig.model) return;
    let active = true;
    // Capacity is metadata, not a generation probe. Load it for the saved model
    // so the chat meter works without opening Settings and pressing Load models.
    void fetchProviderModels(endpoint, {
      provider: apiConfig.provider, api_key: "", base_url: apiConfig.base_url || "",
      model: apiConfig.model, api_type: apiConfig.api_type || apiConfig.apiType,
    }).then((payload) => {
      if (!active) return;
      setModelOptions(payload.models || []);
      setModelOptionsScope({ provider: payload.provider || apiConfig.provider, baseUrl: payload.baseUrl || apiConfig.base_url || "" });
    }).catch(() => { /* Unavailable metadata remains unknown; never invent a cap. */ });
    return () => { active = false; };
  }, [endpoint, runtimeConnected, apiConfig?.configured, apiConfig?.provider, apiConfig?.model,
    apiConfig?.base_url, apiConfig?.api_type, apiConfig?.apiType, apiConfig?.credentialFingerprint, apiProvider, apiModel]);

  useEffect(() => {
    let cancelled = false;
    if (!runtimeConnected || !endpoint || !apiProvider || apiProvider === "chatgpt" || !apiModel.trim()) {
      setReasoningVariants(null);
      return () => {
        cancelled = true;
      };
    }
    void fetchReasoningVariants(endpoint, { provider: apiProvider, model: apiModel.trim(), api_type: apiType })
      .then((descriptor) => {
        if (cancelled) {
          return;
        }
        setReasoningVariants(descriptor);
      })
      .catch(() => {
        if (!cancelled) {
          setReasoningVariants(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [endpoint, runtimeConnected, apiProvider, apiModel, apiType]);

  useEffect(() => {
    if (!visionConfig) {
      return;
    }
    setVisionProvider(visionConfig.provider || "");
    setVisionBaseUrl(visionConfig.base_url || "");
    setVisionModel(visionConfig.model || "");
    setVisionEnabled(visionConfig.enabled === true);
  }, [visionConfig?.provider, visionConfig?.base_url, visionConfig?.model, visionConfig?.enabled]);

  const apiKeySaved = hasSavedProviderKey(apiProvider, apiConfig, visionConfig);
  const savedProvider = apiConfig?.provider || apiProvider;
  const savedProviderLabel = apiConfig?.providerLabel || providerDisplayName(savedProvider);
  const savedModel = apiConfig?.model || apiModel || defaultModelForProvider(savedProvider);
  const savedBaseUrl = apiConfig?.base_url || apiBaseUrl;
  const providerConfigured = runtimeConnected && apiConfig?.configured === true;
  const providerSnapshot: ProviderSnapshot = {
    provider: savedProvider,
    providerLabel: savedProviderLabel,
    model: savedModel,
  };
  const persistedProviderFingerprint = apiConfig
    ? providerFingerprint({ provider: apiConfig.provider || "gemini", baseUrl: apiConfig.base_url || "", model: apiConfig.model || defaultModelForProvider(apiConfig.provider || "gemini"), apiType: apiConfig.api_type || apiConfig.apiType || "auto" })
    : "";
  const persistedProviderConfigured = apiConfig?.configured === true;
  const selectedModelInfo = modelOptions.find((item) => item.id === apiModel);
  const selectedModelCapabilities = selectedModelInfo?.capabilities ??
    (apiConfig?.provider === apiProvider && apiConfig?.model === apiModel ? apiConfig.capabilities : undefined);
  const selectedModelCapabilitySource = selectedModelInfo?.capabilitySource ??
    (apiConfig?.provider === apiProvider && apiConfig?.model === apiModel ? apiConfig.capabilitySource : undefined);

  async function ensureRuntime(): Promise<string | null> {
    if (runtimeConnected) {
      return endpoint;
    }
    return startRuntime();
  }

  async function saveApiProvider(event?: FormEvent) {
    event?.preventDefault();
    if (apiProvider === "deepseek" && apiModel.trim() === "deepseek-auto") {
      setModelsError(t("provider.selectModel"));
      return;
    }
    const normalizedContextWindow = normalizeContextWindowK(apiContextWindow);
    if (
      normalizedContextWindow === null
      || !apiProvider
      || apiProvider === "chatgpt"
      || !apiModel
      || (providerNeedsApiKey(apiProvider) && !apiKey.trim() && !apiKeySaved)
    ) {
      return;
    }
    setSavingApiConfig(true);
    setError("");
    try {
      const targetEndpoint = await ensureRuntime();
      if (!targetEndpoint) {
        return;
      }
      // A successful save changes the credential binding even when provider,
      // model and endpoint stay identical. Require a fresh probe for the new
      // persisted configuration.
      invalidateProviderTest();
      await updateApiConfig(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
        api_type: apiType,
        thinking_level: apiThinkingLevel === "default" ? "" : apiThinkingLevel,
        context_window: normalizedContextWindow,
      });
      setApiKey("");
      await refresh(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingApiConfig(false);
    }
  }

  function handleProviderChange(provider: string) {
    previousExplicitProtocol.current = null;
    setApiProvider(provider);
    setApiModel(defaultModelForProvider(provider));
    setApiType("auto");
    setApiBaseUrl(defaultBaseUrlForProvider(provider));
    setApiThinkingLevel("default");
    setReasoningVariants(null);
    setModelOptions([]);
    setModelOptionsScope(null);
    setModelsError("");
    invalidateProviderTest();
  }

  function handleApiModelChange(model: string) {
    setApiModel(model);
    invalidateProviderTest();
  }

  function handleProtocolAutoNegotiationChange(enabled: boolean) {
    const explicitOptions = supportedApiTypeOptions(apiProvider, apiModel).filter((option) => option !== "auto");
    if (enabled) {
      if (apiType !== "auto" && explicitOptions.includes(apiType)) {
        previousExplicitProtocol.current = { provider: apiProvider, apiType };
      }
      setApiType("auto");
    } else {
      const previous = previousExplicitProtocol.current;
      setApiType(previous?.provider === apiProvider && previous.apiType !== "auto" && explicitOptions.includes(previous.apiType)
        ? previous.apiType
        : explicitOptions[0] || "auto");
    }
    invalidateProviderTest();
  }

  function invalidateProviderTest() {
    providerTestGeneration.current += 1;
    setTestingProvider("");
    setProviderTestPassed(false);
    setProviderTestFingerprint("");
    setProviderTestCredentialFingerprint("");
    setProviderTestEndpoint("");
  }

  function handleApiKeyChange(value: string) {
    setApiKey(value);
    invalidateProviderTest();
  }

  function handleApiBaseUrlChange(value: string) {
    setApiBaseUrl(value);
    invalidateProviderTest();
  }

  function handleApiTypeChange(value: ProviderApiType) {
    setApiType(value);
    invalidateProviderTest();
  }

  function handleApiThinkingLevelChange(value: string) {
    setApiThinkingLevel(value);
    invalidateProviderTest();
  }

  function handleVisionProviderChange(provider: string) {
    setVisionProvider(provider);
    setVisionModel(provider ? defaultModelForProvider(provider) : "");
    setVisionBaseUrl(provider ? defaultBaseUrlForProvider(provider) : "");
  }

  async function saveVisionProfile(event?: FormEvent) {
    event?.preventDefault();
    const visionKeySaved = hasSavedProviderKey(visionProvider, visionConfig, apiConfig);
    if (visionEnabled && visionProvider && !visionModel.trim()) {
      return;
    }
    if (visionEnabled && visionProvider && providerNeedsApiKey(visionProvider) && !visionApiKey.trim() && !visionKeySaved) {
      return;
    }
    setSavingVisionConfig(true);
    setError("");
    try {
      const targetEndpoint = await ensureRuntime();
      if (!targetEndpoint) {
        return;
      }
      await updateVisionConfig(targetEndpoint, {
        provider: visionProvider,
        api_key: visionApiKey.trim(),
        base_url: visionBaseUrl.trim(),
        model: visionModel.trim(),
        enabled: visionEnabled,
      });
      setVisionApiKey("");
      await refresh(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingVisionConfig(false);
    }
  }

  async function clearVisionProfile() {
    setSavingVisionConfig(true);
    setError("");
    try {
      await updateVisionConfig(endpoint, { provider: "", api_key: "", base_url: "", model: "", enabled: false });
      setVisionProvider("");
      setVisionApiKey("");
      setVisionBaseUrl("");
      setVisionModel("");
      setVisionEnabled(false);
      await refresh(endpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingVisionConfig(false);
    }
  }

  async function loadModels() {
    if (loadingModels) {
      return;
    }
    setLoadingModels(true);
    setModelsError("");
    try {
      const targetEndpoint = await ensureRuntime();
      if (!targetEndpoint) {
        setModelsError(t("provider.coreDisconnectedModels"));
        return;
      }
      const payload = await fetchProviderModels(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
        api_type: apiType,
      });
      const models = payload.models || [];
      setModelOptions(models);
      setModelOptionsScope({ provider: payload.provider || apiProvider, baseUrl: payload.baseUrl || apiBaseUrl.trim() });
      if (models.length === 0) {
        setModelsError(t("provider.noModelsReturned"));
      } else if (!apiModel.trim()) {
        handleApiModelChange(payload.selectedModel && models.some((item) => item.id === payload.selectedModel) ? payload.selectedModel : models[0].id);
      }
    } catch (cause) {
      setModelOptions([]);
      setModelOptionsScope(null);
      setModelsError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingModels(false);
    }
  }

  async function runProviderTest(capability: "text" | "structured" | "vision") {
    if (apiProvider === "deepseek" && apiModel.trim() === "deepseek-auto") {
      setModelsError(t("provider.selectModel"));
      return;
    }
    if (testingProvider) {
      return;
    }
    setTestingProvider(capability);
    const generation = ++providerTestGeneration.current;
    setProviderTestMessage("");
    setProviderTestPassed(false);
    setProviderTestFingerprint("");
    setModelsError("");
    try {
      const targetEndpoint = await ensureRuntime();
      if (!targetEndpoint) {
        setModelsError("Runtime is not connected.");
        return;
      }
      const payload = await testProviderCapability(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
        api_type: apiType,
        thinking_level: apiThinkingLevel === "default" ? "" : apiThinkingLevel,
        capability,
      });
      const attempts = payload.attempts?.map((item) => `${item.model} / ${item.apiType}: ${item.status}`).join("; ");
      if (generation !== providerTestGeneration.current) {
        return;
      }
      setProviderTestMessage(`${payload.capability}: ${payload.status} - ${payload.message}${attempts ? ` (${attempts})` : ""}`);
      const passed = capability === "text" && Boolean(payload.ok);
      setProviderTestPassed(passed);
      setProviderTestFingerprint(passed ? providerFingerprint({ provider: apiProvider, baseUrl: apiBaseUrl, model: apiModel, apiType }) : "");
      setProviderTestCredentialFingerprint(passed
        ? payload.credentialFingerprint || (!apiKey.trim() ? apiConfig?.credentialFingerprint || "" : "")
        : "");
      setProviderTestEndpoint(passed ? targetEndpoint : "");
      if (!payload.ok && payload.status !== "skipped") {
        setModelsError(payload.message);
      }
    } catch (cause) {
      setModelsError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (generation === providerTestGeneration.current) {
        setTestingProvider("");
      }
    }
  }

  return {
    apiProvider,
    setApiProvider,
    apiKey,
    setApiKey: handleApiKeyChange,
    apiBaseUrl,
    setApiBaseUrl: handleApiBaseUrlChange,
    apiModel,
    setApiModel: handleApiModelChange,
    apiType,
    setApiType: handleApiTypeChange,
    apiContextWindow,
    setApiContextWindow,
    selectedModelCapabilities,
    selectedModelCapabilitySource,
    apiThinkingLevel,
    setApiThinkingLevel: handleApiThinkingLevelChange,
    reasoningVariants,
    apiKeySaved,
    savingApiConfig,
    modelOptions,
    modelOptionsScope,
    loadingModels,
    modelsError,
    testingProvider,
    providerTestMessage,
    providerTestPassed: providerTestPassed && providerTestFingerprint === providerFingerprint({ provider: apiProvider, baseUrl: apiBaseUrl, model: apiModel, apiType }) && providerTestEndpoint === endpoint,
    providerTestPassedForPersistedConfig: providerTestPassed
      && runtimeConnected
      && persistedProviderConfigured
      && !apiKey.trim()
      && providerTestFingerprint === persistedProviderFingerprint
      && providerTestEndpoint === endpoint
      && apiConfig?.credentialFingerprint !== undefined
      && providerTestCredentialFingerprint === apiConfig.credentialFingerprint,
    providerReadyForOnboarding: providerTestPassed
      && runtimeConnected
      && persistedProviderConfigured
      && !apiKey.trim()
      && providerTestFingerprint === persistedProviderFingerprint
      && providerTestEndpoint === endpoint
      && apiConfig?.credentialFingerprint !== undefined
      && providerTestCredentialFingerprint === apiConfig.credentialFingerprint,
    visionProvider,
    setVisionProvider,
    visionApiKey,
    setVisionApiKey,
    visionBaseUrl,
    setVisionBaseUrl,
    visionModel,
    setVisionModel,
    visionEnabled,
    setVisionEnabled,
    savingVisionConfig,
    savedProviderLabel,
    savedBaseUrl,
    providerConfigured,
    providerSnapshot,
    saveApiProvider,
    handleProviderChange,
    handleProtocolAutoNegotiationChange,
    handleVisionProviderChange,
    saveVisionProfile,
    clearVisionProfile,
    loadModels,
    runProviderTest,
  };
}

function normalizeContextWindowK(value: string): number | null {
  if (!value.trim()) {
    return 0;
  }
  const thousands = Number(value);
  if (!Number.isInteger(thousands) || thousands < 4 || thousands > 10_000) {
    return null;
  }
  return thousands * 1000;
}

function providerFingerprint(config: { provider: string; baseUrl: string; model: string; apiType: ProviderApiType }): string {
  return [config.provider, config.baseUrl.trim(), config.model.trim(), config.apiType].join("\u001f");
}
