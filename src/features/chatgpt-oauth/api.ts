import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "../../lib/api/http";

export type ChatGptOAuthAccount = {
  id: string;
  label: string;
  email: string;
  connected: boolean;
};

export type ChatGptOAuthStatus = {
  accounts: ChatGptOAuthAccount[];
  pending: boolean;
  error: string;
  activeAccountId?: string | null;
  activeModel?: string | null;
};

export type ChatGptOAuthModel = {
  id: string;
  name: string;
};

export type ChatGptOAuthModels = {
  models: ChatGptOAuthModel[];
};

export type ChatGptOAuthLogoutResult = {
  revoked: boolean;
};

export async function fetchChatGptOAuthStatus(
  endpoint: string,
  signal?: AbortSignal,
): Promise<ChatGptOAuthStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("chatgpt_oauth_status", { request: { timeoutMs: 5000 } }, signal);
  }
  return requestJson<ChatGptOAuthStatus>(`${endpoint}/api/chatgpt/status`, { signal, timeoutMs: 5000 });
}

export async function beginChatGptOAuthLogin(
  endpoint: string,
  accountId = "",
  signal?: AbortSignal,
): Promise<void> {
  if (hasTauriInternals()) {
    await invokeTauriWithAbort<void>("chatgpt_oauth_login", {
      request: { accountId, timeoutMs: 15000 },
    }, signal);
    return;
  }

  const authWindow = window.open("about:blank", "_blank");
  if (!authWindow) {
    throw new Error("The browser blocked the ChatGPT sign-in window.");
  }
  authWindow.opener = null;
  try {
    const response = await requestJson<{ authorizationUrl: string }>(`${endpoint}/api/chatgpt/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: accountId || "" }),
      signal,
      timeoutMs: 15000,
    });
    authWindow.location.replace(validateChatGptAuthorizationUrl(response.authorizationUrl));
  } catch (cause) {
    authWindow.close();
    throw cause;
  }
}

export async function cancelChatGptOAuthLogin(endpoint: string): Promise<void> {
  if (hasTauriInternals()) {
    await invokeTauriWithAbort<void>("chatgpt_oauth_cancel", { request: { timeoutMs: 10000 } });
    return;
  }
  await requestJson<unknown>(`${endpoint}/api/chatgpt/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
    timeoutMs: 10000,
  });
}

export async function logoutChatGptOAuthAccount(
  endpoint: string,
  accountId: string,
): Promise<ChatGptOAuthLogoutResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("chatgpt_oauth_logout", {
      request: { accountId, timeoutMs: 15000 },
    });
  }
  return requestJson<ChatGptOAuthLogoutResult>(`${endpoint}/api/chatgpt/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_id: accountId }),
    timeoutMs: 15000,
  });
}

export async function fetchChatGptOAuthModels(
  endpoint: string,
  accountId: string,
  signal?: AbortSignal,
): Promise<ChatGptOAuthModels> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("chatgpt_oauth_models", {
      request: { accountId, timeoutMs: 30000 },
    }, signal);
  }
  return requestJson<ChatGptOAuthModels>(`${endpoint}/api/chatgpt/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_id: accountId }),
    signal,
    timeoutMs: 30000,
  });
}

export async function activateChatGptOAuthModel(
  endpoint: string,
  accountId: string,
  model: string,
): Promise<void> {
  if (hasTauriInternals()) {
    await invokeTauriWithAbort<void>("chatgpt_oauth_activate", {
      request: { accountId, model, timeoutMs: 30000 },
    });
    return;
  }
  await requestJson<unknown>(`${endpoint}/api/chatgpt/activate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_id: accountId, model }),
    timeoutMs: 30000,
  });
}

export function validateChatGptAuthorizationUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("ChatGPT returned an invalid sign-in URL.");
  }
  if (
    url.origin !== "https://auth.openai.com"
    || url.pathname !== "/api/accounts/authorize"
    || url.username
    || url.password
    || url.hash
  ) {
    throw new Error("ChatGPT returned an unsupported sign-in URL.");
  }
  return url.toString();
}
