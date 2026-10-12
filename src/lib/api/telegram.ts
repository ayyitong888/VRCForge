import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export type TelegramStatus = {
  enabled: boolean;
  configured: boolean;
  ownerId: number | null;
  projects: string[];
  state: string;
  errorCode?: string | null;
  proxyMode?: "environment" | "direct" | "custom";
  proxyUrl?: string;
};

export type TelegramConfig = { token: string; ownerId: number; projects: string[]; proxyMode: "environment" | "direct" | "custom"; proxyUrl: string };

export function testTelegramConnection(endpoint: string, signal?: AbortSignal): Promise<{ ok: boolean }> {
  if (hasTauriInternals()) return invokeTauriWithAbort("telegram_test_connection", {request: {timeoutMs: 20000}}, signal);
  return requestJson(`${endpoint}/api/telegram/test-connection`, {method: "POST", signal});
}

export async function openTelegramBotFather(): Promise<void> {
  if (hasTauriInternals()) {
    await invokeTauriWithAbort("telegram_open_botfather", {});
  } else {
    window.open("https://t.me/BotFather", "_blank", "noopener,noreferrer");
  }
}

// Desktop commands own the authenticated loopback transport; tokens only travel
// in request bodies and remain in component memory until the save completes.
export function fetchTelegramStatus(endpoint: string, signal?: AbortSignal): Promise<TelegramStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("telegram_status", { request: { timeoutMs: 30000 } }, signal);
  }
  return requestJson(`${endpoint}/api/telegram/status`, { signal });
}

export function configureTelegram(endpoint: string, config: TelegramConfig, signal?: AbortSignal): Promise<TelegramStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("telegram_configure", { request: { ...config, timeoutMs: 30000 } }, signal);
  }
  return requestJson(`${endpoint}/api/telegram/config`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config), signal,
  });
}

export function startTelegram(endpoint: string, signal?: AbortSignal): Promise<TelegramStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("telegram_start", { request: { timeoutMs: 30000 } }, signal);
  }
  return requestJson(`${endpoint}/api/telegram/start`, { method: "POST", signal });
}

export function stopTelegram(endpoint: string, signal?: AbortSignal): Promise<TelegramStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("telegram_stop", { request: { timeoutMs: 30000 } }, signal);
  }
  return requestJson(`${endpoint}/api/telegram/stop`, { method: "POST", signal });
}
