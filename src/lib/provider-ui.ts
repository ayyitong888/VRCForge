import i18n from "../i18n";

export type ProviderCapabilityTone = "ok" | "warn" | "danger" | "muted" | "default";

export type ProviderCapability = {
  label: string;
  tone: ProviderCapabilityTone;
};

export function defaultModelForProvider(provider: string): string {
  switch (provider) {
    case "anthropic":
      return "claude-opus-4-6";
    case "deepseek":
      return "deepseek-chat";
    case "openrouter":
      return "openai/gpt-4.1-mini";
    case "openai":
      return "gpt-4.1-mini";
    case "ollama":
      return "llama3.2";
    case "vertexai":
      return "gemini-2.5-flash";
    case "custom":
      return "gpt-4.1-mini";
    case "gemini":
    default:
      return "gemini-2.5-flash";
  }
}

export function providerDisplayName(provider: string): string {
  switch (provider) {
    case "anthropic":
      return "Anthropic";
    case "deepseek":
      return "DeepSeek";
    case "openrouter":
      return "OpenRouter";
    case "openai":
      return "OpenAI";
    case "ollama":
      return "Ollama";
    case "vertexai":
      return "Vertex AI";
    case "custom":
      return "Custom";
    case "gemini":
      return "Gemini";
    default:
      return provider || "Provider";
  }
}

export function defaultBaseUrlForProvider(provider: string): string {
  switch (provider) {
    case "openai":
      return "https://api.openai.com/v1";
    case "deepseek":
      return "https://api.deepseek.com";
    case "openrouter":
      return "https://openrouter.ai/api/v1";
    case "ollama":
      return "http://127.0.0.1:11434/v1";
    default:
      return "";
  }
}

export function providerNeedsApiKey(provider: string): boolean {
  return provider !== "ollama" && provider !== "vertexai";
}

export function providerCapabilities(provider: string): ProviderCapability[] {
  const paid = provider !== "ollama";
  const local = provider === "ollama";
  const capabilities: ProviderCapability[] = [
    { label: i18n.t("providerCapability.text"), tone: "muted" },
    { label: i18n.t("providerCapability.structuredJson"), tone: "muted" },
  ];
  if (["gemini", "openai", "openrouter", "vertexai"].includes(provider)) {
    capabilities.push({ label: i18n.t("providerCapability.vision"), tone: "muted" });
  }
  if (local) {
    capabilities.push(
      { label: i18n.t("providerCapability.local"), tone: "ok" },
      { label: i18n.t("providerCapability.offline"), tone: "ok" },
      { label: i18n.t("providerCapability.freeLocal"), tone: "ok" },
    );
  }
  if (paid) {
    capabilities.push({ label: i18n.t("providerCapability.paidApi"), tone: "warn" });
  }
  if (["gemini", "anthropic", "openai", "openrouter", "vertexai"].includes(provider)) {
    capabilities.push({ label: i18n.t("providerCapability.longContext"), tone: "muted" });
  }
  return capabilities;
}

export function thinkingStatusForModelLabel(provider: string, model: string): string {
  const key = `${provider || ""} ${model || ""}`.toLowerCase();
  if (/(deepseek-reasoner|deepseek-r1|\br1\b|\bo[134](?:-|$)|reason|thinking)/.test(key)) {
    return i18n.t("thinking.reasoning");
  }
  if (/(claude|anthropic)/.test(key)) {
    return i18n.t("thinking.thinking");
  }
  if (/(gemini|google|vertex)/.test(key)) {
    return i18n.t("thinking.thinking");
  }
  if (/(gpt|openai)/.test(key)) {
    return i18n.t("thinking.thinking");
  }
  if (/(deepseek|grok|x-ai|openrouter)/.test(key)) {
    return i18n.t("thinking.thinking");
  }
  if (/(ollama|llama|qwen|mistral|mixtral|phi|local|custom)/.test(key)) {
    return i18n.t("thinking.workingOnIt");
  }
  return i18n.t("thinking.workingOnIt");
}

export function thinkingTraceLabel(provider: string, model: string): string {
  const key = `${provider || ""} ${model || ""}`.toLowerCase();
  if (/(deepseek-reasoner|deepseek-r1|\br1\b|\bo[134](?:-|$)|reason|thinking)/.test(key)) {
    return i18n.t("thinking.reasoning");
  }
  return i18n.t("thinking.summary");
}
