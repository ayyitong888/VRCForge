import { Check, Eye, Loader2, MessageSquare, RefreshCw } from "lucide-react";
import type { FormEvent, ReactNode } from "react";
import i18n from "../../i18n";
import type { ProviderApiType, ProviderModelInfo, ProviderReasoningVariants } from "../../lib/api";
import { providerCapabilities, providerNeedsApiKey } from "../../lib/provider-ui";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

type ProviderSetupProps = {
  provider: string;
  apiKey: string;
  baseUrl: string;
  model: string;
  apiType: ProviderApiType;
  contextWindow: string;
  modelCapabilities?: readonly string[];
  capabilitySource?: string;
  /** Backend-resolved reasoning variant; `default` sends no override. */
  thinkingLevel: string;
  reasoningVariants: ProviderReasoningVariants | null;
  saving: boolean;
  models: ProviderModelInfo[];
  loadingModels: boolean;
  modelsError: string;
  testingProvider: string;
  providerTestMessage: string;
  runtimeConnected: boolean;
  keySaved?: boolean;
  onLoadModels: () => void;
  onTestProvider: (capability: "text" | "structured" | "vision") => void;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onApiTypeChange: (value: ProviderApiType) => void;
  onContextWindowChange: (value: string) => void;
  onThinkingLevelChange: (value: string) => void;
  onSubmit: (event?: FormEvent) => void;
};

type VisionProfileSetupProps = {
  provider: string;
  apiKey: string;
  baseUrl: string;
  model: string;
  enabled: boolean;
  saving: boolean;
  runtimeConnected: boolean;
  keySaved?: boolean;
  configured?: boolean;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onEnabledChange: (value: boolean) => void;
  onSubmit: (event?: FormEvent) => void;
  onClear: () => void;
};

