import type { ProviderApiType } from "./api";

export function supportedApiTypeOptions(provider: string, model: string): ProviderApiType[] {
  if (provider === "chatgpt") return ["auto", "responses"];
  if (provider === "deepseek") {
    if (model.trim() === "deepseek-auto") {
      return ["auto"];
    }
    return model.trim() === "deepseek-flash" || model.trim() === "deepseek-v4-flash" || model.trim() === "deepseek-v4-pro"
      ? ["auto", "responses", "messages", "chat_completions"]
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

