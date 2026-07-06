import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AppBootstrap,
  ProviderModelInfo,
  fetchProviderModels,
  testProviderCapability,
  updateApiConfig,
  updateVisionConfig,
} from "../lib/api";
import { defaultBaseUrlForProvider, defaultModelForProvider, providerDisplayName, providerNeedsApiKey } from "../lib/provider-ui";

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
  const [savingApiConfig, setSavingApiConfig] = useState(false);
  const [visionProvider, setVisionProvider] = useState("");
  const [visionApiKey, setVisionApiKey] = useState("");
  const [visionBaseUrl, setVisionBaseUrl] = useState("");
  const [visionModel, setVisionModel] = useState("");
  const [visionEnabled, setVisionEnabled] = useState(true);
  const [savingVisionConfig, setSavingVisionConfig] = useState(false);
  const [modelOptions, setModelOptions] = useState<ProviderModelInfo[]>([]);
  const [modelOptionsScope, setModelOptionsScope] = useState<ModelOptionsScope | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [testingProvider, setTestingProvider] = useState("");
  const [providerTestMessage, setProviderTestMessage] = useState("");

  useEffect(() => {
    if (!apiConfig) {
      return;
    }
    setApiProvider(apiConfig.provider || "gemini");
    setApiBaseUrl(apiConfig.base_url || "");
    setApiModel(apiConfig.model || defaultModelForProvider(apiConfig.provider || "gemini"));
    setModelOptions([]);
    setModelOptionsScope(null);
  }, [apiConfig?.provider, apiConfig?.base_url, apiConfig?.model]);

  useEffect(() => {
    if (!visionConfig) {
      return;
    }
    setVisionProvider(visionConfig.provider || "");
    setVisionBaseUrl(visionConfig.base_url || "");
    setVisionModel(visionConfig.model || "");
    setVisionEnabled(visionConfig.enabled !== false);
  }, [visionConfig?.provider, visionConfig?.base_url, visionConfig?.model, visionConfig?.enabled]);

  const apiKeySaved = Boolean(apiConfig?.apiKeyPresent && (apiConfig?.provider || "") === apiProvider);
  const savedProvider = apiConfig?.provider || apiProvider;
  const savedProviderLabel = apiConfig?.providerLabel || providerDisplayName(savedProvider);
  const savedModel = apiConfig?.model || apiModel || defaultModelForProvider(savedProvider);
  const savedBaseUrl = apiConfig?.base_url || apiBaseUrl;
  const providerConfigured = runtimeConnected && Boolean(apiConfig) && (!apiConfig?.apiKeyRequired || Boolean(apiConfig?.apiKeyPresent));
  const providerSnapshot: ProviderSnapshot = {
    provider: savedProvider,
    providerLabel: savedProviderLabel,
    model: savedModel,
  };

  async function ensureRuntime(): Promise<string | null> {
    if (runtimeConnected) {
      return endpoint;
    }
    return startRuntime();
  }

  async function saveApiProvider(event?: FormEvent) {
    event?.preventDefault();
    if (!apiProvider || !apiModel || (providerNeedsApiKey(apiProvider) && !apiKey.trim() && !apiKeySaved)) {
      return;
    }
    setSavingApiConfig(true);
    setError("");
    try {
      const targetEndpoint = await ensureRuntime();
      if (!targetEndpoint) {
        return;
      }
      await updateApiConfig(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
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
    setApiProvider(provider);
    setApiModel(defaultModelForProvider(provider));
    setApiBaseUrl(defaultBaseUrlForProvider(provider));
    setModelOptions([]);
    setModelOptionsScope(null);
    setModelsError("");
  }

  function handleVisionProviderChange(provider: string) {
    setVisionProvider(provider);
    setVisionModel(provider ? defaultModelForProvider(provider) : "");
    setVisionBaseUrl(provider ? defaultBaseUrlForProvider(provider) : "");
  }

  async function saveVisionProfile(event?: FormEvent) {
    event?.preventDefault();
    const visionKeySaved = Boolean(visionConfig?.apiKeyPresent && (visionConfig?.provider || "") === visionProvider);
    if (visionProvider && !visionModel.trim()) {
      return;
    }
    if (visionProvider && providerNeedsApiKey(visionProvider) && !visionApiKey.trim() && !visionKeySaved) {
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
      setVisionEnabled(true);
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
      });
      const models = payload.models || [];
      setModelOptions(models);
      setModelOptionsScope({ provider: payload.provider || apiProvider, baseUrl: payload.baseUrl || apiBaseUrl.trim() });
      if (models.length === 0) {
        setModelsError(t("provider.noModelsReturned"));
      } else if (!models.some((item) => item.id === apiModel)) {
        setApiModel(payload.selectedModel && models.some((item) => item.id === payload.selectedModel) ? payload.selectedModel : models[0].id);
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
    if (testingProvider) {
      return;
    }
    setTestingProvider(capability);
    setProviderTestMessage("");
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
        capability,
      });
      setProviderTestMessage(`${payload.capability}: ${payload.status} - ${payload.message}`);
      if (!payload.ok && payload.status !== "skipped") {
        setModelsError(payload.message);
      }
    } catch (cause) {
      setModelsError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setTestingProvider("");
    }
  }

  return {
    apiProvider,
    setApiProvider,
    apiKey,
    setApiKey,
    apiBaseUrl,
    setApiBaseUrl,
    apiModel,
    setApiModel,
    apiKeySaved,
    savingApiConfig,
    modelOptions,
    modelOptionsScope,
    loadingModels,
    modelsError,
    testingProvider,
    providerTestMessage,
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
    handleVisionProviderChange,
    saveVisionProfile,
    clearVisionProfile,
    loadModels,
    runProviderTest,
  };
}
