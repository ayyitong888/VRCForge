import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export type ChatResourceImageContext = {
  endpoint: string;
  chatId: string;
  projectPath: string;
  sessionId: string;
  clientTurnId: string;
};

export type ChatResourceImagePayload = {
  uri: string;
  mimeType: string;
  sha256: string;
  pngBase64: string;
};

export async function loadChatResourceImage(
  endpoint: string,
  context: ChatResourceImageContext & { uri: string },
  signal?: AbortSignal,
): Promise<ChatResourceImagePayload> {
  const body = {
    uri: context.uri,
    chatId: context.chatId,
    projectPath: context.projectPath,
    sessionId: context.sessionId,
    clientTurnId: context.clientTurnId,
  };
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ChatResourceImagePayload>("fetch_agent_resource_image", {
      request: { body, timeoutMs: 10_000 },
    }, signal);
  }
  return requestJson<ChatResourceImagePayload>(`${endpoint}/api/app/agent/resource-image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs: 10_000,
    signal,
  });
}