const PROVIDERS_REQUIRING_BASE_URL = ["openai", "deepseek", "openrouter", "ollama", "vertexai", "custom"];
export function ProviderSetup({
  provider,
  apiKey,
  baseUrl,
  model,
  apiType,
  contextWindow,
  modelCapabilities,
  capabilitySource,
  thinkingLevel,
  reasoningVariants,
  saving,
  models,
  loadingModels,
  modelsError,
  testingProvider,
  providerTestMessage,
  runtimeConnected,
  keySaved = false,
  onLoadModels,
  onTestProvider,
  onProviderChange,
  onApiKeyChange,
  onBaseUrlChange,
  onModelChange,
  onApiTypeChange,
  onContextWindowChange,
  onThinkingLevelChange,
  onSubmit,
}: ProviderSetupProps) {
  const requiresBaseUrl = PROVIDERS_REQUIRING_BASE_URL.includes(provider);
  const supportedReasoningVariants = reasoningVariants?.variants ?? [];
  const supportsReasoning = supportedReasoningVariants.length > 0;
  const hasUnsupportedReasoningVariant =
    thinkingLevel !== "default" && !supportedReasoningVariants.some((variant) => variant.key === thinkingLevel);
  const displayModels = provider === "deepseek"
    ? [
        { id: "deepseek-auto", label: i18n.t("provider.deepseekAutoModel") } as ProviderModelInfo,
        ...models.filter((item) => item.id !== "deepseek-auto"),
      ]
    : models;
  const hasModelList = models.length > 0;
  const capabilities = providerCapabilities(modelCapabilities, capabilitySource);
  const apiTypeOptions = supportedApiTypeOptions(provider, model);
  const selectedModelInfo = displayModels.find((item) => item.id === model);
  const detectedContextWindow = firstPositiveContextWindow(selectedModelInfo);
  const contextWindowK = contextWindow.trim() ? Number(contextWindow) : 0;
  const contextWindowValid = !contextWindow.trim()
    || (Number.isInteger(contextWindowK) && contextWindowK >= 4 && contextWindowK <= 10_000);
  const sliderMax = Math.min(10_000, Math.max(1_000, Math.ceil((detectedContextWindow || 0) / 1000), contextWindowK || 0));
  const sliderValue = contextWindowValid && contextWindowK > 0
    ? Math.min(sliderMax, contextWindowK)
    : Math.min(sliderMax, Math.max(4, Math.ceil((detectedContextWindow || 128_000) / 1000)));

  return (
    <form onSubmit={onSubmit} className="rounded-2xl border border-border bg-card p-5 shadow-composer">
      <div className="grid gap-4">
        <SettingsFieldLabel label={i18n.t("provider.apiProvider")}>
          <select
            value={provider}
            onChange={(event) => onProviderChange(event.target.value)}
            className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
          >
            <option value="gemini">Google AI Studio</option>
            <option value="anthropic">Anthropic</option>
            <option value="openai">OpenAI</option>
            <option value="deepseek">DeepSeek</option>
            <option value="openrouter">OpenRouter</option>
            <option value="ollama">Ollama</option>
            <option value="vertexai">Vertex AI</option>
            <option value="custom">{i18n.t("provider.customEndpoint")}</option>
          </select>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {capabilities.map((capability) => (
              <Badge key={capability.label} tone={capability.tone} className="h-6 px-2 text-[10px]">
                {capability.label}
              </Badge>
            ))}
          </div>
        </SettingsFieldLabel>
        <SettingsFieldLabel label={i18n.t("provider.apiKey")}>
          {providerNeedsApiKey(provider) ? (
            <input
              value={apiKey}
              onChange={(event) => onApiKeyChange(event.target.value)}
              type="password"
              placeholder={keySaved ? i18n.t("provider.savedKeyHint") : i18n.t("provider.apiKeyPlaceholder")}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary"
              autoComplete="off"
            />
          ) : (
            <input
              value={i18n.t("provider.noKeyNeeded")}
              readOnly
              className="h-10 w-full rounded-md border border-border bg-muted px-3 text-sm text-muted-foreground outline-none"
            />
          )}
        </SettingsFieldLabel>
        {requiresBaseUrl ? (
          <SettingsFieldLabel label={i18n.t("provider.baseUrl")}>
            <input
              value={baseUrl}
              onChange={(event) => onBaseUrlChange(event.target.value)}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
          </SettingsFieldLabel>
        ) : null}
        <SettingsFieldLabel label={i18n.t("provider.model")}>
          <div className="flex min-w-0 items-center gap-2">
            {hasModelList ? (
              <select
                value={displayModels.some((item) => item.id === model) ? model : ""}
                onChange={(event) => onModelChange(event.target.value)}
                className="h-10 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              >
                {!displayModels.some((item) => item.id === model) ? (
                  <option value="" disabled>
                    {i18n.t("provider.selectModel")}
                  </option>
                ) : null}
                {displayModels.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label || item.id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={model}
                onChange={(event) => onModelChange(event.target.value)}
                placeholder={i18n.t("provider.modelPlaceholder")}
                className="h-10 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary"
              />
            )}
            <Button
              type="button"
              variant="outline"
              className="h-10 shrink-0 gap-2 px-3 text-sm"
              onClick={onLoadModels}
              disabled={!runtimeConnected || loadingModels || saving}
            >
              {loadingModels ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              {i18n.t("provider.refreshModels")}
            </Button>
          </div>
          {modelsError ? <div className="mt-1.5 text-xs text-destructive/80">{modelsError}</div> : null}
          {providerTestMessage ? <div className="mt-1.5 text-xs text-muted-foreground">{providerTestMessage}</div> : null}
          {hasModelList && !modelsError ? (
            <div className="mt-1.5 text-xs text-muted-foreground">{i18n.t("provider.fetchedModels", { count: models.length })}</div>
          ) : null}
        </SettingsFieldLabel>
        <SettingsFieldLabel label={i18n.t("provider.contextWindow")}>
          <div className="grid gap-2">
            <input
              type="range"
              min={4}
              max={sliderMax}
              step={1}
              value={sliderValue}
              onChange={(event) => onContextWindowChange(event.target.value)}
              aria-label={i18n.t("provider.contextWindow")}
              className="w-full accent-primary"
            />
            <div className="flex min-w-0 items-center gap-2">
              <div className="relative min-w-0 flex-1">
                <input
                  type="number"
                  min={4}
                  max={10_000}
                  step={1}
                  value={contextWindow}
                  onChange={(event) => onContextWindowChange(event.target.value)}
                  placeholder={i18n.t("provider.contextWindowAuto")}
                  aria-label={i18n.t("provider.contextWindowInput")}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 pr-10 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary"
                />
                <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-muted-foreground">K</span>
              </div>
              <Button type="button" variant="outline" className="h-10 shrink-0" disabled={!contextWindow} onClick={() => onContextWindowChange("")}>
                {i18n.t("provider.contextWindowAuto")}
              </Button>
            </div>
          </div>
          <div className={`mt-1.5 text-xs ${contextWindowValid ? "text-muted-foreground" : "text-destructive/80"}`}>
            {contextWindowValid
              ? i18n.t("provider.contextWindowHint", {
                  detected: detectedContextWindow ? `${Math.ceil(detectedContextWindow / 1000)}K` : i18n.t("provider.contextWindowUnknown"),
                })
              : i18n.t("provider.contextWindowInvalid")}
          </div>
        </SettingsFieldLabel>
        <SettingsFieldLabel label={i18n.t("provider.apiType")}>
          <select
            value={apiTypeOptions.includes(apiType) ? apiType : "auto"}
            onChange={(event) => onApiTypeChange(event.target.value as ProviderApiType)}
            className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
          >
            {apiTypeOptions.map((option) => (
              <option key={option} value={option}>{i18n.t(`provider.apiType_${option}`)}</option>
            ))}
          </select>
          <div className="mt-1.5 text-xs text-muted-foreground">{i18n.t("provider.apiTypeHint")}</div>
        </SettingsFieldLabel>
        {supportsReasoning || hasUnsupportedReasoningVariant ? (
          <SettingsFieldLabel label={i18n.t("provider.reasoningEffort")}>
            <select
              value={thinkingLevel}
              onChange={(event) => onThinkingLevelChange(event.target.value)}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            >
              <option value="default">{i18n.t("provider.reasoning_default")}</option>
              {hasUnsupportedReasoningVariant ? (
                <option value={thinkingLevel} disabled>
                  {i18n.t("provider.reasoningUnsupported", { level: thinkingLevel })}
                </option>
              ) : null}
              {supportedReasoningVariants.map((variant) => (
                <option key={variant.key} value={variant.key}>
                  {i18n.t(variant.displayKey)}
                </option>
              ))}
            </select>
            <div className={`mt-1.5 text-xs ${hasUnsupportedReasoningVariant ? "text-destructive/80" : "text-muted-foreground"}`}>
              {hasUnsupportedReasoningVariant
                ? i18n.t("provider.reasoningUnsupportedHint")
                : i18n.t("provider.reasoningEffortHint")}
            </div>
          </SettingsFieldLabel>
        ) : null}
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <Button type="button" variant="outline" disabled={!runtimeConnected || saving || Boolean(testingProvider)} onClick={() => onTestProvider("text")}>
          {testingProvider === "text" ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageSquare className="h-4 w-4" />}
          Text
        </Button>
        <Button type="button" variant="outline" disabled={!runtimeConnected || saving || Boolean(testingProvider)} onClick={() => onTestProvider("structured")}>
          {testingProvider === "structured" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          JSON
        </Button>
        <Button type="button" variant="outline" disabled={!runtimeConnected || saving || Boolean(testingProvider)} onClick={() => onTestProvider("vision")}>
          {testingProvider === "vision" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Eye className="h-4 w-4" />}
          Vision
        </Button>
        <Button disabled={!runtimeConnected || saving || !contextWindowValid || (providerNeedsApiKey(provider) && !apiKey.trim() && !keySaved) || !model.trim()} type="submit">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {i18n.t("common.save")}
        </Button>
      </div>
    </form>
  );
}

function firstPositiveContextWindow(model: ProviderModelInfo | undefined): number | undefined {
  for (const value of [model?.inputTokenLimit, model?.contextWindow, model?.maxInputTokens]) {
    if (typeof value === "number" && Number.isFinite(value) && value > 0) {
      return value;
    }
  }
  return undefined;
}

function supportedApiTypeOptions(provider: string, model: string): ProviderApiType[] {
  if (provider === "deepseek") {
    if (model.trim() === "deepseek-auto") {
      return ["auto"];
    }
    return model.trim() === "deepseek-v4-flash"
      ? ["auto", "responses", "chat_completions"]
      : ["auto", "chat_completions"];
  }
  if (provider === "custom") {
    return ["auto", "responses", "chat_completions", "messages", "generate_content"];
  }
  if (provider === "openai") {
    return ["auto", "responses", "chat_completions"];
  }
  if (provider === "gemini" || provider === "vertexai") {
    return ["auto", "generate_content"];
  }
  if (provider === "anthropic") {
    return ["auto", "messages"];
  }
  return ["auto", "chat_completions"];
}

export function VisionProfileSetup({
  provider,
  apiKey,
  baseUrl,
  model,
  enabled,
  saving,
  runtimeConnected,
  keySaved = false,
  configured = false,
  onProviderChange,
  onApiKeyChange,
  onBaseUrlChange,
  onModelChange,
  onEnabledChange,
  onSubmit,
  onClear,
}: VisionProfileSetupProps) {
  const requiresBaseUrl = PROVIDERS_REQUIRING_BASE_URL.includes(provider);
  const saveDisabled =
    !runtimeConnected || saving || !provider || !model.trim() || (providerNeedsApiKey(provider) && !apiKey.trim() && !keySaved);

  return (
    <form onSubmit={onSubmit} className="rounded-2xl border border-border bg-card p-5 shadow-composer">
      <div className="grid gap-4">
        <SettingsFieldLabel label={i18n.t("provider.apiProvider")}>
          <select
            value={provider}
            onChange={(event) => onProviderChange(event.target.value)}
            className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
          >
            <option value="">{i18n.t("vision.notConfigured")}</option>
            <option value="gemini">Google AI Studio</option>
            <option value="anthropic">Anthropic</option>
            <option value="openai">OpenAI</option>
            <option value="deepseek">DeepSeek</option>
            <option value="openrouter">OpenRouter</option>
            <option value="ollama">Ollama</option>
            <option value="vertexai">Vertex AI</option>
            <option value="custom">{i18n.t("provider.customEndpoint")}</option>
          </select>
        </SettingsFieldLabel>
        {provider ? (
          <>
            <SettingsFieldLabel label={i18n.t("provider.apiKey")}>
              {providerNeedsApiKey(provider) ? (
                <input
                  value={apiKey}
                  onChange={(event) => onApiKeyChange(event.target.value)}
                  type="password"
                  placeholder={keySaved ? i18n.t("provider.savedKeyHint") : i18n.t("provider.apiKeyPlaceholder")}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary"
                  autoComplete="off"
                />
              ) : (
                <input
                  value={i18n.t("provider.noKeyNeeded")}
                  readOnly
                  className="h-10 w-full rounded-md border border-border bg-muted px-3 text-sm text-muted-foreground outline-none"
                />
              )}
            </SettingsFieldLabel>
            {requiresBaseUrl ? (
              <SettingsFieldLabel label={i18n.t("provider.baseUrl")}>
                <input
                  value={baseUrl}
                  onChange={(event) => onBaseUrlChange(event.target.value)}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
                />
              </SettingsFieldLabel>
            ) : null}
            <SettingsFieldLabel label={i18n.t("provider.model")}>
              <input
                value={model}
                onChange={(event) => onModelChange(event.target.value)}
                placeholder={i18n.t("provider.modelPlaceholder")}
                className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary"
              />
            </SettingsFieldLabel>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(event) => onEnabledChange(event.target.checked)}
                className="h-4 w-4 accent-primary"
              />
              {i18n.t("vision.enabledLabel")}
            </label>
          </>
        ) : null}
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        {configured ? (
          <Button type="button" variant="outline" disabled={!runtimeConnected || saving} onClick={onClear}>
            {i18n.t("vision.clearProfile")}
          </Button>
        ) : null}
        <Button disabled={saveDisabled} type="submit">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {i18n.t("common.save")}
        </Button>
      </div>
    </form>
  );
}

function SettingsFieldLabel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid min-w-0 gap-2 text-sm">
      <span className="truncate font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}
